import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "ffprobe")
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
FPS = 30
MIN_SCENE_SECONDS = 0.20
DEFAULT_TRANSITION_SECONDS = 0.35


@dataclass
class SceneSpec:
    asset_path: str
    duration_seconds: float


def _run(cmd: List[str]) -> None:
    logger.debug("Running command: %s", " ".join(shlex.quote(part) for part in cmd))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "FFmpeg command failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"Exit code: {exc.returncode}\n"
            f"STDOUT: {(exc.stdout or '').strip()}\n"
            f"STDERR: {(exc.stderr or '').strip()}"
        ) from exc


def _probe_duration_seconds(path: str) -> float:
    cmd = [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
    output = subprocess.check_output(cmd, text=True)
    parsed = json.loads(output)
    duration = float(parsed["format"]["duration"])
    if duration <= 0:
        raise ValueError(f"Non-positive media duration for: {path}")
    return duration


def _is_image(path: str) -> bool:
    return Path(path).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _normalize_scene_durations(raw_durations: List[float], total_duration: float) -> List[float]:
    if not raw_durations:
        raise ValueError("Scene durations are required")
    cleaned = [max(MIN_SCENE_SECONDS, float(d)) for d in raw_durations]
    raw_sum = sum(cleaned)
    if raw_sum <= 0:
        raise ValueError("Invalid scene durations")
    scale = total_duration / raw_sum
    normalized = [max(MIN_SCENE_SECONDS, d * scale) for d in cleaned]
    correction = total_duration - sum(normalized)
    normalized[-1] += correction
    normalized[-1] = max(MIN_SCENE_SECONDS, normalized[-1])
    return normalized


def _scene_specs_from_assets(audio_duration: float, assets: List[str]) -> List[SceneSpec]:
    if not assets:
        raise ValueError("assets list is empty")
    for asset in assets:
        if not Path(asset).exists():
            raise FileNotFoundError(f"Asset not found: {asset}")
    per_asset_duration = audio_duration / len(assets)
    return [SceneSpec(asset_path=asset, duration_seconds=per_asset_duration) for asset in assets]


def _scene_specs_from_scene_list(audio_duration: float, scenes: List[Dict[str, object]]) -> List[SceneSpec]:
    if not scenes:
        raise ValueError("scenes list is empty")
    assets: List[str] = []
    durations: List[float] = []
    for i, scene in enumerate(scenes):
        asset = str(scene.get("asset") or scene.get("asset_path") or "").strip()
        duration = scene.get("duration_seconds")
        if not asset:
            raise ValueError(f"scene[{i}] missing asset path")
        if duration is None:
            raise ValueError(f"scene[{i}] missing duration_seconds")
        if not Path(asset).exists():
            raise FileNotFoundError(f"Asset not found: {asset}")
        assets.append(asset)
        durations.append(float(duration))
    normalized = _normalize_scene_durations(durations, audio_duration)
    return [SceneSpec(asset_path=assets[i], duration_seconds=normalized[i]) for i in range(len(assets))]


def compose_video(audio_path: str, output_path: str, assets: Optional[List[str]] = None, scenes: Optional[List[Dict[str, object]]] = None) -> str:
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if (not assets and not scenes) or (assets and scenes):
        raise ValueError("Provide exactly one of: assets or scenes")

    audio_duration = _probe_duration_seconds(audio_path)
    scene_specs = _scene_specs_from_scene_list(audio_duration, scenes) if scenes else _scene_specs_from_assets(audio_duration, assets or [])

    transition_duration = min(DEFAULT_TRANSITION_SECONDS, min(spec.duration_seconds for spec in scene_specs) / 3)
    transition_duration = max(0.15, transition_duration)

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    subtitle_path: Optional[str] = os.getenv("VIDEO_SUBTITLE_PATH")
    if not subtitle_path:
        sibling_srt = output_file.with_suffix(".srt")
        if sibling_srt.exists():
            subtitle_path = str(sibling_srt)

    with tempfile.TemporaryDirectory(prefix="video_compose_") as temp_dir:
        prepared_paths: List[str] = []
        segment_durations: List[float] = []

        for idx, scene in enumerate(scene_specs):
            segment_path = str(Path(temp_dir) / f"segment_{idx:03d}.mp4")
            vf = (
                f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
                f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
                f"fps={FPS},format=yuv420p"
            )
            common = [
                FFMPEG_BIN,
                "-y",
                "-threads",
                "2",
                "-i",
                scene.asset_path,
                "-t",
                f"{scene.duration_seconds:.3f}",
                "-vf",
                vf,
                "-an",
                "-vsync",
                "cfr",
                "-r",
                str(FPS),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                segment_path,
            ]
            cmd = common
            if _is_image(scene.asset_path):
                cmd = [FFMPEG_BIN, "-y", "-threads", "2", "-loop", "1", "-i", scene.asset_path] + common[5:]

            try:
                _run(cmd)
            except Exception as exc:
                logger.warning("Skipping corrupt or unsupported clip %s: %s", scene.asset_path, exc)
                fallback_cmd = [
                    FFMPEG_BIN,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black:s={TARGET_WIDTH}x{TARGET_HEIGHT}:d={scene.duration_seconds:.3f}",
                    "-vf",
                    f"fps={FPS},format=yuv420p",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "24",
                    segment_path,
                ]
                _run(fallback_cmd)

            prepared_paths.append(segment_path)
            segment_durations.append(scene.duration_seconds)

        ffmpeg_cmd: List[str] = [FFMPEG_BIN, "-y", "-threads", "2"]
        for segment in prepared_paths:
            ffmpeg_cmd += ["-i", segment]
        ffmpeg_cmd += ["-i", audio_path]

        filter_parts: List[str] = []
        if len(prepared_paths) == 1:
            filter_parts.append("[0:v]setpts=PTS-STARTPTS[vmix]")
            video_label = "vmix"
        else:
            current_label = "0:v"
            cumulative = max(segment_durations[0] - transition_duration, 0.01)
            for i in range(1, len(prepared_paths)):
                out = f"vxf{i}"
                filter_parts.append(
                    f"[{current_label}][{i}:v]xfade=transition=fade:duration={transition_duration:.3f}:offset={cumulative:.3f}[{out}]"
                )
                current_label = out
                cumulative += max(segment_durations[i] - transition_duration, 0.01)
            video_label = current_label

        filter_parts.append(f"[{video_label}]fps={FPS},format=yuv420p,trim=duration={audio_duration:.3f}[vtrim]")
        video_label = "vtrim"

        if subtitle_path:
            escaped_sub = subtitle_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            filter_parts.append(f"[{video_label}]subtitles='{escaped_sub}'[vfinal]")
            video_label = "vfinal"

        ffmpeg_cmd += [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            f"[{video_label}]",
            "-map",
            f"{len(prepared_paths)}:a",
            "-af",
            "aresample=async=1:min_hard_comp=0.100:first_pts=0",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-g",
            str(FPS * 2),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-shortest",
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "2048",
            str(output_file),
        ]
        _run(ffmpeg_cmd)

    return str(output_file.resolve())


class VideoComposerService:
    """Composes final video from footage + voice-over + subtitles."""

    def compose(
        self,
        audio_path: str,
        output_path: str,
        script: str,
        assets: Optional[List[str]] = None,
        scenes: Optional[List[Dict[str, object]]] = None,
    ) -> Dict[str, str]:
        logger.info("Composing video output=%s", output_path)
        composed = compose_video(audio_path=audio_path, output_path=output_path, assets=assets, scenes=scenes)
        return {"final_video_path": composed}
