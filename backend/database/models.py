import enum
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, Uuid, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── Enums ─────────────────────────────────────────────────────────────────────

class ProjectStatus(str, enum.Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class SegmentType(str, enum.Enum):
    GOOD = "good"
    BAD = "bad"
    HIGHLIGHT = "highlight"
    TRANSITION = "transition"
    BROLL = "broll"
    SILENCE = "silence"
    FILLER = "filler"


class NarrativeRole(str, enum.Enum):
    HOOK = "hook"
    CONTEXT = "context"
    RISING_ACTION = "rising_action"
    CLIMAX = "climax"
    REACTION = "reaction"
    RESOLUTION = "resolution"
    BROLL = "broll"
    TITLE_CARD = "title_card"


class TransitionType(str, enum.Enum):
    CUT = "cut"
    DISSOLVE = "dissolve"
    FADE_TO_BLACK = "fade_to_black"
    WIPE = "wipe"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    CROSS_FADE = "cross_fade"


class OutputFormat(str, enum.Enum):
    YOUTUBE = "youtube"          # 16:9  1920×1080
    SHORTS = "shorts"            # 9:16  1080×1920
    REELS = "reels"              # 9:16  1080×1920
    TIKTOK = "tiktok"            # 9:16  1080×1920
    LINKEDIN = "linkedin"        # 16:9  1920×1080


# ── Mixins ────────────────────────────────────────────────────────────────────

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


# ── Tables ────────────────────────────────────────────────────────────────────

class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    projects: Mapped[List["Project"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.CREATED, nullable=False
    )
    target_duration_sec: Mapped[Optional[int]] = mapped_column(Integer)
    target_style: Mapped[Optional[Dict]] = mapped_column(JSON)        # e.g. {"pacing": "fast", "tone": "energetic"}
    output_formats: Mapped[Optional[List]] = mapped_column(JSON)      # list of OutputFormat values
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="projects")
    clips: Mapped[List["Clip"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    story_timelines: Mapped[List["StoryTimeline"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    outputs: Mapped[List["Output"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    agent_tasks: Mapped[List["AgentTask"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Clip(TimestampMixin, Base):
    __tablename__ = "clips"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    s3_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    fps: Mapped[Optional[float]] = mapped_column(Float)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    codec_video: Mapped[Optional[str]] = mapped_column(String(32))
    codec_audio: Mapped[Optional[str]] = mapped_column(String(32))
    bitrate_kbps: Mapped[Optional[int]] = mapped_column(Integer)
    upload_order: Mapped[int] = mapped_column(Integer, nullable=False)
    thumbnail_s3_key: Mapped[Optional[str]] = mapped_column(Text)
    is_ingested: Mapped[bool] = mapped_column(Boolean, default=False)
    ingestion_metadata: Mapped[Optional[Dict]] = mapped_column(JSON)   # full ffprobe output

    project: Mapped["Project"] = relationship(back_populates="clips")
    segments: Mapped[List["Segment"]] = relationship(back_populates="clip", cascade="all, delete-orphan")
    transcripts: Mapped[List["Transcript"]] = relationship(back_populates="clip", cascade="all, delete-orphan")


class Segment(TimestampMixin, Base):
    __tablename__ = "segments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_type: Mapped[SegmentType] = mapped_column(Enum(SegmentType), nullable=False)

    # Scoring
    quality_score: Mapped[Optional[float]] = mapped_column(Float)        # 0-1 (sharpness, stability, exposure)
    engagement_score: Mapped[Optional[float]] = mapped_column(Float)     # 0-1 composite
    motion_score: Mapped[Optional[float]] = mapped_column(Float)         # 0-1 (camera movement)
    audio_energy: Mapped[Optional[float]] = mapped_column(Float)         # RMS dB

    # Content flags
    has_face: Mapped[bool] = mapped_column(Boolean, default=False)
    has_speech: Mapped[bool] = mapped_column(Boolean, default=False)
    has_music: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blurry: Mapped[bool] = mapped_column(Boolean, default=False)
    is_shaky: Mapped[bool] = mapped_column(Boolean, default=False)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)

    # Rich metadata
    emotion_labels: Mapped[Optional[Dict]] = mapped_column(JSON)        # {"happy": 0.8, "excited": 0.6}
    scene_description: Mapped[Optional[str]] = mapped_column(Text)       # Florence-2 caption
    objects_detected: Mapped[Optional[List]] = mapped_column(JSON)      # YOLO detections
    keyframe_s3_key: Mapped[Optional[str]] = mapped_column(Text)
    face_count: Mapped[Optional[int]] = mapped_column(Integer)

    clip: Mapped["Clip"] = relationship(back_populates="segments")
    story_entries: Mapped[List["StoryTimeline"]] = relationship(back_populates="segment")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    clip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=False)
    speaker_id: Mapped[Optional[str]] = mapped_column(String(50))        # "SPEAKER_00", "SPEAKER_01"
    word: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    is_filler: Mapped[bool] = mapped_column(Boolean, default=False)
    is_silence: Mapped[bool] = mapped_column(Boolean, default=False)

    clip: Mapped["Clip"] = relationship(back_populates="transcripts")


class StoryTimeline(TimestampMixin, Base):
    """Ordered list of segments that form the final edit."""
    __tablename__ = "story_timelines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("segments.id", ondelete="SET NULL"))
    position_order: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative_role: Mapped[NarrativeRole] = mapped_column(Enum(NarrativeRole), nullable=False)
    transition_in: Mapped[TransitionType] = mapped_column(Enum(TransitionType), default=TransitionType.CUT)
    trim_start_ms: Mapped[Optional[int]] = mapped_column(Integer)        # sub-trim within segment
    trim_end_ms: Mapped[Optional[int]] = mapped_column(Integer)
    zoom_params: Mapped[Optional[Dict]] = mapped_column(JSON)           # {"factor": 1.15, "duration_ms": 500}
    reframe_params: Mapped[Optional[Dict]] = mapped_column(JSON)        # {"x": 0.1, "y": 0.0}
    edit_reasoning: Mapped[Optional[str]] = mapped_column(Text)          # LLM's explanation
    llm_confidence: Mapped[Optional[float]] = mapped_column(Float)

    __table_args__ = (UniqueConstraint("project_id", "position_order"),)

    project: Mapped["Project"] = relationship(back_populates="story_timelines")
    segment: Mapped[Optional["Segment"]] = relationship(back_populates="story_entries")


class Output(TimestampMixin, Base):
    __tablename__ = "outputs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    format: Mapped[OutputFormat] = mapped_column(Enum(OutputFormat), nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(10), nullable=False)     # "16:9", "9:16"
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    s3_key: Mapped[Optional[str]] = mapped_column(Text)
    s3_bucket: Mapped[Optional[str]] = mapped_column(String(128))
    cdn_url: Mapped[Optional[str]] = mapped_column(Text)
    quality_score: Mapped[Optional[float]] = mapped_column(Float)             # VMAF score
    subtitle_s3_key: Mapped[Optional[str]] = mapped_column(Text)             # .srt / .vtt
    render_metadata: Mapped[Optional[Dict]] = mapped_column(JSON)

    project: Mapped["Project"] = relationship(back_populates="outputs")


class AgentTask(TimestampMixin, Base):
    __tablename__ = "agent_tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    clip_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"))
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus), default=AgentStatus.PENDING)
    progress_pct: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    result_metadata: Mapped[Optional[Dict]] = mapped_column(JSON)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped["Project"] = relationship(back_populates="agent_tasks")
