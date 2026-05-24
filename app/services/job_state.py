import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from google.cloud import firestore


class JobStateStore:
    def __init__(self, collection: str | None = None) -> None:
        self.collection = collection or os.getenv("FIRESTORE_JOBS_COLLECTION", "video_jobs")
        self.client = firestore.Client()

    def _doc(self, job_id: str):
        return self.client.collection(self.collection).document(job_id)

    def create_job(self, job_id: str, payload: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._doc(job_id).set({
            "job_id": job_id,
            "status": "running",
            "current_step": "initialized",
            "created_at": now,
            "updated_at": now,
            "step_logs": [],
            "step_results": {},
            **payload,
        })

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        snap = self._doc(job_id).get()
        if not snap.exists:
            return None
        return snap.to_dict()

    def log_step(self, job_id: str, step: str, status: str, message: str, attempt: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._doc(job_id).update({
            "updated_at": now,
            "current_step": step,
            "step_logs": firestore.ArrayUnion([{
                "step": step,
                "status": status,
                "message": message,
                "attempt": attempt,
                "at": now,
            }]),
        })

    def save_step_result(self, job_id: str, step: str, result: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._doc(job_id).set({
            "updated_at": now,
            "step_results": {step: result},
        }, merge=True)

    def set_status(self, job_id: str, status: str, error: str | None = None) -> None:
        payload: Dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if error:
            payload["error"] = error
        self._doc(job_id).set(payload, merge=True)
