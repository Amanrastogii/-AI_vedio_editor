"""
Agent 1: Video Ingestion Agent

Responsibilities:
- Download each clip from S3 (user uploaded)
- Run ffprobe to extract metadata (codec, fps, resolution, duration, bitrate)
- Detect basic quality issues (corrupted file, no audio, unsupported format)
- Generate thumbnail (frame at 1s)
- Write metadata to DB
- Publish clip.ingested event
"""
import json
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.repositories import ClipRepository, ProjectRepository
from backend.storage import s3_client

logger = logging.getLogger(__name__)


class IngestionAgent(BaseAgent):
    name = "ingestion_agent"

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        async with AsyncSessionLocal() as session:
            clip_repo = ClipRepository(session)
            clips = await clip_repo.list_for_project(uuid.UUID(project_id))

        if not clips:
            raise ValueError(f"No clips found for project {project_id}")

        results = []
        for i, clip in enumerate(clips):
            pct = int((i / len(clips)) * 90)
            await self.update_progress(ctx, pct, f"Ingesting clip {i+1}/{len(clips)}: {clip.original_filename}")

            clip_data = await self._ingest_clip(clip, project_id)
            results.append(clip_data)

        await self.update_progress(ctx, 95, "Finalizing ingestion")
        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={"clips_ingested": len(results), "clips": results},
        )

    async def _ingest_clip(self, clip, project_id: str) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / clip.filename

            # Download from S3
            await s3_client.download_file(clip.s3_bucket, clip.s3_key, local_path)

            # Extract metadata via ffprobe
            metadata = self._run_ffprobe(local_path)

            # Generate thumbnail
            thumb_path = Path(tmpdir) / f"{clip.id}_thumb.jpg"
            self._extract_thumbnail(local_path, thumb_path)

            # Upload thumbnail
            thumb_key = s3_client.make_thumbnail_key(project_id, str(clip.id))
            if thumb_path.exists():
                await s3_client.upload_file(thumb_path, settings.S3_BUCKET_ASSETS, thumb_key, "image/jpeg")

            # Update DB
            video_stream = self._get_video_stream(metadata)
            audio_stream = self._get_audio_stream(metadata)
            format_info = metadata.get("format", {})

            video_info = {
                "duration_ms": int(float(format_info.get("duration", 0)) * 1000),
                "fps": eval(video_stream.get("r_frame_rate", "0/1"))
                       if video_stream else None,
                "width": int(video_stream.get("width", 0)) if video_stream else None,
                "height": int(video_stream.get("height", 0)) if video_stream else None,
                "codec_video": video_stream.get("codec_name") if video_stream else None,
                "codec_audio": audio_stream.get("codec_name") if audio_stream else None,
                "bitrate_kbps": int(int(format_info.get("bit_rate", 0)) / 1000),
                "file_size_bytes": int(format_info.get("size", 0)),
                "thumbnail_s3_key": thumb_key,
            }

            async with AsyncSessionLocal() as session:
                repo = ClipRepository(session)
                await repo.update_video_info(clip.id, **video_info)
                await repo.mark_ingested(clip.id, metadata)

            return {"clip_id": str(clip.id), **video_info}

    def _run_ffprobe(self, path: Path) -> dict:
        cmd = [
            settings.FFPROBE_PATH, "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr}")
        return json.loads(result.stdout)

    def _extract_thumbnail(self, video_path: Path, output_path: Path) -> None:
        cmd = [
            settings.FFMPEG_PATH, "-y",
            "-ss", "00:00:01",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)

    def _get_video_stream(self, metadata: dict) -> dict | None:
        for s in metadata.get("streams", []):
            if s.get("codec_type") == "video":
                return s
        return None

    def _get_audio_stream(self, metadata: dict) -> dict | None:
        for s in metadata.get("streams", []):
            if s.get("codec_type") == "audio":
                return s
        return None
