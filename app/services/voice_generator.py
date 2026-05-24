import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union
from urllib import error, request

import time

from app.utils.logger import classify_failure_reason, get_logger, log_timed_event
from app.utils.retry import (
    CircuitBreaker,
    CircuitBreakerPolicy,
    ErrorCategory,
    PermanentExternalError,
    RetryPolicy,
    TransientExternalError,
    retryable,
)

logger = get_logger(__name__)


SceneInput = Dict[str, object]
NarrationInput = Union[str, Sequence[SceneInput]]


class VoiceGeneratorService:
    """Generate narration audio with ElevenLabs and cache deterministic outputs."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        stability: Optional[float] = None,
        similarity_boost: Optional[float] = None,
        fallback_voice_id: Optional[str] = None,
        model_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        max_retries: Optional[int] = None,
        base_retry_delay_seconds: Optional[float] = None,
    ) -> None:
        self.voice_provider = "elevenlabs"
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
        self.voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
        self.fallback_voice_id = fallback_voice_id or os.getenv("ELEVENLABS_FALLBACK_VOICE_ID", "MF3mGyEYCl7XYWbV9V6O")
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
        self.retry_policy = RetryPolicy(
            max_retries=self.max_retries,
            base_delay_seconds=self.base_retry_delay_seconds,
            timeout_seconds=float(self.timeout_seconds),
            backoff_multiplier=float(os.getenv("ELEVENLABS_BACKOFF_MULTIPLIER", "2.0")),
            max_delay_seconds=float(os.getenv("ELEVENLABS_MAX_DELAY_SECONDS", "30.0")),
        )
        self.circuit_breaker = CircuitBreaker(
            CircuitBreakerPolicy(
                failure_threshold=int(os.getenv("ELEVENLABS_CIRCUIT_FAILURE_THRESHOLD", "5")),
                recovery_timeout_seconds=float(os.getenv("ELEVENLABS_CIRCUIT_RECOVERY_SECONDS", "30.0")),
            )
        )


    @staticmethod
    def _normalize_float(value: float, field_name: str) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{field_name} must be between 0.0 and 1.0, got {value}")
        return value

    def _render_narration_text(self, narration: NarrationInput) -> str:
        if isinstance(narration, str):
            text = narration.strip()
            if not text:
                raise ValueError("script must be a non-empty string")
            return text

        if not narration:
            raise ValueError("scene list must not be empty")

        lines: List[str] = []
        for index, scene in enumerate(narration, start=1):
            if not isinstance(scene, dict):
                raise ValueError(f"scene at index {index - 1} must be an object")

            text = str(scene.get("text", "")).strip()
            if not text:
                continue

            duration_seconds = scene.get("duration_seconds")
            prefix = f"Scene {index}"
            if duration_seconds is not None:
                prefix = f"{prefix} ({duration_seconds}s)"
            lines.append(f"{prefix}: {text}")

        combined = "\n".join(lines).strip()
        if not combined:
            raise ValueError("scene list did not contain any narration text")
        return combined

    def _cache_key(self, text: str, voice_id: str) -> str:
        payload = {
            "text": text,
            "voice_id": voice_id,
            "fallback_voice_id": self.fallback_voice_id,
            "stability": self.stability,
            "similarity_boost": self.similarity_boost,
            "model_id": self.model_id,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        return digest

    def _classify_elevenlabs_error(self, exc: Exception) -> ErrorCategory:
        if isinstance(exc, PermanentExternalError):
            return ErrorCategory.PERMANENT
        return ErrorCategory.TRANSIENT

    def _call_elevenlabs(self, text: str, output_file: Path, voice_id: str) -> str:
        endpoint = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        payload = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": self.stability,
                "similarity_boost": self.similarity_boost,
            },
        }
        data = json.dumps(payload).encode("utf-8")

        @retryable(retry_policy=self.retry_policy, circuit_breaker=self.circuit_breaker, classifier=self._classify_elevenlabs_error)
        def _attempt_request() -> str:
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
            start_time = time.perf_counter()
            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    status_code = getattr(response, "status", 200)
                    if status_code >= 500:
                        raise TransientExternalError(f"ElevenLabs HTTP {status_code}")
                    if status_code >= 400:
                        raise PermanentExternalError(f"ElevenLabs HTTP {status_code}")
                    audio_bytes = response.read()
                    log_timed_event(logger, event="external_api_latency", start_time=start_time, provider="elevenlabs", endpoint="text_to_speech", status_code=status_code)
            except error.HTTPError as exc:
                logger.warning("ElevenLabs HTTP error", extra={"event": "external_api_latency", "failure_reason": classify_failure_reason(exc), "details": {"provider": "elevenlabs", "status_code": exc.code}})
                if exc.code >= 500 or exc.code == 429:
                    raise TransientExternalError(f"ElevenLabs HTTPError {exc.code}") from exc
                raise PermanentExternalError(f"ElevenLabs HTTPError {exc.code}") from exc
            except (error.URLError, TimeoutError) as exc:
                raise TransientExternalError(str(exc)) from exc

            if not audio_bytes:
                raise TransientExternalError("ElevenLabs API returned empty audio data")
            output_file.write_bytes(audio_bytes)
            logger.info("Generated narration with voice_id=%s at %s", voice_id, output_file)
            return str(output_file)

        return _attempt_request()

    def generate_voice(self, narration: NarrationInput, output_path: str) -> str:
        """Generate narration from script text or scene list and save one synchronized mp3."""
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is required")

        text = self._render_narration_text(narration)
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        cache_key = self._cache_key(text=text, voice_id=self.voice_id)
        try:
            generated_path = self._call_elevenlabs(text=text, output_file=output_file, voice_id=self.voice_id)
        except Exception as primary_error:
            logger.warning("Primary voice generation failed for voice_id=%s; trying fallback voice_id=%s", self.voice_id, self.fallback_voice_id)
            generated_path = self._call_elevenlabs(text=text, output_file=output_file, voice_id=self.fallback_voice_id)
            logger.info("Fallback voice succeeded after primary failure: %s", primary_error)

        return generated_path

    def synthesize(self, script: NarrationInput, audio_path: str) -> Dict[str, str]:
        """Generate narration audio file to a caller-provided path."""
        logger.info("Synthesizing narration provider=%s", self.voice_provider)

        generated_path = self.generate_voice(narration=script, output_path=audio_path)

        return {
            "provider": self.voice_provider,
            "audio_path": generated_path,
        }
