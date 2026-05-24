import asyncio
import json
import os
from typing import Any, Dict

from google import genai
from google.genai import types

from app.utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "You are an expert YouTube Shorts scriptwriter. "
    "Write high-retention, punchy scripts with short lines and strong pacing. "
    "Return strict JSON only and follow the response schema exactly."
)


def _build_user_prompt(topic: str, language: str, tone: str) -> str:
    return f"""
Create a YouTube Shorts-style script about: {topic}

Language: {language}
Tone: {tone}

Requirements:
- Hook must grab attention in <= 10 words.
- Keep sections concise and easy to narrate.
- Use energetic, viral short-form style.
- Sections should read like storyboard beats.
- CTA should be one clear action.

Return JSON with exactly this shape:
{{
  "hook": "string",
  "sections": ["string", "string", "string"],
  "cta": "string"
}}
""".strip()


class ScriptGeneratorService:
    """Generates a structured narration script from an input topic via Gemini on Vertex AI."""

    SUPPORTED_MODELS = {"gemini-2.5-pro", "gemini-2.5-flash"}

    def __init__(self) -> None:
        self.model_name = os.getenv("SCRIPT_MODEL", "gemini-2.5-flash")
        if self.model_name not in self.SUPPORTED_MODELS:
            raise ValueError(
                f"Unsupported SCRIPT_MODEL='{self.model_name}'. "
                f"Allowed: {', '.join(sorted(self.SUPPORTED_MODELS))}"
            )

        self.default_language = os.getenv("SCRIPT_LANGUAGE", "English")
        self.max_retries = int(os.getenv("SCRIPT_GENERATION_MAX_RETRIES", "3"))
        self.retry_base_delay = float(os.getenv("SCRIPT_GENERATION_RETRY_BASE_DELAY_SECONDS", "1.0"))

        self.client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )

    async def generate_async(self, topic: str, tone: str = "engaging", language: str | None = None) -> Dict[str, Any]:
        """Generate a structured short-video script using Vertex AI Gemini (async-safe)."""
        selected_language = language or self.default_language
        logger.info(
            "Generating structured script with model=%s topic=%s language=%s tone=%s",
            self.model_name,
            topic,
            selected_language,
            tone,
        )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.8,
            response_mime_type="application/json",
            response_schema={
                "type": "object",
                "properties": {
                    "hook": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 6,
                    },
                    "cta": {"type": "string"},
                },
                "required": ["hook", "sections", "cta"],
                "additionalProperties": False,
            },
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=_build_user_prompt(topic=topic, language=selected_language, tone=tone),
                    config=config,
                )

                content = (response.text or "").strip()
                parsed = json.loads(content)
                self._validate_output(parsed)

                return {
                    "topic": topic,
                    "tone": tone,
                    "language": selected_language,
                    "model": self.model_name,
                    "script": parsed,
                    "style": "youtube_shorts",
                    "raw": parsed,
                }
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Script generation attempt %s/%s failed; retrying in %.2fs. error=%s",
                    attempt,
                    self.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("Failed to generate script after retries") from last_error

    def generate(self, topic: str, tone: str = "engaging", language: str | None = None) -> Dict[str, Any]:
        """Synchronous wrapper for script generation."""
        return asyncio.run(self.generate_async(topic=topic, tone=tone, language=language))

    @staticmethod
    def _validate_output(payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Script response must be a JSON object")

        hook = payload.get("hook")
        sections = payload.get("sections")
        cta = payload.get("cta")

        if not isinstance(hook, str) or not hook.strip():
            raise ValueError("hook must be a non-empty string")
        if not isinstance(cta, str) or not cta.strip():
            raise ValueError("cta must be a non-empty string")
        if not isinstance(sections, list) or not sections:
            raise ValueError("sections must be a non-empty list")
        if not all(isinstance(item, str) and item.strip() for item in sections):
            raise ValueError("sections must contain non-empty strings")


def generate_script(topic: str) -> Dict[str, Any]:
    """Return a structured YouTube Shorts script for a topic."""
    service = ScriptGeneratorService()
    return service.generate(topic=topic)
