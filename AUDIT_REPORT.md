# AI Video Generation System Audit (Cloud Run + Vertex AI)

Date: 2026-05-24

## Scope checked
- FastAPI entrypoint and request lifecycle
- Pipeline orchestration behavior
- Gemini script generation path
- ElevenLabs TTS integration
- Asset fetch and FFmpeg composition
- Subtitle generation path
- Production/cloud readiness against stated target stack

## 1) Critical blocking issues

1. **Implemented architecture does not match stated production architecture (GCS / Firestore / Pub/Sub are not wired).**
   - The current implementation is a single synchronous HTTP request that executes all stages inline and returns local filesystem paths.
   - There is no upload to GCS, no Firestore state machine, and no Pub/Sub fan-out / retry queue.
   - This is a **go-live blocker** for Cloud Run reliability and scale.

2. **Pipeline is synchronous and likely to exceed Cloud Run request timeout under real workloads.**
   - `/generate-video` is a sync route invoking a sync orchestrator that does network calls + FFmpeg in one request cycle.
   - Long renders, retries, and stock media downloads can exceed request timeout; no job handoff is implemented.

3. **Subtitle generation depends on OpenAI, not Gemini/Vertex + ElevenLabs-only design.**
   - `generate_subtitles` calls OpenAI transcription (`gpt-4o-transcribe`) and requires `OPENAI_API_KEY`.
   - This is a hidden dependency and an unexpected production failure mode if that key/model is unavailable.

4. **No durable job state or idempotency controls.**
   - A UUID is generated per call, but there is no persisted workflow state, step checkpointing, dedupe token, or restart recovery.
   - Any crash mid-pipeline loses progress and can leave partial files.

## 2) High risk issues

1. **Local disk lifecycle and leak risk.**
   - Asset/video/audio outputs are persisted under local directories without cleanup policy.
   - Cloud Run instance disk is ephemeral and size-limited; repeated invocations can exhaust disk/memory.

2. **Cloud Run concurrency hazards with CPU-heavy FFmpeg.**
   - FFmpeg runs via subprocess with default service concurrency unspecified in code/docs.
   - Multiple concurrent requests on one instance can saturate CPU/RAM and cause tail latency spikes/OOM.

3. **Retry logic is incomplete for upstream API resilience.**
   - Gemini retries all exceptions with exponential delay, but without jitter or per-error classification.
   - ElevenLabs retries network and 5xx/429 scenarios, but lacks jitter and explicit `Retry-After` handling.
   - Pexels fetch path has minimal retry behavior and no circuit-breaker/backpressure.

4. **Async correctness risk in mixed sync/async usage.**
   - `ScriptGeneratorService.generate()` calls `asyncio.run(...)` inside a sync context. If reused inside an existing event loop (future async route/worker), this will fail.

5. **FFmpeg stability and observability gaps.**
   - No explicit subprocess timeout or watchdog around long/blocked ffmpeg commands.
   - No fallback path when one asset is corrupt beyond throwing and failing entire job.

6. **Schema drift and prompt contract risk.**
   - Prompt says hook <= 10 words, but validation only checks non-empty string. Missing hard enforcement can degrade output quality deterministically.

7. **Security/operational risk from unbounded input fan-out.**
   - Asset fetch splits text into many “sentences,” potentially causing many external calls/downloads with no strict global cap.

## 3) Improvements before first real video

1. **Implement real async architecture (must-have):**
   - FastAPI endpoint should enqueue job (Pub/Sub), return `job_id` immediately.
   - Worker service processes steps asynchronously.
   - Persist step state in Firestore (`queued`, `script_done`, `tts_done`, `assets_done`, `render_done`, `failed`, `completed`) with timestamps and error payloads.

2. **Use GCS for all artifacts:**
   - Store script JSON, audio, temp manifests, final MP4, subtitles in GCS.
   - Keep only short-lived temp files locally; enforce deletion in `finally`.

3. **Hard Cloud Run envelopes:**
   - Separate API and renderer services.
   - API: high concurrency, low CPU.
   - Renderer: concurrency=1, larger memory/CPU, longer timeout, min instances if needed.

4. **Strengthen retries and failure taxonomy:**
   - Add jittered exponential backoff, honor `Retry-After`, cap total deadline per stage.
   - Classify permanent vs transient failures and write reason codes into Firestore.

5. **Idempotency and resume:**
   - Introduce deterministic `job_id`/idempotency key per request.
   - On retry, skip completed stages by reading Firestore checkpoints.

6. **FFmpeg robustness:**
   - Add subprocess timeout.
   - Pre-probe assets and reject/replace incompatible clips.
   - Fallback render path: image-only compilation when video clips fail.

7. **Cost/perf guardrails:**
   - Cap sentence/asset count globally.
   - Enforce max script length for TTS.
   - Add per-job storage + API usage budgets.

8. **Observability and SLO readiness:**
   - Structured logs with `job_id`, `step`, `attempt`.
   - Metrics: success rate, p95 stage duration, retries, failure codes.
   - Error reporting + alerting thresholds.

9. **Dependency alignment:**
   - Either formalize OpenAI subtitle dependency in architecture, or replace with approved stack component.

10. **Quality gates/tests before launch:**
   - Unit tests for each stage + contract tests with mocked APIs.
   - End-to-end integration test in CI with stub media.

## 4) Deployment readiness score (0-100)

**36 / 100**

Rationale:
- Core happy-path functionality exists (script->voice->assets->render).
- But key production platform requirements (async queueing, durable state, object storage integration, idempotency, and Cloud Run workload partitioning) are missing.
- System is currently best viewed as a prototype suitable for low-volume manual use, not production traffic.
