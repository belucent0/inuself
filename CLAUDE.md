# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**torch-asr** is an ASR (Automatic Speech Recognition) + Speaker Diarization system with Architecture V5 design. The system uses an API-based approach where:
- **LiteLLM** handles routing (API gateway)
- **Worker** orchestrates parallel API calls (no GPU dependencies)
- **Host GPU/NPU servers** perform actual AI inference

## Common Commands

### Development

```bash
# Start infrastructure (Docker containers)
docker compose up -d

# Start Provider Manager (manages GPU/NPU servers on host)
pm2 start ecosystem.config.js

# View Provider Manager logs
pm2 logs provider-manager
tail -f logs/provider-manager.log

# Restart Provider Manager after config changes
pm2 restart provider-manager
```

### Backend (FastAPI)

```bash
cd backend
poetry install
poetry run uvicorn app.main:app --reload --port 8000
```

### Worker (Celery)

```bash
cd worker
poetry install
celery -A worker.celery_app worker --loglevel=info -Q celery,asr,asr_tasks,llm_summary,ocr_tasks
```

### Frontend (Next.js)

```bash
cd client
npm install
npm run dev      # Development
npm run build    # Production build
npm run lint     # ESLint
```

### LiteLLM Proxy (Local)

```bash
cd infra/litellm
python run_proxy.py
```

## Architecture

### Request Flow (Batch ASR)

```
User Upload → Backend → Redis Queue → Worker
                                        ↓
                        ┌───────────────┴───────────────┐
                        ↓ (parallel)                    ↓
                   ASR API                      Diarization API
                   (whisper.cpp:8001)          (pyannote:8003)
                        ↓                              ↓
                        └───────────────┬───────────────┘
                                        ↓
                              Merge Results → DB/S3
```

### Key Components

| Component | Port | Description |
|-----------|------|-------------|
| Backend | 8000 | FastAPI, handles uploads, WebSocket |
| Frontend | 3000 | Next.js, React 19, Radix UI |
| Worker | - | Celery, orchestrates ASR+Diarization |
| LiteLLM | 4000 | API gateway, Prometheus-based routing |
| whisper.cpp | 8001 | GPU ASR server (speed mode) |
| insanely-fast | 8002 | GPU ASR server (accuracy mode) |
| diarization | 8003 | pyannote speaker diarization |
| FLM | 11434 | NPU ASR (currently disabled) |
| Provider Manager | - | PM2-managed, starts/stops GPU servers on-demand |

### Directory Structure

```
├── backend/          # FastAPI backend
│   └── app/
│       ├── controllers/   # API routes
│       ├── services/      # Business logic
│       └── repositories/  # Database access
├── worker/           # Celery worker (no GPU deps)
│   ├── pipelines/
│   │   ├── asr/          # ASR pipeline (audio_gateway_client.py)
│   │   ├── llm/          # LLM summarization
│   │   └── ocr/          # OCR processing
│   └── tasks/            # Celery task definitions
├── client/           # Next.js frontend
│   ├── app/              # App Router pages
│   └── components/       # React components
├── infra/
│   ├── litellm/          # Custom LiteLLM handler
│   └── provider_manager/ # GPU server lifecycle manager
└── scripts/          # GPU server scripts (diarization_server.py, etc.)
```

## Key Files

- `worker/pipelines/asr/audio_gateway_client.py`: ASR/Diarization API client
- `worker/pipelines/asr/pipeline.py`: Main ASR+Diarization orchestration
- `worker/pipelines/asr/diarization_utils.py`: Speaker segment merging
- `infra/litellm/custom_handler.py`: Prometheus-based GPU/NPU routing
- `infra/provider_manager/main.py`: On-demand server management via Redis pub/sub
- `scripts/diarization_server.py`: pyannote FastAPI server

## Configuration

### Environment Variables (.env)

Key variables used across the system:
- `REDIS_URL`: Redis connection (default: `redis://localhost:6379/0`)
- `DATABASE_URL`: PostgreSQL connection
- `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`: MinIO/S3 storage
- `WHISPER_CPP_URL`: whisper.cpp server (default: `http://localhost:8001`)
- `DIARIZATION_URL`: Diarization server (default: `http://localhost:8003`)
- `PROMETHEUS_URL`: For GPU/NPU utilization metrics
- `HF_TOKEN`: HuggingFace token for pyannote models

### Provider Manager Timeouts

Configured in `infra/provider_manager/main.py`:
- `whisper-server`: 120s idle timeout
- `diarization-server`: 120s (pyannote model loading ~35s)
- `insanely-fast-server`: 180s

## Important Notes

### Windows-Specific

- Provider Manager uses `DETACHED_PROCESS` flag (not `CREATE_NO_WINDOW`) to avoid ROCm/PyTorch initialization issues
- ROCm environment at `rocm_env/` for GPU servers

### LiteLLM Prisma Error

If LiteLLM fails with Prisma errors, ensure `DATABASE_URL` is removed from environment before importing litellm (handled in `run_proxy.py`).

### Diarization Server Startup

The diarization server loads pyannote model in a background thread to allow immediate HTTP connections while model loads (~35s). Health check via `/ready` endpoint.
