import os
from pathlib import Path
from typing import Dict

from app.utils.logger import get_logger

logger = get_logger(__name__)


class VoiceGeneratorService:
    """Converts a script into voice-over audio."""

    def __init__(self) -> None:
        self.voice_provider = os.getenv("VOICE_PROVIDER", "elevenlabs")
        self.output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def synthesize(self, script: str, video_id: str) -> Dict[str, str]:
        """
        Generate narration audio file.

        Replace with a real TTS provider integration in production.
        """
        logger.info("Synthesizing voice with provider=%s", self.voice_provider)

        audio_path = self.output_dir / f"{video_id}.txt"
        audio_path.write_text(f"[TTS Placeholder]\n{script}", encoding="utf-8")

        return {
            "provider": self.voice_provider,
            "audio_path": str(audio_path),
        }
