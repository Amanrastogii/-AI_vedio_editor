"""
Agent 7: Editing Decision Agent (LLM)

The most critical agent. Takes the story plan and makes frame-accurate editing decisions:
- Exact cut points (ms precision)
- Transition types and durations
- Zoom/pan/reframe parameters
- Audio adjustments per segment
- Color grade and music mood selection

Outputs a machine-readable EDL (Edit Decision List) stored in the DB and S3.
"""
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.models import TransitionType
from backend.database.repositories import SegmentRepository, StoryTimelineRepository
from backend.storage import s3_client

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "ml" / "prompts" / "editing_decision_v1.txt"


class EditingDecisionAgent(BaseAgent):
    name = "editing_decision_agent"

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        await self.update_progress(ctx, 10, "Loading story plan")
        story_json, segments_json = await self._load_context(project_id)

        await self.update_progress(ctx, 30, "Calling Claude for editing decisions")
        edl = await self._call_llm(story_json, segments_json)

        if not edl:
            raise RuntimeError("LLM returned empty EDL")

        await self.update_progress(ctx, 70, "Updating timeline with precise cut points")
        await self._apply_edl_to_timeline(project_id, edl)

        # Store EDL as JSON in S3 for the Rendering Agent to consume
        await self.update_progress(ctx, 85, "Saving EDL to S3")
        edl_key = f"projects/{project_id}/edl.json"
        edl_bytes = json.dumps(edl, indent=2).encode()
        await s3_client.upload_bytes(edl_bytes, settings.S3_BUCKET_ASSETS, edl_key, "application/json")

        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={
                "edl_s3_key": edl_key,
                "total_cuts": len(edl.get("edit_decisions", [])),
                "total_duration_ms": edl.get("total_duration_ms", 0),
                "color_grade_style": edl.get("color_grade_style", "clean_bright"),
                "background_music_mood": edl.get("background_music_mood", "uplifting_energetic"),
            },
        )

    async def _load_context(self, project_id: str):
        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(uuid.UUID(project_id))

        story_entries = []
        segment_details = []

        for entry in timeline:
            story_entries.append({
                "position": entry.position_order,
                "segment_id": str(entry.segment_id) if entry.segment_id else None,
                "narrative_role": entry.narrative_role.value,
                "trim_start_ms": entry.trim_start_ms,
                "trim_end_ms": entry.trim_end_ms,
                "reasoning": entry.edit_reasoning,
            })

            if entry.segment:
                seg = entry.segment
                segment_details.append({
                    "segment_id": str(seg.id),
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "quality_score": seg.quality_score,
                    "engagement_score": seg.engagement_score,
                    "has_face": seg.has_face,
                    "has_speech": seg.has_speech,
                    "emotions": seg.emotion_labels or {},
                    "description": seg.scene_description or "",
                    "is_shaky": seg.is_shaky,
                    "suggested_trim_start": entry.trim_start_ms,
                    "suggested_trim_end": entry.trim_end_ms,
                })

        return json.dumps(story_entries, indent=2), json.dumps(segment_details, indent=2)

    async def _call_llm(self, story_json: str, segments_json: str) -> Optional[Dict]:
        prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        prompt = (
            prompt_template
            .replace("{story_json}", story_json)
            .replace("{segments_json}", segments_json)
        )

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=settings.CLAUDE_EDIT_MODEL,
            max_tokens=settings.CLAUDE_MAX_TOKENS,
            system=(
                "You are a master video editor. "
                "Respond with valid JSON only. No markdown fences, no prose."
            ),
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("EDL LLM returned invalid JSON: %s", e)
            raise RuntimeError(f"Editing Decision LLM returned invalid JSON: {e}")

    async def _apply_edl_to_timeline(self, project_id: str, edl: Dict) -> None:
        """Update StoryTimeline rows with the precise EDL values."""
        decisions = edl.get("edit_decisions", [])

        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(uuid.UUID(project_id))
            timeline_map = {entry.position_order: entry for entry in timeline}

        for decision in decisions:
            position = decision.get("position")
            if position not in timeline_map:
                continue
            entry = timeline_map[position]

            zoom = decision.get("zoom", {})
            reframe = decision.get("reframe", {})
            audio = decision.get("audio_adjustments", {})

            transition_str = decision.get("transition_in_type", "cut")
            try:
                transition = TransitionType(transition_str)
            except ValueError:
                transition = TransitionType.CUT

            async with AsyncSessionLocal() as session:
                from sqlalchemy import update
                from backend.database.models import StoryTimeline
                await session.execute(
                    update(StoryTimeline)
                    .where(StoryTimeline.id == entry.id)
                    .values(
                        trim_start_ms=decision.get("precise_start_ms", entry.trim_start_ms),
                        trim_end_ms=decision.get("precise_end_ms", entry.trim_end_ms),
                        transition_in=transition,
                        zoom_params={
                            "enabled": zoom.get("enabled", False),
                            "start_scale": zoom.get("start_scale", 1.0),
                            "end_scale": zoom.get("end_scale", 1.0),
                            "duration_ms": zoom.get("duration_ms", 0),
                            "easing": zoom.get("easing", "linear"),
                        } if zoom.get("enabled") else None,
                        reframe_params={
                            "x": reframe.get("x_offset", 0.0),
                            "y": reframe.get("y_offset", 0.0),
                        } if reframe else None,
                        edit_reasoning=decision.get("cut_reasoning", entry.edit_reasoning),
                    )
                )
                await session.commit()
