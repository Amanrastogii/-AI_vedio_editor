"""Data access layer — all DB reads/writes go through here, never raw queries in agents."""
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    AgentStatus, AgentTask, Clip, Output, OutputFormat,
    Project, ProjectStatus, Segment, StoryTimeline, Transcript, User,
)


# ── Project ────────────────────────────────────────────────────────────────────

class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: uuid.UUID, title: str, **kwargs) -> Project:
        project = Project(user_id=user_id, title=title, **kwargs)
        self.session.add(project)
        await self.session.commit()
        await self.session.refresh(project)
        return project

    async def get(self, project_id: uuid.UUID) -> Optional[Project]:
        result = await self.session.execute(
            select(Project)
            .where(Project.id == project_id)
            .options(selectinload(Project.clips), selectinload(Project.agent_tasks))
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID, limit: int = 50, offset: int = 0) -> List[Project]:
        result = await self.session.execute(
            select(Project)
            .where(Project.user_id == user_id)
            .order_by(Project.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def update_status(self, project_id: uuid.UUID, status: ProjectStatus, error: Optional[str] = None) -> None:
        values = {"status": status}
        if status == ProjectStatus.COMPLETED:
            values["completed_at"] = datetime.now(timezone.utc)
        if error:
            values["error_message"] = error
        await self.session.execute(update(Project).where(Project.id == project_id).values(**values))
        await self.session.commit()

    async def delete(self, project_id: uuid.UUID) -> None:
        project = await self.get(project_id)
        if project:
            await self.session.delete(project)
            await self.session.commit()


# ── Clip ───────────────────────────────────────────────────────────────────────

class ClipRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, project_id: uuid.UUID, filename: str, original_filename: str,
                     s3_key: str, s3_bucket: str, upload_order: int, **kwargs) -> Clip:
        clip = Clip(
            project_id=project_id,
            filename=filename,
            original_filename=original_filename,
            s3_key=s3_key,
            s3_bucket=s3_bucket,
            upload_order=upload_order,
            **kwargs,
        )
        self.session.add(clip)
        await self.session.commit()
        await self.session.refresh(clip)
        return clip

    async def get(self, clip_id: uuid.UUID) -> Optional[Clip]:
        result = await self.session.execute(select(Clip).where(Clip.id == clip_id))
        return result.scalar_one_or_none()

    async def list_for_project(self, project_id: uuid.UUID) -> List[Clip]:
        result = await self.session.execute(
            select(Clip)
            .where(Clip.project_id == project_id)
            .order_by(Clip.upload_order)
        )
        return list(result.scalars().all())

    async def mark_ingested(self, clip_id: uuid.UUID, metadata: dict) -> None:
        await self.session.execute(
            update(Clip)
            .where(Clip.id == clip_id)
            .values(is_ingested=True, ingestion_metadata=metadata)
        )
        await self.session.commit()

    async def update_video_info(self, clip_id: uuid.UUID, **kwargs) -> None:
        await self.session.execute(update(Clip).where(Clip.id == clip_id).values(**kwargs))
        await self.session.commit()


# ── Segment ────────────────────────────────────────────────────────────────────

class SegmentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_create(self, segments: List[dict]) -> List[Segment]:
        objs = [Segment(**s) for s in segments]
        self.session.add_all(objs)
        await self.session.commit()
        return objs

    async def list_for_clip(self, clip_id: uuid.UUID) -> List[Segment]:
        result = await self.session.execute(
            select(Segment)
            .where(Segment.clip_id == clip_id)
            .order_by(Segment.start_ms)
        )
        return list(result.scalars().all())

    async def update_scores(self, segment_id: uuid.UUID, **kwargs) -> None:
        await self.session.execute(update(Segment).where(Segment.id == segment_id).values(**kwargs))
        await self.session.commit()

    async def get_highlights(self, project_id: uuid.UUID, min_score: float = 0.7) -> List[Segment]:
        result = await self.session.execute(
            select(Segment)
            .join(Clip, Clip.id == Segment.clip_id)
            .where(Clip.project_id == project_id)
            .where(Segment.engagement_score >= min_score)
            .order_by(Segment.engagement_score.desc())
        )
        return list(result.scalars().all())


# ── Transcript ─────────────────────────────────────────────────────────────────

class TranscriptRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_create(self, words: List[dict]) -> None:
        self.session.add_all([Transcript(**w) for w in words])
        await self.session.commit()

    async def get_for_clip(self, clip_id: uuid.UUID, exclude_fillers: bool = False) -> List[Transcript]:
        q = select(Transcript).where(Transcript.clip_id == clip_id).order_by(Transcript.start_ms)
        if exclude_fillers:
            q = q.where(Transcript.is_filler.is_(False))
        result = await self.session.execute(q)
        return list(result.scalars().all())


# ── Story Timeline ─────────────────────────────────────────────────────────────

class StoryTimelineRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_create(self, entries: List[dict]) -> List[StoryTimeline]:
        objs = [StoryTimeline(**e) for e in entries]
        self.session.add_all(objs)
        await self.session.commit()
        return objs

    async def get_ordered(self, project_id: uuid.UUID) -> List[StoryTimeline]:
        result = await self.session.execute(
            select(StoryTimeline)
            .where(StoryTimeline.project_id == project_id)
            .order_by(StoryTimeline.position_order)
            .options(selectinload(StoryTimeline.segment))
        )
        return list(result.scalars().all())


# ── Output ─────────────────────────────────────────────────────────────────────

class OutputRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, project_id: uuid.UUID, format: OutputFormat, **kwargs) -> Output:
        output = Output(project_id=project_id, format=format, **kwargs)
        self.session.add(output)
        await self.session.commit()
        await self.session.refresh(output)
        return output

    async def list_for_project(self, project_id: uuid.UUID) -> List[Output]:
        result = await self.session.execute(
            select(Output).where(Output.project_id == project_id)
        )
        return list(result.scalars().all())

    async def update(self, output_id: uuid.UUID, **kwargs) -> None:
        await self.session.execute(update(Output).where(Output.id == output_id).values(**kwargs))
        await self.session.commit()


# ── Agent Task ─────────────────────────────────────────────────────────────────

class AgentTaskRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, project_id: uuid.UUID, agent_name: str,
                     clip_id: Optional[uuid.UUID] = None) -> AgentTask:
        task = AgentTask(project_id=project_id, agent_name=agent_name, clip_id=clip_id)
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def update_status(self, task_id: uuid.UUID, status: AgentStatus,
                            celery_task_id: Optional[str] = None,
                            progress_pct: Optional[int] = None,
                            result_metadata: Optional[dict] = None,
                            error_message: Optional[str] = None) -> None:
        values: dict = {"status": status}
        if celery_task_id:
            values["celery_task_id"] = celery_task_id
        if progress_pct is not None:
            values["progress_pct"] = progress_pct
        if result_metadata is not None:
            values["result_metadata"] = result_metadata
        if error_message:
            values["error_message"] = error_message
        if status == AgentStatus.RUNNING:
            values["started_at"] = datetime.now(timezone.utc)
        if status in (AgentStatus.COMPLETED, AgentStatus.FAILED):
            values["completed_at"] = datetime.now(timezone.utc)
        await self.session.execute(update(AgentTask).where(AgentTask.id == task_id).values(**values))
        await self.session.commit()

    async def get_for_project(self, project_id: uuid.UUID) -> List[AgentTask]:
        result = await self.session.execute(
            select(AgentTask)
            .where(AgentTask.project_id == project_id)
            .order_by(AgentTask.created_at)
        )
        return list(result.scalars().all())
