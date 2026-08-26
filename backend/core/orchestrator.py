"""Pipeline orchestrator — launches the Celery pipeline and tracks overall project status."""
import uuid
import logging

from backend.database.db import AsyncSessionLocal
from backend.database.models import ProjectStatus
from backend.database.repositories import ProjectRepository
from backend.workers.tasks import build_pipeline

logger = logging.getLogger(__name__)


class PipelineOrchestrator:

    async def start(self, project_id: str) -> str:
        """Kick off the full 11-agent pipeline and return the Celery task ID."""
        async with AsyncSessionLocal() as session:
            repo = ProjectRepository(session)
            await repo.update_status(uuid.UUID(project_id), ProjectStatus.PROCESSING)

        pipeline = build_pipeline(project_id)
        result = pipeline.apply_async()
        logger.info("Pipeline started for project %s — root task %s", project_id, result.id)
        return result.id

    async def cancel(self, project_id: str, celery_task_id: str) -> None:
        from backend.workers.celery_app import celery_app
        celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
        async with AsyncSessionLocal() as session:
            repo = ProjectRepository(session)
            await repo.update_status(uuid.UUID(project_id), ProjectStatus.CANCELLED)
        logger.info("Pipeline cancelled for project %s", project_id)
