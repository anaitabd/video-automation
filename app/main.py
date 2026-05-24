import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.pipeline.orchestrator import PipelineRequest, VideoAutomationOrchestrator
from app.utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

app = FastAPI(title="AI Video Automation API", version="1.0.0", description="AI video orchestration service")
orchestrator = VideoAutomationOrchestrator()


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
