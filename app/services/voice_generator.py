import json
import os
import time
from pathlib import Path
from typing import Dict, Optional
from urllib import error, request

from app.utils.logger import get_logger

logger = get_logger(__name__)


class VoiceGeneratorService:
    """Converts a script into voice-over audio using ElevenLabs."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        stability: Optional[float] = None,
        similarity_boost: Optional[float] = None,
        model_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: Optional[int] = None,
        base_retry_delay_seconds: Optional[float] = None,
    ) -> None:
        self.voice_provider = os.getenv("VOICE_PROVIDER", "elevenlabs")
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
        self.voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
        self.stability = self._normalize_float(
            stability if stability is not None else float(os.getenv("ELEVENLABS_STABILITY", "0.5")),
            "stability",
        )
        self.similarity_boost = self._normalize_float(
            similarity_boost
            if similarity_boost is not None
            else float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.75")),
            "similarity_boost",
        )
        self.model_id = model_id or os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
        self.timeout_seconds = timeout_seconds or int(os.getenv("ELEVENLABS_TIMEOUT_SECONDS", "60"))
        self.max_retries = max_retries if max_retries is not None else int(os.getenv("ELEVENLABS_MAX_RETRIES", "3"))
        self.base_retry_delay_seconds = (
            base_retry_delay_seconds
            if base_retry_delay_seconds is not None
            else float(os.getenv("ELEVENLABS_BASE_RETRY_DELAY_SECONDS", "1.5"))
        )

        self.output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_float(value: float, field_name: str) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{field_name} must be between 0.0 and 1.0, got {value}")
        return value

    def generate_voice(self, script: str, output_path: str) -> str:
        """Generate speech from script text and save an mp3 file locally."""
        if not script or not script.strip():
            raise ValueError("script must be a non-empty string")
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is required")

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        endpoint = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        payload = {
            "text": script,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity_boost,
            },
        }
        data = json.dumps(payload).encode("utf-8")

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            req = request.Request(
                url=endpoint,
                data=data,
                headers={
                    "Accept": "audio/mpeg",
                    "Content-Type": "application/json",
                    "xi-api-key": self.api_key,
                },
                method="POST",
            )

            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    status_code = getattr(response, "status", 200)
                    if status_code >= 400:
                        raise RuntimeError(f"ElevenLabs API returned HTTP {status_code}")
                    audio_bytes = response.read()

                if not audio_bytes:
                    raise RuntimeError("ElevenLabs API returned empty audio data")

                output_file.write_bytes(audio_bytes)
                logger.info("Generated voice audio file at %s", output_file)
                return str(output_file)

            except error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
                message = f"ElevenLabs HTTPError {exc.code}: {body}"
                logger.warning("Attempt %s/%s failed: %s", attempt, self.max_retries, message)
                last_error = RuntimeError(message)
                if exc.code < 500 and exc.code != 429:
                    break
            except (error.URLError, TimeoutError, RuntimeError) as exc:
                logger.warning("Attempt %s/%s failed: %s", attempt, self.max_retries, exc)
                last_error = exc

            if attempt < self.max_retries:
                sleep_seconds = self.base_retry_delay_seconds * (2 ** (attempt - 1))
                time.sleep(sleep_seconds)

        raise RuntimeError(f"Failed to generate voice after {self.max_retries} attempts") from last_error

    def synthesize(self, script: str, video_id: str) -> Dict[str, str]:
        """Generate narration audio file for a video id."""
        logger.info("Synthesizing voice with provider=%s", self.voice_provider)

        audio_path = self.output_dir / f"{video_id}.mp3"
        generated_path = self.generate_voice(script=script, output_path=str(audio_path))

        return {
            "provider": self.voice_provider,
            "audio_path": generated_path,
        }
