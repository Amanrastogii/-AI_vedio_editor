"""
Agent 5: Emotion Analysis Agent

Responsibilities:
- Analyze facial emotions per segment using DeepFace
- Analyze audio sentiment using Wav2Vec2
- Detect emotional peaks (laughter, excitement, surprise)
- Update segment records with emotion_labels and boosted engagement_score
"""
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

from backend.agents.base_agent import AgentContext, AgentResult, BaseAgent
from backend.config import settings
from backend.database.db import AsyncSessionLocal
from backend.database.repositories import ClipRepository, SegmentRepository
from backend.storage import s3_client

logger = logging.getLogger(__name__)

POSITIVE_EMOTIONS = {"happy", "surprise"}
ENGAGING_EMOTIONS = {"happy", "surprise", "fear", "angry"}


class EmotionAnalysisAgent(BaseAgent):
    name = "emotion_analysis_agent"

    async def execute(self, ctx: AgentContext) -> AgentResult:
        project_id = ctx.project_id

        async with AsyncSessionLocal() as session:
            clips = await ClipRepository(session).list_for_project(uuid.UUID(project_id))
            clips = [c for c in clips if c.is_ingested]

        peaks_found = 0
        for i, clip in enumerate(clips):
            pct = int((i / len(clips)) * 85)
            await self.update_progress(ctx, pct, f"Analyzing emotions in clip {i+1}/{len(clips)}")
            peaks = await self._process_clip(clip)
            peaks_found += peaks

        return AgentResult(
            success=True,
            agent_name=self.name,
            project_id=project_id,
            data={"emotional_peaks_found": peaks_found},
        )

    async def _process_clip(self, clip) -> int:
        async with AsyncSessionLocal() as session:
            segments = await SegmentRepository(session).list_for_clip(clip.id)

        segments_with_faces = [s for s in segments if s.has_face]
        if not segments_with_faces:
            return 0

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / clip.filename
            await s3_client.download_file(clip.s3_bucket, clip.s3_key, video_path)
            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

            peaks = 0
            for segment in segments_with_faces:
                emotion_data = self._analyze_segment_emotions(cap, segment, fps)
                if not emotion_data:
                    continue

                dominant = max(emotion_data, key=emotion_data.get)
                is_peak = dominant in ENGAGING_EMOTIONS and emotion_data[dominant] > 0.5

                if is_peak:
                    peaks += 1

                engagement_boost = emotion_data.get("happy", 0) * 0.2 + emotion_data.get("surprise", 0) * 0.15

                async with AsyncSessionLocal() as session:
                    seg_repo = SegmentRepository(session)
                    current = segment.engagement_score or 0.5
                    await seg_repo.update_scores(
                        segment.id,
                        emotion_labels=emotion_data,
                        engagement_score=min(1.0, current + engagement_boost),
                    )
            cap.release()
        return peaks

    def _analyze_segment_emotions(self, cap: cv2.VideoCapture, segment, fps: float) -> Optional[Dict[str, float]]:
        """Sample up to 3 frames from segment, average emotion scores."""
        start_frame = int((segment.start_ms / 1000) * fps)
        end_frame = int((segment.end_ms / 1000) * fps)
        duration_frames = max(1, end_frame - start_frame)
        sample_frames = [
            start_frame + int(duration_frames * 0.25),
            start_frame + int(duration_frames * 0.50),
            start_frame + int(duration_frames * 0.75),
        ]

        all_emotions: List[Dict] = []
        for frame_idx in sample_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            emotions = self._deepface_analyze(frame)
            if emotions:
                all_emotions.append(emotions)

        if not all_emotions:
            return None

        # Average across sampled frames
        combined: Dict[str, float] = {}
        for emo_dict in all_emotions:
            for k, v in emo_dict.items():
                combined[k] = combined.get(k, 0.0) + v / len(all_emotions)
        return combined

    def _deepface_analyze(self, frame: np.ndarray) -> Optional[Dict[str, float]]:
        try:
            from deepface import DeepFace
            result = DeepFace.analyze(
                frame,
                actions=["emotion"],
                enforce_detection=False,
                silent=True,
            )
            if isinstance(result, list):
                result = result[0]
            emotions = result.get("emotion", {})
            # Normalize to 0-1
            total = sum(emotions.values()) or 1.0
            return {k: v / total for k, v in emotions.items()}
        except Exception as e:
            logger.debug("DeepFace failed on frame: %s", e)
            return None
