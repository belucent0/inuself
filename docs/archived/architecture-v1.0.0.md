# Architecture v1.0.0

> V8.5 문서 뼈대를 유지하고, 현재 코드 기준으로 업데이트한 기준 아키텍처 문서

## v1.0.0 업데이트 요약 (2026-02-13)

### 왜 v1.0.0인가?

V8.5에서 확보한 강점(SSE 진행률, 검색 재시도, Vite 전환)은 유지하면서,
현재 코드에서는 **콘텐츠 처리 파이프라인의 책임 경계와 상태 전이 규칙**이 더 명확해졌습니다.

핵심 변경:
1. **Backend-Worker 경계 명확화**: Worker는 처리/결과발행, Backend는 소비/DB반영/상태전이
2. **이벤트 기반 E2E 고도화**: `stream:worker:results` → `StreamConsumer` → Pub/Sub/SSE UI 반영
3. **파이프라인 최신화 반영**: ASR 병렬 처리/후처리/fallback, OCR 전처리/정확도 모드 라우팅

### V8.5 → v1.0.0 변경 흐름

| 버전 | 핵심 변경 |
|------|----------|
| **V8.5** | SSE 실시간 진행률, Frontend Vite, `thread_id` 통일 |
| **v1.0.0** | Worker-Backend 책임 분리 강화, 상태머신 중심 전이, 콘텐츠 E2E 플로우 정식화 |

---

## 버전 히스토리

| 버전 | 변경 내용 |
|------|----------|
| V7.5 | Redis → Valkey 마이그레이션 |
| V8.0 | LangGraph 기반 AI Agent 시스템 |
| V8.2 | Langfuse LLM Observability |
| V8.4 | 검색 재시도 메커니즘 (LangGraph 루프) |
| V8.5 | SSE 실시간 진행률 + Frontend Vite 마이그레이션 |
| **v1.0.0** | **콘텐츠 처리 토폴로지/상태머신/컴포넌트 책임 재정렬** |

---

## 개괄 아키텍처 (v1.0.0)

> 맞습니다. 이전 버전보다 서비스가 늘었는데 다이어그램이 과도하게 단순화되어 있었습니다.
> 아래는 `docker-compose.yml` + `infra/provider_manager/README.md` 기준으로 전체 토폴로지를 확장한 버전입니다.

### 1) Docker Compose 전체 서비스 토폴로지

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Docker                                                                                                      │
│                                                                                                             │
│  Client                                                                                                     │
│   │                                                                                                         │
│   ▼                                                                                                         │
│ ┌──────────┐      ┌──────────┐                                                                              │
│ │  nginx   │─────▶│ frontend │                                                                              │
│ └────┬─────┘      └──────────┘                                                                              │
│      │  /api,/ws,/events,/media,/grafana,/flower,/langfuse                                                 │
│      ▼                                                                                                      │
│ ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐                                        │
│ │ backend  │◀───▶│  valkey  │◀───▶│  worker  │─────▶│ litellm  │                                        │
│ │ FastAPI  │      │ streams/  │      │ celery   │      │ router   │                                        │
│ │ agents   │      │ pubsub    │      │ asr/ocr  │      │          │                                        │
│ └──┬───┬───┘      └──────────┘      └──┬───┬───┘      └──────────┘                                        │
│    │   │                                │   │                                                               │
│    │   └──────────────▶┌──────────┐     │   └──────────────▶┌──────────┐                                   │
│    │                   │ postgres │     │                   │  minio   │                                   │
│    └──────────────────▶│ +pgvector│     └──────────────────▶│   s3     │                                   │
│                        └──────────┘                         └──────────┘                                   │
│                                                                                                             │
│  Search                                                                                                     │
│   backend/agents ───────────────────────────────────────────────────────────────────────────────▶ searxng   │
│                                                                                                             │
│  Observability                                                                                              │
│   backend/worker ─▶ tempo(OTLP)                                                                            │
│   docker logs   ─▶ promtail ─▶ loki                                                                        │
│   metrics       ─▶ prometheus ─▶ grafana                                                                   │
│   llm traces    ─▶ langfuse                                                                                │
│   celery state  ─▶ flower                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2) Docker ↔ Windows Host (Provider Manager + GPU/NPU 추론 서비스)

```text
┌────────────────────────────────────────── Docker ──────────────────────────────────────────┐
│                                                                                            │
│  worker / litellm / backend                                                                │
│      │                                                                                     │
│      │ Redis Streams                                                                       │
│      ├──────────────▶ stream:gpu:requests                                                  │
│      ◀─────────────── stream:gpu:responses                                                 │
│                                                                                            │
└───────────────────────────────────────┬────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌──────────────────────────────────── Windows Host ───────────────────────────────────────────┐
│                                                                                             │
│  ┌────────────────────┐                                                                     │
│  │  Provider Manager  │  (process lifecycle / health check / auto recovery / status share) │
│  └─────────┬──────────┘                                                                     │
│            │ localhost HTTP                                                                  │
│            ├────────────▶ flm-asr (NPU, :11434)                                             │
│            ├────────────▶ flm-llm (NPU, :11435)                                             │
│            ├────────────▶ flm-ocr (NPU, :11436)                                             │
│            ├────────────▶ llama-server (GPU, :8080)                                         │
│            ├────────────▶ llama-ocr-server (GPU, :8081)                                     │
│            ├────────────▶ whisper-server (GPU, :8001)                                       │
│            ├────────────▶ insanely-fast-server (GPU, :8002)                                 │
│            └────────────▶ diarization-server (GPU, :8003)                                   │
│                                                                                             │
│  Status/Event 공유: providers:status, stream:provider:events                               │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 기술 스택 (v1.0.0)

### Backend

| 구성요소 | 기술 | 현재 포인트 |
|----------|------|-------------|
| Web Framework | FastAPI | API + SSE/WebSocket 엔드포인트 |
| Agent Framework | LangGraph | 검색 재시도 루프 포함 |
| LLM Gateway | LiteLLM | 모델 라우팅 |
| Queue/Event | Valkey + Celery + Streams/PubSub | 비동기 처리/이벤트 전달 |
| DB | PostgreSQL (+ pgvector) | 콘텐츠/채팅 영속화 |
| Object Storage | MinIO | 원본/결과 파일 저장 |
| State Machine | ContentStateMachine | 상태 전이 검증 |
| Observability | OTel/Tempo + Langfuse + Prometheus/Grafana | 이중 관측성 |

### Frontend

| 구성요소 | 기술 |
|----------|------|
| 빌드 도구 | Vite |
| Framework | React + TypeScript |
| 스타일링 | Tailwind CSS |
| UI | shadcn/ui (Radix) |
| 서버 상태 | TanStack Query + Custom Hooks |
| 실시간 통신 | SSE (진행률), WebSocket (실시간 ASR) |

---

## 검색 품질 아키텍처 (V8.4 계열 유지)

### LangGraph 검색 재시도 루프

```text
IntentParser
   ↓
Searcher → SearchEvaluator
   ↑         ↓
   └── QueryRewriter (조건부)
             ↓
       FallbackHandler (조건부)
             ↓
          Generator
```

### 관련 파일

- `backend/app/agents/graph.py`
- `backend/app/agents/nodes/searcher.py`
- `backend/app/agents/nodes/search_evaluator.py`
- `backend/app/agents/nodes/query_rewriter.py`
- `backend/app/agents/nodes/fallback_handler.py`
- `backend/app/agents/nodes/generator.py`

---

## 실시간 진행률 파이프라인 (V8.5 계열 유지 + v1.0.0 정합화)

### End-to-End 흐름

```text
Worker (결과 이벤트 발행)
  -> stream:worker:results
StreamConsumer (Backend)
  -> 상태 반영 + PipelineProgress
Valkey Pub/Sub
  -> events:file_progress:*
SSE Endpoint
  -> GET /api/events/file-progress/stream
Frontend
  -> useFileProgressSSE
  -> useDisplayProgress
  -> ContentCard / ContentList 반영
```

### 핵심 파일

- `worker/utils/result_publisher.py`
- `backend/app/services/stream_consumer.py`
- `backend/app/utils/progress_tracker.py`
- `backend/app/controllers/events_controller.py`
- `frontend/src/shared/hooks/useFileProgressSSE.ts`
- `frontend/src/features/content/hooks/useDisplayProgress.ts`

### 진행률 표시 원칙 (프론트)

- **Mode A**: SSE progress가 없을 때 시간 기반 추정
- **Mode B**: SSE 마일스톤 사이 보간
- `highWaterMark`로 단조 증가 보장 (역행 방지)

---

## 콘텐츠 처리 파이프라인 (v1.0.0 핵심)

### Backend-Worker 분리 아키텍처

```text
Upload API (Backend)
  -> enqueue (ASR/OCR)
Worker
  -> 원본 다운로드
  -> ASR/OCR pipeline 실행
  -> 결과 업로드 + started/completed/failed 이벤트 발행
Backend StreamConsumer
  -> 결과 소비
  -> DB 저장 (Transcription/Document)
  -> 상태 전이 + 요약 처리
Frontend
  -> SSE로 상태/진행률 반영
```

### 상태 전이 (ContentStateMachine)

```text
QUEUED
  -> PULLING / PROCESSING / OCR_PROCESSING
  -> SUMMARY_QUEUED
  -> SUMMARIZING
  -> COMPLETED

실패/종료 계열:
DOWNLOAD_FAILED / ASR_FAILED / OCR_FAILED / SUMMARY_FAILED / CANCELLED
```

### 파이프라인 최신 반영

- ASR: 병렬 처리, 후처리, fallback 전략 반영
- OCR: Worker 전처리(`preprocessor.py`) + 정확도 모드 기반 provider 분기
- 중복 큐잉 방지: `active_job:{task_type}:{file_id}` 키 사용

### 관련 파일

- `backend/app/controllers/content_controller.py`
- `backend/app/services/file_service.py`
- `backend/app/services/stream_consumer.py`
- `backend/app/state_machines/machines/content_machine.py`
- `backend/app/utils/task_queue_adapter.py`
- `worker/processors/asr_processor.py`
- `worker/processors/ocr_processor.py`
- `worker/pipelines/asr/pipeline.py`
- `worker/pipelines/ocr/preprocessor.py`
- `worker/pipelines/ocr/vision.py`

---

## AI 채팅 워크플로우 (thread_id 기준)

```text
Client
  -> /api/threads
  -> /api/threads/{id}/messages
  -> /api/threads/{id}/messages/{message_id}/stream (SSE)
Backend
  -> thread/message 영속화
  -> Agent 실행
  -> partial_content 저장 + 재연결 복구
```

### 관련 파일

- `backend/app/controllers/ai_chat_controller.py`
- `backend/app/services/thread_service.py`
- `backend/app/repositories/thread_repository.py`
- `backend/app/db/models.py`
- `frontend/src/shared/services/endpoints/threads.ts`
- `frontend/src/shared/hooks/useThreads.ts`

---

## Frontend 아키텍처 (Feature-based)

```text
frontend/src/
├─ features/
│  ├─ chat/
│  ├─ content/
│  └─ upload/
├─ pages/
├─ routes/
└─ shared/
   ├─ components/
   ├─ hooks/
   └─ services/
```

### 주요 파일

- `frontend/src/features/content/components/ContentList.tsx`
- `frontend/src/features/content/components/ContentCard.tsx`
- `frontend/src/features/content/components/ContentDetailLayout.tsx`
- `frontend/src/shared/components/layout/UploadForm.tsx`
- `frontend/src/shared/hooks/useContents.ts`
- `frontend/src/shared/hooks/useFileProgressSSE.ts`
- `frontend/src/shared/services/endpoints/upload.ts`
- `frontend/src/shared/services/endpoints/contents.ts`
- `frontend/src/shared/services/endpoints/threads.ts`

---

## Observability Stack

```text
OpenTelemetry -> Tempo (trace)
Promtail -> Loki (logs)
Prometheus -> Grafana (metrics)
Langfuse (LLM observability)
```

참고:
- `docs/observability-v1.0.md`
- `docs/valkey-v1.0.md`

---

## 주요 파일 맵 (요약)

### Backend
- `backend/app/main.py`
- `backend/app/controllers/content_controller.py`
- `backend/app/controllers/ai_chat_controller.py`
- `backend/app/controllers/events_controller.py`
- `backend/app/controllers/websocket_controller.py`
- `backend/app/services/stream_consumer.py`
- `backend/app/services/llm_summary_service.py`
- `backend/app/agents/graph.py`
- `backend/app/utils/progress_tracker.py`
- `backend/app/state_machines/machines/content_machine.py`

### Worker
- `worker/celery_app.py`
- `worker/tasks/asr_task.py`
- `worker/tasks/ocr_task.py`
- `worker/processors/asr_processor.py`
- `worker/processors/ocr_processor.py`
- `worker/pipelines/asr/pipeline.py`
- `worker/pipelines/ocr/preprocessor.py`
- `worker/pipelines/ocr/vision.py`
- `worker/utils/result_publisher.py`

### Frontend
- `frontend/src/features/chat/components/*`
- `frontend/src/features/content/components/*`
- `frontend/src/features/content/hooks/useDisplayProgress.ts`
- `frontend/src/shared/components/layout/UploadForm.tsx`
- `frontend/src/shared/hooks/useContents.ts`
- `frontend/src/shared/hooks/useFileProgressSSE.ts`
- `frontend/src/shared/services/api/httpClient.ts`
- `frontend/src/shared/services/endpoints/*`

---

## 참고/아카이브

- `docs/archived/architecture-v8.5.md`
- `docs/search-retry-architecture.md`
- `docs/v8.4-search-retry-implementation.md`
- `infra/provider_manager/README.md`
