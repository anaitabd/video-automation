import contextlib
import json
import logging
import os
import time
import traceback
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

_job_id_ctx: ContextVar[str] = ContextVar("job_id", default="unknown")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "job_id": getattr(record, "job_id", get_job_id()),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "event": getattr(record, "event", "application_log"),
        }
        if hasattr(record, "duration_ms"):
            payload["duration_ms"] = getattr(record, "duration_ms")
        if hasattr(record, "failure_reason"):
            payload["failure_reason"] = getattr(record, "failure_reason")
        if hasattr(record, "details"):
            payload["details"] = getattr(record, "details")

        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info))

        return json.dumps(payload, ensure_ascii=False)


class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("job_id", get_job_id())
        return msg, kwargs


def set_job_id(job_id: str) -> None:
    _job_id_ctx.set(job_id or "unknown")


def get_job_id() -> str:
    return _job_id_ctx.get()


@contextlib.contextmanager
def job_context(job_id: str) -> Iterator[None]:
    token = _job_id_ctx.set(job_id or "unknown")
    try:
        yield
    finally:
        _job_id_ctx.reset(token)


@dataclass
class TimerResult:
    duration_ms: float


def classify_failure_reason(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in text:
        return "timeout"
    if "http" in text or "api" in text or "url" in text:
        return "external_api"
    if "ffmpeg" in text or "codec" in text:
        return "ffmpeg_render"
    if "file not found" in text or "not found" in text:
        return "missing_asset"
    if isinstance(exc, ValueError):
        return "validation"
    return "internal_error"


def log_timed_event(logger: logging.Logger, event: str, start_time: float, **details: Any) -> TimerResult:
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    logger.info(
        f"{event} completed",
        extra={"event": event, "duration_ms": duration_ms, "details": details or None},
    )
    return TimerResult(duration_ms=duration_ms)


def get_logger(name: str) -> logging.LoggerAdapter:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(log_level)
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return ContextAdapter(logger, {})
