FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    ENVIRONMENT=production \
    STORAGE_BACKEND=gcs \
    GCS_BUCKET= \
    GCS_PREFIX=video-automation \
    TMP_DIR=/tmp/video-automation

WORKDIR /app

# Install FFmpeg and runtime dependencies needed by common video/audio pipelines.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better build-cache reuse.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy source code.
COPY app ./app

# Cloud Run sends SIGTERM on shutdown; run with shell to expand runtime env vars.
ENTRYPOINT ["/usr/bin/env", "bash", "-lc"]
CMD ["mkdir -p ${TMP_DIR} && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

EXPOSE 8080
