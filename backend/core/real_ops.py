"""
Real (CPU-only) media operations for LOCAL_MODE.

These run genuinely on this machine — no GPU, no cloud:
- probe_metadata : real ffprobe
- detect_scenes  : real PySceneDetect content-aware shot detection
- extract_keyframe / quality_score : real OpenCV
- render_edit    : real ffmpeg trim + scale/pad + concat → a true edited MP4

Everything is best-effort with graceful fallbacks so the pipeline never hard-fails
on a weird input.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from backend.config import settings

logger = logging.getLogger(__name__)


def _resolve_binary(name: str, configured: str) -> str:
    """
    Resolve an absolute path to ffmpeg/ffprobe so subprocess calls work
    regardless of the server process's PATH (preview-launched uvicorn may not
    inherit the user's PATH that contains the WinGet/Homebrew shims).
    """
    candidates = [configured, name]
    for cand in candidates:
        found = shutil.which(cand)
        if found:
            return found
    # Common Windows WinGet location
    winget = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
    if winget.exists():
        return str(winget)
    # Common POSIX locations
    for p in (f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"):
        if Path(p).exists():
            return p
    logger.warning("Could not resolve absolute path for %s; falling back to bare name", name)
    return configured


FFMPEG = _resolve_binary("ffmpeg", settings.FFMPEG_PATH)
FFPROBE = _resolve_binary("ffprobe", settings.FFPROBE_PATH)

# Ensure the ffmpeg dir is on PATH for libraries that shell out (PySceneDetect, OpenCV).
if os.path.isabs(FFMPEG):
    _bindir = str(Path(FFMPEG).parent)
    if _bindir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _bindir + os.pathsep + os.environ.get("PATH", "")

logger.info("real_ops using ffmpeg=%s ffprobe=%s", FFMPEG, FFPROBE)


# ── Metadata ──────────────────────────────────────────────────────────────────

def probe_metadata(path: Path) -> dict:
    """Return {duration_ms, fps, width, height, codec_video, codec_audio, bitrate_kbps}."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout)
        fmt = data.get("format", {})
        v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})

        fps = None
        rate = v.get("r_frame_rate", "0/1")
        try:
            num, den = rate.split("/")
            fps = round(float(num) / float(den), 2) if float(den) else None
        except Exception:
            pass

        return {
            "duration_ms": int(float(fmt.get("duration", 0)) * 1000) or None,
            "fps": fps,
            "width": int(v["width"]) if v.get("width") else None,
            "height": int(v["height"]) if v.get("height") else None,
            "codec_video": v.get("codec_name"),
            "codec_audio": a.get("codec_name"),
            "bitrate_kbps": int(int(fmt.get("bit_rate", 0)) / 1000) or None,
            "has_audio": bool(a),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("ffprobe failed for %s: %s", path, e)
        return {}


def extract_thumbnail(video: Path, out: Path, at_sec: float = 1.0) -> bool:
    try:
        subprocess.run(
            [FFMPEG, "-y", "-ss", str(at_sec), "-i", str(video),
             "-frames:v", "1", "-q:v", "3", str(out)],
            capture_output=True, timeout=30,
        )
        return out.exists()
    except Exception:
        return False


# ── Scene detection (PySceneDetect, CPU) ──────────────────────────────────────

def detect_scenes(video: Path, total_ms: int) -> List[Tuple[int, int]]:
    """Return list of (start_ms, end_ms) shot boundaries."""
    try:
        from scenedetect import ContentDetector, SceneManager, open_video

        vid = open_video(str(video))
        mgr = SceneManager()
        mgr.add_detector(ContentDetector(threshold=settings.SCENE_THRESHOLD))
        mgr.detect_scenes(vid, show_progress=False)
        scenes = mgr.get_scene_list()
        boundaries = [
            (int(s[0].get_seconds() * 1000), int(s[1].get_seconds() * 1000))
            for s in scenes
        ]
        if boundaries:
            return boundaries
    except Exception as e:  # noqa: BLE001
        logger.warning("PySceneDetect failed (%s); chunking evenly", e)

    # Fallback: even ~8s chunks
    if total_ms <= 0:
        total_ms = 60_000
    step = 8_000
    return [(c, min(c + step, total_ms)) for c in range(0, total_ms, step)]


def keyframe_and_quality(video: Path, at_ms: int, out: Optional[Path]) -> float:
    """Grab a frame at at_ms, optionally save it, return a 0-1 quality score."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video))
        cap.set(cv2.CAP_PROP_POS_MSEC, at_ms)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return 0.5
        if out is not None:
            cv2.imwrite(str(out), frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharp = min(cv2.Laplacian(gray, cv2.CV_64F).var() / 500.0, 1.0)
        bright = gray.mean() / 255.0
        exposure = 1.0 - abs(bright - 0.5) * 2
        return float(max(0.0, min(1.0, sharp * 0.6 + exposure * 0.4)))
    except Exception as e:  # noqa: BLE001
        logger.debug("keyframe/quality failed: %s", e)
        return 0.5


# ── Real rendering (ffmpeg) ───────────────────────────────────────────────────

def render_edit(
    segments: List[dict],
    out_path: Path,
    width: int,
    height: int,
    burn_subtitle: Optional[Path] = None,
) -> bool:
    """
    segments: [{"src": Path, "start_ms": int, "end_ms": int}]
    Trims each segment, scales/pads to width×height, concatenates → out_path.
    Returns True on success.
    """
    if not segments:
        return False

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        parts: List[Path] = []

        for i, seg in enumerate(segments):
            src = Path(seg["src"])
            if not src.exists():
                continue
            start = max(0, seg["start_ms"]) / 1000.0
            dur = max(0.3, (seg["end_ms"] - seg["start_ms"]) / 1000.0)
            part = tmp / f"part_{i:03d}.mp4"
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30"
            )
            cmd = [
                FFMPEG, "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", str(src),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-pix_fmt", "yuv420p",
                str(part),
            ]
            r = subprocess.run(cmd, capture_output=True, timeout=300)
            if r.returncode == 0 and part.exists():
                parts.append(part)
            else:
                logger.warning("segment %d render failed: %s", i, r.stderr.decode()[:200])

        if not parts:
            return False

        # Concat via demuxer (all parts share codec/params now)
        listf = tmp / "list.txt"
        listf.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        out_path.parent.mkdir(parents=True, exist_ok=True)

        concat_cmd = [
            FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
        ]
        if burn_subtitle and burn_subtitle.exists():
            sub = burn_subtitle.as_posix().replace(":", "\\:")
            concat_cmd += ["-vf", f"subtitles='{sub}'",
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                           "-c:a", "aac", "-movflags", "+faststart", str(out_path)]
        else:
            concat_cmd += ["-c", "copy", "-movflags", "+faststart", str(out_path)]

        r = subprocess.run(concat_cmd, capture_output=True, timeout=600)
        if r.returncode != 0:
            # Retry concat with re-encode (covers copy-incompatibility)
            r2 = subprocess.run(
                [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-c:a", "aac", "-movflags", "+faststart", str(out_path)],
                capture_output=True, timeout=600,
            )
            if r2.returncode != 0:
                logger.error("concat failed: %s", r2.stderr.decode()[:300])
                return False

        return out_path.exists()
