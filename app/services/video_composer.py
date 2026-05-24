import os
from pathlib import Path
from typing import Dict, List

from app.utils.logger import get_logger

logger = get_logger(__name__)


class VideoComposerService:
    """Composes final video from footage + voice-over + subtitles."""

    def __init__(self) -> None:
        self.output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def compose(self, video_id: str, assets: List[str], audio_path: str, script: str) -> Dict[str, str]:
        """
        Compose final output.

        Replace this placeholder with ffmpeg/moviepy/premiere automation.
        """
        logger.info("Composing video for video_id=%s", video_id)

        final_path = self.output_dir / f"{video_id}.mp4"
        final_path.write_text(
            "\n".join(
                [
                    f"Video ID: {video_id}",
                    f"Audio: {audio_path}",
                    f"Assets: {', '.join(assets)}",
                    f"Script:\n{script}",
                ]
            ),
            encoding="utf-8",
        )

        return {"final_video_path": str(final_path)}
