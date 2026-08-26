"""
All 11 agent Celery tasks, wired into a pipeline using chord/chain/group.

Pipeline flow:
  ingest → detect_scenes → [speech + faces + emotions (parallel)] →
  build_story → make_editing_decisions → [enhance_audio + subtitles (parallel)] →
  render_video → quality_check
"""
import uuid
from celery import chain, chord, group, shared_task

from .celery_app import celery_app


# ── Helper: build the full Celery workflow for a project ──────────────────────

def build_pipeline(project_id: str) -> chain:
    """Returns a Celery chord/chain that runs all 11 agents in order."""
    pid = project_id

    analysis_group = group(
        analyze_speech_task.si(pid),
        detect_faces_task.si(pid),
        analyze_emotions_task.si(pid),
    )

    post_analysis_chain = chain(
        build_story_task.si(pid),
        make_editing_decisions_task.si(pid),
        chord(
            group(
                enhance_audio_task.si(pid),
                generate_subtitles_task.si(pid),
            ),
            render_video_task.si(pid),
        ),
        quality_check_task.si(pid),
        notify_pipeline_complete.si(pid),
    )

    return chain(
        ingest_video_task.si(pid),
        detect_scenes_task.si(pid),
        chord(analysis_group, post_analysis_chain),
    )


# ── Agent 1: Video Ingestion ──────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.ingest_video_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def ingest_video_task(self, project_id: str) -> dict:
    from backend.agents.ingestion.agent import IngestionAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        IngestionAgent().run(project_id=project_id, task_instance=self)
    )


# ── Agent 2: Scene Detection ──────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.detect_scenes_task",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def detect_scenes_task(self, project_id: str) -> dict:
    from backend.agents.scene_detection.agent import SceneDetectionAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        SceneDetectionAgent().run(project_id=project_id, task_instance=self)
    )


# ── Agent 3: Speech Analysis ──────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.analyze_speech_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def analyze_speech_task(self, project_id: str) -> dict:
    from backend.agents.speech_analysis.agent import SpeechAnalysisAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        SpeechAnalysisAgent().run(project_id=project_id, task_instance=self)
    )


# ── Agent 4: Face Detection ───────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.detect_faces_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def detect_faces_task(self, project_id: str) -> dict:
    from backend.agents.face_detection.agent import FaceDetectionAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        FaceDetectionAgent().run(project_id=project_id, task_instance=self)
    )


# ── Agent 5: Emotion Analysis ─────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.analyze_emotions_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def analyze_emotions_task(self, project_id: str) -> dict:
    from backend.agents.emotion_analysis.agent import EmotionAnalysisAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        EmotionAnalysisAgent().run(project_id=project_id, task_instance=self)
    )


# ── Agent 6: Story Builder ────────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.build_story_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def build_story_task(self, results: list, project_id: str) -> dict:
    """results = list of outputs from the parallel analysis chord."""
    from backend.agents.story_builder.agent import StoryBuilderAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        StoryBuilderAgent().run(project_id=project_id, upstream_results=results, task_instance=self)
    )


# ── Agent 7: Editing Decision ─────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.make_editing_decisions_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def make_editing_decisions_task(self, project_id: str) -> dict:
    from backend.agents.editing_decision.agent import EditingDecisionAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        EditingDecisionAgent().run(project_id=project_id, task_instance=self)
    )


# ── Agent 8: Audio Enhancement ────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.enhance_audio_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def enhance_audio_task(self, project_id: str) -> dict:
    from backend.agents.audio_enhancement.agent import AudioEnhancementAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        AudioEnhancementAgent().run(project_id=project_id, task_instance=self)
    )


# ── Agent 9: Subtitle ─────────────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.generate_subtitles_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def generate_subtitles_task(self, project_id: str) -> dict:
    from backend.agents.subtitle.agent import SubtitleAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        SubtitleAgent().run(project_id=project_id, task_instance=self)
    )


# ── Agent 10: Rendering ───────────────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.render_video_task",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
)
def render_video_task(self, results: list, project_id: str) -> dict:
    """results = outputs from [enhance_audio, generate_subtitles] chord."""
    from backend.agents.rendering.agent import RenderingAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        RenderingAgent().run(project_id=project_id, upstream_results=results, task_instance=self)
    )


# ── Agent 11: Quality Assurance ───────────────────────────────────────────────

@celery_app.task(
    name="backend.workers.tasks.quality_check_task",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def quality_check_task(self, project_id: str) -> dict:
    from backend.agents.quality_assurance.agent import QualityAssuranceAgent
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        QualityAssuranceAgent().run(project_id=project_id, task_instance=self)
    )


# ── Pipeline complete notification ────────────────────────────────────────────

@celery_app.task(name="backend.workers.tasks.notify_pipeline_complete")
def notify_pipeline_complete(project_id: str) -> dict:
    from backend.core.event_bus import EventBus
    import asyncio
    async def _notify():
        bus = EventBus()
        await bus.publish(f"project:{project_id}", {"event": "pipeline.complete", "project_id": project_id})
    asyncio.get_event_loop().run_until_complete(_notify())
    return {"status": "notified", "project_id": project_id}


# ── Maintenance ───────────────────────────────────────────────────────────────

@celery_app.task(name="backend.workers.tasks.cleanup_stale_tasks")
def cleanup_stale_tasks() -> dict:
    """Mark agent tasks that have been RUNNING for > 2h as FAILED."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    from backend.database.db import AsyncSessionLocal
    from backend.database.models import AgentStatus, AgentTask
    from sqlalchemy import update

    async def _cleanup():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(AgentTask)
                .where(AgentTask.status == AgentStatus.RUNNING)
                .where(AgentTask.started_at < cutoff)
                .values(status=AgentStatus.FAILED, error_message="Timed out after 2h")
            )
            await session.commit()

    asyncio.get_event_loop().run_until_complete(_cleanup())
    return {"status": "cleaned"}
