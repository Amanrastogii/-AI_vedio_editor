# AI Video Editor — Complete API Guide

Everything you need to drive the platform: every endpoint, the exact call order,
auth, WebSocket, and which external API keys matter.

- **Base URL (local):** `http://localhost:8000`
- **Interactive docs:** `http://localhost:8000/docs` (Swagger UI — try every endpoint live)
- **Auth:** JWT Bearer token. Get it from `/auth/login` or `/auth/register`, then send
  `Authorization: Bearer <token>` on every other call.
- All API routes are prefixed with `/api/v1` (the WebSocket and `/files` are not).

---

## The full workflow (call order)

```
1. POST /api/v1/auth/register            → get JWT          (once)
2. POST /api/v1/projects                 → create project   → project_id
3. POST /api/v1/projects/{id}/uploads/local  (×N clips)     → upload each clip
4. POST /api/v1/projects/{id}/process    → start 11-agent pipeline
5. WS   /ws/projects/{id}?token=JWT      → watch live agent progress
   (or poll  GET /api/v1/projects/{id}/pipeline)
6. GET  /api/v1/projects/{id}/outputs    → get rendered videos + download URLs
```

---

## 1. Auth

### Register
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"password123","full_name":"You"}'
# → { "access_token": "eyJ...", "token_type": "bearer" }
```

### Login
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"password123"}'
# → { "access_token": "eyJ...", "token_type": "bearer" }
```

Save the token:
```bash
TOKEN="eyJ..."
```

---

## 2. Projects

| Method | Path | Purpose |
|---|---|---|
| POST   | `/api/v1/projects` | Create a project |
| GET    | `/api/v1/projects` | List your projects |
| GET    | `/api/v1/projects/{id}` | Get one project |
| DELETE | `/api/v1/projects/{id}` | Delete project + its files |

```bash
# Create — choose output formats here
curl -X POST http://localhost:8000/api/v1/projects \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"My Reels","output_formats":["reels","youtube"],
       "target_style":{"pacing":"dynamic","tone":"energetic"}}'
# → { "id": "<project_id>", "status": "created", ... }
```

`output_formats` options: `youtube` (16:9), `shorts` (9:16), `reels` (9:16),
`tiktok` (9:16), `linkedin` (16:9).

---

## 3. Upload clips

**Local mode (default):** direct multipart upload to disk — no S3 needed.

```bash
curl -X POST "http://localhost:8000/api/v1/projects/$PID/uploads/local" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/clip1.mp4"
# repeat for each clip (up to ~10+)
```

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/projects/{id}/uploads/local` | **Upload a clip (local mode)** |
| GET  | `/api/v1/projects/{id}/uploads` | List uploaded clips |
| DELETE | `/api/v1/projects/{id}/uploads/{clip_id}` | Remove a clip (before processing) |
| POST | `/api/v1/projects/{id}/uploads/initiate` | (Production/S3) presigned upload start |
| POST | `/api/v1/projects/{id}/uploads/complete` | (Production/S3) finalize upload |

---

## 4. Start processing

```bash
curl -X POST "http://localhost:8000/api/v1/projects/$PID/process" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}'   # optional: {"output_formats":["reels"]} to override
# → { "celery_task_id":"local-<id>", "message":"Local pipeline started" }
```

This launches all 11 agents:
`ingestion → scene_detection → [speech ∥ face ∥ emotion] → story_builder →
editing_decision → [audio ∥ subtitle] → rendering → quality_assurance`.

---

## 5. Watch progress

### Option A — WebSocket (live, recommended)
```
ws://localhost:8000/ws/projects/{project_id}?token=<JWT>
```
Emits JSON events: `pipeline.started`, `agent.started`, `agent.progress`
(`agent`, `progress_pct`, `message`), `agent.completed`, `pipeline.complete`.

### Option B — Poll
```bash
curl "http://localhost:8000/api/v1/projects/$PID/pipeline" \
  -H "Authorization: Bearer $TOKEN"
# → { "project_status":"processing",
#     "agents":[{"agent":"ingestion","status":"completed","progress_pct":100}, ...] }
```

### Inspect intermediate results
| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/projects/{id}/story` | The narrative beats (roles, trims, reasoning) |

---

## 6. Get the edited videos

```bash
curl "http://localhost:8000/api/v1/projects/$PID/outputs" \
  -H "Authorization: Bearer $TOKEN"
# → [{ "format":"reels","aspect_ratio":"9:16","width":1080,"height":1920,
#      "duration_ms":45000,"quality_score":85.5,
#      "download_url":"http://localhost:8000/files/projects/.../outputs/reels.mp4" }, ...]
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/projects/{id}/outputs` | List outputs + streamable URLs |
| GET | `/api/v1/projects/{id}/outputs/{output_id}/download` | Fresh download URL |

The `download_url` streams directly (supports seeking) and works in an HTML
`<video>` tag or any download tool.

---

## External API keys — what you actually need

| Capability | Needed? | Key / Setup |
|---|---|---|
| **Run the whole app locally** | **Nothing** | SQLite + local disk + in-process pipeline. Zero keys. |
| Real **story + editing** decisions (LLM) | Optional | `ANTHROPIC_API_KEY` in `.env` → uses Claude `claude-sonnet-4-6`. Without it, smart heuristics are used. |
| Real **speech-to-text** (production) | Optional | None for WhisperX itself; a **HuggingFace token** is only needed for speaker *diarization* (`pyannote`). Requires GPU. |
| Real **face/emotion** (production) | Optional | None — open models (InsightFace/DeepFace). Requires GPU/onnxruntime. |
| **Storage in the cloud** (production) | Optional | S3/MinIO creds (`S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT_URL`). |

> **Bottom line for localhost:** you need **no API keys at all** to run end-to-end.
> Add only `ANTHROPIC_API_KEY` if you want the LLM to make the story/editing
> decisions instead of the built-in heuristics.

---

## What's real vs simulated on localhost

| Agent | Local (this machine) |
|---|---|
| Ingestion | **REAL** — ffprobe metadata + thumbnails |
| Scene Detection | **REAL** — PySceneDetect + OpenCV keyframes/quality |
| Rendering | **REAL** — ffmpeg trim + scale/pad + concat → true multi-format edit |
| QA | **REAL** — ffprobe checks + quality score |
| Speech / Face / Emotion | Simulated (need GPU/torch — won't build on Python 3.13) |
| Story / Editing | Heuristic locally; **REAL Claude** when `ANTHROPIC_API_KEY` is set |

To switch to the full production path: set `LOCAL_MODE=false` + a Postgres
`DATABASE_URL` + Redis + S3 creds, and run the Celery workers (GPU box).
