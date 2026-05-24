import uuid
from dataclasses import dataclass
from typing import Dict

from app.services.script_generator import ScriptGeneratorService
from app.services.video_composer import VideoComposerService
from app.services.video_fetcher import VideoFetcherService
from app.services.voice_generator import VoiceGeneratorService
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineRequest:
    topic: str
    tone: str = "professional"


class VideoAutomationOrchestrator:
    """Coordinates all pipeline stages for generating an AI video."""

    def __init__(self) -> None:
        self.script_generator = ScriptGeneratorService()
        self.voice_generator = VoiceGeneratorService()
        self.video_fetcher = VideoFetcherService()
        self.video_composer = VideoComposerService()

    def run(self, request: PipelineRequest) -> Dict[str, str]:
        video_id = str(uuid.uuid4())
        logger.info("Pipeline started video_id=%s topic=%s", video_id, request.topic)

        script_data = self.script_generator.generate(topic=request.topic, tone=request.tone)
        voice_data = self.voice_generator.synthesize(script=script_data["script"], video_id=video_id)
        fetched_videos = self.video_fetcher.fetch(topic=request.topic)
        composed_video = self.video_composer.compose(
            video_id=video_id,
            assets=fetched_videos["assets"],
            audio_path=voice_data["audio_path"],
            script=script_data["script"],
        )

        logger.info("Pipeline completed video_id=%s", video_id)
        return {
            "video_id": video_id,
            "topic": request.topic,
            "tone": request.tone,
            "script_model": script_data["model"],
            "voice_provider": voice_data["provider"],
            "video_provider": fetched_videos["provider"],
            "final_video_path": composed_video["final_video_path"],
        }
