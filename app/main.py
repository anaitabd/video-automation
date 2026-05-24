import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.pipeline.orchestrator import PipelineRequest, VideoAutomationOrchestrator
from app.utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

app = FastAPI(
    title="AI Video Automation API",
    version="1.0.0",
    description="Production-ready FastAPI service for AI video automation pipelines.",
)

orchestrator = VideoAutomationOrchestrator()


class GenerateVideoRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=200)
    tone: str = Field(default="documentary", min_length=3, max_length=50)
    target_duration_seconds: int = Field(default=120, ge=90, le=150)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "video-automation",
        "environment": os.getenv("ENVIRONMENT", "development"),
    }


@app.post("/generate-video")
def generate_video(payload: GenerateVideoRequest) -> dict:
    try:
        pipeline_request = PipelineRequest(topic=payload.topic, tone=payload.tone, target_duration_seconds=payload.target_duration_seconds)
        return orchestrator.run(request=pipeline_request)
    except ValueError as exc:
        logger.exception("Video generation validation failed")
        raise HTTPException(status_code=400, detail=f"Invalid request: {exc}") from exc
    except Exception as exc:
        logger.exception("Video generation failed")
        raise HTTPException(status_code=500, detail="Video generation failed") from exc
