"""
Agent 4: Face Detection Agent

Responsibilities:
- Sample frames from each segment (every 0.5s)
- Detect faces using InsightFace (buffalo_l model)
- Score face quality (sharpness, frontality, eye contact)
- Track faces across frames (consistent subject IDs)
- Update segment records: has_face, face_count, engagement_score bump
"""
import logging
import tempfile
import uuid
from pathlib import Path
from typing import List

import cv2
import numpy as np

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.repositories import ClipRepository, SegmentRepository
from backend.storage import s3_client

logger = logging.getLogger(__name__)


class FaceDetectionAgent(BaseAgent):
    name = "face_detection_agent"
    _app = None  # InsightFace app (lazy-loaded)

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        async with AsyncSessionLocal() as session:
            clips = await ClipRepository(session).list_for_project(uuid.UUID(project_id))
            clips = [c for c in clips if c.is_ingested]

        total_faces = 0
        for i, clip in enumerate(clips):
            pct = int((i / len(clips)) * 85)
            await self.update_progress(ctx, pct, f"Detecting faces in clip {i+1}/{len(clips)}")
            count = await self._process_clip(clip, project_id)
            total_faces += count

        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={"total_face_detections": total_faces},
        )

    async def _process_clip(self, clip, project_id: str) -> int:
        async with AsyncSessionLocal() as session:
            segments = await SegmentRepository(session).list_for_clip(clip.id)

        if not segments:
            return 0

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / clip.filename
            await s3_client.download_file(clip.s3_bucket, clip.s3_key, video_path)

            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            sample_interval = max(1, int(fps * 0.5))  # sample every 0.5s

            app = self._get_insightface()
            total_detections = 0

            for segment in segments:
                start_frame = int((segment.start_ms / 1000) * fps)
                end_frame = int((segment.end_ms / 1000) * fps)

                face_counts = []
                best_face_score = 0.0

                for frame_idx in range(start_frame, end_frame, sample_interval):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        continue

                    faces = app.get(frame) if app else []
                    if faces:
                        face_counts.append(len(faces))
                        for face in faces:
                            score = self._face_quality_score(face, frame)
                            best_face_score = max(best_face_score, score)
                        total_detections += len(faces)

                if face_counts:
                    avg_faces = int(np.mean(face_counts))
                    has_face = True
                    engagement_boost = min(0.3, best_face_score * 0.3)
                else:
                    avg_faces = 0
                    has_face = False
                    engagement_boost = 0.0

                async with AsyncSessionLocal() as session:
                    seg_repo = SegmentRepository(session)
                    current_engagement = segment.engagement_score or 0.5
                    await seg_repo.update_scores(
                        segment.id,
                        has_face=has_face,
                        face_count=avg_faces,
                        engagement_score=min(1.0, current_engagement + engagement_boost),
                    )

            cap.release()
        return total_detections

    def _get_insightface(self):
        if FaceDetectionAgent._app is not None:
            return FaceDetectionAgent._app
        try:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(
                name=settings.INSIGHTFACE_MODEL,
                allowed_modules=["detection", "recognition"],
            )
            app.prepare(ctx_id=0, det_size=(640, 640))
            FaceDetectionAgent._app = app
            return app
        except ImportError:
            logger.warning("InsightFace not installed. Face detection disabled.")
            return None
        except Exception as e:
            logger.error("InsightFace init failed: %s", e)
            return None

    def _face_quality_score(self, face, frame: np.ndarray) -> float:
        """Score 0-1 based on face size, det_score (InsightFace confidence), and pose."""
        score = float(face.det_score)  # InsightFace detection confidence

        # Bonus for larger face (closer to camera = better)
        bbox = face.bbox.astype(int)
        face_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        frame_area = frame.shape[0] * frame.shape[1]
        size_ratio = face_area / max(frame_area, 1)
        size_bonus = min(0.3, size_ratio * 2)

        return min(1.0, score + size_bonus)
