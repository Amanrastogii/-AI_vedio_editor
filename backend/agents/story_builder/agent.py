"""
Agent 6: Story Builder Agent (LLM)

Responsibilities:
- Aggregate all analysis data (transcripts, emotions, faces, scenes) into structured context
- Call Claude claude-sonnet-4-6 with expert video editor system prompt
- Parse LLM response into StoryTimeline records
- Write ordered StoryTimeline to DB
"""
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.agents.story_builder.context_aggregator import build_context
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.models import NarrativeRole, TransitionType
from backend.database.repositories import StoryTimelineRepository

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "ml" / "prompts" / "story_builder_v1.txt"


class StoryBuilderAgent(BaseAgent):
    name = "story_builder_agent"

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        await self.update_progress(ctx, 10, "Aggregating analysis data")
        context = await build_context(project_id)

        await self.update_progress(ctx, 30, "Calling Claude for story planning")
        story_plan = await self._call_llm(context)

        if not story_plan:
            raise RuntimeError("LLM returned empty story plan")

        await self.update_progress(ctx, 80, "Writing story timeline to database")
        count = await self._save_story(project_id, story_plan)

        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={
                "story_title": story_plan.get("narrative_title", ""),
                "story_summary": story_plan.get("narrative_summary", ""),
                "segments_in_story": count,
                "estimated_duration_sec": story_plan.get("estimated_duration_sec", 0),
            },
        )

    async def _call_llm(self, context: Dict[str, Any]) -> Optional[Dict]:
        """Call Claude with the full analysis context and get back a story plan."""
        prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        # Keep context JSON compact but readable
        context_json = json.dumps(context, indent=2, default=str)
        # Truncate if context is very large (> 100k chars)
        if len(context_json) > 80000:
            context_json = self._compress_context(context)

        prompt = prompt_template.replace("{context_json}", context_json)

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=settings.CLAUDE_STORY_MODEL,
            max_tokens=settings.CLAUDE_MAX_TOKENS,
            system=(
                "You are an expert video editor. Always respond with valid JSON only. "
                "No markdown, no explanations outside the JSON."
            ),
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        # Strip potential markdown code fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("LLM returned invalid JSON: %s\nRaw: %s", e, raw[:500])
            raise RuntimeError(f"Story Builder LLM returned invalid JSON: {e}")

    def _compress_context(self, context: Dict) -> str:
        """Reduce context size by keeping only top highlights per clip."""
        compressed = {
            "project_id": context["project_id"],
            "total_clips": context["total_clips"],
            "clips": [],
        }
        for clip in context["clips"]:
            compressed["clips"].append({
                "clip_id": clip["clip_id"],
                "filename": clip["filename"],
                "upload_order": clip["upload_order"],
                "duration_sec": clip["duration_sec"],
                "transcript_excerpt": clip["transcript_excerpt"][:500],
                "top_highlights": clip["top_highlights"][:3],
            })
        return json.dumps(compressed, default=str)

    async def _save_story(self, project_id: str, story_plan: Dict) -> int:
        story = story_plan.get("story", [])
        rows = []

        for entry in story:
            narrative_role_str = entry.get("narrative_role", "context")
            try:
                narrative_role = NarrativeRole(narrative_role_str)
            except ValueError:
                narrative_role = NarrativeRole.CONTEXT

            transition_str = entry.get("suggested_transition_out", "cut")
            try:
                transition = TransitionType(transition_str)
            except ValueError:
                transition = TransitionType.CUT

            segment_id_str = entry.get("segment_id")
            segment_uuid = uuid.UUID(segment_id_str) if segment_id_str else None

            trim_start = entry.get("trim_start_sec")
            trim_end = entry.get("trim_end_sec")

            rows.append({
                "project_id": uuid.UUID(project_id),
                "segment_id": segment_uuid,
                "position_order": int(entry.get("position", len(rows) + 1)),
                "narrative_role": narrative_role,
                "transition_in": transition,
                "trim_start_ms": int(trim_start * 1000) if trim_start is not None else None,
                "trim_end_ms": int(trim_end * 1000) if trim_end is not None else None,
                "edit_reasoning": entry.get("reasoning", ""),
            })

        async with AsyncSessionLocal() as session:
            repo = StoryTimelineRepository(session)
            await repo.bulk_create(rows)

        return len(rows)
