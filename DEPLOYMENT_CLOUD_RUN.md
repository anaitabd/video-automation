# Cloud Run Deployment Guide

This service is containerized for **Google Cloud Run** and designed to be stateless.

## 1) Prerequisites

- Google Cloud project with billing enabled.
- APIs enabled:
  - Cloud Run API
  - Cloud Build API
  - Artifact Registry API
- `gcloud` CLI authenticated and project set.

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud auth login
```

## 2) Environment Variables

The container supports runtime configuration through environment variables:

- `PORT` (default: `8080`) - Cloud Run runtime port.
- `ENVIRONMENT` (default: `production`)
- `OPENAI_API_KEY` (required, recommended via Secret Manager)
- `STORAGE_BACKEND` (default: `gcs`)
- `GCS_BUCKET` (required for GCS uploads)
- `GCS_PREFIX` (default: `video-automation`)
- `TMP_DIR` (default: `/tmp/video-automation`)

> Cloud Run filesystem is ephemeral. Persist outputs to GCS, not local disk.

## 3) Build and Deploy (manual)

```bash
PROJECT_ID="YOUR_PROJECT_ID"
REGION="us-central1"
REPOSITORY="video-automation"
SERVICE="video-automation"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPOSITORY}/${SERVICE}:$(date +%Y%m%d-%H%M%S)"

gcloud artifacts repositories create "${REPOSITORY}" \
  --repository-format=docker \
  --location="${REGION}" || true

gcloud auth configure-docker "${REGION}-docker.pkg.dev"

docker build -t "${IMAGE}" .
docker push "${IMAGE}"

gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 \
  --memory 2Gi \
  --concurrency 10 \
  --max-instances 10 \
  --set-env-vars "ENVIRONMENT=production,STORAGE_BACKEND=gcs,GCS_BUCKET=YOUR_BUCKET,GCS_PREFIX=video-automation" \
  --set-secrets "OPENAI_API_KEY=OPENAI_API_KEY:latest"
```

## 4) Build and Deploy (Cloud Build)

Use `cloudbuild.yaml` as-is or override substitutions:

```bash
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions _REGION=us-central1,_REPOSITORY=video-automation,_SERVICE=video-automation
```

## 5) Stateless + GCS Integration Notes

- Treat local writes as temporary-only (`/tmp`).
- Move generated videos/subtitles/audio artifacts to `gs://$GCS_BUCKET/$GCS_PREFIX/...`.
- Use a service account on Cloud Run with least-privilege role:
  - `roles/storage.objectAdmin` (or narrower, e.g. objectCreator + objectViewer)
- Set the runtime service account:

```bash
gcloud run services update video-automation \
  --region us-central1 \
  --service-account video-automation-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```
