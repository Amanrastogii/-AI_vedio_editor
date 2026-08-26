from functools import lru_cache
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME: str = "AI Video Editor"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

    # ── Local mode ────────────────────────────────────────────────────────────
    # When True: SQLite + local-disk storage + in-process pipeline + in-memory
    # event bus. Zero external infra (no Docker/Postgres/Redis/MinIO needed).
    LOCAL_MODE: bool = True
    LOCAL_STORAGE_ROOT: str = "storage_local"

    # ── Database ──────────────────────────────────────────────────────────────
    # Default to SQLite for frictionless localhost. Override with a Postgres URL
    # in production (postgresql+asyncpg://...).
    DATABASE_URL: str = "sqlite+aiosqlite:///./aivideo_local.db"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_BROKER_URL: str = "redis://localhost:6379/1"
    REDIS_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── S3 / MinIO ────────────────────────────────────────────────────────────
    S3_ENDPOINT_URL: Optional[str] = None          # None = real AWS, set for MinIO
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_REGION: str = "us-east-1"
    S3_BUCKET_VIDEOS: str = "aivideo-inputs"
    S3_BUCKET_OUTPUTS: str = "aivideo-outputs"
    S3_BUCKET_ASSETS: str = "aivideo-assets"       # thumbnails, keyframes, LUTs
    S3_PRESIGN_EXPIRY: int = 3600                  # seconds

    # ── Anthropic / Claude ────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_STORY_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_EDIT_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_FAST_MODEL: str = "claude-haiku-4-5-20251001"
    CLAUDE_MAX_TOKENS: int = 8192

    # ── FFmpeg ────────────────────────────────────────────────────────────────
    FFMPEG_PATH: str = "ffmpeg"
    FFPROBE_PATH: str = "ffprobe"
    FFMPEG_THREADS: int = 4
    FFMPEG_HWACCEL: Optional[str] = None           # "cuda", "videotoolbox", None

    # ── AI Models ────────────────────────────────────────────────────────────
    WHISPERX_MODEL_SIZE: str = "large-v3"          # tiny/base/small/medium/large-v3
    WHISPERX_DEVICE: str = "cuda"                  # cuda / cpu
    WHISPERX_COMPUTE_TYPE: str = "float16"         # float16 / int8
    WHISPERX_BATCH_SIZE: int = 16

    FLORENCE_MODEL_ID: str = "microsoft/Florence-2-large"
    YOLO_MODEL_PATH: str = "ml/models/yolov10x.pt"
    INSIGHTFACE_MODEL: str = "buffalo_l"
    TRANSNET_MODEL_PATH: str = "ml/models/TransNetV2"
    DEEPFILTER_MODEL: str = "DeepFilterNet3"
    DEMUCS_MODEL: str = "htdemucs_ft"

    CLIP_MODEL_ID: str = "openai/clip-vit-large-patch14"

    # ── Qdrant (vector DB) ───────────────────────────────────────────────────
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_COLLECTION_CLIPS: str = "clip_embeddings"
    QDRANT_COLLECTION_TRANSCRIPTS: str = "transcript_embeddings"

    # ── Processing ────────────────────────────────────────────────────────────
    FRAME_EXTRACTION_FPS: float = 1.0              # frames/sec for analysis
    SCENE_THRESHOLD: float = 27.0                  # PySceneDetect content threshold
    MIN_SEGMENT_DURATION_MS: int = 500             # discard micro-segments
    SILENCE_THRESHOLD_DB: float = -40.0
    SILENCE_MIN_DURATION_MS: int = 300
    FILLER_WORDS: List[str] = ["um", "uh", "like", "you know", "basically", "literally", "actually"]

    # ── Output formats ────────────────────────────────────────────────────────
    OUTPUT_FORMATS: List[str] = ["youtube", "shorts", "reels", "tiktok", "linkedin"]
    OUTPUT_VIDEO_CODEC: str = "libx264"
    OUTPUT_AUDIO_CODEC: str = "aac"
    OUTPUT_CRF: int = 18                           # quality (lower = better)

    # ── QA ────────────────────────────────────────────────────────────────────
    QA_MIN_VMAF_SCORE: float = 75.0
    QA_MAX_RETRIES: int = 2

    # ── Celery ────────────────────────────────────────────────────────────────
    CELERY_TASK_SOFT_TIME_LIMIT: int = 3600        # 1h
    CELERY_TASK_TIME_LIMIT: int = 7200             # 2h hard limit
    CELERY_MAX_RETRIES: int = 3
    CELERY_RETRY_BACKOFF: int = 60                 # seconds

    @property
    def is_dev(self) -> bool:
        return self.DEBUG

    @property
    def s3_use_ssl(self) -> bool:
        return self.S3_ENDPOINT_URL is None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
