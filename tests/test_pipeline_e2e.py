from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from app.pipeline.orchestrator import PipelineRequest, VideoAutomationOrchestrator
from app.services.job_state import JobState


class InMemoryJobStateStore:
    def __init__(self) -> None:
        self.jobs: Dict[str, Dict[str, Any]] = {}

    def _now(self) -> str:
        return "2026-05-24T00:00:00+00:00"

    def create_job(self, job_id: str, payload: Dict[str, Any]) -> None:
        self.jobs[job_id] = {
            "job_id": job_id,
            "state": JobState.RECEIVED.value,
            "status": "running",
            "current_step": "received",
            "step_logs": [],
            "step_results": {},
            "attempts": {},
            "failed_step": None,
            "error": None,
            **payload,
        }

    def get_job(self, job_id: str) -> Dict[str, Any] | None:
        return self.jobs.get(job_id)

    def update_job_state(self, job_id: str, state: JobState, *, step: str, status: str = "running", error: str | None = None, clear_failed_step: bool = False) -> None:
        job = self.jobs[job_id]
        job.update({"state": state.value, "status": status, "current_step": step})
        if error is not None:
            job["error"] = error
            job["failed_step"] = step
        if clear_failed_step:
            job["failed_step"] = None
            job["error"] = None

    def log_step(self, job_id: str, step: str, status: str, message: str, attempt: int) -> None:
        job = self.jobs[job_id]
        job["attempts"][step] = attempt
        job["step_logs"].append({"step": step, "status": status, "message": message, "attempt": attempt})

    def save_step_result(self, job_id: str, step: str, result: Dict[str, Any]) -> None:
        self.jobs[job_id]["step_results"][step] = result

    def get_resume_context(self, job_id: str) -> Dict[str, Any]:
        job = self.jobs[job_id]
        return {
            "state": job["state"],
            "step_results": job["step_results"],
            "failed_step": job["failed_step"],
            "status": job["status"],
        }

    def retry_failed_step(self, job_id: str) -> str | None:
        job = self.jobs[job_id]
        if job["state"] != JobState.FAILED.value:
            return None
        failed_step = job.get("failed_step")
        if failed_step:
            job["step_results"].pop(failed_step, None)
            job.update({"status": "running", "failed_step": None, "error": None})
        return failed_step

    def set_status(self, job_id: str, status: str, error: str | None = None) -> None:
        self.jobs[job_id]["status"] = status
        if status == "completed":
            self.jobs[job_id]["state"] = JobState.COMPLETED.value
            self.jobs[job_id]["failed_step"] = None
            self.jobs[job_id]["error"] = None
        elif status == "failed":
            self.jobs[job_id]["state"] = JobState.FAILED.value
            self.jobs[job_id]["error"] = error


@pytest.fixture()
def orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> VideoAutomationOrchestrator:
    monkeypatch.setenv("GCS_BUCKET_NAME", "test-bucket")
    monkeypatch.setattr("app.pipeline.orchestrator.generate_subtitles", lambda audio_path, output_path: Path(output_path).write_text("1\n00:00:00,000 --> 00:00:01,000\nMock\n", encoding="utf-8"))
    orchestrator = VideoAutomationOrchestrator()
    store = InMemoryJobStateStore()
    calls: list[str] = []

    class MockScriptGenerator:
        def generate(self, topic: str) -> Dict[str, Any]:
            calls.append("script_generation")
            return {
                "script": {
                    "title": "Mock title",
                    "scenes": [
                        {"text": "Scene 1 text", "duration_seconds": 2, "visual_description": "desc1", "emotion": "curious"},
                        {"text": "Scene 2 text", "duration_seconds": 2, "visual_description": "desc2", "emotion": "serious"},
                    ],
                }
            }

    class MockVoiceGenerator:
        def synthesize(self, script: str, audio_path: str) -> Dict[str, str]:
            calls.append("audio_generation")
            Path(audio_path).write_bytes(b"mock-elevenlabs-mp3")
            return {"audio_path": audio_path}

    class MockVisualGenerator:
        def generate(self, script: Dict[str, Any]) -> list[str]:
            calls.append("visual_descriptions")
            return ["Prompt 1", "Prompt 2"]

    class MockVideoFetcher:
        def fetch(self, topic: str, download_dir: str, limit: int) -> Dict[str, Any]:
            calls.append("asset_fetch")
            assets = []
            Path(download_dir).mkdir(parents=True, exist_ok=True)
            for idx in range(2):
                asset = Path(download_dir) / f"asset_{idx}.jpg"
                asset.write_bytes(b"img")
                assets.append(str(asset))
            return {"assets": assets}

    class MockVideoComposer:
        def compose(self, assets: list[str], audio_path: str, output_path: str, script: str) -> Dict[str, Any]:
            calls.append("render_video")
            scene_count = len(assets)
            expected_duration = scene_count * 2
            rendered = Path(output_path)
            rendered.write_bytes(b"mock-mp4")
            return {
                "final_video_path": str(rendered),
                "duration_seconds": expected_duration,
                "scene_count": scene_count,
            }

    class MockStorage:
        def __init__(self) -> None:
            self.uploaded: Dict[str, bytes] = {}
            self.uris: Dict[str, str] = {}
            self.fail_upload_once = False

        def upload_file(self, local_path: str, remote_path: str) -> str:
            calls.append("upload_video" if remote_path.endswith("final_video.mp4") else f"upload:{remote_path}")
            if self.fail_upload_once and remote_path.endswith("final_video.mp4"):
                self.fail_upload_once = False
                raise RuntimeError("Transient GCS upload error")
            blob = Path(local_path).read_bytes() if Path(local_path).exists() else b"mock-mp4"
            uri = f"gs://test-bucket/{remote_path}"
            self.uploaded[remote_path] = blob
            self.uris[remote_path] = uri
            return uri

    orchestrator.state = store
    orchestrator.script_generator = MockScriptGenerator()
    orchestrator.voice_generator = MockVoiceGenerator()
    orchestrator.visual_generator = MockVisualGenerator()
    orchestrator.video_fetcher = MockVideoFetcher()
    orchestrator.video_composer = MockVideoComposer()
    orchestrator.storage = MockStorage()
    orchestrator.max_step_retries = 1

    setattr(orchestrator, "_test_calls", calls)
    return orchestrator


def test_pipeline_e2e_success(orchestrator: VideoAutomationOrchestrator) -> None:
    result = orchestrator.run(PipelineRequest(topic="AI chips"))

    assert result["status"] == "completed"
    assert result["video_url"].endswith("/final_video.mp4")

    job = orchestrator.state.get_job(result["job_id"])
    assert job is not None

    expected_steps = {"script_generation", "audio_generation", "visual_descriptions", "asset_fetch", "render_video", "upload_video"}
    assert expected_steps.issubset(job["step_results"].keys())

    rendered = job["step_results"]["render_video"]
    assert rendered["scene_count"] == 2
    assert rendered["duration_seconds"] == 4

    uploaded = orchestrator.storage.uploaded
    assert f"{result['job_id']}/final_video.mp4" in uploaded
    assert uploaded[f"{result['job_id']}/final_video.mp4"] == b"mock-mp4"

    called_steps = {log["step"] for log in job["step_logs"] if log["status"] == "started"}
    assert expected_steps == called_steps


def test_pipeline_failure_recovery_retries_only_failed_step(orchestrator: VideoAutomationOrchestrator) -> None:
    job_id = "job-retry-001"
    orchestrator.storage.fail_upload_once = True

    with pytest.raises(RuntimeError):
        orchestrator.run(PipelineRequest(topic="Datacenters", job_id=job_id))

    failed_job = orchestrator.state.get_job(job_id)
    assert failed_job["status"] == "failed"
    assert failed_job["failed_step"] == "upload_video"

    before_calls = list(orchestrator._test_calls)
    result = orchestrator.run(PipelineRequest(topic="Datacenters", job_id=job_id, retry_failed=True))
    assert result["status"] == "completed"

    after_calls = orchestrator._test_calls[len(before_calls):]
    assert "upload_video" in after_calls
    assert "script_generation" not in after_calls
    assert "audio_generation" not in after_calls
    assert "visual_descriptions" not in after_calls
    assert "asset_fetch" not in after_calls
    assert "render_video" not in after_calls
