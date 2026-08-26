"""
Agent 9: Subtitle Agent

Responsibilities:
- Build subtitle segments from Transcript records aligned to the story timeline
- Generate SRT and VTT files
- Create word-level highlighted ASS/SSA format for TikTok/Reels style
- Upload subtitle files to S3
"""
import logging
import uuid
from pathlib import Path
from typing import List, Tuple

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.repositories import StoryTimelineRepository, TranscriptRepository
from backend.storage import s3_client

logger = logging.getLogger(__name__)


def _ms_to_srt_time(ms: int) -> str:
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1_000
    cs = (ms % 1_000) // 10
    return f"{h:02d}:{m:02d}:{s:02d},{cs*10:03d}"


def _ms_to_vtt_time(ms: int) -> str:
    return _ms_to_srt_time(ms).replace(",", ".")


class SubtitleAgent(BaseAgent):
    name = "subtitle_agent"

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        await self.update_progress(ctx, 10, "Loading story timeline")
        cue_groups = await self._build_cues(project_id)

        if not cue_groups:
            logger.warning("No subtitle cues generated for project %s", project_id)
            return AgentResult(
                success=True, agent_name=self.name, project_id=project_id,
                data={"subtitle_count": 0}
            )

        await self.update_progress(ctx, 40, "Generating SRT")
        srt_content = self._build_srt(cue_groups)

        await self.update_progress(ctx, 60, "Generating VTT")
        vtt_content = self._build_vtt(cue_groups)

        await self.update_progress(ctx, 80, "Uploading subtitle files")
        srt_key = s3_client.make_subtitle_key(project_id, "main", "srt")
        vtt_key = s3_client.make_subtitle_key(project_id, "main", "vtt")

        await s3_client.upload_bytes(srt_content.encode(), settings.S3_BUCKET_ASSETS, srt_key, "text/plain")
        await s3_client.upload_bytes(vtt_content.encode(), settings.S3_BUCKET_ASSETS, vtt_key, "text/vtt")

        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={
                "subtitle_count": len(cue_groups),
                "srt_key": srt_key,
                "vtt_key": vtt_key,
            },
        )

    async def _build_cues(self, project_id: str) -> List[Tuple[int, int, str]]:
        """Returns list of (start_ms, end_ms, text) aligned to the edit timeline."""
        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(uuid.UUID(project_id))

        # Build a timeline offset map: each entry has a position start in the output video
        output_offset_ms = 0
        cues = []

        for entry in timeline:
            if not entry.segment:
                continue
            seg = entry.segment

            # The actual clip range used
            clip_start = entry.trim_start_ms or seg.start_ms
            clip_end = entry.trim_end_ms or seg.end_ms
            used_duration_ms = clip_end - clip_start

            async with AsyncSessionLocal() as session:
                words = await TranscriptRepository(session).get_for_clip(
                    seg.clip_id, exclude_fillers=True
                )

            # Filter words within this segment's used range
            seg_words = [
                w for w in words
                if w.start_ms >= clip_start and w.end_ms <= clip_end
                and not w.is_silence
            ]

            # Group words into cues (max 8 words or 3 seconds per cue)
            current_group = []
            current_start = None

            for word in seg_words:
                word_start_in_output = output_offset_ms + (word.start_ms - clip_start)
                word_end_in_output = output_offset_ms + (word.end_ms - clip_start)

                if current_start is None:
                    current_start = word_start_in_output

                current_group.append((word_end_in_output, word.word))

                if len(current_group) >= 8 or (word_end_in_output - current_start) > 3000:
                    text = " ".join(w for _, w in current_group)
                    end_ms = current_group[-1][0]
                    cues.append((current_start, end_ms, text))
                    current_group = []
                    current_start = None

            if current_group:
                text = " ".join(w for _, w in current_group)
                end_ms = current_group[-1][0]
                cues.append((current_start or output_offset_ms, end_ms, text))

            output_offset_ms += used_duration_ms

        return cues

    def _build_srt(self, cues: List[Tuple[int, int, str]]) -> str:
        lines = []
        for i, (start, end, text) in enumerate(cues, 1):
            lines.append(str(i))
            lines.append(f"{_ms_to_srt_time(start)} --> {_ms_to_srt_time(end)}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines)

    def _build_vtt(self, cues: List[Tuple[int, int, str]]) -> str:
        lines = ["WEBVTT", ""]
        for i, (start, end, text) in enumerate(cues, 1):
            lines.append(f"cue-{i}")
            lines.append(f"{_ms_to_vtt_time(start)} --> {_ms_to_vtt_time(end)}")
            lines.append(text)
            lines.append("")
        return "\n".join(lines)
