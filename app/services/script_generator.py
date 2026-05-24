import os
from typing import Dict

from app.utils.logger import get_logger

logger = get_logger(__name__)


class ScriptGeneratorService:
    """Generates a narration script from an input topic/prompt."""

    def __init__(self) -> None:
        self.model_name = os.getenv("SCRIPT_MODEL", "gpt-4o-mini")

    def generate(self, topic: str, tone: str = "professional") -> Dict[str, str]:
        """
        Generate script content.

        In production, replace this placeholder with your LLM provider SDK call.
        """
        logger.info("Generating script with model=%s, topic=%s", self.model_name, topic)

        script_text = (
            f"Title: {topic}\n\n"
            f"Tone: {tone}\n"
            "Intro: Hook the audience with a concise opening.\n"
            "Body: Explain key points with clear examples.\n"
            "Outro: End with a call to action."
        )

        return {
            "topic": topic,
            "tone": tone,
            "model": self.model_name,
            "script": script_text,
        }
