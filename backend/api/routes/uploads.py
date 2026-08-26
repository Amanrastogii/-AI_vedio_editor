import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.schemas import (
    ClipResponse, CompleteUploadRequest, InitiateUploadRequest, InitiateUploadResponse,
)
from backend.config import settings
from backend.database.db import get_db
from backend.database.models import ProjectStatus, User
from backend.database.repositories import ClipRepository, ProjectRepository
from backend.storage import local_storage, s3_client

router = APIRouter(prefix="/projects/{project_id}/uploads", tags=["uploads"])


@router.post("/initiate", response_model=InitiateUploadResponse)
async def initiate_upload(
    project_id: uuid.UUID,
    body: InitiateUploadRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status not in (ProjectStatus.CREATED, ProjectStatus.UPLOADING):
        raise HTTPException(status_code=400, detail="Project is already processing")

    # Count existing clips to determine order
    clip_repo = ClipRepository(db)
    existing = await clip_repo.list_for_project(project_id)
    upload_order = len(existing) + 1

    clip_id = uuid.uuid4()
    s3_key = s3_client.make_video_key(str(project_id), str(clip_id), body.filename)

    # Create clip record
    clip = await clip_repo.create(
        project_id=project_id,
        filename=Path(body.filename).name,
        original_filename=body.filename,
        s3_key=s3_key,
        s3_bucket=settings.S3_BUCKET_VIDEOS,
        upload_order=upload_order,
        file_size_bytes=body.file_size_bytes,
    )

    # Generate presigned upload URL (PUT)
    presigned_url = await s3_client.generate_presigned_url(
        settings.S3_BUCKET_VIDEOS, s3_key,
        expiry=3600,
        method="put_object",
    )

    await proj_repo.update_status(project_id, ProjectStatus.UPLOADING)

    return InitiateUploadResponse(
        upload_id=str(uuid.uuid4()),  # client correlation ID
        clip_id=clip.id,
        s3_key=s3_key,
        presigned_url=presigned_url,
    )


@router.post("/local", response_model=ClipResponse)
async def upload_local(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Direct multipart upload to local disk (LOCAL_MODE — no S3/presign needed)."""
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status not in (ProjectStatus.CREATED, ProjectStatus.UPLOADING):
        raise HTTPException(status_code=400, detail="Project is already processing")

    clip_repo = ClipRepository(db)
    existing = await clip_repo.list_for_project(project_id)
    upload_order = len(existing) + 1

    clip_id = uuid.uuid4()
    key = local_storage.make_video_key(str(project_id), str(clip_id), file.filename or "clip.mp4")
    size = await local_storage.save_upload(key, file)

    clip = await clip_repo.create(
        project_id=project_id,
        filename=Path(file.filename or "clip.mp4").name,
        original_filename=file.filename or "clip.mp4",
        s3_key=key,
        s3_bucket="local",
        upload_order=upload_order,
        file_size_bytes=size,
        id=clip_id,
    )
    await proj_repo.update_status(project_id, ProjectStatus.UPLOADING)
    return ClipResponse.model_validate(clip)


@router.post("/complete")
async def complete_upload(
    project_id: uuid.UUID,
    body: CompleteUploadRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    # Verify object actually exists in S3
    exists = await s3_client.object_exists(settings.S3_BUCKET_VIDEOS, body.s3_key)
    if not exists:
        raise HTTPException(status_code=400, detail="Upload not found in S3 — ensure PUT completed successfully")

    return {"message": "Upload registered", "clip_id": str(body.clip_id)}


@router.get("", response_model=List[ClipResponse])
async def list_clips(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    clip_repo = ClipRepository(db)
    clips = await clip_repo.list_for_project(project_id)

    responses = []
    for clip in clips:
        thumb_url = None
        if clip.thumbnail_s3_key:
            thumb_url = await s3_client.generate_presigned_url(
                settings.S3_BUCKET_ASSETS, clip.thumbnail_s3_key
            )
        cr = ClipResponse.model_validate(clip)
        cr.thumbnail_url = thumb_url
        responses.append(cr)
    return responses


@router.delete("/{clip_id}", status_code=204)
async def delete_clip(
    project_id: uuid.UUID,
    clip_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status not in (ProjectStatus.CREATED, ProjectStatus.UPLOADING):
        raise HTTPException(status_code=400, detail="Cannot delete clips while processing")

    clip_repo = ClipRepository(db)
    clip = await clip_repo.get(clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    await s3_client.delete_object(clip.s3_bucket, clip.s3_key)
    # DB cascade deletes via FK
    async with db as session:
        await session.delete(clip)
        await session.commit()
