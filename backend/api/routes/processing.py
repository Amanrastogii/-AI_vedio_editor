import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.schemas import PipelineStatusResponse, ProcessRequest, ProcessResponse
from backend.config import settings
from backend.core.orchestrator import PipelineOrchestrator
from backend.database.db import get_db
from backend.database.models import ProjectStatus, User
from backend.database.repositories import AgentTaskRepository, ProjectRepository

router = APIRouter(prefix="/projects/{project_id}", tags=["processing"])


@router.post("/process", response_model=ProcessResponse)
async def start_processing(
    project_id: uuid.UUID,
    body: ProcessRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    repo = ProjectRepository(db)
    project = await repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status == ProjectStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Pipeline already running")
    if project.status == ProjectStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Project already completed. Use /reprocess to re-run.")

    # Apply any overrides from request
    if body.output_formats or body.target_style or body.target_duration_sec:
        from sqlalchemy import update
        from backend.database.models import Project
        updates = {}
        if body.output_formats:
            updates["output_formats"] = body.output_formats
        if body.target_style:
            updates["target_style"] = body.target_style
        if body.target_duration_sec:
            updates["target_duration_sec"] = body.target_duration_sec
        if updates:
            await db.execute(update(Project).where(Project.id == project_id).values(**updates))
            await db.commit()

    formats = body.output_formats or project.output_formats or settings.OUTPUT_FORMATS

    if settings.LOCAL_MODE:
        # Run the 11-agent pipeline in-process (no Celery/Redis needed locally).
        from backend.core.local_pipeline import launch
        launch(str(project_id), formats)
        return ProcessResponse(
            project_id=project_id,
            celery_task_id=f"local-{project_id}",
            message="Local pipeline started",
        )

    orchestrator = PipelineOrchestrator()
    celery_task_id = await orchestrator.start(str(project_id))
    return ProcessResponse(
        project_id=project_id,
        celery_task_id=celery_task_id,
    )


@router.get("/pipeline", response_model=PipelineStatusResponse)
async def pipeline_status(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    task_repo = AgentTaskRepository(db)
    agent_tasks = await task_repo.get_for_project(project_id)

    agents = [
        {
            "agent": t.agent_name,
            "status": t.status.value,
            "progress_pct": t.progress_pct,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            "error": t.error_message,
        }
        for t in agent_tasks
    ]

    return PipelineStatusResponse(
        project_id=project_id,
        project_status=project.status.value,
        agents=agents,
    )


@router.post("/cancel", status_code=200)
async def cancel_pipeline(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status != ProjectStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="No active pipeline to cancel")

    task_repo = AgentTaskRepository(db)
    tasks = await task_repo.get_for_project(project_id)
    running = [t for t in tasks if t.celery_task_id and t.status.value == "running"]

    orchestrator = PipelineOrchestrator()
    for t in running:
        await orchestrator.cancel(str(project_id), t.celery_task_id)

    return {"message": "Pipeline cancellation requested"}
