# Architecture v1.2.0 (현행)

> **Timblo AI Platform — 종합 아키텍처 문서 (Source of Truth)**
>
> 작성일: 2026-05-04 | 버전: v1.2.0 | 대상 코드베이스: `develop` 브랜치
>
> 이 문서가 현재 운영 architecture를 기술하는 단일 SoT입니다. 옛 버전(v1.0.x / v1.1.0)은 `docs/archived/`로 이동되었으며, LiteLLM Proxy / Provider Manager / PM2 / Redis Stream 추론 라우팅은 폐기되었습니다. 현재 NPU 경로는 ai-gateway가 Windows FastFlowLM을 직접 호출합니다.

---

## 버전 히스토리

| 버전 | 날짜 | 주요 변경 |
|------|------|-----------|
| v1.0.0 | 2026-02-21 | Worker-Backend 분리, 초기 종합 문서 |
| v1.0.1 | 2026-02-21 | lemonade-server / LLMTier 상수 / model_copy 정리 (incremental) |
| v1.1.0 | 2026-02-27 | Frontend 상세, Backend 레이어, AI Agent 전체, WPI, DB 스키마, API 레퍼런스, Valkey 구조 추가 |
| **v1.2.0** | 2026-05-04 | **현행.** Provider Manager(Redis Stream + Host PM2 프로세스) · LiteLLM 프록시 · 옛 추론 Stream(`stream:chat:requests` 등) 폐기. ai-gateway가 로컬 추론 컨테이너와 Windows FastFlowLM NPU를 httpx로 직접 호출. backend/worker 코드의 `litellm` 명칭도 `ai_gateway`로 일소(PR #124, #127). |

---

## 목차

1. [System Overview](#1-system-overview)
2. [Frontend Architecture](#2-frontend-architecture)
3. [Backend Architecture](#3-backend-architecture)
4. [AI Agent System](#4-ai-agent-system)
5. [WPI 심리검사 시스템](#5-wpi-심리검사-시스템)
6. [Content Pipeline](#6-content-pipeline)
7. [Progress Pipeline](#7-progress-pipeline)
8. [AI Chat Workflow](#8-ai-chat-workflow)
9. [Database Schema](#9-database-schema)
10. [API Reference](#10-api-reference)
11. [Worker Architecture](#11-worker-architecture)
12. [AI Gateway & Inference Containers](#12-ai-gateway--inference-containers)
13. [Valkey Data Architecture](#13-valkey-data-architecture)
14. [Observability Stack](#14-observability-stack)
15. [Key File Map](#15-key-file-map)
16. [Appendix](#16-appendix)

---

## 1. System Overview

### 1.1 시스템 다이어그램

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLIENT                                                             │
│  Browser (Vite + React + TypeScript)                                │
└────────────────────────┬────────────────────────────────────────────┘
                         │ HTTPS
═════════════════════════╪════════════════════════════════════════════
  Docker Environment (WSL2 + ROCm)                                    │
┌────────────────────────▼────────────────────────────────────────────┐
│  APPLICATION                                                        │
│  Backend (FastAPI) — Controllers → Services → Repositories          │
│  LangGraph AI Agent — 9 Nodes · 6 Tools · 5 Modes                   │
└──────────┬──────────────────────────────────┬───────────────────────┘
           │ Celery                            │ OpenAI SDK (httpx)
┌──────────▼──────────┐           ┌────────────▼──────────────────────┐
│  WORKER             │           │  AI GATEWAY                       │
│  Celery Workers     │──── HTTP ─▶  ai-gateway (FastAPI)             │
│  파일 전처리        │           │  routing.py · tier 매핑           │
│  (FFmpeg/PDF 변환)  │           │  serverless 폴백(Codex/RunPod)    │
└─────────────────────┘           └────┬────┬────┬────┬────┬─────────┘
                                       │httpx│httpx│httpx│httpx│httpx
                                       ▼    ▼    ▼    ▼    ▼
┌─────────────────────────┐   ┌──────────────────────────────────────┐
│  DATA                   │   │  INFERENCE CONTAINERS (GPU + CPU)    │
│  PostgreSQL + pgvector  │   │  ai-llm       :8000 vLLM (Gemma 4)   │
│  Valkey (Redis)         │   │  ai-asr-vllm  :8000 Whisper-large-v3 │
│  MinIO (S3) · SearXNG   │   │  ai-diarize   :8003 pyannote comm-1  │
└─────────────────────────┘   │  ai-embedding :8000 EmbeddingGemma   │
                              └──────────────────────────────────────┘
```

> **마이그레이션 요약 (v1.1.0 → v1.2.0)**
> - Provider Manager(Host Python 프로세스, Redis Stream consumer, PM2 supervised) **폐기**
> - LiteLLM 프록시(`infra/litellm`) **폐기** — ai-gateway가 직접 라우팅
> - 추론 백엔드는 Docker 컨테이너로 통일 (`ai-*` prefix), 호스트 프로세스 없음
> - NPU(FLM) 통합은 v1.2.0 시점 미사용 — 로컬 GPU/CPU 추론만 운영

### 1.2 Docker 서비스 토폴로지

```
docker-compose.yml
│
├── nginx          :80/:443    — Reverse Proxy (SSL Termination)
├── frontend       :3000       — Vite + React SPA
├── backend        :8000       — FastAPI Application
├── worker         (no port)   — Celery Workers (3 queues)
│
├── ai-gateway     :4000       — FastAPI 추론 라우터 (httpx + AsyncOpenAI)
├── cli-proxy-api  :8317/:1455 — OAuth CLI Proxy (Codex 접근, serverless 폴백)
│
├── ai-llm         :8000       — vLLM 0.26 Gemma 4 26B A4B AWQ INT4 + MTP k=4 (ROCm)
├── ai-asr-vllm    :8000       — vLLM Whisper-large-v3-turbo
├── ai-diarize     :8003       — pyannote community-1 (ROCm)
├── ai-embedding   :8000(int)  — EmbeddingGemma 308M Q4 GGUF (llama.cpp)
│
├── postgres       :5432       — PostgreSQL + pgvector
├── valkey         :6379       — Redis-compatible (Valkey 9)
├── minio          :9000/:9001 — S3-compatible Object Storage
├── searxng        :8888       — Meta Search Engine
│
├── prometheus     :9090       — Metrics Collection
├── grafana        :3002       — Monitoring Dashboard
├── loki           :3100       — Log Aggregation
├── promtail       (agent)     — Log Collector
├── tempo          :3200/:4317 — Distributed Tracing (OTLP)
├── flower         :5555       — Celery Task Monitor
└── langfuse       :3001       — LLM Observability
```

**서비스 의존성:**
- `backend` → postgres(healthy), valkey(healthy), minio(healthy), ai-gateway(healthy)
- `worker` → valkey(healthy), postgres(healthy), tempo(started)
- `ai-gateway` → ai-llm/ai-asr-vllm/ai-diarize/ai-embedding (httpx 호출 시점에만)
- `langfuse` → postgres(healthy)

### 1.3 호스트 환경 (WSL2 + ROCm)

v1.2.0 기준 **모든 추론 백엔드는 컨테이너**로 운영됩니다 (Provider Manager Host 프로세스 없음).

```
Windows Host
└── WSL2 Ubuntu 24.04
    ├── Docker Engine
    │   ├── ai-llm / ai-asr-vllm / ai-diarize (ROCm iGPU)
    │   └── ai-embedding (CPU)
    ├── self-hosted runner (GitHub Actions, langfuse-eval-gate.yml)
    └── HF model cache (named volume `hf-cache-fast` 공유)

GPU: AMD Radeon 890M (gfx1150) iGPU, ROCm 6.x
.wslconfig: memory=32GB, swap=8GB (vLLM weight load OOM 회피)
```

### 1.4 기술 스택

| 영역 | 기술 | 비고 |
|------|------|------|
| **Frontend** | Vite + React + TypeScript | React 19, shadcn/ui |
| **UI Library** | shadcn/ui (Radix UI) + Tailwind CSS | — |
| **상태관리** | Zustand + React Context | — |
| **Backend** | FastAPI (Python) | 3.11+ |
| **ORM** | SQLAlchemy (async) + Alembic | — |
| **AI Framework** | LangGraph (LangChain 기반) | — |
| **AI Gateway** | FastAPI + httpx + AsyncOpenAI | LiteLLM 대체 (v1.2.0~) |
| **추론 백엔드** | vLLM · llama.cpp(CPU) · pyannote · FastFlowLM | ROCm/CPU 컨테이너 + Windows NPU |
| **Database** | PostgreSQL + pgvector | pg18 |
| **Cache/Queue** | Valkey 9 (Redis-compatible) | — |
| **Object Storage** | MinIO (S3-compatible) | — |
| **Task Queue** | Celery | — |
| **Search** | SearXNG (Meta Search) | — |
| **Observability** | OpenTelemetry + Langfuse + Prometheus + Grafana + Loki + Tempo | — |

---

## 2. Frontend Architecture

### 2.1 디렉토리 구조

```
frontend/src/
├── pages/                        # 페이지 컴포넌트 (라우트 단위)
│   ├── ChatPage.tsx              # AI 채팅 (메인)
│   ├── ContentDetailPage.tsx     # 콘텐츠 상세
│   ├── ContentsPage.tsx          # 콘텐츠 목록
│   ├── HomePage.tsx              # 홈
│   ├── LoginPage.tsx / SignupPage.tsx
│   ├── ScanPage.tsx              # WPI 스캔 홈
│   ├── ScanHistoryPage.tsx / ScanDetailPage.tsx
│   ├── WpiTestPage.tsx / WpiResultPage.tsx
│   ├── MonitoringPage.tsx        # Langfuse 대시보드
│   ├── ThreadsPage.tsx / UploadPage.tsx / RoadmapPage.tsx
│   └── index.ts
│
├── features/                     # 기능 도메인 (feature-first)
│   ├── chat/                     # AI 채팅
│   │   └── components/
│   │       ├── ChatArea.tsx              # 메인 채팅 영역
│   │       ├── ChatMessage.tsx           # 메시지 렌더링
│   │       ├── MessageList.tsx           # 메시지 목록
│   │       ├── ChatInput.tsx             # 입력창 + 전송
│   │       ├── AIModeSelector.tsx        # SIMPLE/SEARCH/RAG/...
│   │       ├── QueryAnalysisDisplay.tsx  # 쿼리 분석 표시
│   │       ├── ThinkingDisplay.tsx       # 사고 과정 (SSE)
│   │       ├── SourceCarousel.tsx        # 웹 출처 목록
│   │       ├── MarkdownContent.tsx       # 마크다운 렌더링
│   │       └── QueuedMessage.tsx         # 전송 대기 상태
│   │
│   ├── content/                  # 콘텐츠 관리
│   │   └── components/
│   │       ├── ContentList.tsx / ContentCard.tsx
│   │       ├── ContentDetailView.tsx     # 상세 뷰 (레이아웃)
│   │       ├── ContentChatPanel.tsx      # 콘텐츠 기반 채팅
│   │       ├── SummaryDisplay.tsx        # AI 요약
│   │       ├── viewers/                  # MediaViewer, PdfViewer, ImageViewer
│   │       ├── results/                  # TranscriptionSegments, OcrTextDisplay
│   │       └── modals/                   # OcrRetryModal, AsrRetryModal
│   │
│   ├── scan/                     # WPI 심리검사
│   │   ├── api/scanApi.ts
│   │   ├── hooks/useScan.ts
│   │   └── components/WpiResultChart.tsx
│   │
│   ├── upload/                   # 파일 업로드
│   │   └── components/ (FileUploader, AudioUploadModal, DocumentUploadModal)
│   │
│   ├── thread/                   # 스레드 목록
│   └── monitoring/               # Langfuse UI
│
├── shared/                       # 공유 모듈
│   ├── components/
│   │   ├── ui/                   # shadcn/ui 컴포넌트 (~20개)
│   │   ├── layout/               # RootLayout, AppSidebar, UploadForm
│   │   └── auth/                 # AuthBrandMark
│   ├── contexts/                 # AuthContext, ThreadTitleContext
│   ├── hooks/                    # 공유 훅 (~10개)
│   ├── services/
│   │   ├── api/httpClient.ts     # Axios 기반 HTTP 클라이언트
│   │   ├── endpoints/            # auth, contents, threads, upload, langfuse
│   │   ├── authToken.ts          # JWT 토큰 관리
│   │   └── chatStreamService.ts  # SSE 스트리밍 서비스
│   ├── stores/chatStore.ts       # Zustand 채팅 상태
│   ├── types/                    # 공유 타입 정의
│   └── config/                   # navigation, scan 설정
│
├── routes/index.tsx              # React Router v6 설정
├── App.tsx                       # 앱 루트
└── main.tsx                      # 엔트리 포인트
```

### 2.2 라우팅 구조

```
React Router v6
│
├── /login              — LoginPage (Public)
├── /signup             — SignupPage (Public)
│
└── / (ProtectedRoute + RootLayout)
    ├── /               — HomePage
    ├── /chat/:threadId — ChatPage        ← 메인 AI 채팅
    ├── /threads        — ThreadsPage
    ├── /contents       — ContentsPage
    ├── /contents/:id   — ContentDetailPage
    ├── /upload         — UploadPage
    ├── /monitoring     — MonitoringPage
    ├── /roadmap        — RoadmapPage
    └── /scan           — WPI 스캔 라우트 그룹
        ├── /scan           — ScanPage
        ├── /scan/wpi       — WpiTestPage
        ├── /scan/wpi/result — WpiResultPage
        ├── /scan/history   — ScanHistoryPage
        └── /scan/history/:resultId — ScanDetailPage
```

### 2.3 상태관리 패턴

#### Zustand (chatStore)

```typescript
// frontend/src/shared/stores/chatStore.ts
interface ChatStore {
  // 채팅 메시지 상태
  messages: Message[]
  streamingContent: string        // 스트리밍 중인 청크 누적
  isStreaming: boolean
  currentMode: AIMode

  // 쿼리 분석 & 사고 과정
  queryAnalysis: QueryAnalysis | null
  thinkingSteps: ThinkingStep[]
  sources: SearchResult[]

  // 액션
  addMessage: (msg: Message) => void
  appendChunk: (chunk: string) => void
  setStreaming: (v: boolean) => void
  resetChat: () => void
}
```

#### React Context

| 컨텍스트 | 파일 | 역할 |
|---------|------|------|
| `AuthContext` | shared/contexts/AuthContext.tsx | 로그인 상태, JWT 토큰, 사용자 정보 |
| `ThreadTitleContext` | shared/contexts/ThreadTitleContext.tsx | 스레드 제목 동적 업데이트 |

### 2.4 SSE 스트리밍 아키텍처

```
Frontend: chatStreamService.ts
│
├── POST /api/threads/{id}/messages   — 메시지 생성 (queued 상태)
│
└── GET  /api/threads/{id}/messages/{msgId}/stream  — SSE 구독
    │
    ├── event: "status"    → UI 상태 표시 (analyzing/searching/...)
    ├── event: "thinking"  → ThinkingDisplay 컴포넌트 업데이트
    ├── event: "sources"   → SourceCarousel 컴포넌트 업데이트
    ├── event: "token"     → chatStore.appendChunk() 호출
    ├── event: "error"     → 에러 UI 표시
    └── event: "done"      → isStreaming = false, 메시지 완료

재연결 전략:
- EventSource 자동 재연결 (브라우저 기본)
- 취소: POST /api/threads/{id}/messages/{msgId}/cancel
```

### 2.5 주요 컴포넌트 계층

```
RootLayout
└── AppSidebar (네비게이션)
└── <Outlet>
    └── ChatPage
        └── ChatArea
            ├── MessageList
            │   ├── ChatMessage (assistant)
            │   │   ├── ThinkingDisplay (SSE 사고 과정)
            │   │   ├── MarkdownContent (응답 렌더링)
            │   │   ├── SourceCarousel (출처 목록)
            │   │   └── MessageActions (복사/재생성)
            │   └── QueuedMessage (user, 전송 중)
            ├── ChatInput (입력창)
            └── AIModeSelector (모드 선택)
```

---

## 3. Backend Architecture

### 3.1 레이어 구조

```
HTTP Request
    │
    ▼
┌─────────────────────────────────────┐
│         Controller Layer            │  backend/app/controllers/
│  (FastAPI Router + 요청/응답 처리)  │
└──────────────┬──────────────────────┘
               │ 비즈니스 로직 위임
               ▼
┌─────────────────────────────────────┐
│          Service Layer              │  backend/app/services/
│  (도메인 로직, 트랜잭션, LLM 호출) │
└──────────────┬──────────────────────┘
               │ 데이터 접근
               ▼
┌─────────────────────────────────────┐
│        Repository Layer             │  backend/app/repositories/
│   (SQLAlchemy 쿼리, DB 추상화)      │
└──────────────┬──────────────────────┘
               │ SQL
               ▼
┌─────────────────────────────────────┐
│      PostgreSQL + pgvector          │
└─────────────────────────────────────┘
```

### 3.2 Controller → Service 매핑

| Controller | 경로 prefix | 연결 Service |
|------------|-------------|--------------|
| `auth_controller.py` | `/api/auth` | AuthService |
| `content_controller.py` | `/api/contents` | ContentService, FileService |
| `ai_chat_controller.py` | `/api/threads` | ThreadService, LangGraph Agent |
| `chat_controller.py` | `/api/chat` | AI Gateway (OpenAI SDK) |
| `search_controller.py` | `/api/search` | SearchService (SearXNG) |
| `scan_controller.py` | `/api/scan` | WpiService, ScanRepository |
| `admin_controller.py` | `/api/admin` | StateWatchdog, StateReconciler |
| `events_controller.py` | `/api/events` | StreamConsumer |
| `media_controller.py` | `/api/media` | MediaCacheService, FileService |
| `langfuse_controller.py` | `/api/admin/langfuse` | LangfuseDashboardService |
| `websocket_controller.py` | `/ws` | Valkey Pub/Sub |

### 3.3 FastAPI 앱 라이프사이클

```python
# backend/app/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()                    # DB 연결 초기화
    await stream_consumer.start()      # Valkey Stream 구독 시작
    await watchdog_scheduler.start()   # Watchdog 5분 주기 시작
    yield
    # Shutdown
    await stream_consumer.stop()
    await watchdog_scheduler.stop()
```

### 3.4 메시지 상태머신

```
queued → analyzing → searching → thinking → generating → completed
                                                        ↘ failed
```

각 상태 변환 시 SSE 이벤트 발행 → Frontend UI 업데이트.

### 3.5 주요 서비스 목록

| 서비스 | 파일 | 역할 |
|--------|------|------|
| ContentService | content_service.py | 콘텐츠 CRUD, 상태 관리, 재시도 |
| FileService | file_service.py | 파일 업로드/다운로드, S3 연동 |
| ThreadService | thread_service.py | AI 스레드/메시지 관리 |
| WpiService | wpi_service.py | WPI 채점, 프로필 생성, AI 보고서 |
| AIGatewayClient | ai_gateway_client.py | AI Gateway 통한 LLM 요청 (OpenAI SDK) |
| YoutubeService | youtube_service.py | YouTube URL 검증 및 메타데이터 |
| OcrService | ocr_service.py | OCR 처리 (MarkItDown, Tesseract) |
| TranscriptionPostprocess | transcription_postprocess.py | ASR 후처리, 화자 클러스터링 |
| StreamConsumer | stream_consumer.py | Valkey Stream 소비 |
| StateWatchdog | state_watchdog.py | 비정상 상태 감지 |
| StateReconciler | state_reconciler.py | 비정상 상태 자동 복구 |
| WatchdogScheduler | watchdog_scheduler.py | 5분 주기 Watchdog |
| MediaCacheService | media_cache_service.py | 미디어 로컬 캐시 |
| LangfuseDashboardService | langfuse_dashboard_service.py | Langfuse 메트릭 집계 |
| EventTrackingService | event_tracking_service.py | 사용자 행동 로깅 |

---

## 4. AI Agent System

### 4.1 LangGraph 그래프 구조

#### 기본 그래프 (`create_ai_graph`)

```
                    ┌─────────────────┐
                    │  intent_parser  │ ← 진입점
                    └────────┬────────┘
                             │ route_by_mode
              ┌──────────────┼──────────────────┐
              │              │                  │
         SIMPLE          SEARCH/HYBRID         RAG
              │              │                  │
              │         ┌────▼────┐        ┌────▼────┐
              │         │searcher │        │rag_ret  │
              │         └────┬────┘        └────┬────┘
              │              │                  │
              │    route_after_search            │
              │      ├─ (not hybrid) ───────────┤
              │      └─ (hybrid) → rag_retriever─┤
              │                                  │
        REASONING    ┌──────────────────────────┤
              │      │                          │
         ┌────▼────┐  │                   ┌─────▼────┐
         │reasoner │  │                   │generator │
         └────┬────┘  │                   └────┬─────┘
              │        │                        │
              └────────┴────────────────────────┘
                                │
                          (선택적) reflector
                                │
                               END
```

#### V8.4 재시도 그래프 (`create_ai_graph_with_retry`)

```
intent_parser
    ├─ SEARCH/HYBRID → searcher → search_evaluator
    │                                ├─ 품질 OK    → generator
    │                                ├─ 재시도 가능 → query_rewriter → searcher (루프, 최대 3회)
    │                                ├─ 한계+HYBRID → fallback_handler → rag_retriever
    │                                └─ 한계+SEARCH → fallback_handler → generator (내장지식)
    ├─ RAG          → rag_retriever → generator
    ├─ REASONING    → reasoner
    └─ SIMPLE       → generator
```

### 4.2 5가지 동작 모드 (AIMode)

```python
# backend/app/agents/state.py
class AIMode(str, Enum):
    SIMPLE    = "simple"    # 단순 대화 — 검색 없이 직접 응답
    SEARCH    = "search"    # 웹 검색 — SearXNG 기반 실시간 검색
    RAG       = "rag"       # 내부 RAG — 업로드된 콘텐츠 벡터 검색
    REASONING = "reasoning" # 추론 — Chain-of-Thought 단계별 분석
    HYBRID    = "hybrid"    # 하이브리드 — 웹 검색 + RAG 통합
```

| 모드 | 실행 경로 | 사용 사례 |
|------|-----------|----------|
| SIMPLE | Intent → Generator | 인사, 일반 질문, 잡담 |
| SEARCH | Intent → Searcher → Generator | 최신 뉴스, 트렌드, 실시간 정보 |
| RAG | Intent → RAGRetriever → Generator | 업로드 파일 기반 Q&A |
| REASONING | Intent → Reasoner | 복잡한 분석, 논리적 추론 |
| HYBRID | Intent → Searcher → RAGRetriever → Generator | 최신 정보 + 내부 문서 결합 |

### 4.3 9개 노드 상세

| # | 노드 | 파일 | 역할 |
|---|------|------|------|
| 1 | `intent_parser` | `nodes/intent_parser.py` | 쿼리 의도 분석, 모드/Tier 결정, QueryAnalysis 생성 |
| 2 | `searcher` | `nodes/searcher.py` | Multi-Query 웹 검색, RRF 리랭킹(K=60), 품질 평가 |
| 3 | `rag_retriever` | `nodes/rag_retriever.py` | 내부 문서 벡터 검색, content_id 필터링 |
| 4 | `reasoner` | `nodes/reasoner.py` | Chain-of-Thought 추론 (단계별 논리 분석) |
| 5 | `generator` | `nodes/generator.py` | 최종 응답 생성, Citation 처리, 대화 히스토리(최근 4턴) |
| 6 | `reflector` | `nodes/reflector.py` | 응답 품질 검증 (관련성/완전성/정확성/명확성, 임계 6.0) |
| 7 | `search_evaluator` | `nodes/search_evaluator.py` | 검색 결과 품질 평가 (V8.4: 개수/품질/관련성 3기준) |
| 8 | `query_rewriter` | `nodes/query_rewriter.py` | 쿼리 재작성 (broaden/narrow/synonym 전략, 최대 3회) |
| 9 | `fallback_handler` | `nodes/fallback_handler.py` | 검색 실패 시 대안 (내장 지식 또는 RAG 전환) |

### 4.4 6개 도구 상세

| # | 도구 | 파일 | 핵심 함수 |
|---|------|------|-----------|
| 1 | LLM 클라이언트 | `tools/llm_client.py` | `async_llm_completion()`, `async_llm_completion_stream()` |
| 2 | 웹 검색 | `tools/web_search.py` | `search_web()`, `fetch_url_content()` |
| 3 | RAG 검색 | `tools/rag_search.py` | `search_internal_content()`, `get_content_context()` |
| 4 | Tier 라우터 | `tools/model_router.py` | `TierRouter.route()` — 임베딩 기반 모델 선택 |
| 5 | 날짜 도구 | `tools/datetime_tool.py` | `get_current_datetime()` — 현재 날짜 주입 |
| 6 | 콘텐츠 컨텍스트 | `tools/content_context.py` | 콘텐츠 상세 채팅용 컨텍스트 로드 |

### 4.5 GraphState 스키마

```python
# backend/app/agents/state.py
class GraphState(TypedDict):
    # 대화
    messages: Annotated[list[BaseMessage], add_messages]
    query: str
    mode: AIMode
    selected_model: str | None
    intent_confidence: float
    requires_clarification: bool
    clarification_question: str | None

    # 쿼리 분석
    query_analysis: QueryAnalysis | None
    search_queries: list[str]

    # 검색 결과
    search_results: list[SearchResult]
    thinking_steps: list[ThinkingStep]
    sources: list[SearchResult]
    citations: list[CitationInfo]

    # 응답
    response: str
    error: str | None

    # 메타데이터
    thread_id: str | None
    metadata: dict
    content_context: str          # 콘텐츠 상세 채팅 시 요약/전사 직접 주입

    # V8.4 검색 재시도 필드
    search_retry_count: int       # 현재 재시도 횟수 (max 3)
    search_quality_score: float   # 품질 점수 (0-100)
    original_search_queries: list[str]
    failed_queries: list[str]
    needs_retry: bool
    retry_reason: str
```

### 4.6 Tier 라우팅

IntentParserNode의 TierRouter가 쿼리 복잡도에 따라 LLM Tier를 결정합니다.

| Tier | 모델 (v1.2.0) | 특징 |
|------|--------------|------|
| `tier-simple` | NPU Gemma 4 E4B → GPU Gemma 4 A4B fallback | 일반 대화, 번역 |
| `tier-thinking` | Codex → `gemma4-a4b` local fallback | 복잡한 추론, 분석 |
| `tier-recap` | `gemma4-a4b` (요약 전용 라우팅) | 문서 요약 |
| `codex-medium` | CLIProxy API (OpenAI Codex) | WPI 보고서, 코드 생성 (serverless 모드) |

### 4.7 검색 재시도 플로우 (V8.4)

```
SearchEvaluator 품질 평가 기준:
  ① 결과 개수:     MIN_RESULTS_HARD_GATE = 3
  ② 평균 품질:     MIN_AVG_QUALITY_100   = 55.0
  ③ 관련성 비율:   MIN_AVG_RELEVANCE_RATIO = 0.45
  ④ 총점:          MIN_TOTAL_SCORE       = 55.0
  ⑤ 콘텐츠 커버리지: MIN_CONTENT_COVERAGE_RATIO = 0.25

QueryRewriter 전략 (재시도 횟수별):
  1회차 → broaden:  더 넓은 범위, 일반적 용어
  2회차 → narrow:   더 구체적, 특정 용어
  3회차 → synonym:  동의어, 관련어 사용
```

---

## 5. WPI 심리검사 시스템

### 5.1 시스템 개요

WPI (Work Personality Inventory)는 사용자의 직업 성격 유형을 측정하는 심리검사 시스템입니다.

| 항목 | 내용 |
|------|------|
| 검사 유형 | I-Test (5유형) + ME-Test (5유형) |
| GAP 축 | 5개 축 분석 |
| AI 보고서 | LLM 기반 자동 생성 |
| 데이터 저장 | scan_result 테이블 (JSONB) |

### 5.2 검사 유형

**I-Test (내적 유형 — 5가지):**
- Realist (리얼리스트)
- Romanticist (로맨티스트)
- Humanist (휴머니스트)
- Idealist (아이디얼리스트)
- Agent (에이전트)

**ME-Test (대인 유형 — 5가지):**
- Relation (릴레이션)
- Trust (트러스트)
- Manual (매뉴얼)
- Self (셀프)
- Culture (컬처)

### 5.3 GAP 분석 축 (5개)

| 축 | 측정 대상 |
|----|----------|
| relation_recognition | 관계 인식 |
| emotion_trust | 정서 신뢰 |
| social_control | 사회성 통제 |
| independence_self | 독립성 자기 |
| achievement_culture | 성취 문화 |

### 5.4 데이터 플로우

```
1. GET /api/scan/wpi/questions      — 문항 로드 (I-Test + ME-Test)
        │
        ▼
2. POST /api/scan/wpi/submit        — 응답 제출
        │
        ▼ WpiService.score_responses()
3.  가중치 채점 (RANK_WEIGHTS = {1:7, 2:5, 3:3})
        │
        ▼ WpiService.analyze_gap()
4.  GAP 분석 (5개 축 점수 계산)
        │
        ▼ WpiService.create_profile()
5.  프로필 생성 (I-Type + ME-Type 결정)
        │
        ▼ ScanRepository.save()
6.  scan_result 테이블에 JSONB 저장
        │
        ▼
7. GET /api/scan/wpi/profile        — 프로필 조회
        │
8. POST /api/scan/history/{id}/ai-report/enqueue  — AI 보고서 생성 요청
        │
        ▼ WpiService.generate_report() + LiteLLM (codex-medium)
9.  AI 보고서 생성 후 DB 저장
        │
10. GET /api/scan/history/{id}/ai-report  — 보고서 조회
```

### 5.5 DB 스키마 (scan_result)

```sql
scan_result (
    id          UUID PK,
    user_id     UUID FK → user,
    scan_type   VARCHAR  -- "wpi", "wsi" 등
    status      VARCHAR  -- "completed", "in_progress"
    data        JSONB    -- {
                         --   i_type: "Realist",
                         --   me_type: "Trust",
                         --   gap_scores: {relation_recognition: 82, ...},
                         --   raw_responses: [...],
                         --   ai_report: "...",
                         --   version: 1
                         -- }
    created_at  TIMESTAMP,
    updated_at  TIMESTAMP
)
```

---

## 6. Content Pipeline

### 6.1 E2E 콘텐츠 처리 플로우

```
[사용자]
    │ POST /api/contents/upload (파일)
    │ POST /api/contents/upload-youtube (URL)
    ▼
[Backend: ContentController]
    │ FileService.upload_to_s3()
    │ ContentService.create()  → DB: file(QUEUED) + content
    ▼
[Celery Worker: asr/ocr queue]
    │ ASR Pipeline (오디오): WAV → ai-asr-vllm (Whisper-large-v3-turbo) → 전사 텍스트
    │ OCR Pipeline (문서): PDF/Image → MarkItDown/Tesseract + ai-llm (Gemma 4 vision) → 텍스트
    │ LLM Pipeline: 전사/OCR 텍스트 → ai-gateway → ai-llm (Gemma 4) → 요약 (JSONB)
    ▼
[Valkey Pub/Sub]
    │ PUBLISH events:file_progress:{file_id}
    ▼
[Backend: StreamConsumer]
    │ ContentService.update_status() → COMPLETED
    │ SSE 이벤트 발행 → Frontend
    ▼
[Frontend: useFileProgressSSE]
    └─ 파일 카드 상태 실시간 업데이트
```

### 6.2 파일 상태 머신

```
QUEUED
  ↓ 다운로드 시작 (YouTube)
PULLING
  ↓ 완료
PROCESSING (ASR 실행 중)
  ↓
OCR_PROCESSING (문서 OCR)
  ↓
SUMMARY_QUEUED
  ↓
SUMMARIZING
  ↓
COMPLETED
```

**오류 상태:**
- `DOWNLOAD_FAILED` — YouTube 다운로드 실패
- `ASR_FAILED` — 음성 인식 실패
- `OCR_FAILED` — OCR 처리 실패
- `SUMMARY_FAILED` — 요약 생성 실패
- `CANCELLED` — 사용자 취소

**재시도:** `POST /api/contents/{id}/retry?step=download|asr|summary|ocr`

### 6.3 콘텐츠 타입별 파이프라인

| 타입 | 업로드 형식 | ASR | OCR | LLM 요약 |
|------|------------|-----|-----|----------|
| AUDIO | WAV, MP3, M4A | ✅ Whisper | ❌ | ✅ |
| DOCUMENT | PDF, DOCX, XLSX, PNG, JPG | ❌ | ✅ MarkItDown/Tesseract | ✅ |
| PORTRAY | 이미지 (캡셔닝) | ❌ | ✅ (caption) | ✅ |

---

## 7. Progress Pipeline

### 7.1 E2E 진행률 파이프라인

```
[Worker]
    │ 처리 진행 시마다
    ▼
Valkey PUBLISH "events:file_progress:{file_id}"
    {"file_id": "...", "status": "PROCESSING", "progress": 45}
    │
    ▼
[Backend: StreamConsumer 구독]
    │ Valkey Subscribe
    ▼
[Backend: EventsController]
    GET /api/events/file-progress/stream  (SSE)
    │
    ▼
[Frontend: useFileProgressSSE]
    │ EventSource 수신
    └─ 진행률 바 / 상태 배지 업데이트
```

### 7.2 표시 모드

| 상태 | UI 표시 | 설명 |
|------|---------|------|
| QUEUED | 대기 중 배지 | 처리 대기 |
| PULLING / PROCESSING | 프로그레스 바 (%) | 처리 진행 중 |
| SUMMARY_QUEUED / SUMMARIZING | 요약 중 스피너 | LLM 요약 진행 |
| COMPLETED | 완료 배지 | 처리 완료 |
| *_FAILED | 오류 배지 + 재시도 버튼 | 처리 실패 |

---

## 8. AI Chat Workflow

### 8.1 스레드 생명주기

```
POST /api/threads  →  Thread 생성 (새 대화)
    │
    ▼
POST /api/threads/{id}/messages  →  Message 생성 (status: queued)
    │
    ▼
GET  /api/threads/{id}/messages/{msgId}/stream  →  SSE 구독
    │
    ├─ status: "analyzing"   → IntentParser 실행
    ├─ status: "searching"   → Searcher / RAGRetriever 실행
    ├─ status: "thinking"    → Reasoner 실행
    ├─ status: "generating"  → Generator 토큰 스트리밍
    └─ status: "completed"   → SSE 스트림 종료

PATCH /api/threads/{id}           → 스레드 제목/아카이브 수정
DELETE /api/threads/{id}          → 스레드 삭제
POST /api/threads/bulk-delete     → 다중 삭제
POST /api/threads/{id}/regenerate → 마지막 응답 재생성
```

### 8.2 메시지 상태 전환

```
queued → analyzing → searching → thinking → generating → completed
                                                          ↘ failed
```

`ai_message.status` 컬럼에 실시간 저장 + SSE 이벤트 발행.
2초마다 partial_content를 DB에 저장 (중단 복구 지원).

### 8.3 SSE 재연결 처리

```typescript
// frontend/src/shared/services/chatStreamService.ts
const eventSource = new EventSource(streamUrl)

eventSource.addEventListener('token', (e) => {
  chatStore.appendChunk(e.data)
})
eventSource.addEventListener('sources', (e) => {
  chatStore.setSources(JSON.parse(e.data))
})
eventSource.addEventListener('done', (e) => {
  chatStore.setStreaming(false)
  eventSource.close()
})
eventSource.onerror = () => {
  // 브라우저 자동 재연결 (3초 후)
  // cancel 요청 시: POST /cancel
}
```

### 8.4 콘텐츠 기반 채팅

`/contents/:id` 페이지에서 콘텐츠에 연결된 채팅:

```
ContentDetailPage
    └── ContentChatPanel
        │ content_id 포함하여 메시지 전송
        ▼
    Backend: content_context 주입
        └── GraphState.content_context = 해당 콘텐츠의 요약/전사 텍스트
            └── RAG 모드로 자동 라우팅
```

---

## 9. Database Schema

### 9.1 ER 다이어그램 (주요 관계)

```
user ─────┬──────────────── content ──── file
          │                    │
          │                    ├── transcription
          │                    └── document
          │
          ├──────────────── ai_thread ── ai_message
          │
          ├──────────────── scan_result
          │
          ├──────────────── user_event
          │
          └──────────────── client ────── counseling_session
                                               │
                                          speaker_profile
```

### 9.2 테이블 카탈로그 (14개)

#### file

```sql
file (
    id              UUID PK (v7),
    filename        VARCHAR NOT NULL,
    object_key      VARCHAR NOT NULL,    -- S3 키 (storage_key/filename)
    content_type    ENUM(AUDIO, DOCUMENT, PORTRAY),
    size_bytes      BIGINT,
    duration_sec    FLOAT,              -- 오디오 길이 (초)
    source_url      VARCHAR,            -- YouTube URL 등
    status          ENUM(QUEUED → COMPLETED | *_FAILED),
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)
```

#### content

```sql
content (
    id              UUID PK (v7),
    file_id         UUID FK → file,
    user_id         UUID FK → user,
    title           VARCHAR,
    summary_md      TEXT,               -- LLM 생성 요약 (Markdown)
    embedding       vector(768),        -- ai-embedding (EmbeddingGemma 300M)
    status          ENUM (file.status와 동기화),
    metadata        JSONB,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)
```

#### transcription

```sql
transcription (
    id              UUID PK (v7),
    content_id      UUID FK → content (UNIQUE),
    speakers        JSONB,              -- [{id, name, color}, ...]
    duration        FLOAT,
    transcription   JSONB,              -- [{speaker_id, start, end, text}, ...]
    created_at      TIMESTAMP
)
```

#### document

```sql
document (
    id              UUID PK (v7),
    content_id      UUID FK → content (UNIQUE),
    ocr_text        TEXT,               -- OCR 추출 전문
    page_count      INTEGER,
    html_content    TEXT,               -- MarkItDown HTML 결과
    created_at      TIMESTAMP
)
```

#### user

```sql
user (
    id              UUID PK (v7),
    email           VARCHAR UNIQUE,
    password        VARCHAR (bcrypt),
    storage_key     VARCHAR UNIQUE,     -- S3 개인 네임스페이스
    is_active       BOOLEAN,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)
```

#### ai_thread

```sql
ai_thread (
    id              UUID PK (v7),
    user_id         UUID FK → user,
    content_id      UUID FK → content (nullable),
    title           VARCHAR,
    is_archived     BOOLEAN,
    metadata        JSONB,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)
```

#### ai_message

```sql
ai_message (
    id              UUID PK (v7),
    thread_id       UUID FK → ai_thread,
    role            VARCHAR ('user' | 'assistant'),
    content         TEXT,
    partial_content TEXT,               -- 스트리밍 중 2초마다 저장
    status          VARCHAR ('queued' | 'analyzing' | 'searching' |
                             'thinking' | 'generating' | 'completed' | 'failed'),
    sources         JSONB,              -- [{title, url, snippet}, ...]
    citations       JSONB,              -- [{id, title, url, verified}, ...]
    metadata        JSONB,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)
```

#### scan_result

```sql
scan_result (
    id              UUID PK (v7),
    user_id         UUID FK → user,
    scan_type       VARCHAR,            -- 'wpi', 'wsi' 등
    status          VARCHAR,            -- 'completed', 'in_progress'
    data            JSONB,              -- 검사별 결과 구조
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)
```

#### stt_log

```sql
stt_log (
    id              UUID PK (v7),
    content_id      UUID FK → content,
    log_data        JSONB,              -- ASR/OCR 처리 로그
    created_at      TIMESTAMP
)
```

#### llm_log

```sql
llm_log (
    id              UUID PK (v7),
    content_id      UUID FK → content,
    model           VARCHAR,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    log_data        JSONB,
    created_at      TIMESTAMP
)
```

#### user_event

```sql
user_event (
    id              UUID PK (v7),
    user_id         UUID FK → user,
    event_type      VARCHAR,            -- 'upload', 'chat', 'search' 등
    content_id      UUID FK → content (nullable),
    metadata        JSONB,
    created_at      TIMESTAMP
)
```

#### speaker_profile

```sql
speaker_profile (
    id              UUID PK (v7),
    speaker_type    VARCHAR,
    name            VARCHAR,
    voice_embedding vector(512),        -- Pyannote 임베딩
    metadata        JSONB,
    created_at      TIMESTAMP
)
```

#### client

```sql
client (
    id              UUID PK (v7),
    name            VARCHAR,
    latest_profile  JSONB,
    profile_embedding vector(768),
    metadata        JSONB,
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
)
```

#### counseling_session

```sql
counseling_session (
    id                  UUID PK (v7),
    client_id           UUID FK → client,
    counselor_profile_id UUID FK → speaker_profile,
    session_number      INTEGER,
    analysis            JSONB,
    self_expressions    JSONB,
    created_at          TIMESTAMP
)
```

### 9.3 벡터 컬럼 정리

| 테이블 | 컬럼 | 차원 | 모델 |
|--------|------|------|------|
| content | embedding | 768D | ai-embedding (EmbeddingGemma 300M) |
| speaker_profile | voice_embedding | 512D | pyannote (ai-diarize) |
| client | profile_embedding | 768D | ai-embedding (EmbeddingGemma 300M) |

---

## 10. API Reference

### 10.1 인증 (`/api/auth`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/check-id` | 로그인 ID 중복 확인 |
| POST | `/signup` | 회원가입 |
| POST | `/login` | 이메일/비밀번호 로그인 |
| POST | `/refresh` | JWT 액세스 토큰 갱신 |
| POST | `/logout` | 단일 기기 로그아웃 |
| POST | `/logout-all` | 모든 기기 로그아웃 |
| GET | `/me` | 현재 사용자 프로필 |

### 10.2 콘텐츠 (`/api/contents`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 콘텐츠 목록 (페이지네이션, 검색) |
| GET | `/{content_id}` | 콘텐츠 상세 |
| POST | `/upload` | 파일 업로드 |
| POST | `/upload-youtube` | YouTube URL 다운로드 (비동기) |
| DELETE | `/queued` | 대기 중 콘텐츠 전체 삭제 |
| POST | `/bulk-delete` | 다중 콘텐츠 삭제 |
| POST | `/{content_id}/retry` | 실패 단계 재처리 |
| POST | `/{content_id}/recluster-speakers` | 화자 재클러스터링 |

### 10.3 AI 채팅 (`/api/threads`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 스레드 목록 |
| POST | `/` | 새 스레드 생성 |
| GET | `/{thread_id}` | 스레드 상세 |
| PATCH | `/{thread_id}` | 스레드 수정 (제목/아카이브) |
| PATCH | `/{thread_id}/metadata` | 메타데이터 수정 |
| DELETE | `/{thread_id}` | 스레드 삭제 |
| POST | `/bulk-delete` | 다중 스레드 삭제 |
| POST | `/{thread_id}/messages` | 메시지 전송 |
| GET | `/{thread_id}/messages/{message_id}/stream` | SSE 스트리밍 응답 |
| POST | `/{thread_id}/messages/{message_id}/cancel` | 스트리밍 취소 |
| POST | `/{thread_id}/regenerate` | 마지막 응답 재생성 |

### 10.4 검색 (`/api/search`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/search` | 심층 검색 (SSE 스트리밍) |
| GET | `/search` | GET 방식 검색 |

**SSE 이벤트:** `status`, `sources`, `token`, `error`, `done`

### 10.5 WPI 심리검사 (`/api/scan`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/history` | 검사 이력 목록 |
| GET | `/history/{result_id}` | 검사 결과 상세 |
| GET | `/history/{result_id}/ai-report` | AI 생성 보고서 |
| POST | `/history/{result_id}/ai-report/enqueue` | AI 보고서 생성 요청 |
| GET | `/wpi/questions` | WPI 문항 로드 |
| POST | `/wpi/submit` | WPI 응답 제출 |
| GET | `/wpi/profile` | 최신 WPI 프로필 |
| GET | `/wpi/status` | WPI 완료 여부 |
| DELETE | `/wpi/in-progress` | 진행 중 WPI 삭제 |

### 10.6 이벤트 (`/api/events`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/events/file-progress/stream` | 파일 진행률 SSE 스트림 |

### 10.7 미디어 (`/api/media`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/{content_id}` | 미디어 스트리밍 (인증, Range 지원) |

### 10.8 관리자 (`/api/admin`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/watchdog/scan` | 비정상 파일 상태 스캔 |
| POST | `/watchdog/reconcile` | 비정상 상태 자동 복구 (`?dry_run=true`) |
| GET | `/admin/langfuse/overview` | LLM 관측성 요약 |
| GET | `/admin/langfuse/traces` | LLM 트레이스 목록 |
| GET | `/admin/langfuse/traces/{trace_id}` | 트레이스 상세 |
| GET | `/admin/langfuse/sessions/{session_id}` | 세션 타임라인 |

### 10.9 WebSocket (`/ws`)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| WS | `/test-ws` | WebSocket 테스트 |
| WS | `/file-progress-simple/{file_id}` | 파일 진행률 (레거시) |
| WS | `/file-progress/global` | 전역 파일 진행률 (Valkey Pub/Sub) |

---

## 11. Worker Architecture

### 11.1 Celery 설정

```python
# worker/celery_app.py
app = Celery('asr-worker')
app.config_from_object('worker.config')

# 3개 독립 큐
QUEUES = ['asr', 'ocr', 'llm']
BROKER_URL = "redis://asr-valkey:6379/0"
RESULT_BACKEND = "redis://asr-valkey:6379/1"
```

**실행 명령:**
```bash
# ASR 워커
celery -A worker.celery_app worker --queues=asr --hostname=worker-asr@%h

# OCR 워커
celery -A worker.celery_app worker --queues=ocr --hostname=worker-ocr@%h

# LLM 워커
celery -A worker.celery_app worker --queues=llm --hostname=worker-llm@%h
```

### 11.2 Worker 디렉토리 구조

```
worker/
├── celery_app.py           # Celery 앱 인스턴스 + 큐 설정
├── config.py              # 런타임 설정 (Broker, Timeout, Retry)
├── constants.py           # 상수 (QUEUE_NAME, STATUS 등)
├── logging_config.py      # 로깅 설정
├── telemetry.py           # OpenTelemetry 분산 추적
│
├── pipelines/             # ai-gateway 호출 파이프라인 (로컬 추론은 컨테이너에서)
│   ├── asr/
│   │   ├── __init__.py             # ASR 실행 진입점
│   │   ├── ai_gateway_audio_client.py  # ai-gateway → ai-asr-vllm/ai-diarize
│   │   └── vad_utils.py            # Voice Activity Detection
│   ├── ocr/
│   │   └── __init__.py    # OCR 실행 진입점 (ai-gateway → ai-llm vision)
│   ├── llm/
│   │   ├── __init__.py             # LLM 실행 진입점
│   │   └── ai_gateway_client.py    # ai-gateway → ai-llm
│   └── search/
│       └── __init__.py    # 검색 파이프라인
│
├── processors/            # Task ↔ Pipeline 브릿지
│   └── [processor_files]  # Task에서 Pipeline 호출 래퍼
│
└── utils/                 # 공유 유틸리티
    ├── event_loop.py      # 이벤트 루프 관리
    ├── task_queue.py      # 큐 관리 헬퍼
    ├── semaphore.py       # 병렬 처리 제한 (GPU 동시성)
    └── postprocess.py     # 후처리 (패딩 제거 등)
```

### 11.3 Task → Processor → Pipeline 흐름

```
Celery Task (큐에서 수신)
    │ processors/
    ▼
Processor (Task ↔ Pipeline 브릿지)
    │ S3에서 파일 다운로드
    │ 파이프라인 호출
    │ 결과 후처리
    │ Backend API로 결과 전송
    │ Valkey Pub/Sub 진행률 발행
    ▼
Pipeline (ai-gateway 호출, 실제 추론은 ai-* GPU/CPU 컨테이너에서)
    │ pipelines/asr/ → ai-gateway → ai-asr-vllm (Whisper) / ai-diarize (pyannote)
    │ pipelines/ocr/ → ai-gateway → ai-llm (Gemma 4 vision)
    └ pipelines/llm/ → ai-gateway → ai-llm (vLLM Gemma 4)
```

### 11.4 큐 분리 전략

| 큐 | 용도 | 하드웨어 | 특징 |
|----|------|---------|------|
| `asr` | 음성→텍스트 | GPU (CUDA) | 긴 오디오 처리 |
| `ocr` | 이미지→텍스트 | GPU/CPU | 페이지 단위 병렬 |
| `llm` | 텍스트 추론 | GPU (CUDA) | VRAM 집약적 |

---

## 12. AI Gateway & Inference Containers

### 12.1 호출 구조 (v1.2.0)

```
Backend / Worker / Frontend
    │ HTTP POST /v1/chat/completions  (OpenAI 호환)
    │ HTTP POST /v1/embeddings
    │ HTTP POST /v1/audio/transcriptions
    │ HTTP POST /v1/ocr  (커스텀 엔드포인트)
    ▼
ai-gateway (FastAPI :4000)
    │ infra/ai-gateway/services/routing.py
    │   - tier_config.py로 tier→model 매핑
    │   - DEPLOY_MODE=local-gpu | serverless 분기
    │   - serverless 모드: Codex(CLIProxy) / RunPod 폴백
    ▼ httpx.AsyncClient
┌──────────────────┬──────────────────┬──────────────────┐
ai-llm :8000       ai-asr-vllm :8000  ai-diarize :8003
(vLLM Gemma 4)     (vLLM Whisper)      (pyannote comm-1)

ai-embedding :8000(internal)
(EmbeddingGemma 308M GGUF)
```

> v1.1.0의 **LiteLLM 프록시 + Provider Manager(Redis Stream + Host PM2)**는 v1.2.0에서 제거.
> ai-gateway가 단일 프로세스로 라우팅·모델 매핑·serverless 폴백을 직접 수행.

### 12.2 ai-gateway 모듈 구조

```
infra/ai-gateway/
├── main.py                  — FastAPI app + lifespan
├── config.py                — *_BASE_URL · *_REQUEST_TIMEOUT · DEPLOY_MODE
├── routes/
│   ├── chat.py              — /v1/chat/completions (스트리밍 + non-stream)
│   ├── embeddings.py        — /v1/embeddings
│   └── media.py             — /v1/audio/transcriptions, /v1/ocr 등
├── services/
│   └── routing.py           — tier 매핑 + serverless 폴백 (slim, ~60 lines)
├── core/
│   └── redis.py             — Valkey 동시성 세마포어 (옵션)
├── middleware/
│   └── telemetry.py         — OTel trace context 전파
└── utils/
    └── response.py          — OpenAI 응답 정규화
```

### 12.3 모델 라우팅 (tier 기반)

`infra/shared/tier_config.py`의 `TIER_MODEL_MAP`이 tier 이름을 실제 모델로 매핑.

| Tier | Primary | Fallback | 용도 |
|------|---------|----------|------|
| `tier-simple` | NPU Gemma 4 E4B | `gemma4-a4b` | 짧은 응답, 분류, 번역 |
| `tier-standard` | `gemma4-a4b` (ai-llm) | codex-medium | 일반 채팅, RAG |
| `tier-thinking` | Codex medium | `gemma4-a4b` (ai-llm) | 추론, WPI 보고서 |
| `embedding` | `embeddinggemma-300m` (ai-embedding) | bge-small-en-v1.5 (RunPod) | 벡터화 |
| `asr` | Whisper-large-v3-turbo (ai-asr-vllm) | — | 음성 전사 |
| `diarization` | pyannote community-1 (ai-diarize) | — | 화자 분리 |
| `ocr` | `gemma4-a4b` vision (ai-llm) | — | 이미지 OCR + 표 |

라우팅 정책: `DEPLOY_MODE=local-gpu`(기본)에서 `tier-simple`은 NPU Gemma 4 E4B를
우선 호출하고 연결/API 실패 시 GPU Gemma 4 A4B로 fallback한다. 나머지 텍스트 tier와
OCR은 GPU Gemma 4 A4B를 사용한다. 현재 라우팅은 부하 기반이 아니라 요청 tier 기반이다.

> `ai-embedding`은 벡터 생성 전용이다. 별도 cross-encoder 리랭커 서비스는 없으며,
> 웹 검색은 RRF·키워드 관련성·품질/본문 점수로, 문서 RAG는 키워드 검색과
> pgvector 유사도를 RRF로 병합해 재정렬한다. 임베딩 호출이 실패하면 키워드 검색으로 fallback한다.

### 12.4 추론 컨테이너 사양

| 컨테이너 | 이미지/베이스 | GPU 사용 | 모델 | 컨텍스트 |
|---------|--------------|---------|------|---------|
| `ai-llm` | `vllm/vllm-openai-rocm:v0.26.0` | gfx1150 | Gemma 4 26B A4B AWQ INT4 + MTP k=4 | 32768 |
| `ai-asr-vllm` | `ai-llm-gemma4:0.26.0` (공유 vLLM ROCm 빌드) | gfx1150 | Whisper-large-v3-turbo | 448 |
| `ai-diarize` | `pyannote-audio` (custom ROCm build) | gfx1150 | community-1 | — |
| `ai-embedding` | `llama.cpp` (CPU) | CPU | EmbeddingGemma 300M Q4 GGUF | 2048 |

**공유 자원:**
- Docker named volume `hf-cache-fast` — HuggingFace 모델 캐시 공유
- Docker named volume `vllm-compile-cache` — ASR vLLM torch.compile 캐시 보존
- Docker named volume `tvm-ffi-cache` — vLLM 0.26 ROCm 보조 모듈 cold compile 캐시
- WSL 추론 컨테이너에 `/dev/dxg`와 `libdxcore.so` 마운트

`ai-asr-vllm`은 WSL UVA 제약 때문에 V1 runner를 사용한다. 운영값은 고정 KV 512 MiB,
`max-num-batched-tokens=1536`, `max-num-seqs=1`이며 Pyannote와 병행한다.

### 12.5 ai-llm 서빙 체크리스트

- Hugging Face에서 target/assistant 모델 사용 조건에 동의하고 `.env`에 `HF_TOKEN`을 설정한다. 이미 캐시된 호스트에서도 clean deploy를 위해 유지한다.
- WSL 메모리는 32 GiB로 제한한다. `ai-llm`은 A4B target+assistant 약 17.37 GiB와 고정 3 GiB KV cache를 사용하며, `gpu_memory_utilization`은 사용하지 않는다.
- 운영 설정은 `max-model-len=32768`, `max-num-seqs=4`, `max-num-batched-tokens=1024`, image=1, video/audio=0이다. 멀티모달은 이미지 OCR만 활성화한다.
- A4B 부하 실측 peak는 29.436 GiB, ASR·pyannote 공존 high-water는 30.195 GiB였고 swap은 증가하지 않았다. cold start는 약 10~12분이며 첫 요청은 ROCm Triton JIT로 느릴 수 있다.
- 공유 iGPU에서 Whisper와 pyannote 동시 실행은 경합으로 약 5배 느렸으므로 worker는 같은 GPU lock 안에서 ASR 후 화자분리를 직렬 실행한다.
- pyannote는 startup에서 패키지에 포함된 30초 음성 fixture를 warm-up하며, 각 downstream entrypoint는 upstream 장애를 계속 감시해 A4B-first 순서로 자동 재대기한다.
- 실제 A4B 자식 프로세스 장애 시험에서 하위 서비스가 먼저 종료된 뒤 전체 체인이 약 14분 21초에 복구됐고, peak 30.172 GiB와 swap 0을 기록했다. 복구 후 `gemma4-a4b` 실제 completion도 통과했다.
- 로그의 `TRITON_ATTN`/Triton JIT는 vLLM 내부 ROCm 커널이다. 별도 NVIDIA Triton Inference Server는 PoC 후 채택하지 않았으며 운영 서비스에 없다.

```bash
# This host uses the local ROCr WSL poll-fix images. Keep both files on every
# recreate; the override fails closed if a local patched image is missing.
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.rocr-wsl-pollfix.yml"

# Build only services that do not use the local patched ROCr images.
docker compose $COMPOSE_FILES build ai-embedding ai-gateway backend worker

# 32 GiB 공유 UMA에서 교체 중 순간 OOM 방지
docker compose $COMPOSE_FILES stop ai-gateway backend worker
docker compose $COMPOSE_FILES stop ai-llm ai-asr-vllm ai-diarize ai-embedding
docker compose $COMPOSE_FILES up -d --no-deps --no-build ai-llm

# aux entrypoint가 ai-llm → ASR → pyannote → embedding 순서를 강제한다.
docker compose $COMPOSE_FILES up -d --no-deps --no-build ai-asr-vllm ai-diarize ai-embedding

# gateway를 닫은 채 full-stack health / OCR peak·swap을 확인한다.
curl -f http://localhost:18000/v1/models
python -X utf8 infra/inference/bench/ocr_smoke.py /path/to/image.jpg

# OCR 검증 통과 후 요청 생산자를 마지막에 재개한다.
docker compose $COMPOSE_FILES up -d --no-deps ai-gateway backend worker
```

배포 후 `docker compose ps`와 `docker logs ai-llm`에서 모든 추론 서비스의 health,
OOM/restart, `SpecDecoding metrics`를 확인한다. 현재 기본 `docker-compose.yml`
단독 실행은 stock ROCr 롤백 경로다. 롤백 시 GPU 서비스를 중지한 뒤 base
Compose의 stock LLM/ASR 이미지를 빌드하고 diarize 이미지를 별도로 빌드해 같은
LLM-first 순서로 기동한다.
32 GiB에서 Gemma와 나머지 GPU 서비스의 동시 상주는 별도 soak 검증 전까지
순차 기동을 원칙으로 한다.

### 12.6 호출 예시 (chat completion)

```
client → ai-gateway POST /v1/chat/completions {model: "tier-thinking", messages: [...]}
   │
   ▼ routes/chat.py: Codex medium primary
   │                  └─ API 실패 시 model="gemma4-a4b", base=ai-llm
   ▼ SSE 스트림 → ai-gateway가 chunk 단위 relay
   │
client ← ai-gateway SSE 스트림
```

> serverless 폴백은 `_handle_serverless()` 함수에서 Codex(`CLIProxy`)로 redirect.

---

## 13. Valkey Data Architecture

> v1.1.0 시기에는 추론 요청도 Valkey Stream(`stream:chat:requests` / `stream:media:requests` / `stream:gpu:responses` 등)으로 Provider Manager에 라우팅했지만, v1.2.0에서 ai-gateway가 추론 컨테이너를 httpx로 직접 호출하면서 **Stream은 추론 경로에서 제거됨**. 현재 Valkey 사용처는 **Pub/Sub 이벤트 + 캐시 + 동시성 세마포어** 3가지뿐.

### 13.1 Pub/Sub 채널

| 채널 패턴 | 용도 | 발행자 | 구독자 |
|-----------|------|--------|--------|
| `events:file_progress:{file_id}` | 파일 처리 진행률 | Worker | Backend SSE |
| `events:asr_stream:{file_id}` | 실시간 ASR 텍스트 | Worker | Backend WebSocket |
| `events:llm_stream:{file_id}` | 실시간 LLM 토큰 | Worker | Backend WebSocket |
| `events:content_created` | 콘텐츠 생성 완료 | Worker | Backend |
| `events:file_progress:global` | 전역 파일 진행률 | Worker | Backend WebSocket |

### 13.2 Cache Key 패턴 & TTL

| 키 패턴 | TTL | 용도 |
|---------|-----|------|
| `cache:search:{hash}` | 3,600s (1시간) | 웹 검색 결과 캐시 |
| `history:{session_id}` | 604,800s (7일) | 대화 히스토리 |
| `job:{job_id}` | 86,400s (24시간) | 작업 임시 데이터 |
| `worker:{device}:active` | 3,600s (1시간) | GPU 동시성 세마포어 |

### 13.3 메모리 정책

```yaml
maxmemory: 2gb
maxmemory-policy: volatile-lru    # TTL 있는 키부터 LRU 삭제
```

### 13.4 데이터 플로우 요약 (v1.2.0)

```
Backend ── HTTP ──► ai-gateway ── HTTP ──► ai-llm / ai-asr-vllm / ai-diarize / ai-embedding
                                              (vLLM / vLLM / pyannote / llama.cpp)

Worker ── PUBLISH events:* ──► Valkey ──► SUBSCRIBE events:* ──► Backend SSE/WebSocket
Backend ── SET cache:search ──► Valkey ──► GET cache:search
Backend / Worker ── SET worker:gpu:active ──► Valkey (TTL-based 세마포어)
```

---

## 14. Observability Stack

### 14.1 서비스별 역할

| 서비스 | 포트 | 역할 | 데이터 소스 |
|--------|------|------|------------|
| **Prometheus** | 9090 | 메트릭 수집 (Pull 방식) | Backend `/metrics`, Worker |
| **Grafana** | 3002 | 대시보드 & 알림 | Prometheus, Loki, Tempo |
| **Loki** | 3100 | 로그 집계 | Promtail 수집 |
| **Promtail** | (agent) | Docker 로그 수집 → Loki | /var/lib/docker/containers |
| **Tempo** | 3200, 4317 | 분산 추적 (OTLP) | Backend, Worker, ai-gateway |
| **Langfuse** | 3001 | LLM 관측성 (트레이스, 비용) | Backend LangGraph 콜백 |
| **Flower** | 5555 | Celery 작업 모니터링 | Celery 브로커 |

### 14.2 추적 흐름

```
HTTP Request (Trace ID 생성)
    │
    ├── Backend (FastAPI + OpenTelemetry) → Tempo :4317
    │       └── LangGraph 노드별 Span
    │
    ├── Worker (Celery + OpenTelemetry) → Tempo :4317
    │
    └── ai-gateway → Tempo :4317
            └── 추론 컨테이너(ai-llm/ai-asr-vllm/...) 호출 Span

Langfuse (LLM 전용):
    LangGraph Generator Node → Langfuse 콜백 → Trace 저장
    → Backend API /api/admin/langfuse/traces 조회
```

### 14.3 메트릭 수집

```python
# backend/app/main.py
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

---

## 15. Key File Map

### 15.1 Frontend 핵심 파일

| 역할 | 경로 |
|------|------|
| 라우팅 정의 | `frontend/src/routes/index.tsx` |
| 채팅 상태 관리 | `frontend/src/shared/stores/chatStore.ts` |
| SSE 스트리밍 서비스 | `frontend/src/shared/services/chatStreamService.ts` |
| HTTP 클라이언트 | `frontend/src/shared/services/api/httpClient.ts` |
| 채팅 영역 컴포넌트 | `frontend/src/features/chat/components/ChatArea.tsx` |
| 채팅 페이지 | `frontend/src/pages/ChatPage.tsx` |
| 앱 레이아웃 | `frontend/src/shared/components/layout/RootLayout.tsx` |
| 인증 컨텍스트 | `frontend/src/shared/contexts/AuthContext.tsx` |

### 15.2 Backend 핵심 파일

| 역할 | 경로 |
|------|------|
| FastAPI 앱 진입점 | `backend/app/main.py` |
| DB 모델 | `backend/app/db/models.py` |
| AI 채팅 컨트롤러 | `backend/app/controllers/ai_chat_controller.py` |
| LangGraph 그래프 | `backend/app/agents/graph.py` |
| GraphState | `backend/app/agents/state.py` |
| WPI 서비스 | `backend/app/services/wpi_service.py` |
| ai-gateway 클라이언트 | `backend/app/services/litellm_client.py` (히스토리 명칭, ai-gateway HTTP 호출) |
| Valkey Stream 소비 | `backend/app/services/stream_consumer.py` (Pub/Sub 이벤트용) |

### 15.3 AI Agent 핵심 파일

| 역할 | 경로 |
|------|------|
| IntentParserNode | `backend/app/agents/nodes/intent_parser.py` |
| SearcherNode | `backend/app/agents/nodes/searcher.py` |
| RAGRetrieverNode | `backend/app/agents/nodes/rag_retriever.py` |
| ReasonerNode | `backend/app/agents/nodes/reasoner.py` |
| GeneratorNode | `backend/app/agents/nodes/generator.py` |
| ReflectorNode | `backend/app/agents/nodes/reflector.py` |
| SearchEvaluatorNode | `backend/app/agents/nodes/search_evaluator.py` |
| QueryRewriterNode | `backend/app/agents/nodes/query_rewriter.py` |
| FallbackHandlerNode | `backend/app/agents/nodes/fallback_handler.py` |
| LLM 도구 | `backend/app/agents/tools/llm_client.py` |
| 웹 검색 도구 | `backend/app/agents/tools/web_search.py` |
| RAG 검색 도구 | `backend/app/agents/tools/rag_search.py` |
| Tier 라우터 도구 | `backend/app/agents/tools/model_router.py` |

### 15.4 인프라 핵심 파일 (v1.2.0)

| 역할 | 경로 |
|------|------|
| Docker 서비스 정의 | `docker-compose.yml` |
| ai-gateway FastAPI 앱 | `infra/ai-gateway/main.py` |
| ai-gateway 설정 | `infra/ai-gateway/config.py` |
| 라우팅·tier 매핑 | `infra/ai-gateway/services/routing.py` |
| Chat 엔드포인트 | `infra/ai-gateway/routes/chat.py` |
| Embeddings 엔드포인트 | `infra/ai-gateway/routes/embeddings.py` |
| Media (ASR/OCR) 엔드포인트 | `infra/ai-gateway/routes/media.py` |
| Tier ↔ 모델 매핑 | `infra/shared/tier_config.py` |
| ai-llm (vLLM) | `infra/inference/llm/` (Dockerfile + WSL shims/MTP patch) |
| ai-asr-vllm (Whisper) | `docker-compose.yml`의 vLLM 서비스 정의 |
| ai-diarize (pyannote) | `infra/inference/diarize/` |
| ai-embedding (EmbeddingGemma) | `infra/inference/embedding/` |
| Codex(CLIProxy) 폴백 | `infra/cliproxy/` |
| Celery 앱 | `worker/celery_app.py` |
| ASR 파이프라인 | `worker/pipelines/asr/__init__.py` |
| OCR 파이프라인 | `worker/pipelines/ocr/__init__.py` |
| LLM 파이프라인 | `worker/pipelines/llm/__init__.py` |

---

## 16. Appendix

### 16.1 관련 문서

| 문서 | 경로 | 내용 |
|------|------|------|
| Valkey 아키텍처 | `docs/valkey-v1.0.md` | Redis Stream/Pub-Sub 상세 |
| LangGraph 가이드 | `docs/langgraph-v1.0.0.md` | LangGraph 패턴 |
| 검색 품질 튜닝 | `docs/search-quality-tuning.md` | 검색 파라미터 가이드 |
| 검색 재시도 아키텍처 | `docs/search-retry-architecture.md` | V8.4 재시도 상세 |
| Observability | `docs/observability-v1.0.md` | 모니터링 스택 |
| AI Chat 워크플로우 | `docs/workflow-ai-chat.md` | 채팅 플로우 상세 |
| WPI 관련 | `docs/wpi/` | WPI 문서 모음 |
| Agent 가이드 | `docs/AGENTS.md` | AI Agent 개발 가이드 |

### 16.2 아카이브 히스토리

| 파일 | 비고 |
|------|------|
| `docs/archived/architecture-v1.1.0.md` | 2026-02-27 작성, v1.2.0으로 대체 (Provider Manager + LiteLLM 시기) |
| `docs/archived/architecture-v1.0.1.md` | 2026-02-21 작성, v1.0.0 incremental 갱신 (lemonade-server / LLMTier / model_copy 정리) |
| `docs/archived/architecture-v1.0.0.md` | 2026-02-21 작성, v1.1.0으로 대체 |
| `docs/archived/architecture-v8.5.md` | 이전 V8 계열 아키텍처 |
| `docs/archived/architecture-v8.2.md` | V8.2 (LangGraph 도입) |
| `docs/archived/architecture-v7.*.md` | V7 계열 |
| `docs/archived/architecture_v4.md` ~ `v6.*` | 초기 아키텍처 |

### 16.3 용어 정리

| 용어 | 설명 |
|------|------|
| FastFlowLM | Windows NPU LLM 서버 — `tier-simple` 우선 경로, 실패 시 GPU fallback |
| ai-gateway | 추론 라우터 컨테이너 — LiteLLM/Provider Manager의 후속 (v1.2.0~) |
| RRF | Reciprocal Rank Fusion — 다중 검색 결과 리랭킹 알고리즘 |
| pgvector | PostgreSQL 벡터 확장 — 임베딩 유사도 검색 |
| VAD | Voice Activity Detection — 음성 감지 (침묵 제거) |
| Diarization | 화자 분리 — 누가 말했는지 구분 |
| Tier | LLM 성능 티어 — simple(빠름) / thinking(정확) |
| GraphState | LangGraph 상태 객체 — 노드 간 데이터 공유 |
| SSE | Server-Sent Events — 서버→클라이언트 단방향 실시간 스트림 |
| Watchdog | 비정상 파일 상태 감지 및 자동 복구 스케줄러 |
