"""
LOCAL_MODE pipeline — runs all 11 agents in-process as an asyncio task.

This is the orchestrator used on localhost when the heavy ML stack / GPU / Celery
are unavailable. It performs the REAL pipeline choreography (ordering, the two
parallel groups, DB writes, per-agent status, the EDL/story structures, output
creation) and emits REAL WebSocket events — but the per-agent "analysis" is
simulated with realistic timing and plausible data instead of calling the models.

The production path (Celery + real agents in backend/agents/*) is unchanged.
"""
import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Dict, List

from backend.config import settings
from backend.core.event_bus import EventBus
from backend.database.db import AsyncSessionLocal
from backend.database.models import (
    AgentStatus, NarrativeRole, OutputFormat, ProjectStatus, SegmentType, TransitionType,
)
from backend.database.repositories import (
    AgentTaskRepository, ClipRepository, OutputRepository,
    ProjectRepository, SegmentRepository, StoryTimelineRepository, TranscriptRepository,
)
from backend.core import real_ops
from backend.storage import local_storage

logger = logging.getLogger(__name__)

# Canonical agent registry — the frontend renders cards keyed by `key`.
AGENTS: List[Dict] = [
    {"key": "ingestion",         "label": "Video Ingestion",    "icon": "🎬"},
    {"key": "scene_detection",   "label": "Scene Detection",    "icon": "🎞"},
    {"key": "speech_analysis",   "label": "Speech Analysis",    "icon": "🗣"},
    {"key": "face_detection",    "label": "Face Detection",     "icon": "👤"},
    {"key": "emotion_analysis",  "label": "Emotion Analysis",   "icon": "😊"},
    {"key": "story_builder",     "label": "Story Builder",      "icon": "📖"},
    {"key": "editing_decision",  "label": "Editing Decision",   "icon": "✂️"},
    {"key": "audio_enhancement", "label": "Audio Enhancement",  "icon": "🔊"},
    {"key": "subtitle",          "label": "Subtitle",           "icon": "📝"},
    {"key": "rendering",         "label": "Rendering",          "icon": "🎥"},
    {"key": "quality_assurance", "label": "Quality Assurance",  "icon": "✅"},
]

OUTPUT_SPECS = {
    "youtube":  (1920, 1080, "16:9"),
    "shorts":   (1080, 1920, "9:16"),
    "reels":    (1080, 1920, "9:16"),
    "tiktok":   (1080, 1920, "9:16"),
    "linkedin": (1920, 1080, "16:9"),
}

SAMPLE_WORDS = (
    "so today I want to show you something really exciting that completely "
    "changed how we think about this whole process and honestly the results "
    "speak for themselves let me walk you through exactly what happened"
).split()

_db_lock = asyncio.Lock()  # serialize SQLite writes


class LocalPipeline:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.bus = EventBus()
        self.channel = f"project:{project_id}"

    async def _emit(self, payload: dict) -> None:
        payload.setdefault("project_id", self.project_id)
        await self.bus.publish(self.channel, payload)

    async def _agent_task(self, key: str) -> uuid.UUID:
        async with _db_lock, AsyncSessionLocal() as session:
            task = await AgentTaskRepository(session).create(
                project_id=uuid.UUID(self.project_id), agent_name=key
            )
            await AgentTaskRepository(session).update_status(task.id, AgentStatus.RUNNING)
            return task.id

    async def _finish_task(self, task_id: uuid.UUID, summary: dict) -> None:
        async with _db_lock, AsyncSessionLocal() as session:
            await AgentTaskRepository(session).update_status(
                task_id, AgentStatus.COMPLETED, progress_pct=100, result_metadata=summary
            )

    async def _run_steps(self, key: str, label: str, steps: List[str], step_delay=(0.4, 0.9)):
        """Emit running + progress events across `steps` messages."""
        task_id = await self._agent_task(key)
        await self._emit({"event": "agent.started", "agent": key, "label": label})
        n = len(steps)
        for i, msg in enumerate(steps):
            pct = int(((i + 1) / n) * 100)
            await self._emit({
                "event": "agent.progress", "agent": key, "label": label,
                "status": "running", "progress_pct": pct, "message": msg,
            })
            await asyncio.sleep(random.uniform(*step_delay))
        return task_id

    async def run(self) -> None:
        try:
            await self._run()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Local pipeline failed for %s", self.project_id)
            async with _db_lock, AsyncSessionLocal() as session:
                await ProjectRepository(session).update_status(
                    uuid.UUID(self.project_id), ProjectStatus.FAILED, error=str(exc)
                )
            await self._emit({"event": "pipeline.failed", "error": str(exc)})
        finally:
            await self.bus.close()

    async def _run(self) -> None:
        pid = uuid.UUID(self.project_id)

        async with _db_lock, AsyncSessionLocal() as session:
            await ProjectRepository(session).update_status(pid, ProjectStatus.PROCESSING)
            clips = await ClipRepository(session).list_for_project(pid)

        if not clips:
            raise ValueError("No clips uploaded")

        await self._emit({
            "event": "pipeline.started",
            "agents": [{"key": a["key"], "label": a["label"], "icon": a["icon"]} for a in AGENTS],
            "clip_count": len(clips),
        })

        # ── Agent 1: Ingestion ────────────────────────────────────────────────
        t = await self._run_steps("ingestion", "Video Ingestion", [
            f"Validating {len(clips)} uploaded clip(s)",
            "ffprobe: codec, resolution, fps, duration",
            "Generating thumbnails",
            "Registering media in database",
        ])
        primary_clip_key = None
        async with _db_lock, AsyncSessionLocal() as session:
            repo = ClipRepository(session)
            for clip in clips:
                if primary_clip_key is None:
                    primary_clip_key = clip.s3_key
                # REAL ffprobe on the locally-stored file (off the event loop).
                local_path = local_storage._full(clip.s3_key)
                meta = await asyncio.to_thread(real_ops.probe_metadata, local_path) if local_path.exists() else {}
                # Real thumbnail
                thumb_key = local_storage.make_thumbnail_key(self.project_id, str(clip.id))
                await asyncio.to_thread(real_ops.extract_thumbnail, local_path, local_storage._full(thumb_key))
                await repo.update_video_info(
                    clip.id,
                    duration_ms=meta.get("duration_ms") or clip.duration_ms or random.randint(45_000, 180_000),
                    fps=meta.get("fps") or clip.fps or 30.0,
                    width=meta.get("width") or clip.width or 1920,
                    height=meta.get("height") or clip.height or 1080,
                    codec_video=meta.get("codec_video") or clip.codec_video or "h264",
                    codec_audio=meta.get("codec_audio") or clip.codec_audio or "aac",
                    bitrate_kbps=meta.get("bitrate_kbps"),
                    thumbnail_s3_key=thumb_key,
                )
                await repo.mark_ingested(clip.id, {"local_mode": True, "real_probe": bool(meta)})
        await self._finish_task(t, {"clips_ingested": len(clips)})
        await self._emit({"event": "agent.completed", "agent": "ingestion",
                          "summary": f"{len(clips)} clips ingested"})

        # Reload clips with durations
        async with AsyncSessionLocal() as session:
            clips = await ClipRepository(session).list_for_project(pid)

        # ── Agent 2: Scene Detection ──────────────────────────────────────────
        t = await self._run_steps("scene_detection", "Scene Detection", [
            "PySceneDetect: content-aware shot boundaries",
            "Extracting real keyframes (OpenCV)",
            "Scoring sharpness + exposure",
            "Classifying scene types",
        ], step_delay=(0.3, 0.6))
        all_segments: List[dict] = []
        async with _db_lock, AsyncSessionLocal() as session:
            seg_repo = SegmentRepository(session)
            for clip in clips:
                dur = clip.duration_ms or 90_000
                local_path = local_storage._full(clip.s3_key)
                # REAL shot-boundary detection (off the event loop)
                boundaries = (
                    await asyncio.to_thread(real_ops.detect_scenes, local_path, dur)
                    if local_path.exists() else [(0, dur)]
                )
                clip_segs = []
                for (start_ms, end_ms) in boundaries:
                    if end_ms - start_ms < settings.MIN_SEGMENT_DURATION_MS:
                        continue
                    seg_id = uuid.uuid4()
                    kf_key = local_storage.make_keyframe_key(self.project_id, str(clip.id), str(seg_id))
                    # REAL keyframe + quality from the actual footage
                    q = await asyncio.to_thread(
                        real_ops.keyframe_and_quality,
                        local_path, (start_ms + end_ms) // 2, local_storage._full(kf_key)
                    ) if local_path.exists() else round(random.uniform(0.5, 0.9), 2)
                    clip_segs.append({
                        "id": seg_id,
                        "clip_id": clip.id,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "segment_type": SegmentType.HIGHLIGHT if q > 0.78 else SegmentType.GOOD,
                        "quality_score": round(q, 2),
                        "engagement_score": round(q * 0.6, 2),
                        "scene_description": None,
                        "keyframe_s3_key": kf_key,
                        "is_blurry": q < 0.35,
                    })
                await seg_repo.bulk_create(clip_segs)
                all_segments.extend(clip_segs)
        await self._finish_task(t, {"total_segments": len(all_segments)})
        await self._emit({"event": "agent.completed", "agent": "scene_detection",
                          "summary": f"{len(all_segments)} segments detected"})

        # ── Agents 3,4,5: parallel analysis group ─────────────────────────────
        await self._emit({"event": "group.started", "agents": ["speech_analysis", "face_detection", "emotion_analysis"],
                          "label": "Parallel analysis"})

        word_total = await self._speech_agent(clips)
        await self._face_agent(all_segments)
        peaks = await self._emotion_agent(all_segments)

        # ── Agent 6: Story Builder ────────────────────────────────────────────
        t = await self._run_steps("story_builder", "Story Builder", [
            "Aggregating analysis from all agents",
            "Claude: identifying narrative arc",
            "Selecting best segments by engagement",
            "Assigning hook → climax → resolution roles",
        ])
        story_len = await self._build_story(pid, all_segments)
        await self._finish_task(t, {"segments_in_story": story_len})
        await self._emit({"event": "agent.completed", "agent": "story_builder",
                          "summary": f"{story_len}-beat narrative built"})

        # ── Agent 7: Editing Decision ─────────────────────────────────────────
        t = await self._run_steps("editing_decision", "Editing Decision", [
            "Claude: computing frame-accurate cut points",
            "Choosing transitions & pacing rhythm",
            "Planning zoom / reframe per beat",
            "Emitting Edit Decision List (EDL)",
        ])
        await self._finish_task(t, {"color_grade": "cinematic_warm", "pacing": "dynamic"})
        await self._emit({"event": "agent.completed", "agent": "editing_decision",
                          "summary": "EDL ready · cinematic_warm"})

        # ── Agents 8,9: parallel (audio + subtitle) ───────────────────────────
        await self._emit({"event": "group.started", "agents": ["audio_enhancement", "subtitle"],
                          "label": "Parallel post-production"})
        await self._audio_agent()
        await self._subtitle_agent(word_total)

        # ── Agent 10: Rendering ───────────────────────────────────────────────
        formats = self.project_formats or settings.OUTPUT_FORMATS
        t = await self._run_steps("rendering", "Rendering", [
            "Building FFmpeg filter graph from EDL",
            "Applying color grade (LUT) + stabilization",
            f"Rendering {len(formats)} formats: {', '.join(formats)}",
            "Uploading outputs",
        ], step_delay=(0.6, 1.1))
        total_dur = await self._create_outputs(pid, primary_clip_key, formats)
        await self._finish_task(t, {"formats": formats})
        await self._emit({"event": "agent.completed", "agent": "rendering",
                          "summary": f"{len(formats)} formats rendered"})

        # ── Agent 11: QA ──────────────────────────────────────────────────────
        t = await self._run_steps("quality_assurance", "Quality Assurance", [
            "Scoring perceptual quality (VMAF)",
            "Checking A/V sync + artifacts",
            "Validating aspect ratios",
            "Finalizing project",
        ])
        async with _db_lock, AsyncSessionLocal() as session:
            outs = await OutputRepository(session).list_for_project(pid)
            for o in outs:
                await OutputRepository(session).update(o.id, quality_score=round(random.uniform(82, 94), 1))
            await ProjectRepository(session).update_status(pid, ProjectStatus.COMPLETED)
        await self._finish_task(t, {"all_passed": True})
        await self._emit({"event": "agent.completed", "agent": "quality_assurance",
                          "summary": "QA passed"})

        await self._emit({
            "event": "pipeline.complete",
            "summary": {
                "segments": len(all_segments),
                "words_transcribed": word_total,
                "emotional_peaks": peaks,
                "story_beats": story_len,
                "formats": len(formats),
                "total_duration_ms": total_dur,
            },
        })

    # ── Parallel-group agents ─────────────────────────────────────────────────

    async def _speech_agent(self, clips) -> int:
        t = await self._run_steps("speech_analysis", "Speech Analysis", [
            "WhisperX: transcribing audio",
            "Word-level timestamp alignment",
            "Speaker diarization",
            "Flagging filler words & silence",
        ])
        total = 0
        async with _db_lock, AsyncSessionLocal() as session:
            tr_repo = TranscriptRepository(session)
            for clip in clips:
                dur = clip.duration_ms or 90_000
                rows, cursor = [], 0
                while cursor < dur - 2000:
                    w = random.choice(SAMPLE_WORDS)
                    wlen = random.randint(180, 420)
                    rows.append({
                        "clip_id": clip.id, "speaker_id": "SPEAKER_00", "word": w,
                        "start_ms": cursor, "end_ms": cursor + wlen,
                        "confidence": round(random.uniform(0.8, 0.99), 2),
                        "is_filler": w in ("so", "honestly"), "is_silence": False,
                    })
                    cursor += wlen + random.randint(40, 160)
                await tr_repo.bulk_create(rows)
                total += len(rows)
        await self._finish_task(t, {"words": total})
        await self._emit({"event": "agent.completed", "agent": "speech_analysis",
                          "summary": f"{total} words transcribed"})
        return total

    async def _face_agent(self, segments) -> None:
        t = await self._run_steps("face_detection", "Face Detection", [
            "InsightFace: detecting & tracking faces",
            "Scoring face quality (sharpness, frontality)",
            "Identifying main subjects",
        ])
        async with _db_lock, AsyncSessionLocal() as session:
            seg_repo = SegmentRepository(session)
            faces = 0
            for s in segments:
                has = random.random() > 0.35
                if has:
                    faces += 1
                await seg_repo.update_scores(
                    s["id"], has_face=has, face_count=random.randint(1, 2) if has else 0,
                    engagement_score=min(1.0, (s["engagement_score"] or 0.4) + (0.2 if has else 0)),
                )
        await self._finish_task(t, {"segments_with_faces": faces})
        await self._emit({"event": "agent.completed", "agent": "face_detection",
                          "summary": f"faces in {faces} segments"})

    async def _emotion_agent(self, segments) -> int:
        t = await self._run_steps("emotion_analysis", "Emotion Analysis", [
            "DeepFace: per-frame emotion",
            "Wav2Vec2: audio sentiment",
            "Locating emotional peaks",
        ])
        peaks = 0
        async with _db_lock, AsyncSessionLocal() as session:
            seg_repo = SegmentRepository(session)
            for s in segments:
                happy = round(random.uniform(0, 1), 2)
                surprise = round(random.uniform(0, 0.6), 2)
                if happy > 0.6 or surprise > 0.4:
                    peaks += 1
                await seg_repo.update_scores(
                    s["id"],
                    emotion_labels={"happy": happy, "surprise": surprise,
                                    "neutral": round(1 - happy, 2)},
                    engagement_score=min(1.0, (s["engagement_score"] or 0.4) + happy * 0.2),
                )
        await self._finish_task(t, {"emotional_peaks": peaks})
        await self._emit({"event": "agent.completed", "agent": "emotion_analysis",
                          "summary": f"{peaks} emotional peaks"})
        return peaks

    async def _build_story(self, pid, segments) -> int:
        # pick top segments by engagement
        async with AsyncSessionLocal() as session:
            highlights = await SegmentRepository(session).get_highlights(pid, min_score=0.0)
        top = sorted(highlights, key=lambda s: (s.engagement_score or 0), reverse=True)[:8]
        roles = [NarrativeRole.HOOK, NarrativeRole.CONTEXT, NarrativeRole.RISING_ACTION,
                 NarrativeRole.CLIMAX, NarrativeRole.REACTION, NarrativeRole.RISING_ACTION,
                 NarrativeRole.RESOLUTION, NarrativeRole.BROLL]
        rows = []
        for i, seg in enumerate(top):
            rows.append({
                "project_id": pid,
                "segment_id": seg.id,
                "position_order": i + 1,
                "narrative_role": roles[i % len(roles)],
                "transition_in": TransitionType.CUT if i == 0 else random.choice(
                    [TransitionType.CUT, TransitionType.DISSOLVE, TransitionType.CROSS_FADE]),
                "trim_start_ms": seg.start_ms,
                "trim_end_ms": seg.end_ms,
                "edit_reasoning": random.choice([
                    "Strong opening energy, direct eye contact",
                    "Key context for the story",
                    "Builds tension toward the payoff",
                    "Highest emotional peak in the footage",
                    "Natural resolution beat",
                ]),
            })
        if rows:
            async with _db_lock, AsyncSessionLocal() as session:
                await StoryTimelineRepository(session).bulk_create(rows)
        return len(rows)

    async def _audio_agent(self) -> None:
        t = await self._run_steps("audio_enhancement", "Audio Enhancement", [
            "DeepFilterNet: noise reduction",
            "Normalizing loudness to -14 LUFS",
            "Removing silence & filler cuts",
            "Ducking background music under speech",
        ])
        await self._finish_task(t, {"lufs": -14.0})
        await self._emit({"event": "agent.completed", "agent": "audio_enhancement",
                          "summary": "audio enhanced · -14 LUFS"})

    async def _subtitle_agent(self, words) -> None:
        t = await self._run_steps("subtitle", "Subtitle", [
            "Building cues from transcript",
            "Word-level highlight styling",
            "Exporting SRT / VTT",
        ])
        key = local_storage.make_subtitle_key(self.project_id, "main", "srt")
        await local_storage.save_bytes(
            key, b"1\n00:00:00,000 --> 00:00:03,000\nAuto-generated subtitles (local mode)\n")
        await self._finish_task(t, {"subtitle_key": key})
        await self._emit({"event": "agent.completed", "agent": "subtitle",
                          "summary": "SRT/VTT generated"})

    async def _create_outputs(self, pid, primary_clip_key, formats) -> int:
        # Build the real edit segment list from the story timeline.
        async with AsyncSessionLocal() as session:
            timeline = await StoryTimelineRepository(session).get_ordered(pid)
            clip_repo = ClipRepository(session)
            edit_segments = []
            for entry in timeline:
                if not entry.segment:
                    continue
                clip = await clip_repo.get(entry.segment.clip_id)
                if not clip:
                    continue
                src = local_storage._full(clip.s3_key)
                start = entry.trim_start_ms if entry.trim_start_ms is not None else entry.segment.start_ms
                end = entry.trim_end_ms if entry.trim_end_ms is not None else entry.segment.end_ms
                edit_segments.append({"src": str(src), "start_ms": int(start), "end_ms": int(end)})

        total_dur = sum(s["end_ms"] - s["start_ms"] for s in edit_segments) or random.randint(60_000, 140_000)

        async with _db_lock, AsyncSessionLocal() as session:
            out_repo = OutputRepository(session)
            for idx, fmt in enumerate(formats):
                w, h, ratio = OUTPUT_SPECS.get(fmt, (1920, 1080, "16:9"))
                out_key = local_storage.make_output_key(self.project_id, fmt)
                out_path = local_storage._full(out_key)

                pct = int(((idx + 0.5) / len(formats)) * 100)
                await self._emit({
                    "event": "agent.progress", "agent": "rendering", "label": "Rendering",
                    "status": "running", "progress_pct": pct,
                    "message": f"ffmpeg encoding {fmt} ({w}×{h}) — {len(edit_segments)} cuts",
                })

                # REAL ffmpeg render: trim + scale/pad + concat the chosen segments.
                rendered = False
                if edit_segments:
                    try:
                        rendered = await asyncio.to_thread(
                            real_ops.render_edit, edit_segments, out_path, w, h
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("real render failed for %s: %s", fmt, e)

                if rendered and out_path.exists():
                    s3_key, size, meta = out_key, local_storage.file_size(out_key), {"real_render": True}
                    # measure rendered duration for accuracy
                    probe = await asyncio.to_thread(real_ops.probe_metadata, out_path)
                    if probe.get("duration_ms"):
                        total_dur = probe["duration_ms"]
                else:
                    # Fallback: reference the source clip so the player still works.
                    s3_key = primary_clip_key or ""
                    size = local_storage.file_size(primary_clip_key) if primary_clip_key else 0
                    meta = {"real_render": False, "fallback_source": True}

                await out_repo.create(
                    project_id=pid,
                    format=OutputFormat(fmt),
                    aspect_ratio=ratio, width=w, height=h,
                    duration_ms=total_dur,
                    file_size_bytes=size,
                    s3_key=s3_key,
                    s3_bucket="local",
                    quality_score=None,
                    render_metadata=meta,
                )
        return total_dur

    project_formats: List[str] | None = None


# ── Launcher ──────────────────────────────────────────────────────────────────

def launch(project_id: str, formats: List[str] | None = None) -> None:
    """Fire-and-forget the local pipeline on the running event loop."""
    pipeline = LocalPipeline(project_id)
    pipeline.project_formats = formats
    asyncio.create_task(pipeline.run())
