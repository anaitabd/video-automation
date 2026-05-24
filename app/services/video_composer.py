import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
FPS = 30


def _run(cmd: List[str]) -> None:
    """Run shell command and raise a rich error on failure."""
    logger.debug("Running command: %s", " ".join(shlex.quote(part) for part in cmd))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        raise RuntimeError(
            "FFmpeg command failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"Exit code: {exc.returncode}\n"
            f"STDOUT: {stdout}\n"
            f"STDERR: {stderr}"
        ) from exc


def _probe_duration_seconds(path: str) -> float:
    cmd = [
        FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    output = subprocess.check_output(cmd, text=True)
    parsed = json.loads(output)
    duration = float(parsed["format"]["duration"])
    if duration <= 0:
        raise ValueError(f"Non-positive media duration for: {path}")
    return duration


def _is_image(path: str) -> bool:
    return Path(path).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def compose_video(audio_path: str, assets: List[str], output_path: str) -> str:
    """
    Build a vertical 1080x1920 MP4 by sequencing assets under the full audio duration.

    Features implemented:
    - auto trim each clip segment to fit the total audio duration
    - smooth concatenation with xfade fade in/out transitions
    - optional subtitle burn-in if sibling .srt exists (same stem as output)
      or if VIDEO_SUBTITLE_PATH environment variable is provided
    """
    if not assets:
        raise ValueError("assets list is empty")
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    for asset in assets:
        if not Path(asset).exists():
            raise FileNotFoundError(f"Asset not found: {asset}")

    audio_duration = _probe_duration_seconds(audio_path)
    asset_count = len(assets)

    transition_duration = min(0.6, max(0.2, audio_duration / max(asset_count * 8, 1)))
    per_asset_duration = (audio_duration + (asset_count - 1) * transition_duration) / asset_count

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    subtitle_path: Optional[str] = os.getenv("VIDEO_SUBTITLE_PATH")
    if not subtitle_path:
        sibling_srt = output_file.with_suffix(".srt")
        if sibling_srt.exists():
            subtitle_path = str(sibling_srt)

    with tempfile.TemporaryDirectory(prefix="video_compose_") as temp_dir:
        prepared_paths: List[str] = []

        # Normalize all assets into uniform vertical MP4 segments with no audio.
        for idx, asset in enumerate(assets):
            segment_path = str(Path(temp_dir) / f"segment_{idx:03d}.mp4")
            vf = (
                f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
                f"fps={FPS},format=yuv420p"
            )

            if _is_image(asset):
                cmd = [
                    FFMPEG_BIN,
                    "-y",
                    "-loop",
                    "1",
                    "-t",
                    f"{per_asset_duration:.3f}",
                    "-i",
                    asset,
                    "-vf",
                    vf,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "20",
                    "-r",
                    str(FPS),
                    segment_path,
                ]
            else:
                cmd = [
                    FFMPEG_BIN,
                    "-y",
                    "-i",
                    asset,
                    "-t",
                    f"{per_asset_duration:.3f}",
                    "-vf",
                    vf,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "20",
                    "-r",
                    str(FPS),
                    segment_path,
                ]

            _run(cmd)
            prepared_paths.append(segment_path)

        # Build xfade filter graph for smooth fades between segments.
        ffmpeg_cmd: List[str] = [FFMPEG_BIN, "-y"]
        for segment in prepared_paths:
            ffmpeg_cmd += ["-i", segment]
        ffmpeg_cmd += ["-i", audio_path]

        filter_parts: List[str] = []
        if len(prepared_paths) == 1:
            filter_parts.append("[0:v]setpts=PTS-STARTPTS[vfinal]")
            video_label = "vfinal"
        else:
            current_label = "0:v"
            cumulative_offset = per_asset_duration - transition_duration
            for i in range(1, len(prepared_paths)):
                next_label = f"{i}:v"
                output_label = f"vxf{i}"
                filter_parts.append(
                    f"[{current_label}][{next_label}]"
                    f"xfade=transition=fade:duration={transition_duration:.3f}:offset={cumulative_offset:.3f}"
                    f"[{output_label}]"
                )
                current_label = output_label
                cumulative_offset += per_asset_duration - transition_duration
            video_label = current_label

        if subtitle_path:
            escaped_sub = subtitle_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            filter_parts.append(f"[{video_label}]subtitles='{escaped_sub}'[vfinal]")
            video_label = "vfinal"

        filter_complex = ";".join(filter_parts)

        ffmpeg_cmd += [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{video_label}]",
            "-map",
            f"{len(prepared_paths)}:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_file),
        ]

        _run(ffmpeg_cmd)

    return str(output_file.resolve())


class VideoComposerService:
    """Composes final video from footage + voice-over + subtitles."""

    def compose(self, assets: List[str], audio_path: str, output_path: str, script: str) -> Dict[str, str]:
        logger.info("Composing video output=%s", output_path)
        composed = compose_video(audio_path=audio_path, assets=assets, output_path=output_path)
        return {"final_video_path": composed}
