from celery import Celery
from kombu import Exchange, Queue

from backend.config import settings

celery_app = Celery("aivideo")

celery_app.config_from_object(
    {
        "broker_url": settings.REDIS_BROKER_URL,
        "result_backend": settings.REDIS_RESULT_BACKEND,
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "timezone": "UTC",
        "enable_utc": True,
        "task_track_started": True,
        "task_soft_time_limit": settings.CELERY_TASK_SOFT_TIME_LIMIT,
        "task_time_limit": settings.CELERY_TASK_TIME_LIMIT,
        "task_acks_late": True,           # re-queue if worker dies mid-task
        "worker_prefetch_multiplier": 1,  # one task at a time per worker (GPU tasks are heavy)
        "task_reject_on_worker_lost": True,
        # ── Queues ────────────────────────────────────────────────────────────
        "task_queues": (
            Queue("gpu_queue",  Exchange("gpu"),  routing_key="gpu",
                  queue_arguments={"x-max-priority": 10}),
            Queue("cpu_queue",  Exchange("cpu"),  routing_key="cpu",
                  queue_arguments={"x-max-priority": 5}),
            Queue("io_queue",   Exchange("io"),   routing_key="io",
                  queue_arguments={"x-max-priority": 3}),
        ),
        "task_default_queue": "cpu_queue",
        "task_default_exchange": "cpu",
        "task_default_routing_key": "cpu",
        # ── Routes — each agent gets the right queue ──────────────────────────
        "task_routes": {
            # GPU-heavy tasks
            "backend.workers.tasks.ingest_video_task":          {"queue": "gpu_queue"},
            "backend.workers.tasks.detect_scenes_task":         {"queue": "gpu_queue"},
            "backend.workers.tasks.analyze_speech_task":        {"queue": "gpu_queue"},
            "backend.workers.tasks.detect_faces_task":          {"queue": "gpu_queue"},
            "backend.workers.tasks.analyze_emotions_task":      {"queue": "gpu_queue"},
            "backend.workers.tasks.render_video_task":          {"queue": "gpu_queue"},
            # CPU-bound tasks
            "backend.workers.tasks.build_story_task":           {"queue": "cpu_queue"},
            "backend.workers.tasks.make_editing_decisions_task":{"queue": "cpu_queue"},
            "backend.workers.tasks.enhance_audio_task":         {"queue": "cpu_queue"},
            "backend.workers.tasks.generate_subtitles_task":    {"queue": "cpu_queue"},
            "backend.workers.tasks.quality_check_task":         {"queue": "cpu_queue"},
            # I/O tasks
            "backend.workers.tasks.notify_pipeline_complete":   {"queue": "io_queue"},
        },
        # ── Beat schedule (optional health-check jobs) ─────────────────────────
        "beat_schedule": {
            "cleanup-stale-tasks": {
                "task": "backend.workers.tasks.cleanup_stale_tasks",
                "schedule": 3600.0,   # every hour
            },
        },
    }
)

celery_app.autodiscover_tasks(["backend.workers"])
