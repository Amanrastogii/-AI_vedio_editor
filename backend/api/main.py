"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api.routes import auth, outputs, processing, projects, uploads
from backend.api.websocket import router as ws_router
from backend.config import settings
from backend.database.db import engine
from backend.database.models import Base

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create DB tables on startup (in production use Alembic migrations).
    # Non-fatal: the API still boots if the DB isn't reachable yet, so /health
    # and /docs work during local bring-up before Postgres is online.
    app.state.db_ready = False
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app.state.db_ready = True
        logger.info("Database connected — tables ensured")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Database unavailable at startup (%s). API will serve, but DB-backed "
            "endpoints will fail until Postgres is reachable.", exc,
        )
    logger.info("AI Video Editor API started — v%s", settings.APP_VERSION)
    yield
    await engine.dispose()
    logger.info("API shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered video editing platform — 11-agent pipeline",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://your-domain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(projects.router, prefix=API_PREFIX)
app.include_router(uploads.router, prefix=API_PREFIX)
app.include_router(processing.router, prefix=API_PREFIX)
app.include_router(outputs.router, prefix=API_PREFIX)
app.include_router(ws_router)  # WebSocket (no prefix)

# Serve locally-stored clips/outputs in LOCAL_MODE (StaticFiles supports range
# requests, so the <video> player can stream/seek).
if settings.LOCAL_MODE:
    storage_root = Path(settings.LOCAL_STORAGE_ROOT).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(storage_root)), name="files")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "db_ready": getattr(app.state, "db_ready", False),
    }
