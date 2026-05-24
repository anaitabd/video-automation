import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from google.cloud import storage

from app.utils.retry import CircuitBreaker, CircuitBreakerPolicy, ErrorCategory, RetryPolicy, TransientExternalError, retryable


class StorageClient(Protocol):
    def upload_file(self, local_path: str, remote_path: str) -> str: ...

    def download_file(self, remote_path: str, local_path: str) -> str: ...

    def upload_json(self, payload: dict, remote_path: str) -> str: ...


class GCSStorageClient:
    def __init__(self, bucket_name: str | None = None) -> None:
        self.bucket_name = bucket_name or os.getenv("GCS_BUCKET_NAME", "")
        if not self.bucket_name:
            raise ValueError("GCS_BUCKET_NAME is required")
        self.client = storage.Client()
        self.bucket = self.client.bucket(self.bucket_name)
        self.retry_policy = RetryPolicy(
            max_retries=int(os.getenv("GCS_MAX_RETRIES", "3")),
            base_delay_seconds=float(os.getenv("GCS_RETRY_BASE_DELAY_SECONDS", "1.0")),
            timeout_seconds=float(os.getenv("GCS_TIMEOUT_SECONDS", "60")),
        )
        self.circuit_breaker = CircuitBreaker(
            CircuitBreakerPolicy(
                failure_threshold=int(os.getenv("GCS_CIRCUIT_FAILURE_THRESHOLD", "5")),
                recovery_timeout_seconds=float(os.getenv("GCS_CIRCUIT_RECOVERY_SECONDS", "30.0")),
            )
        )

    def upload_file(self, local_path: str, remote_path: str) -> str:
        @retryable(retry_policy=self.retry_policy, circuit_breaker=self.circuit_breaker, classifier=lambda exc: ErrorCategory.TRANSIENT)
        def _upload() -> str:
            blob = self.bucket.blob(remote_path)
            blob.upload_from_filename(local_path, timeout=self.retry_policy.timeout_seconds)
            return f"gs://{self.bucket_name}/{remote_path}"

        return _upload()

    def download_file(self, remote_path: str, local_path: str) -> str:
        blob = self.bucket.blob(remote_path)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(local_path)
        return local_path

    def upload_json(self, payload: dict, remote_path: str) -> str:
        @retryable(retry_policy=self.retry_policy, circuit_breaker=self.circuit_breaker, classifier=lambda exc: ErrorCategory.TRANSIENT)
        def _upload() -> str:
            blob = self.bucket.blob(remote_path)
            blob.upload_from_string(
                json.dumps(payload, ensure_ascii=False),
                content_type="application/json",
                timeout=self.retry_policy.timeout_seconds,
            )
            return f"gs://{self.bucket_name}/{remote_path}"

        return _upload()


class JobWorkspace:
    """Ephemeral workspace for a single job. Auto-cleans after pipeline finishes."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.root = Path(tempfile.mkdtemp(prefix=f"job_{job_id}_"))

    def path(self, *parts: str) -> Path:
        target = self.root.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
