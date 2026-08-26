"""Pydantic request/response schemas for all API endpoints."""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Project ───────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    target_duration_sec: Optional[int] = Field(None, gt=0, le=3600)
    target_style: Optional[Dict[str, Any]] = None
    output_formats: Optional[List[str]] = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    target_duration_sec: Optional[int]
    target_style: Optional[Dict]
    output_formats: Optional[List[str]]
    created_at: datetime
    completed_at: Optional[datetime]
    error_message: Optional[str]

    model_config = {"from_attributes": True}


# ── Upload ────────────────────────────────────────────────────────────────────

class InitiateUploadRequest(BaseModel):
    filename: str
    file_size_bytes: int
    content_type: str = "video/mp4"


class InitiateUploadResponse(BaseModel):
    upload_id: str
    clip_id: uuid.UUID
    s3_key: str
    presigned_url: str   # single-part for small files


class CompleteUploadRequest(BaseModel):
    clip_id: uuid.UUID
    s3_key: str


# ── Processing ────────────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    output_formats: Optional[List[str]] = None
    target_style: Optional[Dict[str, Any]] = None
    target_duration_sec: Optional[int] = None


class ProcessResponse(BaseModel):
    project_id: uuid.UUID
    celery_task_id: str
    message: str = "Pipeline started"


class PipelineStatusResponse(BaseModel):
    project_id: uuid.UUID
    project_status: str
    agents: List[Dict[str, Any]]


# ── Clip ─────────────────────────────────────────────────────────────────────

class ClipResponse(BaseModel):
    id: uuid.UUID
    filename: str
    original_filename: str
    duration_ms: Optional[int]
    fps: Optional[float]
    width: Optional[int]
    height: Optional[int]
    codec_video: Optional[str]
    codec_audio: Optional[str]
    file_size_bytes: Optional[int]
    upload_order: int
    is_ingested: bool
    thumbnail_url: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Output ───────────────────────────────────────────────────────────────────

class OutputResponse(BaseModel):
    id: uuid.UUID
    format: str
    aspect_ratio: str
    width: Optional[int]
    height: Optional[int]
    duration_ms: Optional[int]
    file_size_bytes: Optional[int]
    quality_score: Optional[float]
    download_url: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Story / EDL ───────────────────────────────────────────────────────────────

class StoryEntryResponse(BaseModel):
    position: int
    narrative_role: str
    segment_id: Optional[uuid.UUID]
    trim_start_ms: Optional[int]
    trim_end_ms: Optional[int]
    transition_in: str
    edit_reasoning: Optional[str]

    model_config = {"from_attributes": True}
