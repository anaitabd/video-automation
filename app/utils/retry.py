import asyncio
import random
import time
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from threading import Lock
from typing import Any, Callable, Optional, TypeVar


class ErrorCategory(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class PermanentExternalError(Exception):
    """Non-retryable external failure."""


class TransientExternalError(Exception):
    """Retryable external failure."""


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open."""


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay_seconds: float = 1.0
    timeout_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0
    jitter_ratio: float = 0.2


@dataclass
class CircuitBreakerPolicy:
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0


class CircuitBreaker:
    def __init__(self, policy: CircuitBreakerPolicy) -> None:
        self.policy = policy
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._lock = Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._state != "open":
                return
            if time.monotonic() - self._opened_at >= self.policy.recovery_timeout_seconds:
                self._state = "half_open"
                return
            raise CircuitOpenError("Circuit breaker is open")

    def on_success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0

    def on_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.policy.failure_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


def classify_exception(exc: Exception) -> ErrorCategory:
    if isinstance(exc, PermanentExternalError):
        return ErrorCategory.PERMANENT
    return ErrorCategory.TRANSIENT


F = TypeVar("F", bound=Callable[..., Any])


def retryable(
    *,
    retry_policy: RetryPolicy,
    circuit_breaker: CircuitBreaker,
    classifier: Optional[Callable[[Exception], ErrorCategory]] = None,
) -> Callable[[F], F]:
    classifier_fn = classifier or classify_exception

    def decorator(func: F) -> F:
        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                last_error: Optional[Exception] = None
                for attempt in range(1, retry_policy.max_retries + 1):
                    circuit_breaker.before_call()
                    try:
                        result = await asyncio.wait_for(func(*args, **kwargs), timeout=retry_policy.timeout_seconds)
                        circuit_breaker.on_success()
                        return result
                    except Exception as exc:
                        if isinstance(exc, asyncio.TimeoutError):
                            exc = TransientExternalError("Operation timed out")
                        last_error = exc
                        category = classifier_fn(exc)
                        if category == ErrorCategory.PERMANENT:
                            circuit_breaker.on_failure()
                            raise
                        circuit_breaker.on_failure()
                        if attempt >= retry_policy.max_retries:
                            break
                        await asyncio.sleep(_compute_backoff(retry_policy, attempt))
                raise RuntimeError(f"Operation failed after {retry_policy.max_retries} attempts") from last_error

            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Optional[Exception] = None
            for attempt in range(1, retry_policy.max_retries + 1):
                circuit_breaker.before_call()
                try:
                    result = func(*args, **kwargs)
                    circuit_breaker.on_success()
                    return result
                except Exception as exc:
                    last_error = exc
                    category = classifier_fn(exc)
                    if category == ErrorCategory.PERMANENT:
                        circuit_breaker.on_failure()
                        raise
                    circuit_breaker.on_failure()
                    if attempt >= retry_policy.max_retries:
                        break
                    time.sleep(_compute_backoff(retry_policy, attempt))
            raise RuntimeError(f"Operation failed after {retry_policy.max_retries} attempts") from last_error

        return sync_wrapper  # type: ignore[return-value]

    return decorator


def _compute_backoff(policy: RetryPolicy, attempt: int) -> float:
    delay = min(policy.max_delay_seconds, policy.base_delay_seconds * (policy.backoff_multiplier ** (attempt - 1)))
    if policy.jitter_ratio <= 0:
        return delay
    jitter = delay * policy.jitter_ratio
    return max(0.0, delay + random.uniform(-jitter, jitter))
