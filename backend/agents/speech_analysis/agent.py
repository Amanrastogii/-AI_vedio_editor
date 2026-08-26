"""
Agent 3: Speech Analysis Agent

Responsibilities:
- Transcribe all clips with WhisperX (word-level timestamps)
- Speaker diarization with pyannote
- Detect silence regions
- Flag filler words (um, uh, like...)
- Write Transcript records (one row per word) to DB
"""
import logging
import tempfile
import uuid
from pathlib import Path
from typing import List

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.repositories import ClipRepository, TranscriptRepository
from backend.storage import s3_client

logger = logging.getLogger(__name__)

FILLER_WORDS = set(w.lower() for w in settings.FILLER_WORDS)


class SpeechAnalysisAgent(BaseAgent):
    name = "speech_analysis_agent"
    _model = None  # lazy-loaded WhisperX model

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        async with AsyncSessionLocal() as session:
            clips = await ClipRepository(session).list_for_project(uuid.UUID(project_id))
            clips = [c for c in clips if c.is_ingested]

        total_words = 0
        for i, clip in enumerate(clips):
            pct = int((i / len(clips)) * 85)
            await self.update_progress(ctx, pct, f"Transcribing clip {i+1}/{len(clips)}")
            count = await self._transcribe_clip(clip)
            total_words += count

        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={"total_words": total_words, "clips_transcribed": len(clips)},
        )

    async def _transcribe_clip(self, clip) -> int:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / f"{clip.id}.wav"

            # Extract audio with ffmpeg (16kHz mono — Whisper's native format)
            await self._extract_audio(clip.s3_bucket, clip.s3_key, audio_path, tmpdir)

            if not audio_path.exists():
                logger.warning("No audio extracted for clip %s", clip.id)
                return 0

            words = self._run_whisperx(audio_path)
            rows = self._build_transcript_rows(clip.id, words)

            async with AsyncSessionLocal() as session:
                await TranscriptRepository(session).bulk_create(rows)

            return len(rows)

    async def _extract_audio(self, bucket: str, key: str, audio_path: Path, tmpdir: str) -> None:
        video_path = Path(tmpdir) / "input.mp4"
        await s3_client.download_file(bucket, key, video_path)

        import subprocess
        cmd = [
            settings.FFMPEG_PATH, "-y",
            "-i", str(video_path),
            "-ar", "16000",      # 16kHz
            "-ac", "1",          # mono
            "-f", "wav",
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"Audio extraction failed: {result.stderr.decode()}")

    def _run_whisperx(self, audio_path: Path) -> List[dict]:
        """Run WhisperX and return word-level dicts with speaker labels."""
        try:
            import whisperx
            import torch

            device = settings.WHISPERX_DEVICE
            compute_type = settings.WHISPERX_COMPUTE_TYPE

            if SpeechAnalysisAgent._model is None:
                SpeechAnalysisAgent._model = whisperx.load_model(
                    settings.WHISPERX_MODEL_SIZE,
                    device=device,
                    compute_type=compute_type,
                )

            model = SpeechAnalysisAgent._model
            audio = whisperx.load_audio(str(audio_path))

            # Transcribe
            result = model.transcribe(audio, batch_size=settings.WHISPERX_BATCH_SIZE)

            # Word-level alignment
            align_model, metadata = whisperx.load_align_model(
                language_code=result["language"], device=device
            )
            result = whisperx.align(
                result["segments"], align_model, metadata, audio, device,
                return_char_alignments=False,
            )

            # Speaker diarization
            try:
                diarize_model = whisperx.DiarizationPipeline(use_auth_token=None, device=device)
                diarize_segments = diarize_model(audio)
                result = whisperx.assign_word_speakers(diarize_segments, result)
            except Exception as e:
                logger.debug("Diarization failed (likely missing HF token): %s", e)

            words = []
            for seg in result.get("segments", []):
                for w in seg.get("words", []):
                    words.append({
                        "word": w.get("word", "").strip(),
                        "start": w.get("start", 0.0),
                        "end": w.get("end", 0.0),
                        "score": w.get("score", 1.0),
                        "speaker": w.get("speaker", seg.get("speaker", "SPEAKER_00")),
                    })
            return words

        except ImportError:
            logger.error("WhisperX not installed. Run: pip install whisperx")
            return []
        except Exception as e:
            logger.exception("WhisperX failed: %s", e)
            return []

    def _build_transcript_rows(self, clip_id: uuid.UUID, words: List[dict]) -> List[dict]:
        rows = []
        for w in words:
            text = w.get("word", "").strip().lower().strip(".,!?;:")
            is_filler = text in FILLER_WORDS
            is_silence = not text

            rows.append({
                "clip_id": clip_id,
                "speaker_id": w.get("speaker", "SPEAKER_00"),
                "word": w.get("word", ""),
                "start_ms": int(w.get("start", 0) * 1000),
                "end_ms": int(w.get("end", 0) * 1000),
                "confidence": float(w.get("score", 1.0)),
                "is_filler": is_filler,
                "is_silence": is_silence,
            })
        return rows
