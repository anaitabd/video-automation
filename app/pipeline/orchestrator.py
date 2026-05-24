import json
import uuid
from dataclasses import dataclass
from typing import Dict

from app.services.script_generator import ScriptGeneratorService
from app.services.storage import GCSStorageClient, JobWorkspace
from app.services.video_composer import VideoComposerService
from app.services.subtitle_generator import generate_subtitles
from app.services.video_fetcher import VideoFetcherService
from app.services.voice_generator import VoiceGeneratorService
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _render_script_for_voice(script: Dict[str, object]) -> str:
    title = script.get("title", "")
    scenes = script.get("scenes", [])
    if not isinstance(scenes, list):
        scenes = []

    scene_lines = []
    for index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            continue
        text = scene.get("text", "")
        duration = scene.get("duration_seconds", "")
        emotion = scene.get("emotion", "")
        scene_lines.append(f"Scene {index} ({duration}s, {emotion}): {text}")

    return "\n".join([f"Title: {title}", *scene_lines]).strip()


@dataclass
class PipelineRequest:
    topic: str
    tone: str = "documentary"
    target_duration_seconds: int = 120


class VideoAutomationOrchestrator:
    """Coordinates all pipeline stages for generating an AI video."""

    def __init__(self) -> None:
        self.script_generator = ScriptGeneratorService()
        self.voice_generator = VoiceGeneratorService()
        self.video_fetcher = VideoFetcherService()
        self.video_composer = VideoComposerService()
        self.storage = GCSStorageClient()

    def run(self, request: PipelineRequest) -> Dict[str, str]:
        video_id = str(uuid.uuid4())
        job_prefix = f"{video_id}"
        workspace = JobWorkspace(job_id=video_id)
        logger.info("Pipeline started video_id=%s topic=%s", video_id, request.topic)

        try:
            logger.info("Step 1/6 Generate script video_id=%s", video_id)
            script_data = self.script_generator.generate(topic=request.topic, tone=request.tone, target_duration_seconds=request.target_duration_seconds)
            rendered_script = _render_script_for_voice(script=script_data["script"])
            script_json_path = workspace.path("script.json")
            script_json_path.write_text(json.dumps(script_data["script"], ensure_ascii=False), encoding="utf-8")
            script_gcs_uri = self.storage.upload_file(str(script_json_path), f"{job_prefix}/script.json")

            logger.info("Step 2/6 Generate voice video_id=%s", video_id)
            voice_data = self.voice_generator.synthesize(script=rendered_script, audio_path=str(workspace.path("audio.mp3")))
            audio_gcs_uri = self.storage.upload_file(voice_data["audio_path"], f"{job_prefix}/audio.mp3")

            logger.info("Step 3/6 Fetch video assets video_id=%s", video_id)
            assets_dir = workspace.path("scene_assets")
            fetched_videos = self.video_fetcher.fetch(topic=request.topic, download_dir=str(assets_dir))
            scene_asset_uris = []
            for asset_path in fetched_videos["assets"]:
                name = asset_path.split("/")[-1]
                scene_asset_uris.append(self.storage.upload_file(asset_path, f"{job_prefix}/scene_assets/{name}"))

            logger.info("Step 4/6 Compose final video video_id=%s", video_id)
            composed_video = self.video_composer.compose(
                assets=fetched_videos["assets"],
                audio_path=voice_data["audio_path"],
                output_path=str(workspace.path("final_video.mp4")),
                script=rendered_script,
            )

            logger.info("Step 5/6 Generate subtitles video_id=%s", video_id)
            subtitle_path = str(workspace.path("final_video.srt"))
            generate_subtitles(audio_path=voice_data["audio_path"], output_path=subtitle_path)
            final_video_gcs_uri = self.storage.upload_file(composed_video["final_video_path"], f"{job_prefix}/final_video.mp4")

            logger.info("Step 6/6 Pipeline complete video_id=%s", video_id)
            return {
                "job_id": video_id,
                "job_prefix": f"gs://{self.storage.bucket_name}/{job_prefix}/",
                "video_path": final_video_gcs_uri,
                "script": script_gcs_uri,
                "audio": audio_gcs_uri,
                "scene_assets": scene_asset_uris,
            }
        except Exception:
            logger.exception("Pipeline failed video_id=%s", video_id)
            raise
        finally:
            workspace.cleanup()
