# Production Deployment on Google Cloud Run

This guide targets **production video rendering workloads** (FFmpeg + Python API) with Cloud Run.

## Architecture Goals

- **Stateless service**: request-scoped processing only; no local durable state.
- **Secret-based configuration**: API keys and sensitive config from Secret Manager.
- **Concurrency safety**: each request isolated by `job_id` and temporary filesystem paths.
- **Performance tuning**: CPU/memory profile aligned to FFmpeg-heavy rendering.
- **Cold start control**: min instances + slim startup path.

---

## 1) Prerequisites

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud auth login
```

Enable APIs:

```bash
gcloud services enable run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com
```

---

## 2) Secret Manager setup

Create secrets once:

```bash
echo -n 'YOUR_OPENAI_KEY' | gcloud secrets create OPENAI_API_KEY --data-file=-
echo -n 'YOUR_GCS_BUCKET' | gcloud secrets create GCS_BUCKET --data-file=-
```

If secret exists, add new versions instead:

```bash
echo -n 'YOUR_OPENAI_KEY_ROTATED' | gcloud secrets versions add OPENAI_API_KEY --data-file=-
```

Grant Cloud Run runtime service account access:

```bash
PROJECT_ID="YOUR_PROJECT_ID"
SA_NAME="video-automation-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create "${SA_NAME}"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin"

gcloud secrets add-iam-policy-binding OPENAI_API_KEY \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding GCS_BUCKET \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"
```

---

## 3) Build and push container

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
```

---

## 4) Deploy to Cloud Run (production baseline)

```bash
PROJECT_ID="YOUR_PROJECT_ID"
REGION="us-central1"
SERVICE="video-automation"
SA_EMAIL="video-automation-sa@${PROJECT_ID}.iam.gserviceaccount.com"

# For CPU-bound FFmpeg, start with low request concurrency.
# Increase only after load-testing.
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --service-account "${SA_EMAIL}" \
  --no-allow-unauthenticated \
  --port 8080 \
  --cpu 2 \
  --memory 8Gi \
  --timeout 3600 \
  --concurrency 1 \
  --min-instances 1 \
  --max-instances 20 \
  --cpu-boost \
  --execution-environment gen2 \
  --set-env-vars "ENVIRONMENT=production,STORAGE_BACKEND=gcs,GCS_PREFIX=video-automation,TMP_DIR=/tmp/video-automation,WEB_CONCURRENCY=1" \
  --set-secrets "OPENAI_API_KEY=OPENAI_API_KEY:latest,GCS_BUCKET=GCS_BUCKET:latest"
```

---

## 5) Recommended Cloud Run settings

### Compute profile (video rendering)

- **CPU**: `2` (baseline), increase to `4` for high-res / heavy compositing.
- **Memory**: `8Gi` baseline, increase to `16Gi` for longer timelines or large overlays.
- **Timeout**: up to `3600s` for long render requests.
- **Execution environment**: **Gen2**.

### Scaling limits

- **min instances**: `1` (reduces cold starts).
- **max instances**: `20` initial cap (control spend + downstream quota pressure).
- **concurrency**: `1` for CPU-bound FFmpeg jobs.
- Raise concurrency only if profiling shows CPU headroom and predictable latency.

### Cold start optimization

- Keep image lean (multi-stage Docker build).
- Enable `--cpu-boost`.
- Keep `min-instances=1` in production.
- Avoid startup-time network calls and heavy model downloads.

---

## 6) Stateless and concurrency-safe design checklist

1. **Persist outputs to GCS only** (`STORAGE_BACKEND=gcs`).
2. **Use request-unique job IDs** for file/object naming.
3. **Use per-job temp dirs** under `/tmp/video-automation/<job_id>` and clean after completion.
4. **Never rely on in-memory global state across requests** for correctness.
5. **Idempotency**: retries with the same `job_id` should be safe.
6. **Set concurrency=1** first for CPU-heavy rendering; scale by instances, not threads.

---

## 7) Operational tuning workflow

1. Deploy with baseline (`2 CPU`, `8Gi`, `concurrency=1`).
2. Run load tests with representative video lengths.
3. If p95 latency is high and CPU is saturated, scale **CPU** first.
4. If OOM/restarts occur, scale **memory**.
5. Increase `max-instances` after validating GCS/OpenAI quota limits.

---

## 8) Optional Cloud Build deploy

```bash
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions _REGION=us-central1,_REPOSITORY=video-automation,_SERVICE=video-automation
```

