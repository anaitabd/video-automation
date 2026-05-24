# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080 \
    ENVIRONMENT=production \
    STORAGE_BACKEND=gcs \
    GCS_BUCKET= \
    GCS_PREFIX=video-automation \
    TMP_DIR=/tmp/video-automation \
    WEB_CONCURRENCY=1

WORKDIR /app

# FFmpeg runtime + tini for proper signal handling in Cloud Run.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY app ./app

# Cloud Run sends SIGTERM during scale-down; tini forwards signals cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Keep process model simple for CPU-heavy FFmpeg workloads.
CMD ["bash", "-lc", "mkdir -p ${TMP_DIR} && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY}"]

EXPOSE 8080
