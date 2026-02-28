# Architecture v1.1.0

> **Timblo AI Platform — 종합 아키텍처 문서**
>
> 작성일: 2026-02-27 | 버전: v1.1.0 | 대상 코드베이스: `develop` 브랜치

---

## 버전 히스토리

| 버전 | 날짜 | 주요 변경 |
|------|------|-----------|
| v1.0.0 | 2026-02-21 | Worker-Backend 분리, 초기 종합 문서 |
| **v1.1.0** | 2026-02-27 | Frontend 상세, Backend 레이어, AI Agent 전체, WPI, DB 스키마, API 레퍼런스, Valkey 구조 추가 |

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
12. [LiteLLM & Provider Manager](#12-litellm--provider-manager)
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
  Docker Environment     │
┌────────────────────────▼────────────────────────────────────────────┐
│  APPLICATION                                                        │
│  Backend (FastAPI) — Controllers → Services → Repositories          │
│  LangGraph AI Agent — 9 Nodes · 6 Tools · 5 Modes                  │
└──────────┬──────────────────────────────────┬───────────────────────┘
           │ Celery                            │ OpenAI SDK
┌──────────▼──────────┐           ┌────────────▼──────────────────────┐
│  WORKER             │           │  LLM PROXY                        │
│  Celery Workers     │──── HTTP ─→  LiteLLM + custom_handler         │
│  파일 전처리        │           │  GPU Stream Client                 │
│  (FFmpeg/PDF 변환)  │           │                                   │
└─────────────────────┘           └────────────┬──────────────────────┘
                                               │ Redis Stream
┌──────────────────────────────────────────────┼──────────────────────┐
│  DATA                                        │                      │
│  PostgreSQL+pgvector   Valkey(Redis) ◄────────┘                     │
│  MinIO (S3)            SearXNG                                      │
└────────────────────────────┬────────────────────────────────────────┘
═════════════════════════════╪════════════════════════════════════════
  Host Environment           │ Redis Stream
┌────────────────────────────▼────────────────────────────────────────┐
│  PROVIDER MANAGER                                                   │
│  Stream Processor · Provider Lifecycle · Job Tracker                │
├─────────────────────────────────────────────────────────────────────┤
│  GPU Servers                │  NPU Servers                          │
│  llama-server · whisper-cpp │  FLM (ASR · LLM · OCR · Thinking)    │
│  insanely-fast · diarize    │                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Docker 서비스 토폴로지

```
docker-compose.yml (Host Network Mode)
│
├── nginx          :80/:443    — Reverse Proxy (SSL Termination)
├── frontend       :3000       — Vite + React SPA
├── backend        :8000       — FastAPI Application
├── worker         (no port)   — Celery Workers (3 queues)
│
├── litellm        :4000       — LLM Proxy (multi-provider routing)
├── cli-proxy-api  :8317/:1455 — OAuth CLI Proxy (codex 접근)
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
- `backend` → postgres(healthy), valkey(healthy), minio(healthy), litellm(healthy)
- `worker` → valkey(healthy), postgres(healthy), tempo(started)
- `langfuse` → postgres(healthy)

### 1.3 Host 토폴로지 (Provider Manager)

Provider Manager는 **Docker 외부 Host**에서 직접 실행되어 GPU/NPU 하드웨어에 접근합니다.

```
Host Machine (Windows/Linux)
│
├── Provider Manager (Python Process)
│   ├── StreamProcessor  — Valkey Stream Consumer
│   ├── ProviderManager  — GPU/NPU 프로세스 관리
│   ├── JobTracker       — 작업 추적
│   └── IdleManager      — 자동 언로드
│
├── FLM Server  :11434-11436   (NPU — ollama-compatible)
├── llama-server :8080-8081    (GPU — llama.cpp HTTP)
├── whisper-cpp  :8001         (GPU — ASR fast)
├── insanely-fast :8002        (GPU — ASR accurate)
└── diarization  :8003         (GPU — Speaker Diarization)
```

### 1.4 기술 스택

| 영역 | 기술 | 버전 |
|------|------|------|
| **Frontend** | Vite + React + TypeScript | React 18 |
| **UI Library** | shadcn/ui (Radix UI) + Tailwind CSS | — |
| **상태관리** | Zustand + React Context | — |
| **Backend** | FastAPI (Python) | 3.11+ |
| **ORM** | SQLAlchemy (async) + Alembic | — |
| **AI Framework** | LangGraph (LangChain 기반) | — |
| **LLM Proxy** | LiteLLM | — |
| **Database** | PostgreSQL 18 + pgvector | pg18 |
| **Cache/Queue** | Valkey 9 (Redis-compatible) | — |
| **Object Storage** | MinIO (S3-compatible) | — |
| **Task Queue** | Celery | — |
| **Search** | SearXNG (Meta Search) | — |
| **Observability** | OpenTelemetry + Langfuse + Prometheus | — |

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
| `chat_controller.py` | `/api/chat` | LiteLLMClient |
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
| LiteLLMClient | litellm_client.py | LiteLLM 프록시 호출 |
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

| Tier | LiteLLM 모델 | 특징 |
|------|-------------|------|
| `tier-simple` | FLM NPU / 빠른 모델 | 저지연, 일반 대화 |
| `tier-thinking` | Codex(primary) → GPU qwen3(fallback) | 복잡한 추론, 분석 |
| `tier-recap` | 요약 특화 모델 | 문서 요약 |
| `codex-medium` | CLIProxy API (OpenAI) | WPI 보고서, 코드 생성 |

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
    │ ASR Pipeline (오디오): WAV → Whisper → 전사 텍스트
    │ OCR Pipeline (문서): PDF/Image → MarkItDown/Tesseract → 텍스트
    │ LLM Pipeline: 전사/OCR 텍스트 → FLM → 요약 (JSONB)
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
    embedding       vector(768),        -- FLM gemma:300m 임베딩
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
| content | embedding | 768D | FLM gemma:300m |
| speaker_profile | voice_embedding | 512D | Pyannote |
| client | profile_embedding | 768D | 범용 임베딩 |

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
├── pipelines/             # GPU/NPU 실행 파이프라인
│   ├── asr/
│   │   ├── __init__.py    # ASR 실행 진입점
│   │   └── vad_utils.py   # Voice Activity Detection
│   ├── ocr/
│   │   └── __init__.py    # OCR 실행 진입점
│   ├── llm/
│   │   ├── __init__.py    # LLM 실행 진입점
│   │   └── llamacpp_client.py  # llama.cpp HTTP 클라이언트
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
Pipeline (GPU/NPU 실제 실행)
    │ pipelines/asr/ → Whisper 모델 호출
    │ pipelines/ocr/ → Tesseract / EasyOCR 호출
    └ pipelines/llm/ → llamacpp_client (llama.cpp HTTP)
```

### 11.4 큐 분리 전략

| 큐 | 용도 | 하드웨어 | 특징 |
|----|------|---------|------|
| `asr` | 음성→텍스트 | GPU (CUDA) | 긴 오디오 처리 |
| `ocr` | 이미지→텍스트 | GPU/CPU | 페이지 단위 병렬 |
| `llm` | 텍스트 추론 | GPU (CUDA) | VRAM 집약적 |

---

## 12. LiteLLM & Provider Manager

### 12.1 LiteLLM 프록시 구조

```
Backend / Worker
    │ HTTP POST /v1/chat/completions
    ▼
LiteLLM Proxy :4000
    │ litellm_config.yaml 참조
    │ custom_handler.py 경유
    ▼
prometheus_router (CustomLLM)
    │ Redis Stream 기반 작업 전송
    ▼
Provider Manager (Host)
    │ GPU/NPU 서버 선택
    └─ FLM :11434 (NPU) / llama-server :8080 (GPU)
       whisper-cpp :8001 / insanely-fast :8002
```

### 12.2 모델 라우팅 테이블

| 모델명 | 프로바이더 | 용도 | Fallback |
|--------|-----------|------|----------|
| `codex-high` | CLIProxy (OpenAI) | 고급 코드/분석 | — |
| `codex-medium` | CLIProxy (OpenAI) | WPI 보고서, 코드 | — |
| `codex-low` | CLIProxy (OpenAI) | 빠른 코드 | — |
| `tier-thinking` | Codex → GPU qwen3-tk:4b | 복잡한 추론 | tier-thinking-local |
| `tier-thinking-local` | prometheus-router | Tier-thinking 폴백 | — |
| `asr-speed` | prometheus-router | 빠른 ASR | — |
| `asr-accuracy` | prometheus-router | 정확한 ASR | — |
| `diarization` | prometheus-router | 화자 분리 | — |
| `ocr-speed` | prometheus-router | 빠른 OCR (NPU) | — |
| `ocr-accuracy` | prometheus-router | 정확한 OCR (GPU) | — |
| `*` (와일드카드) | prometheus-router | 동적 LLM 라우팅 | — |

**재시도 정책:**
```yaml
num_retries: 10
request_timeout: 600s
TimeoutErrorRetries: 5
RateLimitErrorRetries: 3
InternalServerErrorRetries: 10
```

### 12.3 Provider Manager 상세

#### 아키텍처

```
Provider Manager (Host Process)
├── StreamProcessor      ← Valkey Stream Consumer (XREADGROUP)
│   └── 수신: stream:chat:requests, stream:media:requests, stream:recap:requests
│   └── 발행: stream:gpu:responses
│
├── ProviderManager      ← GPU/NPU 프로세스 Lifecycle
│   ├── start_provider() / stop_provider()
│   ├── health_check() (30초 주기)
│   └── recover_provider() (3회 시도 후 cooldown 5분)
│
├── JobTracker           ← 작업 상태 추적
│   └── job_id → {status, result, error}
│
└── IdleManager          ← 자동 언로드
    └── idle_timeout=600s → 유휴 프로바이더 종료
```

#### Provider Groups

| 그룹 | 서버 | 포트 | 하드웨어 |
|------|------|------|---------|
| flm | FLM NPU | 11434, 11435, 11436 | NPU |
| gpu-llm | llama-server | 8080, 8081 | GPU |
| gpu-asr | whisper-cpp | 8001 | GPU |
| gpu-asr-accurate | insanely-fast | 8002 | GPU |
| gpu-diarization | diarization-server | 8003 | GPU |

#### Provider 상태

```
UP → STOPPING → DOWN
UP → (연속 3회 실패) → RECOVERING → (3회 시도) → COOLDOWN (5분) → DOWN
DOWN → STARTING → UP
```

#### 동시성 제어

```yaml
gpu_max_concurrent: 2   # GPU 동시 작업 수
npu_max_concurrent: 1   # NPU 동시 작업 수
```

### 12.4 custom_handler.py 역할

```python
# infra/litellm/custom_handler.py
class PrometheusRouter(CustomLLM):
    async def acompletion(model, messages, **kwargs):
        1. Task 타입 감지 (ASR / OCR / LLM)
        2. 프로바이더 선택 (GPU/NPU)
        3. Redis Stream XADD (stream:chat:requests 등)
        4. stream:gpu:responses XREAD 대기
        5. OpenTelemetry trace context 전파
        6. 결과 반환 (스트리밍 지원)
```

### 12.5 gpu_stream_client.py 역할

```python
# infra/litellm/gpu_stream_client.py
class GpuStreamClient:
    # LiteLLM ↔ Provider Manager 메시징 레이어
    async def send_request(job_id, payload) → None
    async def stream_response(job_id) → AsyncGenerator[str, None]
    def _inject_trace_context(headers) → dict
```

---

## 13. Valkey Data Architecture

### 13.1 Stream 토폴로지 (3-Track Strategy)

| 스트림 키 | MAXLEN | 데이터 성격 | 용도 |
|-----------|--------|----------|------|
| `stream:media:requests` | 50 | Very High (MB) | ASR/OCR 파일 바이너리 |
| `stream:chat:requests` | 3,000 | Low (KB) | Chat/Thinking 텍스트 |
| `stream:recap:requests` | 1,000 | Medium (KB) | Summarization |
| `stream:provider:events` | 1,000 | Low | 시스템 제어 신호 |
| `stream:gpu:responses` | 300 | Medium | 작업 결과 반환 |

**Consumer Group 패턴:**
```
XGROUP CREATE stream:chat:requests provider-workers $ MKSTREAM
XREADGROUP GROUP provider-workers worker-1 COUNT 1 BLOCK 5000 STREAMS stream:chat:requests >
XACK stream:chat:requests provider-workers {message_id}
```

### 13.2 Pub/Sub 채널

| 채널 패턴 | 용도 | 발행자 | 구독자 |
|-----------|------|--------|--------|
| `events:file_progress:{file_id}` | 파일 처리 진행률 | Worker | Backend SSE |
| `events:asr_stream:{file_id}` | 실시간 ASR 텍스트 | Worker | Backend WebSocket |
| `events:llm_stream:{file_id}` | 실시간 LLM 토큰 | Worker | Backend WebSocket |
| `events:content_created` | 콘텐츠 생성 완료 | Worker | Backend |
| `events:file_progress:global` | 전역 파일 진행률 | Worker | Backend WebSocket |

### 13.3 Cache Key 패턴 & TTL

| 키 패턴 | TTL | 용도 |
|---------|-----|------|
| `cache:search:{hash}` | 3,600s (1시간) | 웹 검색 결과 캐시 |
| `history:{session_id}` | 604,800s (7일) | 대화 히스토리 |
| `job:{job_id}` | 86,400s (24시간) | 작업 임시 데이터 |
| `worker:{device}:active` | 3,600s (1시간) | GPU/NPU 동시성 잠금 |

### 13.4 메모리 정책

```yaml
maxmemory: 2gb
maxmemory-policy: volatile-lru    # TTL 있는 키부터 LRU 삭제
```

스트림(TTL 없음)은 보존, 캐시(TTL 있음)부터 삭제.

### 13.5 데이터 플로우 요약

```
Backend                    Valkey                    Provider Manager
  │                          │                              │
  │── XADD stream:chat ─────►│                              │
  │                          │◄── XREADGROUP ───────────────│
  │                          │                              │ (GPU 처리)
  │                          │◄── XADD stream:gpu:responses─│
  │◄── XREAD stream:gpu ─────│                              │
  │                          │                              │
  │◄── SUBSCRIBE events:* ───│◄── PUBLISH events:file_progress ──Worker
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
| **Tempo** | 3200, 4317 | 분산 추적 (OTLP) | Backend, Worker, Provider Manager |
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
    └── Provider Manager → Tempo :4317
            └── GPU/NPU 작업 Span

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
| LiteLLM 클라이언트 | `backend/app/services/litellm_client.py` |
| Valkey Stream 소비 | `backend/app/services/stream_consumer.py` |

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

### 15.4 인프라 핵심 파일

| 역할 | 경로 |
|------|------|
| Docker 서비스 정의 | `docker-compose.yml` |
| LiteLLM 모델 설정 | `infra/litellm/litellm_config.yaml` |
| LiteLLM 커스텀 핸들러 | `infra/litellm/custom_handler.py` |
| GPU Stream 클라이언트 | `infra/litellm/gpu_stream_client.py` |
| Provider Manager 진입점 | `infra/provider_manager/main.py` |
| Provider 프로세스 관리 | `infra/provider_manager/core/manager.py` |
| Stream 처리 | `infra/provider_manager/services/stream_processor.py` |
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
| `docs/archived/architecture-v1.0.0.md` | 2026-02-21 작성, v1.1.0으로 대체 |
| `docs/archived/architecture-v8.5.md` | 이전 V8 계열 아키텍처 |
| `docs/archived/architecture-v8.2.md` | V8.2 (LangGraph 도입) |
| `docs/archived/architecture-v7.*.md` | V7 계열 |
| `docs/archived/architecture_v4.md` ~ `v6.*` | 초기 아키텍처 |

### 16.3 용어 정리

| 용어 | 설명 |
|------|------|
| FLM | Fast Language Model — NPU 기반 경량 LLM 서버 |
| RRF | Reciprocal Rank Fusion — 다중 검색 결과 리랭킹 알고리즘 |
| pgvector | PostgreSQL 벡터 확장 — 임베딩 유사도 검색 |
| VAD | Voice Activity Detection — 음성 감지 (침묵 제거) |
| Diarization | 화자 분리 — 누가 말했는지 구분 |
| Tier | LLM 성능 티어 — simple(빠름) / thinking(정확) |
| GraphState | LangGraph 상태 객체 — 노드 간 데이터 공유 |
| SSE | Server-Sent Events — 서버→클라이언트 단방향 실시간 스트림 |
| Watchdog | 비정상 파일 상태 감지 및 자동 복구 스케줄러 |
