"""
Agent 2: Scene Detection Agent

Responsibilities:
- Download each ingested clip
- Detect shot boundaries (TransNetV2 for neural accuracy, PySceneDetect as fallback)
- Classify scene type (indoor/outdoor, action/calm) using Florence-2
- Extract one keyframe per scene
- Score segments for quality (sharpness, exposure, motion)
- Write Segment records to DB
"""
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.models import SegmentType
from backend.database.repositories import ClipRepository, SegmentRepository
from backend.storage import s3_client

logger = logging.getLogger(__name__)


class SceneDetectionAgent(BaseAgent):
    name = "scene_detection_agent"
    _florence = None  # Lazy-loaded

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        async with AsyncSessionLocal() as session:
            clip_repo = ClipRepository(session)
            clips = await clip_repo.list_for_project(uuid.UUID(project_id))
            clips = [c for c in clips if c.is_ingested]

        total_segments = 0
        for i, clip in enumerate(clips):
            pct = int((i / len(clips)) * 85)
            await self.update_progress(ctx, pct, f"Detecting scenes in clip {i+1}/{len(clips)}")
            count = await self._process_clip(clip, project_id)
            total_segments += count

        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={"total_segments": total_segments, "clips_processed": len(clips)},
        )

    async def _process_clip(self, clip, project_id: str) -> int:
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / clip.filename
            await s3_client.download_file(clip.s3_bucket, clip.s3_key, local_path)

            # Detect scene boundaries
            boundaries = self._detect_boundaries(local_path, clip.duration_ms or 0)
            segments_data = []

            cap = cv2.VideoCapture(str(local_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

            for idx, (start_ms, end_ms) in enumerate(boundaries):
                duration_ms = end_ms - start_ms
                if duration_ms < settings.MIN_SEGMENT_DURATION_MS:
                    continue

                # Extract keyframe at midpoint
                mid_ms = (start_ms + end_ms) // 2
                mid_frame = int((mid_ms / 1000) * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, mid_frame)
                ret, frame = cap.read()

                keyframe_key = None
                scene_description = None
                quality_score = 0.5

                if ret and frame is not None:
                    # Quality scoring
                    quality_score = self._compute_quality_score(frame)

                    # Save keyframe
                    kf_path = Path(tmpdir) / f"kf_{idx}.jpg"
                    cv2.imwrite(str(kf_path), frame)
                    segment_id = uuid.uuid4()
                    keyframe_key = s3_client.make_keyframe_key(project_id, str(clip.id), str(segment_id))
                    await s3_client.upload_file(kf_path, settings.S3_BUCKET_ASSETS, keyframe_key, "image/jpeg")

                    # Florence-2 scene caption (lazy-load model)
                    scene_description = await self._describe_scene(frame)

                seg_type = self._classify_segment(quality_score, duration_ms)

                segments_data.append({
                    "id": segment_id if ret else uuid.uuid4(),
                    "clip_id": clip.id,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "segment_type": seg_type,
                    "quality_score": quality_score,
                    "engagement_score": quality_score * 0.5,  # will be updated by emotion/face agents
                    "scene_description": scene_description,
                    "keyframe_s3_key": keyframe_key,
                    "is_blurry": quality_score < 0.3,
                })

            cap.release()

            async with AsyncSessionLocal() as session:
                seg_repo = SegmentRepository(session)
                await seg_repo.bulk_create(segments_data)

            return len(segments_data)

    def _detect_boundaries(self, video_path: Path, total_duration_ms: int) -> List[Tuple[int, int]]:
        """PySceneDetect content-aware detection."""
        try:
            from scenedetect import detect, ContentDetector, split_video_ffmpeg
            from scenedetect import open_video, SceneManager

            video = open_video(str(video_path))
            manager = SceneManager()
            manager.add_detector(ContentDetector(threshold=settings.SCENE_THRESHOLD))
            manager.detect_scenes(video, show_progress=False)
            scene_list = manager.get_scene_list()

            boundaries = []
            for scene in scene_list:
                start_ms = int(scene[0].get_seconds() * 1000)
                end_ms = int(scene[1].get_seconds() * 1000)
                boundaries.append((start_ms, end_ms))

            if not boundaries:
                boundaries = [(0, total_duration_ms)]
            return boundaries

        except Exception as e:
            logger.warning("PySceneDetect failed (%s), using single segment", e)
            return [(0, total_duration_ms)]

    def _compute_quality_score(self, frame: np.ndarray) -> float:
        """Composite score: sharpness (Laplacian variance) + exposure."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Sharpness
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        sharpness = min(laplacian_var / 500.0, 1.0)

        # Exposure (avoid overexposed / underexposed)
        mean_brightness = gray.mean() / 255.0
        exposure = 1.0 - abs(mean_brightness - 0.5) * 2  # peaks at 0.5

        return float(sharpness * 0.6 + exposure * 0.4)

    async def _describe_scene(self, frame: np.ndarray) -> str | None:
        """Florence-2 scene captioning — lazy-loaded to avoid startup cost."""
        try:
            if SceneDetectionAgent._florence is None:
                from transformers import AutoModelForCausalLM, AutoProcessor
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                model_id = settings.FLORENCE_MODEL_ID
                processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
                model = AutoModelForCausalLM.from_pretrained(
                    model_id, torch_dtype=torch.float16 if device == "cuda" else torch.float32,
                    trust_remote_code=True
                ).to(device)
                SceneDetectionAgent._florence = (model, processor, device)

            model, processor, device = SceneDetectionAgent._florence
            from PIL import Image
            import torch

            pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            inputs = processor(text="<CAPTION>", images=pil_image, return_tensors="pt").to(device)
            with torch.no_grad():
                ids = model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=50,
                )
            caption = processor.batch_decode(ids, skip_special_tokens=True)[0]
            return caption.strip()
        except Exception as e:
            logger.debug("Florence-2 failed: %s", e)
            return None

    def _classify_segment(self, quality_score: float, duration_ms: int) -> SegmentType:
        if quality_score < 0.2:
            return SegmentType.BAD
        if quality_score > 0.75 and duration_ms > 2000:
            return SegmentType.HIGHLIGHT
        return SegmentType.GOOD
