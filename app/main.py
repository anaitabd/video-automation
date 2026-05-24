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
    tone: str = Field(default="professional", min_length=3, max_length=50)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "video-automation",
        "environment": os.getenv("ENVIRONMENT", "development"),
    }


@app.post("/v1/videos/generate")
def generate_video(payload: GenerateVideoRequest) -> dict:
    try:
        pipeline_request = PipelineRequest(topic=payload.topic, tone=payload.tone)
        result = orchestrator.run(request=pipeline_request)
        return {"success": True, "data": result}
    except Exception as exc:
        logger.exception("Video generation failed")
        raise HTTPException(status_code=500, detail=f"Video generation failed: {exc}") from exc
