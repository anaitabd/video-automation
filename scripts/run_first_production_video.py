#!/usr/bin/env python3
"""Run the full production pipeline for the first real YouTube Shorts-ready video."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

from app.pipeline.orchestrator import PipelineRequest, VideoAutomationOrchestrator


def _ffprobe_duration_seconds(media_path: str, ffprobe_bin: str) -> float:
    cmd = [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "json", media_path]
    raw = subprocess.check_output(cmd, text=True)
    payload = json.loads(raw)
    return float(payload["format"]["duration"])


def _assert_env(required_vars: list[str]) -> None:
    missing = [name for name in required_vars if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def _get_job_document(orchestrator: VideoAutomationOrchestrator, job_id: str) -> Dict[str, Any]:
    job = orchestrator.state.get_job(job_id)
    if not job:
        raise RuntimeError(f"Job {job_id} was not persisted in Firestore")
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the first production video end-to-end")
    parser.add_argument("--topic", default="Why prices are rising in Morocco")
    parser.add_argument("--job-id", default=f"prod-{uuid.uuid4()}")
    parser.add_argument("--min-duration", type=float, default=90.0)
    parser.add_argument("--max-duration", type=float, default=150.0)
    args = parser.parse_args()

    load_dotenv()

    required_env = [
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "PEXELS_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GCS_BUCKET_NAME",
    ]
    _assert_env(required_env)

    ffprobe_bin = os.getenv("FFPROBE_BIN", "ffprobe")
    orchestrator = VideoAutomationOrchestrator()

    print(f"[pipeline] starting job_id={args.job_id} topic={args.topic!r}")
    result = orchestrator.run(PipelineRequest(topic=args.topic, job_id=args.job_id))
    print(f"[pipeline] completed status={result.get('status')} job_id={result.get('job_id')}")

    job = _get_job_document(orchestrator, args.job_id)
    step_logs = job.get("step_logs", [])
    if not step_logs:
        raise RuntimeError("No step logs found in job state")

    print("[pipeline] step log summary")
    for entry in step_logs:
        print(
            "  - "
            f"{entry.get('at')} "
            f"step={entry.get('step')} "
            f"status={entry.get('status')} "
            f"attempt={entry.get('attempt')} "
            f"message={entry.get('message')}"
        )

    step_results = job.get("step_results", {})
    video_path = step_results.get("render_video", {}).get("final_video_path")
    audio_path = step_results.get("audio_generation", {}).get("audio_path")
    subtitle_path = str(Path(video_path).with_suffix(".srt")) if video_path else ""

    if not video_path or not Path(video_path).exists():
        raise RuntimeError(f"Rendered video missing: {video_path}")
    if not audio_path or not Path(audio_path).exists():
        raise RuntimeError(f"Rendered audio missing: {audio_path}")

    if not subtitle_path or not Path(subtitle_path).exists():
        raise RuntimeError(f"Subtitles are missing: {subtitle_path}")

    video_duration = _ffprobe_duration_seconds(video_path, ffprobe_bin)
    audio_duration = _ffprobe_duration_seconds(audio_path, ffprobe_bin)
    av_delta = abs(video_duration - audio_duration)

    if video_duration < args.min_duration or video_duration > args.max_duration:
        raise RuntimeError(
            f"Video duration {video_duration:.2f}s is out of range ({args.min_duration:.0f}-{args.max_duration:.0f}s)"
        )

    if av_delta > 0.75:
        raise RuntimeError(
            f"Audio/video sync check failed: |video-audio|={av_delta:.2f}s (must be <= 0.75s)"
        )

    video_url = step_results.get("upload_video", {}).get("video_uri") or result.get("video_url")
    if not video_url:
        raise RuntimeError("Final GCS video URL was not found")

    print(f"[verify] duration_seconds={video_duration:.2f}")
    print(f"[verify] audio_video_delta_seconds={av_delta:.2f}")
    print(f"[verify] subtitles={subtitle_path}")
    print(f"[output] final_mp4_url={video_url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
