"""
Aggregates all analysis results from Agents 2-5 into a structured JSON
that gets sent to Claude for story building.
"""
import uuid
from typing import Any, Dict, List

from backend.database.db import AsyncSessionLocal
from backend.database.repositories import (
    ClipRepository, SegmentRepository, TranscriptRepository,
)


async def build_context(project_id: str) -> Dict[str, Any]:
    """Build the full analysis context dict to pass to the LLM."""
    pid = uuid.UUID(project_id)

    async with AsyncSessionLocal() as session:
        clips = await ClipRepository(session).list_for_project(pid)
        clips_data = []

        for clip in clips:
            segments = await SegmentRepository(session).list_for_clip(clip.id)
            transcripts = await TranscriptRepository(session).get_for_clip(clip.id, exclude_fillers=True)

            # Build transcript text with timestamps
            full_transcript = " ".join(
                f"[{t.start_ms/1000:.1f}s] {t.word}" for t in transcripts
            )

            # Summarize segments
            seg_summaries = []
            for seg in segments:
                seg_summaries.append({
                    "id": str(seg.id),
                    "start_sec": round(seg.start_ms / 1000, 1),
                    "end_sec": round(seg.end_ms / 1000, 1),
                    "type": seg.segment_type.value,
                    "quality": round(seg.quality_score or 0, 2),
                    "engagement": round(seg.engagement_score or 0, 2),
                    "has_face": seg.has_face,
                    "has_speech": seg.has_speech,
                    "emotions": seg.emotion_labels or {},
                    "description": seg.scene_description or "",
                    "is_blurry": seg.is_blurry,
                    "is_shaky": seg.is_shaky,
                })

            # Find highlights (top 3 by engagement)
            highlights = sorted(
                [s for s in seg_summaries if s["type"] in ("highlight", "good")],
                key=lambda x: x["engagement"],
                reverse=True,
            )[:5]

            clips_data.append({
                "clip_id": str(clip.id),
                "filename": clip.original_filename,
                "upload_order": clip.upload_order,
                "duration_sec": round((clip.duration_ms or 0) / 1000, 1),
                "resolution": f"{clip.width}x{clip.height}" if clip.width else "unknown",
                "total_segments": len(seg_summaries),
                "transcript_excerpt": full_transcript[:2000],  # cap for LLM context
                "top_highlights": highlights,
                "all_segments": seg_summaries,
            })

    return {
        "project_id": project_id,
        "total_clips": len(clips_data),
        "clips": clips_data,
    }
