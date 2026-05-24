import asyncio
import json
import os
from typing import Any, Dict, List

from google import genai
from google.genai import types


class SceneVisualGeneratorService:
    def __init__(self) -> None:
        self.model_name = os.getenv("VISUALS_MODEL", "gemini-2.5-flash")
        self.client = genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )

    async def generate_async(self, script: Dict[str, Any]) -> List[str]:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.4,
        )
        prompt = (
            "Given this script JSON, produce scene-by-scene visual search prompts as JSON array of strings. "
            "Each prompt should be concrete, cinematic, and suitable for stock footage search.\n"
            f"Script: {json.dumps(script, ensure_ascii=False)}"
        )
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model_name,
            contents=prompt,
            config=config,
        )
        visuals = json.loads((response.text or "[]").strip())
        if not isinstance(visuals, list):
            raise ValueError("visual prompts must be a list")
        return [str(x) for x in visuals]

    def generate(self, script: Dict[str, Any]) -> List[str]:
        return asyncio.run(self.generate_async(script=script))
