"""
Local-disk storage backend for LOCAL_MODE.

Mirrors the subset of the s3_client interface the API + local pipeline use, but
writes to ./storage_local and serves files over the /files static mount.
"""
import shutil
from pathlib import Path
from typing import Optional

from backend.config import settings

ROOT = Path(settings.LOCAL_STORAGE_ROOT).resolve()
ROOT.mkdir(parents=True, exist_ok=True)


def _full(key: str) -> Path:
    p = (ROOT / key).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


async def save_upload(key: str, fileobj) -> int:
    """Stream an UploadFile-like object to disk. Returns bytes written."""
    dest = _full(key)
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await fileobj.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)
    return size


async def save_bytes(key: str, data: bytes) -> str:
    dest = _full(key)
    dest.write_bytes(data)
    return key


def copy_local(src_key: str, dest_key: str) -> str:
    src = _full(src_key)
    dest = _full(dest_key)
    if src.exists():
        shutil.copy(src, dest)
    return dest_key


def exists(key: str) -> bool:
    return _full(key).exists()


def file_size(key: str) -> int:
    p = _full(key)
    return p.stat().st_size if p.exists() else 0


def public_url(key: str) -> str:
    """URL the browser can use to stream/download the file (via /files mount)."""
    base = f"http://localhost:8000/files"
    return f"{base}/{key.lstrip('/')}"


# ── Key helpers (mirror s3_client naming) ─────────────────────────────────────

def make_video_key(project_id: str, clip_id: str, original_filename: str) -> str:
    ext = Path(original_filename).suffix.lower() or ".mp4"
    return f"projects/{project_id}/clips/{clip_id}{ext}"


def make_output_key(project_id: str, format_name: str, ext: str = "mp4") -> str:
    return f"projects/{project_id}/outputs/{format_name}.{ext}"


def make_thumbnail_key(project_id: str, clip_id: str) -> str:
    return f"projects/{project_id}/thumbnails/{clip_id}.jpg"


def make_keyframe_key(project_id: str, clip_id: str, segment_id: str) -> str:
    return f"projects/{project_id}/keyframes/{clip_id}/{segment_id}.jpg"


def make_subtitle_key(project_id: str, format_name: str, ext: str = "srt") -> str:
    return f"projects/{project_id}/subtitles/{format_name}.{ext}"
