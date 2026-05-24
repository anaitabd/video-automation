import os

from dotenv import load_dotenv
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from app.pipeline.orchestrator import PipelineRequest, VideoAutomationOrchestrator
from app.utils.logger import classify_failure_reason, get_logger, job_context, log_timed_event

load_dotenv()
logger = get_logger(__name__)

app = FastAPI(title="AI Video Automation API", version="1.0.0", description="AI video orchestration service")
orchestrator = VideoAutomationOrchestrator()


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    header_job_id = request.headers.get("x-job-id")
    body_job_id = None
    if request.method.upper() == "POST" and request.url.path == "/generate-video":
        try:
            body = await request.json()
            if isinstance(body, dict):
                body_job_id = body.get("job_id")
        except Exception:
            pass
    request_job_id = str(body_job_id or header_job_id or "request-scope")

    with job_context(request_job_id):
        start = time.perf_counter()
        try:
            response = await call_next(request)
            log_timed_event(
                logger,
                event="api_request",
                start_time=start,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            )
            response.headers["x-job-id"] = request_job_id
            return response
        except Exception as exc:
            logger.exception(
                "request failed",
                extra={"event": "api_request", "failure_reason": classify_failure_reason(exc)},
            )
            raise


class GenerateVideoRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=200)
    job_id: str | None = None
    retry_failed: bool = False


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "video-automation", "environment": os.getenv("ENVIRONMENT", "development")}


@app.get("/jobs/{job_id}/state")
def get_job_state(job_id: str) -> dict:
    state = orchestrator.state.get_job_state(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "state": state.value}


@app.post("/generate-video")
def generate_video(payload: GenerateVideoRequest) -> dict:
    try:
        return orchestrator.run(request=PipelineRequest(topic=payload.topic, job_id=payload.job_id, retry_failed=payload.retry_failed))
    except ValueError as exc:
        logger.exception("Video generation validation failed")
        raise HTTPException(status_code=400, detail=f"Invalid request: {exc}") from exc
    except Exception as exc:
        logger.exception("Video generation failed")
        raise HTTPException(status_code=500, detail="Video generation failed") from exc
