import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict

from app.services.job_state import JobState, JobStateStore
from app.services.script_generator import ScriptGeneratorService
from app.services.storage import GCSStorageClient, JobWorkspace
from app.services.subtitle_generator import generate_subtitles
from app.services.video_composer import VideoComposerService
from app.services.video_fetcher import VideoFetcherService
from app.services.visual_generator import SceneVisualGeneratorService
from app.services.voice_generator import VoiceGeneratorService
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineRequest:
    topic: str
    job_id: str | None = None
    retry_failed: bool = False


class VideoAutomationOrchestrator:
    def __init__(self) -> None:
        self.script_generator = ScriptGeneratorService()
        self.voice_generator = VoiceGeneratorService()
        self.visual_generator = SceneVisualGeneratorService()
        self.video_fetcher = VideoFetcherService()
        self.video_composer = VideoComposerService()
        self.storage = GCSStorageClient()
        self.state = JobStateStore()
        self.max_step_retries = 3

    def _run_step(self, *, job_id: str, step_name: str, in_state: JobState, out_state: JobState, fn: Callable[[], Any]) -> Any:
        last_error = None
        self.state.update_job_state(job_id, in_state, step=step_name, status="running")
        for attempt in range(1, self.max_step_retries + 1):
            self.state.log_step(job_id, step_name, "started", "step execution started", attempt)
            try:
                result = fn()
                self.state.save_step_result(job_id, step_name, {"ok": True, "result": result})
                self.state.log_step(job_id, step_name, "completed", "step completed", attempt)
                self.state.update_job_state(job_id, out_state, step=step_name, status="running", clear_failed_step=True)
                return result
            except Exception as exc:
                last_error = exc
                self.state.log_step(job_id, step_name, "failed", str(exc), attempt)
                if attempt >= self.max_step_retries:
                    self.state.update_job_state(job_id, JobState.FAILED, step=step_name, status="failed", error=str(exc))
                    raise
        raise RuntimeError(f"Step {step_name} failed") from last_error

    def run(self, request: PipelineRequest) -> Dict[str, Any]:
        job_id = request.job_id or str(uuid.uuid4())
        existing = self.state.get_job(job_id)
        if not existing:
            self.state.create_job(job_id, {"topic": request.topic})
            existing = self.state.get_job(job_id) or {"step_results": {}}
        elif request.retry_failed:
            self.state.retry_failed_step(job_id)

        workspace = JobWorkspace(job_id=job_id)
        step_results = existing.get("step_results", {})
        prefix = job_id

        try:
            if "script_generation" not in step_results:
                script_data = self._run_step(
                    job_id=job_id,
                    step_name="script_generation",
                    in_state=JobState.RECEIVED,
                    out_state=JobState.SCRIPT_GENERATED,
                    fn=lambda: self.script_generator.generate(topic=request.topic),
                )
                script_path = workspace.path("script.json")
                script_path.write_text(json.dumps(script_data["script"], ensure_ascii=False), encoding="utf-8")
                script_uri = self.storage.upload_file(str(script_path), f"{prefix}/script.json")
                self.state.save_step_result(job_id, "script_generation", {"script": script_data["script"], "script_uri": script_uri})

            step_results = self.state.get_resume_context(job_id)["step_results"]
            script = step_results["script_generation"]["script"]

            if "audio_generation" not in step_results:
                rendered_script = "\n".join([scene.get("text", "") for scene in script.get("scenes", [])])
                voice_data = self._run_step(
                    job_id=job_id,
                    step_name="audio_generation",
                    in_state=JobState.SCRIPT_GENERATED,
                    out_state=JobState.AUDIO_GENERATED,
                    fn=lambda: self.voice_generator.synthesize(script=rendered_script, audio_path=str(workspace.path("audio.mp3"))),
                )
                audio_uri = self.storage.upload_file(voice_data["audio_path"], f"{prefix}/audio.mp3")
                self.state.save_step_result(job_id, "audio_generation", {"audio_path": voice_data["audio_path"], "audio_uri": audio_uri})

            if "visual_descriptions" not in step_results:
                visuals = self._run_step(
                    job_id=job_id,
                    step_name="visual_descriptions",
                    in_state=JobState.AUDIO_GENERATED,
                    out_state=JobState.SCENES_READY,
                    fn=lambda: self.visual_generator.generate(script=script),
                )
                self.state.save_step_result(job_id, "visual_descriptions", {"prompts": visuals})

            step_results = self.state.get_resume_context(job_id)["step_results"]
            visual_prompts = step_results["visual_descriptions"]["prompts"]

            if "asset_fetch" not in step_results:
                assets = self._run_step(
                    job_id=job_id,
                    step_name="asset_fetch",
                    in_state=JobState.SCENES_READY,
                    out_state=JobState.SCENES_READY,
                    fn=lambda: self.video_fetcher.fetch(topic=". ".join(visual_prompts), download_dir=str(workspace.path("scene_assets")), limit=max(3, len(visual_prompts))),
                )
                self.state.save_step_result(job_id, "asset_fetch", assets)

            if "render_video" not in step_results:
                step_results = self.state.get_resume_context(job_id)["step_results"]
                fetched_assets = step_results["asset_fetch"]["assets"]
                audio_path = step_results["audio_generation"]["audio_path"]
                video = self._run_step(
                    job_id=job_id,
                    step_name="render_video",
                    in_state=JobState.RENDERING,
                    out_state=JobState.RENDERING,
                    fn=lambda: self.video_composer.compose(assets=fetched_assets, audio_path=audio_path, output_path=str(workspace.path("final_video.mp4")), script=request.topic),
                )
                subtitle_path = str(workspace.path("final_video.srt"))
                generate_subtitles(audio_path=audio_path, output_path=subtitle_path)
                self.state.save_step_result(job_id, "render_video", video)

            if "upload_video" not in step_results:
                step_results = self.state.get_resume_context(job_id)["step_results"]
                final_uri = self._run_step(
                    job_id=job_id,
                    step_name="upload_video",
                    in_state=JobState.UPLOADING,
                    out_state=JobState.UPLOADING,
                    fn=lambda: self.storage.upload_file(step_results["render_video"]["final_video_path"], f"{prefix}/final_video.mp4"),
                )
                self.state.save_step_result(job_id, "upload_video", {"video_uri": final_uri})

            final_job = self.state.get_job(job_id) or {}
            self.state.set_status(job_id, "completed")
            return {"job_id": job_id, "status": "completed", "video_url": final_job.get("step_results", {}).get("upload_video", {}).get("video_uri")}
        except Exception:
            self.state.set_status(job_id, "failed", error="Pipeline execution failed")
            logger.exception("Pipeline failed job_id=%s", job_id)
            raise
        finally:
            workspace.cleanup()
