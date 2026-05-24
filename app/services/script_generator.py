import json
import os
from typing import Any, Dict

from openai import OpenAI

from app.utils.logger import get_logger

logger = get_logger(__name__)


SYSTEM_PROMPT = (
    "You are an expert short-form YouTube script writer. "
    "Create highly engaging scripts with short, punchy lines and strong retention flow. "
    "Return valid JSON only."
)


def _build_user_prompt(topic: str, language: str) -> str:
    return f"""
Create a structured YouTube Shorts script for this topic: {topic}

Language preference: {language}
Allowed language values: English, Arabic, Darija, or mixed if needed.

Requirements:
- Keep sentences short.
- Style: viral YouTube Shorts, high-retention, energetic.
- Keep ideas simple and clear.
- The script must include exactly these sections:
  1) hook_0_5s
  2) problem_introduction
  3) explanation_simple
  4) supporting_points (2 to 3 bullet points)
  5) conclusion
  6) call_to_action

Return JSON object with this schema:
{{
  "topic": "string",
  "language": "string",
  "style": "short engaging retention",
  "script": {{
    "hook_0_5s": "string",
    "problem_introduction": "string",
    "explanation_simple": "string",
    "supporting_points": ["string", "string", "string optional"],
    "conclusion": "string",
    "call_to_action": "string"
  }}
}}
""".strip()


class ScriptGeneratorService:
    """Generates a structured narration script from an input topic."""

    def __init__(self) -> None:
        self.model_name = os.getenv("SCRIPT_MODEL", "gpt-4o-mini")
        self.default_language = os.getenv("SCRIPT_LANGUAGE", "English")
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def generate(self, topic: str, tone: str = "engaging", language: str | None = None) -> Dict[str, Any]:
        """Generate structured short-video script using OpenAI API."""
        selected_language = language or self.default_language
        logger.info(
            "Generating structured script with model=%s topic=%s language=%s tone=%s",
            self.model_name,
            topic,
            selected_language,
            tone,
        )

        response = self.client.responses.create(
            model=self.model_name,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(topic=topic, language=selected_language)},
            ],
            temperature=0.8,
        )

        content = response.output_text.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:].strip()

        parsed = json.loads(content)
        parsed.setdefault("topic", topic)
        parsed.setdefault("language", selected_language)
        parsed.setdefault("style", "short engaging retention")

        return {
            "topic": parsed["topic"],
            "tone": tone,
            "language": parsed["language"],
            "model": self.model_name,
            "script": parsed["script"],
            "style": parsed["style"],
            "raw": parsed,
        }


def generate_script(topic: str) -> Dict[str, Any]:
    """Return a structured YouTube script for a topic."""
    service = ScriptGeneratorService()
    return service.generate(topic=topic)
