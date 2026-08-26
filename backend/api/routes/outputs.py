import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_user
from backend.api.schemas import OutputResponse
from backend.config import settings
from backend.database.db import get_db
from backend.database.models import User
from backend.database.repositories import OutputRepository, ProjectRepository
from backend.storage import local_storage, s3_client


async def _resolve_url(output) -> str | None:
    """Local-mode → /files static URL; production → S3 presigned URL."""
    if not output.s3_key:
        return None
    if settings.LOCAL_MODE or output.s3_bucket == "local":
        return local_storage.public_url(output.s3_key)
    return await s3_client.generate_presigned_url(
        output.s3_bucket or settings.S3_BUCKET_OUTPUTS, output.s3_key, expiry=3600
    )

router = APIRouter(prefix="/projects/{project_id}/outputs", tags=["outputs"])


@router.get("", response_model=List[OutputResponse])
async def list_outputs(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    out_repo = OutputRepository(db)
    outputs = await out_repo.list_for_project(project_id)

    responses = []
    for out in outputs:
        resp = OutputResponse.model_validate(out)
        resp.download_url = await _resolve_url(out)
        responses.append(resp)
    return responses


@router.get("/{output_id}/download")
async def get_download_url(
    project_id: uuid.UUID,
    output_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    proj_repo = ProjectRepository(db)
    project = await proj_repo.get(project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    out_repo = OutputRepository(db)
    outputs = await out_repo.list_for_project(project_id)
    output = next((o for o in outputs if o.id == output_id), None)
    if not output or not output.s3_key:
        raise HTTPException(status_code=404, detail="Output not found or not yet rendered")

    url = await _resolve_url(output)
    return {"download_url": url, "expires_in_seconds": 3600}
