import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from google.cloud import firestore


class JobState(str, Enum):
    RECEIVED = "RECEIVED"
    SCRIPT_GENERATED = "SCRIPT_GENERATED"
    AUDIO_GENERATED = "AUDIO_GENERATED"
    SCENES_READY = "SCENES_READY"
    RENDERING = "RENDERING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStateStore:
    def __init__(self, collection: str | None = None) -> None:
        self.collection = collection or os.getenv("FIRESTORE_JOBS_COLLECTION", "video_jobs")
        self.client = firestore.Client()

    def _doc(self, job_id: str):
        return self.client.collection(self.collection).document(job_id)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_job(self, job_id: str, payload: Dict[str, Any]) -> None:
        now = self._now()
        self._doc(job_id).set({
            "job_id": job_id,
            "state": JobState.RECEIVED.value,
            "status": "running",
            "current_step": "received",
            "created_at": now,
            "updated_at": now,
            "step_logs": [],
            "step_results": {},
            "attempts": {},
            "failed_step": None,
            "error": None,
            **payload,
        }, merge=True)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        snap = self._doc(job_id).get()
        if not snap.exists:
            return None
        return snap.to_dict()

    def update_job_state(
        self,
        job_id: str,
        state: JobState,
        *,
        step: str,
        status: str = "running",
        error: str | None = None,
        clear_failed_step: bool = False,
    ) -> None:
        payload: Dict[str, Any] = {
            "state": state.value,
            "status": status,
            "current_step": step,
            "updated_at": self._now(),
        }
        if error is not None:
            payload["error"] = error
            payload["failed_step"] = step
        if clear_failed_step:
            payload["failed_step"] = None
            payload["error"] = None
        self._doc(job_id).set(payload, merge=True)

    def get_job_state(self, job_id: str) -> Optional[JobState]:
        snap = self._doc(job_id).get()
        if not snap.exists:
            return None
        raw_state = snap.to_dict().get("state", JobState.RECEIVED.value)
        return JobState(raw_state)

    def log_step(self, job_id: str, step: str, status: str, message: str, attempt: int) -> None:
        now = self._now()
        self._doc(job_id).update({
            "updated_at": now,
            "current_step": step,
            "attempts": {step: attempt},
            "step_logs": firestore.ArrayUnion([{
                "step": step,
                "status": status,
                "message": message,
                "attempt": attempt,
                "at": now,
            }]),
        })

    def save_step_result(self, job_id: str, step: str, result: Dict[str, Any]) -> None:
        self._doc(job_id).set({
            "updated_at": self._now(),
            "step_results": {step: result},
        }, merge=True)

    def get_resume_context(self, job_id: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        return {
            "state": job.get("state", JobState.RECEIVED.value),
            "step_results": job.get("step_results", {}),
            "failed_step": job.get("failed_step"),
            "status": job.get("status", "running"),
        }

    def retry_failed_step(self, job_id: str) -> Optional[str]:
        """Mark a failed job as runnable again and return the failed step."""
        doc_ref = self._doc(job_id)
        snap = doc_ref.get()
        if not snap.exists:
            raise ValueError(f"Job {job_id} not found")

        job = snap.to_dict()
        if job.get("state") != JobState.FAILED.value:
            return None

        failed_step = job.get("failed_step")
        if failed_step:
            step_results = job.get("step_results", {})
            if failed_step in step_results:
                del step_results[failed_step]

            doc_ref.set({
                "status": "running",
                "state": self._step_to_state(failed_step).value,
                "step_results": step_results,
                "failed_step": None,
                "error": None,
                "updated_at": self._now(),
            }, merge=True)
        return failed_step

    def _step_to_state(self, step_name: str) -> JobState:
        mapping = {
            "script_generation": JobState.RECEIVED,
            "audio_generation": JobState.SCRIPT_GENERATED,
            "visual_descriptions": JobState.AUDIO_GENERATED,
            "asset_fetch": JobState.AUDIO_GENERATED,
            "render_video": JobState.SCENES_READY,
            "upload_video": JobState.RENDERING,
        }
        return mapping.get(step_name, JobState.RECEIVED)

    def set_status(self, job_id: str, status: str, error: str | None = None) -> None:
        next_state = JobState.COMPLETED if status == "completed" else JobState.FAILED if status == "failed" else JobState.RECEIVED
        self.update_job_state(
            job_id,
            next_state,
            step="completed" if status == "completed" else "failed" if status == "failed" else "running",
            status=status,
            error=error,
            clear_failed_step=status == "completed",
        )
