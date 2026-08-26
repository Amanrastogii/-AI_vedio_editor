"""S3/MinIO client wrapper — all storage I/O goes through here."""
import logging
import mimetypes
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO, Optional

import aioboto3
from botocore.exceptions import ClientError

from backend.config import settings

logger = logging.getLogger(__name__)

_session = aioboto3.Session(
    aws_access_key_id=settings.S3_ACCESS_KEY,
    aws_secret_access_key=settings.S3_SECRET_KEY,
    region_name=settings.S3_REGION,
)

_S3_KWARGS = {}
if settings.S3_ENDPOINT_URL:
    _S3_KWARGS["endpoint_url"] = settings.S3_ENDPOINT_URL


@asynccontextmanager
async def _s3():
    async with _session.client("s3", **_S3_KWARGS) as client:
        yield client


async def upload_file(local_path: str | Path, bucket: str, key: str,
                      content_type: Optional[str] = None) -> str:
    ct = content_type or mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    async with _s3() as client:
        with open(local_path, "rb") as f:
            await client.upload_fileobj(f, bucket, key, ExtraArgs={"ContentType": ct})
    logger.debug("Uploaded %s → s3://%s/%s", local_path, bucket, key)
    return key


async def upload_bytes(data: bytes, bucket: str, key: str, content_type: str = "application/octet-stream") -> str:
    import io
    async with _s3() as client:
        await client.upload_fileobj(io.BytesIO(data), bucket, key, ExtraArgs={"ContentType": content_type})
    return key


async def download_file(bucket: str, key: str, local_path: str | Path) -> Path:
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    async with _s3() as client:
        await client.download_file(bucket, key, str(local_path))
    logger.debug("Downloaded s3://%s/%s → %s", bucket, key, local_path)
    return local_path


async def generate_presigned_url(bucket: str, key: str,
                                  expiry: int = settings.S3_PRESIGN_EXPIRY,
                                  method: str = "get_object") -> str:
    async with _s3() as client:
        url = await client.generate_presigned_url(
            method,
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry,
        )
    return url


async def delete_object(bucket: str, key: str) -> None:
    async with _s3() as client:
        await client.delete_object(Bucket=bucket, Key=key)


async def object_exists(bucket: str, key: str) -> bool:
    async with _s3() as client:
        try:
            await client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise


def make_video_key(project_id: str, clip_id: str, original_filename: str) -> str:
    ext = Path(original_filename).suffix.lower()
    return f"projects/{project_id}/clips/{clip_id}{ext}"


def make_output_key(project_id: str, format_name: str) -> str:
    return f"projects/{project_id}/outputs/{format_name}.mp4"


def make_thumbnail_key(project_id: str, clip_id: str) -> str:
    return f"projects/{project_id}/thumbnails/{clip_id}.jpg"


def make_keyframe_key(project_id: str, clip_id: str, segment_id: str) -> str:
    return f"projects/{project_id}/keyframes/{clip_id}/{segment_id}.jpg"


def make_subtitle_key(project_id: str, format_name: str, ext: str = "srt") -> str:
    return f"projects/{project_id}/subtitles/{format_name}.{ext}"
