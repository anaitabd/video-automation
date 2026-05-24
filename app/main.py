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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "video-automation", "environment": os.getenv("ENVIRONMENT", "development")}


@app.post("/generate-video")
def generate_video(payload: GenerateVideoRequest) -> dict:
    try:
        return orchestrator.run(request=PipelineRequest(topic=payload.topic, job_id=payload.job_id))
    except ValueError as exc:
        logger.exception("Video generation validation failed")
        raise HTTPException(status_code=400, detail=f"Invalid request: {exc}") from exc
    except Exception as exc:
        logger.exception("Video generation failed")
        raise HTTPException(status_code=500, detail="Video generation failed") from exc
