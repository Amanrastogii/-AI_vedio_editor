"""
BaseAgent — abstract foundation for all 11 AI agents.

Every agent:
  1. Creates / updates an AgentTask record in the DB.
  2. Publishes progress events to the EventBus (→ WebSocket → frontend).
  3. Implements `execute()` for its specific work.
  4. Publishes a completion event so the orchestrator can trigger the next agent.
"""
import abc
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.core.event_bus import EventBus, broadcast_progress
from backend.database.db import AsyncSessionLocal
from backend.database.models import AgentStatus
from backend.database.repositories import AgentTaskRepository

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    project_id: str
    task_instance: Any = None                          # Celery task (for retries)
    upstream_results: List[Dict] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    success: bool
    agent_name: str
    project_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_sec: float = 0.0


class BaseAgent(abc.ABC):
    """All agents inherit from this. Only `execute()` needs to be implemented."""

    name: str = "base_agent"
    version: str = "1.0.0"

    async def run(self, project_id: str, task_instance: Any = None,
                  upstream_results: Optional[List] = None) -> Dict:
        ctx = AgentContext(
            project_id=project_id,
            task_instance=task_instance,
            upstream_results=upstream_results or [],
        )

        started_at = datetime.now(timezone.utc)
        agent_task_id: Optional[uuid.UUID] = None

        async with AsyncSessionLocal() as session:
            repo = AgentTaskRepository(session)
            agent_task = await repo.create(
                project_id=uuid.UUID(project_id),
                agent_name=self.name,
            )
            agent_task_id = agent_task.id
            celery_id = task_instance.request.id if task_instance else None
            await repo.update_status(
                agent_task_id,
                AgentStatus.RUNNING,
                celery_task_id=celery_id,
            )

        await self._broadcast(project_id, 0, f"Starting {self.name}")
        logger.info("[%s] Starting for project %s", self.name, project_id)

        try:
            await self.pre_execute(ctx)
            result = await self.execute(ctx)
            await self.post_execute(ctx, result)

            duration = (datetime.now(timezone.utc) - started_at).total_seconds()
            result.duration_sec = duration

            async with AsyncSessionLocal() as session:
                repo = AgentTaskRepository(session)
                await repo.update_status(
                    agent_task_id,
                    AgentStatus.COMPLETED,
                    progress_pct=100,
                    result_metadata=result.data,
                )

            await self._broadcast(project_id, 100, f"{self.name} complete")
            logger.info("[%s] Completed in %.1fs for project %s", self.name, duration, project_id)
            return {"success": True, "agent": self.name, "project_id": project_id, **result.data}

        except Exception as exc:
            logger.exception("[%s] Failed for project %s: %s", self.name, project_id, exc)
            async with AsyncSessionLocal() as session:
                repo = AgentTaskRepository(session)
                await repo.update_status(
                    agent_task_id,
                    AgentStatus.FAILED,
                    error_message=str(exc),
                )
            await self._broadcast(project_id, -1, f"{self.name} failed: {exc}")

            if task_instance and task_instance.request.retries < settings.CELERY_MAX_RETRIES:
                raise task_instance.retry(exc=exc, countdown=settings.CELERY_RETRY_BACKOFF)
            raise

    async def pre_execute(self, ctx: AgentContext) -> None:
        """Override to add setup logic (e.g., model loading, temp dir creation)."""

    @abc.abstractmethod
    async def execute(self, ctx: AgentContext) -> AgentResult:
        """Agent-specific work. Must return AgentResult."""

    async def post_execute(self, ctx: AgentContext, result: AgentResult) -> None:
        """Override to add teardown or chaining logic."""

    async def _broadcast(self, project_id: str, pct: int, message: str) -> None:
        try:
            await broadcast_progress(project_id, self.name, pct, message)
        except Exception:
            pass  # Never let event bus failure crash an agent

    async def update_progress(self, ctx: AgentContext, pct: int, message: str = "") -> None:
        """Call from inside execute() to emit progress updates."""
        await self._broadcast(ctx.project_id, pct, message)
        if ctx.task_instance:
            ctx.task_instance.update_state(
                state="PROGRESS",
                meta={"progress": pct, "agent": self.name, "message": message},
            )
