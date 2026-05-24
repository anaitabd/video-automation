import uuid
from dataclasses import dataclass
from typing import Dict

from app.services.script_generator import ScriptGeneratorService
from app.services.video_composer import VideoComposerService
from app.services.subtitle_generator import generate_subtitles
from app.services.video_fetcher import VideoFetcherService
from app.services.voice_generator import VoiceGeneratorService
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _render_script_for_voice(script: Dict[str, object]) -> str:
    sections = script.get("sections", [])
    if not isinstance(sections, list):
        sections = []

    section_lines = [f"- {section}" for section in sections if isinstance(section, str)]

    return "\n".join(
        [
            f"Hook: {script.get('hook', '')}",
            "Sections:",
            *section_lines,
            f"Call to action: {script.get('cta', '')}",
        ]
    ).strip()


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

        try:
            logger.info("Step 1/6 Generate script video_id=%s", video_id)
            script_data = self.script_generator.generate(topic=request.topic, tone=request.tone)
            rendered_script = _render_script_for_voice(script=script_data["script"])

            logger.info("Step 2/6 Generate voice video_id=%s", video_id)
            voice_data = self.voice_generator.synthesize(script=rendered_script, video_id=video_id)

            logger.info("Step 3/6 Fetch video assets video_id=%s", video_id)
            fetched_videos = self.video_fetcher.fetch(topic=request.topic)

            logger.info("Step 4/6 Compose final video video_id=%s", video_id)
            composed_video = self.video_composer.compose(
                video_id=video_id,
                assets=fetched_videos["assets"],
                audio_path=voice_data["audio_path"],
                script=rendered_script,
            )

            logger.info("Step 5/6 Generate subtitles video_id=%s", video_id)
            subtitle_path = composed_video["final_video_path"].replace(".mp4", ".srt")
            generate_subtitles(audio_path=voice_data["audio_path"], output_path=subtitle_path)

            logger.info("Step 6/6 Pipeline complete video_id=%s", video_id)
            return {
                "video_path": composed_video["final_video_path"],
                "script": rendered_script,
                "audio": voice_data["audio_path"],
            }
        except Exception:
            logger.exception("Pipeline failed video_id=%s", video_id)
            raise
