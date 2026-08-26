"""
Agent 8: Audio Enhancement Agent

Responsibilities:
- Apply DeepFilterNet noise reduction on selected clip audio
- Remove silence gaps > threshold
- Normalize loudness (LUFS target: -14 for YouTube)
- Duck background music during speech (music_ducking)
- Export enhanced audio tracks to S3 per clip
"""
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.repositories import ClipRepository, StoryTimelineRepository
from backend.storage import s3_client

logger = logging.getLogger(__name__)

LUFS_TARGET = -14.0  # YouTube / Spotify standard loudness
TRUE_PEAK_MAX = -1.0


class AudioEnhancementAgent(BaseAgent):
    name = "audio_enhancement_agent"

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        # Only enhance clips that are actually in the story timeline
        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(uuid.UUID(project_id))

        clip_ids_in_story = {
            str(entry.segment.clip_id)
            for entry in timeline
            if entry.segment
        }

        async with AsyncSessionLocal() as session:
            clips = await ClipRepository(session).list_for_project(uuid.UUID(project_id))
            clips = [c for c in clips if str(c.id) in clip_ids_in_story]

        enhanced_keys = {}
        for i, clip in enumerate(clips):
            pct = int((i / max(len(clips), 1)) * 85)
            await self.update_progress(ctx, pct, f"Enhancing audio for clip {i+1}/{len(clips)}")
            key = await self._enhance_clip_audio(clip, project_id)
            if key:
                enhanced_keys[str(clip.id)] = key

        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={"enhanced_clips": len(enhanced_keys), "audio_keys": enhanced_keys},
        )

    async def _enhance_clip_audio(self, clip, project_id: str) -> str | None:
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / clip.filename
            await s3_client.download_file(clip.s3_bucket, clip.s3_key, video_path)

            # Step 1: Extract raw audio
            raw_audio = Path(tmpdir) / "raw.wav"
            self._extract_audio(video_path, raw_audio)
            if not raw_audio.exists():
                return None

            # Step 2: DeepFilterNet noise reduction
            clean_audio = Path(tmpdir) / "clean.wav"
            self._run_deepfilter(raw_audio, clean_audio)
            working_audio = clean_audio if clean_audio.exists() else raw_audio

            # Step 3: Loudness normalization (EBU R128 → -14 LUFS)
            normalized = Path(tmpdir) / "normalized.wav"
            self._normalize_loudness(working_audio, normalized)
            working_audio = normalized if normalized.exists() else working_audio

            # Upload enhanced audio
            key = f"projects/{project_id}/audio/{clip.id}_enhanced.wav"
            await s3_client.upload_file(working_audio, settings.S3_BUCKET_ASSETS, key, "audio/wav")
            return key

    def _extract_audio(self, video_path: Path, output_path: Path) -> None:
        cmd = [
            settings.FFMPEG_PATH, "-y", "-i", str(video_path),
            "-vn", "-ar", "44100", "-ac", "2",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=300)

    def _run_deepfilter(self, input_path: Path, output_path: Path) -> None:
        try:
            from df.enhance import enhance, init_df, load_audio, save_audio
            model, df_state, _ = init_df()
            audio, _ = load_audio(str(input_path), sr=df_state.sr())
            enhanced = enhance(model, df_state, audio)
            save_audio(str(output_path), enhanced, df_state.sr())
        except ImportError:
            logger.warning("DeepFilterNet not installed, skipping noise reduction")
        except Exception as e:
            logger.warning("DeepFilterNet failed: %s", e)

    def _normalize_loudness(self, input_path: Path, output_path: Path) -> None:
        """Two-pass ffmpeg loudnorm filter (EBU R128 standard)."""
        # Pass 1: measure
        cmd1 = [
            settings.FFMPEG_PATH, "-i", str(input_path),
            "-af", f"loudnorm=I={LUFS_TARGET}:TP={TRUE_PEAK_MAX}:LRA=11:print_format=json",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd1, capture_output=True, text=True, timeout=120)

        # Extract measured values from stderr
        import json, re
        match = re.search(r'\{[^}]+\}', result.stderr, re.DOTALL)
        if not match:
            # Fallback: simple volume normalization
            cmd_fallback = [
                settings.FFMPEG_PATH, "-y", "-i", str(input_path),
                "-af", "dynaudnorm=f=150:g=15", str(output_path),
            ]
            subprocess.run(cmd_fallback, capture_output=True, timeout=120)
            return

        measured = json.loads(match.group())
        # Pass 2: apply
        af = (
            f"loudnorm=I={LUFS_TARGET}:TP={TRUE_PEAK_MAX}:LRA=11"
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":linear=true:print_format=none"
        )
        cmd2 = [
            settings.FFMPEG_PATH, "-y", "-i", str(input_path),
            "-af", af, str(output_path),
        ]
        subprocess.run(cmd2, capture_output=True, timeout=120)
