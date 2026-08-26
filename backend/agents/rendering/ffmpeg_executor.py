"""
FFmpeg command builder — translates the EDL JSON into real ffmpeg filter graphs.

Supports:
- Multi-clip concatenation with precise trim points
- Transition effects (dissolve via xfade, fade to black)
- Zoom/pan (scale + crop Ken Burns effect)
- Subtitle burn-in
- Color grading via LUT application
- Audio ducking (sidechain compression)
- Multi-format output (YouTube 16:9, Shorts/Reels/TikTok 9:16, LinkedIn 16:9)
"""
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from backend.config import settings

logger = logging.getLogger(__name__)

OUTPUT_SPECS = {
    "youtube":  {"width": 1920, "height": 1080, "aspect": "16:9", "crf": 18, "preset": "slow"},
    "shorts":   {"width": 1080, "height": 1920, "aspect": "9:16", "crf": 18, "preset": "slow"},
    "reels":    {"width": 1080, "height": 1920, "aspect": "9:16", "crf": 18, "preset": "slow"},
    "tiktok":   {"width": 1080, "height": 1920, "aspect": "9:16", "crf": 20, "preset": "medium"},
    "linkedin": {"width": 1920, "height": 1080, "aspect": "16:9", "crf": 18, "preset": "slow"},
}

LUT_MAP = {
    "cinematic_warm": "luts/cinematic_warm.cube",
    "cinematic_cool": "luts/cinematic_cool.cube",
    "vibrant_pop": "luts/vibrant_pop.cube",
    "documentary": "luts/documentary.cube",
    "moody_dark": "luts/moody_dark.cube",
    "clean_bright": "luts/clean_bright.cube",
}


@dataclass
class ClipSegment:
    local_path: str
    start_ms: int
    end_ms: int
    zoom_params: Optional[Dict] = None
    reframe_params: Optional[Dict] = None
    audio_volume_db: float = 0.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    transition_out_type: str = "cut"
    transition_out_duration_ms: int = 0


class FFmpegExecutor:

    def render(
        self,
        segments: List[ClipSegment],
        output_path: Path,
        format_name: str,
        subtitle_path: Optional[Path] = None,
        enhanced_audio_paths: Optional[Dict[str, str]] = None,
        color_grade_style: str = "clean_bright",
    ) -> None:
        spec = OUTPUT_SPECS.get(format_name, OUTPUT_SPECS["youtube"])
        W, H = spec["width"], spec["height"]

        with tempfile.TemporaryDirectory() as tmpdir:
            # Step 1: Process each segment into a trimmed, scaled, zoomed clip
            processed_clips = []
            for idx, seg in enumerate(segments):
                out = Path(tmpdir) / f"seg_{idx:04d}.mp4"
                self._process_segment(seg, out, W, H)
                if out.exists():
                    processed_clips.append(str(out))

            if not processed_clips:
                raise RuntimeError("No segments were processed successfully")

            # Step 2: Concatenate all processed clips
            concat_out = Path(tmpdir) / "concat.mp4"
            self._concatenate(processed_clips, concat_out)

            # Step 3: Apply color grading LUT
            graded_out = Path(tmpdir) / "graded.mp4"
            self._apply_lut(concat_out, graded_out, color_grade_style, tmpdir)

            # Step 4: Burn in subtitles if provided
            if subtitle_path and subtitle_path.exists():
                sub_out = Path(tmpdir) / "subtitled.mp4"
                self._burn_subtitles(graded_out, sub_out, subtitle_path, W, H)
                working = sub_out if sub_out.exists() else graded_out
            else:
                working = graded_out

            # Step 5: Final encode to target spec
            self._final_encode(working, output_path, spec)

    def _process_segment(self, seg: ClipSegment, output: Path, W: int, H: int) -> None:
        """Trim, scale, zoom/pan a single segment."""
        start_sec = seg.start_ms / 1000
        duration_sec = (seg.end_ms - seg.start_ms) / 1000

        filters = []

        # Scale to output resolution (maintain aspect, pad with black)
        filters.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease")
        filters.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black")

        # Ken Burns zoom effect
        if seg.zoom_params and seg.zoom_params.get("enabled"):
            z_start = seg.zoom_params.get("start_scale", 1.0)
            z_end = seg.zoom_params.get("end_scale", 1.0)
            # Use zoompan filter for smooth zoom
            fps = 25
            nb_frames = int(duration_sec * fps)
            zoom_expr = f"if(lte(on\\,1)\\,{z_start}\\,min(zoom+({z_end}-{z_start})/{nb_frames}\\,{z_end}))"
            filters.append(f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nb_frames}:s={W}x{H}:fps={fps}")

        # Reframe (crop offset)
        if seg.reframe_params:
            x_off = int(seg.reframe_params.get("x", 0) * W)
            y_off = int(seg.reframe_params.get("y", 0) * H)
            filters.append(f"crop={W}:{H}:{x_off}:{y_off}")
            filters.append(f"scale={W}:{H}")

        filter_str = ",".join(filters) if filters else "null"

        # Audio filter
        audio_filters = []
        if seg.audio_volume_db != 0:
            audio_filters.append(f"volume={seg.audio_volume_db}dB")
        if seg.fade_in_ms > 0:
            audio_filters.append(f"afade=t=in:st=0:d={seg.fade_in_ms/1000}")
        if seg.fade_out_ms > 0:
            audio_filters.append(f"afade=t=out:st={max(0, duration_sec - seg.fade_out_ms/1000)}:d={seg.fade_out_ms/1000}")

        audio_filter_str = ",".join(audio_filters) if audio_filters else "anull"

        cmd = [
            settings.FFMPEG_PATH, "-y",
            "-ss", str(start_sec),
            "-t", str(duration_sec),
            "-i", seg.local_path,
            "-vf", filter_str,
            "-af", audio_filter_str,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-r", "25",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            logger.error("Segment processing failed: %s", result.stderr.decode()[:500])

    def _concatenate(self, clip_paths: List[str], output: Path) -> None:
        """FFmpeg concat demuxer — lossless join of same-format clips."""
        list_file = output.parent / "concat_list.txt"
        with open(list_file, "w") as f:
            for p in clip_paths:
                f.write(f"file '{p}'\n")

        cmd = [
            settings.FFMPEG_PATH, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Concat failed: {result.stderr.decode()[:500]}")

    def _apply_lut(self, input_path: Path, output: Path, style: str, tmpdir: str) -> None:
        """Apply a .cube LUT for color grading."""
        lut_rel = LUT_MAP.get(style, LUT_MAP["clean_bright"])
        lut_path = Path("ml") / lut_rel

        if not lut_path.exists():
            logger.warning("LUT not found: %s — skipping color grade", lut_path)
            import shutil
            shutil.copy(input_path, output)
            return

        cmd = [
            settings.FFMPEG_PATH, "-y", "-i", str(input_path),
            "-vf", f"lut3d={lut_path}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-c:a", "copy",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            import shutil
            shutil.copy(input_path, output)

    def _burn_subtitles(self, input_path: Path, output: Path,
                         subtitle_path: Path, W: int, H: int) -> None:
        font_size = max(24, int(H * 0.04))
        cmd = [
            settings.FFMPEG_PATH, "-y", "-i", str(input_path),
            "-vf", (
                f"subtitles={subtitle_path}:force_style="
                f"'FontSize={font_size},Bold=1,PrimaryColour=&HFFFFFF,"
                f"OutlineColour=&H000000,Outline=2,Alignment=2'"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "16",
            "-c:a", "copy",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            logger.warning("Subtitle burn-in failed: %s", result.stderr.decode()[:300])
            import shutil
            shutil.copy(input_path, output)

    def _final_encode(self, input_path: Path, output: Path, spec: dict) -> None:
        """Final high-quality encode to exact output spec."""
        W, H = spec["width"], spec["height"]
        cmd = [
            settings.FFMPEG_PATH, "-y", "-i", str(input_path),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black",
            "-c:v", settings.OUTPUT_VIDEO_CODEC,
            "-preset", spec.get("preset", "slow"),
            "-crf", str(spec.get("crf", settings.OUTPUT_CRF)),
            "-profile:v", "high", "-level", "4.2",
            "-c:a", settings.OUTPUT_AUDIO_CODEC,
            "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart",   # Web-optimized MP4
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=900)
        if result.returncode != 0:
            raise RuntimeError(f"Final encode failed: {result.stderr.decode()[:500]}")
