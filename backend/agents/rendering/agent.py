"""
Agent 10: Rendering Agent

Responsibilities:
- Load EDL from S3
- Download all required clips
- Build ClipSegment list from story timeline + EDL
- Run FFmpegExecutor to produce output videos
- Render all target formats in parallel (YouTube, Shorts, Reels, TikTok, LinkedIn)
- Upload outputs to S3
- Write Output records to DB
"""
import asyncio
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.agents.rendering.ffmpeg_executor import ClipSegment, FFmpegExecutor
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.models import Output, OutputFormat
from backend.database.repositories import (
    ClipRepository, OutputRepository, StoryTimelineRepository,
)
from backend.storage import s3_client

logger = logging.getLogger(__name__)

FORMAT_SPECS = {
    "youtube":  {"width": 1920, "height": 1080, "aspect": "16:9"},
    "shorts":   {"width": 1080, "height": 1920, "aspect": "9:16"},
    "reels":    {"width": 1080, "height": 1920, "aspect": "9:16"},
    "tiktok":   {"width": 1080, "height": 1920, "aspect": "9:16"},
    "linkedin": {"width": 1920, "height": 1080, "aspect": "16:9"},
}


class RenderingAgent(BaseAgent):
    name = "rendering_agent"

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id
        upstream = {r.get("agent"): r for r in ctx.upstream_results if isinstance(r, dict)}

        await self.update_progress(ctx, 5, "Loading EDL")
        edl = await self._load_edl(project_id)
        color_style = edl.get("color_grade_style", "clean_bright")

        await self.update_progress(ctx, 10, "Loading story timeline")
        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(uuid.UUID(project_id))

        audio_keys: Dict[str, str] = {}
        if "audio_enhancement_agent" in upstream:
            audio_keys = upstream["audio_enhancement_agent"].get("audio_keys", {})

        subtitle_key: Optional[str] = None
        if "subtitle_agent" in upstream:
            subtitle_key = upstream["subtitle_agent"].get("srt_key")

        target_formats = settings.OUTPUT_FORMATS

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Download all unique clips needed
            await self.update_progress(ctx, 15, "Downloading video clips")
            clip_paths = await self._download_clips(timeline, tmpdir_path)

            # Download enhanced audio
            enhanced_audio_paths = await self._download_audio(audio_keys, tmpdir_path)

            # Download subtitle file
            subtitle_path = None
            if subtitle_key:
                subtitle_path = tmpdir_path / "subtitles.srt"
                try:
                    await s3_client.download_file(settings.S3_BUCKET_ASSETS, subtitle_key, subtitle_path)
                except Exception:
                    subtitle_path = None

            # Build segment list for rendering
            segments = self._build_segments(timeline, clip_paths, enhanced_audio_paths)

            if not segments:
                raise RuntimeError("No renderable segments found in timeline")

            # Render each format
            executor = FFmpegExecutor()
            rendered_outputs = []

            for i, fmt in enumerate(target_formats):
                pct = 20 + int((i / len(target_formats)) * 70)
                await self.update_progress(ctx, pct, f"Rendering {fmt}")

                output_path = tmpdir_path / f"output_{fmt}.mp4"
                try:
                    executor.render(
                        segments=segments,
                        output_path=output_path,
                        format_name=fmt,
                        subtitle_path=subtitle_path,
                        enhanced_audio_paths=enhanced_audio_paths,
                        color_grade_style=color_style,
                    )
                except Exception as e:
                    logger.error("Render failed for format %s: %s", fmt, e)
                    continue

                if not output_path.exists():
                    continue

                # Upload to S3
                s3_key = s3_client.make_output_key(project_id, fmt)
                await s3_client.upload_file(output_path, settings.S3_BUCKET_OUTPUTS, s3_key, "video/mp4")

                file_size = output_path.stat().st_size
                spec = FORMAT_SPECS.get(fmt, FORMAT_SPECS["youtube"])

                async with AsyncSessionLocal() as session:
                    out = await OutputRepository(session).create(
                        project_id=uuid.UUID(project_id),
                        format=OutputFormat(fmt),
                        aspect_ratio=spec["aspect"],
                        width=spec["width"],
                        height=spec["height"],
                        file_size_bytes=file_size,
                        s3_key=s3_key,
                        s3_bucket=settings.S3_BUCKET_OUTPUTS,
                        subtitle_s3_key=subtitle_key,
                    )
                rendered_outputs.append({"format": fmt, "s3_key": s3_key, "size_mb": round(file_size / 1e6, 1)})

        await self.update_progress(ctx, 95, "Rendering complete")
        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={"outputs": rendered_outputs, "formats_rendered": len(rendered_outputs)},
        )

    async def _load_edl(self, project_id: str) -> dict:
        edl_key = f"projects/{project_id}/edl.json"
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = Path(f.name)
        await s3_client.download_file(settings.S3_BUCKET_ASSETS, edl_key, tmp_path)
        return json.loads(tmp_path.read_text())

    async def _download_clips(self, timeline, tmpdir: Path) -> Dict[str, Path]:
        """Download each unique clip once."""
        async with AsyncSessionLocal() as session:
            clips_needed = {}
            for entry in timeline:
                if entry.segment and str(entry.segment.clip_id) not in clips_needed:
                    clip_id = str(entry.segment.clip_id)
                    clips_needed[clip_id] = entry.segment

        paths = {}
        for clip_id, seg in clips_needed.items():
            dest = tmpdir / f"clip_{clip_id}.mp4"
            async with AsyncSessionLocal() as session:
                clip = await ClipRepository(session).get(seg.clip_id)
            if clip:
                await s3_client.download_file(clip.s3_bucket, clip.s3_key, dest)
                paths[clip_id] = dest
        return paths

    async def _download_audio(self, audio_keys: Dict[str, str], tmpdir: Path) -> Dict[str, Path]:
        paths = {}
        for clip_id, key in audio_keys.items():
            dest = tmpdir / f"audio_{clip_id}.wav"
            try:
                await s3_client.download_file(settings.S3_BUCKET_ASSETS, key, dest)
                paths[clip_id] = dest
            except Exception as e:
                logger.warning("Could not download enhanced audio for clip %s: %s", clip_id, e)
        return paths

    def _build_segments(self, timeline, clip_paths: Dict[str, Path],
                         audio_paths: Dict[str, Path]) -> List[ClipSegment]:
        segments = []
        for entry in timeline:
            if not entry.segment:
                continue
            clip_id = str(entry.segment.clip_id)
            if clip_id not in clip_paths:
                continue

            seg = entry.segment
            start_ms = entry.trim_start_ms or seg.start_ms
            end_ms = entry.trim_end_ms or seg.end_ms

            segments.append(ClipSegment(
                local_path=str(clip_paths[clip_id]),
                start_ms=start_ms,
                end_ms=end_ms,
                zoom_params=entry.zoom_params,
                reframe_params=entry.reframe_params,
                transition_out_type=(entry.transition_in.value if entry.transition_in else "cut"),
            ))
        return segments
