# Architecture V8.5

> SSE 실시간 파이프라인 진행률 + Frontend Vite 마이그레이션 + 검색 재시도 메커니즘

## V8.5 업데이트 요약 (2026-02-08)

### 왜 V8.5로 버전업했는가?

V8.2에서 Langfuse LLM Observability를 완성했으나, 다음 세 가지 주요 영역이 미비했습니다:

1. **검색 품질**: 검색 실패 시 빈 결과만 반환 → 사용자 경험 저하
2. **실시간 피드백**: 파이프라인 처리 상태를 사용자에게 즉시 전달하지 못함
3. **프론트엔드 인프라**: 레거시 구조 → 모던 스택 전환 필요

V8.3~V8.5에서 이 세 영역을 모두 해결했습니다.

### V8.3 → V8.4 → V8.5 변경 흐름

| 버전 | 핵심 변경 |
|------|----------|
| **V8.3** | Query Transformation 플러그인 아키텍처 (ContextualizeTransformer, DecomposeTransformer) |
| **V8.4** | 검색 재시도 메커니즘 (SearchEvaluator, QueryRewriter, FallbackHandler, LangGraph 루프) |
| **V8.5** | SSE 실시간 파이프라인 진행률, Frontend Vite 마이그레이션, `conversation_id` → `thread_id` 통일 |

---

## 버전 히스토리

| 버전 | 변경 내용 |
|------|----------|
| V6.6 | Redis Stream 기반 메시징 (Docker Desktop 크래시 해결) |
| V7.0 | Provider Manager 통합 프로세스 관리 |
| V7.3 | Provider Manager 패키지 구조화 + Consumer Group 자동 복구 |
| V7.4 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager, audio_gateway 제거 |
| V7.5 | Redis → Valkey 마이그레이션 (라이선스/성능 이슈 대응) |
| V7.6 | FLM Thinking Model 지원, Client AI 모드 UI 전면 개편 |
| V8.0 | LangGraph 기반 AI Agent 시스템, Multi-Agent Workflow, Tier 기반 모델 라우팅 |
| V8.1 | Tempo 기반 트레이스 저장소, End-to-End Trace 전파, State Machine 중앙화 |
| V8.2 | Langfuse LLM Observability, Span Link 패턴, 노이즈 필터링 |
| **V8.3** | **Query Transformation 플러그인 아키텍처** |
| **V8.4** | **검색 재시도 메커니즘 (LangGraph 루프 기반)** |
| **V8.5** | **SSE 실시간 파이프라인 진행률, Frontend Vite 마이그레이션** |

---

## 개괄 아키텍처 (V8.5)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Docker                                                                          │
│                                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ Frontend │──▶│ Backend  │──▶│  Valkey  │──▶│  Worker  │──▶│ LiteLLM  │     │
│  │  (Vite)  │   │  :8000   │   │  :6379   │   │ (Celery) │   │  :4000   │     │
│  │  :3000   │   │          │   │          │   │          │   │          │     │
│  │          │   │ LangGraph│   │  Cache   │   │  ASR/OCR │   │  Router  │     │
│  │ React+TS │   │  Agents  │   │ +Stream  │   │   Tasks  │   │          │     │
│  │ Tailwind │   │          │   │ +Pub/Sub │   │          │   │          │     │
│  │ shadcn   │   │          │   │          │   │          │   │          │     │
│  └─────┬────┘   └────┬─────┘   └──────────┘   └────┬─────┘   └────┬─────┘     │
│        │             │              │              │              │            │
│     SSE/WS           │              │              │              │            │
│        │             │              │              │              │            │
│  ┌─────┴─────────────┴──────────────┴──────────────┴──────────────┴─────┐      │
│  │                      관 측 성   스 택  (V8.2~)                        │      │
│  │  ┌────────┐  ┌────────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │      │
│  │  │ Tempo  │  │ Prometheus │  │ Grafana  │  │  Flower  │  │Langfuse│ │      │
│  │  │ :3200  │  │   :9090    │  │  :3002   │  │  :5555   │  │ :3001  │ │      │
│  │  │ :4317  │  │            │  │          │  │          │  │        │ │      │
│  │  └────────┘  └────────────┘  └──────────┘  └──────────┘  └────────┘ │      │
│  └─────────────────────────────────────────────────────────────────────┘      │
│                                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐                    │
│  │  Nginx   │   │  MinIO   │   │PostgreSQL│   │ SearXNG  │                    │
│  │   :80    │   │  :9000   │   │  :5432   │   │  :8080   │                    │
│  │          │   │          │   │ +pgvector│   │          │                    │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘                    │
│                                                                                 │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 기술 스택 (V8.5)

### Backend

| 구성요소 | 기술 | 버전 이후 변경 |
|----------|------|---------------|
| Web Framework | FastAPI | - |
| AI Agent Framework | LangGraph | V8.4: 검색 루프 추가 |
| LLM Backend | LiteLLM (Tier 기반 라우팅) | - |
| 형태소 분석 | kiwipiepy (Kiwi) | - |
| 웹 검색 | SearXNG | - |
| Vector Search | pgvector (768d embeddinggemma) | - |
| Message Queue | Redis Stream + Celery | - |
| Cache / Pub/Sub | Valkey | V8.5: Pub/Sub 진행률 이벤트 추가 |
| Database | PostgreSQL + pgvector | - |
| Storage | MinIO (S3 호환) | - |
| Tracing | Grafana Tempo | - |
| LLM Observability | Langfuse v2 + OpenLLMetry | - |
| Metrics | Prometheus + Grafana | - |
| State Machine | ContentStateMachine (FSM) | V8.1~ |

### Frontend (V8.5 신규)

| 구성요소 | 기술 |
|----------|------|
| 빌드 도구 | Vite 7.x |
| Framework | React 19.x + TypeScript 5.x |
| 스타일링 | Tailwind CSS 3.x + tailwindcss-animate |
| UI Library | shadcn/ui (Radix UI primitives) |
| 상태 관리 | React Hooks + Context API |
| 실시간 통신 | SSE (파이프라인 진행률) + WebSocket (실시간 ASR) |
| 마크다운 렌더링 | react-markdown |
| 검증 | Zod |

---

## V8.3: Query Transformation 플러그인 아키텍처

### 왜 필요했는가?

대화 맥락에 의존하는 질문(예: "그 중에서 가장 빠른 건?")을 검색 엔진이 이해할 수 없었습니다.
대화 히스토리를 참조하여 독립적인 질문으로 재작성하는 Query Transformation이 필요했습니다.

### 플러그인 아키텍처

```python
class QueryTransformer(ABC):
    @abstractmethod
    async def transform(query, state, settings) -> list[str]: ...

    @abstractmethod
    def should_apply(query, state) -> bool: ...

# 플러그인 등록 (순차 실행)
self.transformers = [
    ContextualizeTransformer(),   # 대화 맥락 기반 재작성
    DecomposeTransformer(),        # 복잡한 질문 분해
    # HyDETransformer(),           # Phase 1B
    # StepBackTransformer(),       # Phase 5+
]
```

### ContextualizeTransformer 동작

```
입력: "그 중에서 가장 빠른 건?" + 스레드 히스토리 (최근 3턴)
처리: LLM이 대화 맥락을 분석하여 독립적 질문으로 재작성
출력: "파이썬 웹 프레임워크 중에서 가장 빠른 것은 무엇인가요?"
```

### Intent Parser 내 Query Transformation 흐름

```
IntentParserNode.__call__(state)
  ├─ 1. 모드 감지 (search/rag/reasoning/simple)
  ├─ 2. Tier 라우팅 (tier-simple/tier-complex)
  ├─ 3. Query Transformation
  │     ├─ Phase 1: Transformer 플러그인 순차 실행
  │     │     ├─ ContextualizeTransformer (대화 맥락)
  │     │     └─ DecomposeTransformer (질의 분해)
  │     ├─ Phase 2: 형태소 분석 (Kiwi) - 키워드 추출
  │     └─ Phase 3: 중복 제거 및 최종 목록 (최대 5개)
  └─ 4. 상태 업데이트 (mode, selected_model, search_queries)
```

### 주요 파일

| 파일 | 역할 |
|------|------|
| `agents/nodes/intent_parser.py` | ContextualizeTransformer, DecomposeTransformer 구현 |
| `agents/nodes/intent_router.py` | 플러그인 등록 및 실행 |

---

## V8.4: 검색 재시도 메커니즘

### 왜 필요했는가?

검색 실패 시 빈 결과를 그대로 반환하여 "정보를 찾을 수 없습니다"로 끝나는 문제.
검색 성공률 ~60% → ~85% 개선이 목표.

### LangGraph 루프 기반 재시도

```
IntentParser
    ↓
Searcher ←────────────┐
    ↓                  │
SearchEvaluator        │
    ↓                  │
[품질 50점 이상?]       │
 ├─ YES → Generator    │
 ├─ NO + retry<3 → QueryRewriter ──┘
 └─ NO + retry≥3 → FallbackHandler → Generator
```

### 신규 노드

#### SearchEvaluatorNode
검색 결과 품질을 0-100점으로 평가:

| 평가 기준 | 배점 |
|----------|------|
| 결과 개수 (0개=0점, 5개+=40점) | 40점 |
| 품질 점수 (QualityAssessor 결과) | 40점 |
| 관련성 (키워드 매칭 비율) | 20점 |

50점 미만 → `needs_retry = True`

#### QueryRewriterNode
실패 원인에 따라 적응형 쿼리 재작성:

| 재시도 | 전략 | 설명 |
|--------|------|------|
| 1차 | `broaden` | 더 넓은 범위, 일반적인 용어 |
| 2차 | `narrow` | 더 구체적, 특정한 용어 |
| 3차 | `synonym` | 동의어, 관련어 사용 |

적응형 전략 (실패 원인 기반):
- `no_results` → broaden
- `low_quality` → trusted_domains
- `low_relevance` → keyword_boost

#### FallbackHandlerNode
모든 시도 실패 시 대안:
1. LLM 내장 지식으로 답변 (기본)
2. RAG로 전환 (내부 문서 검색)
3. 사용자에게 명시적 안내

### State 확장

```python
class GraphState(TypedDict):
    # ... 기존 필드 ...

    # V8.4: 검색 재시도
    search_retry_count: int              # 현재 재시도 횟수
    search_quality_score: float          # 품질 점수 (0-100)
    original_search_queries: list[str]   # 원본 쿼리
    failed_queries: list[str]            # 실패 쿼리 목록
    needs_retry: bool                    # 재시도 필요 여부
    retry_reason: str                    # 재시도 이유
```

### LangGraph 조건부 엣지 구현

```python
workflow.add_edge("searcher", "search_evaluator")

workflow.add_conditional_edges(
    "search_evaluator",
    route_after_evaluation,
    {
        "generator": "generator",
        "query_rewriter": "query_rewriter",
        "fallback_handler": "fallback_handler",
    }
)

# QueryRewriter → Searcher (루프!)
workflow.add_edge("query_rewriter", "searcher")
workflow.add_edge("fallback_handler", "generator")
```

### 주요 파일

| 파일 | 역할 |
|------|------|
| `agents/nodes/search_evaluator.py` | 검색 품질 평가 |
| `agents/nodes/query_rewriter.py` | 쿼리 재작성 |
| `agents/nodes/fallback_handler.py` | 폴백 처리 |
| `agents/graph.py` | `create_ai_graph_with_retry()` |
| `agents/state.py` | GraphState 재시도 필드 확장 |

---

## V8.5: SSE 실시간 파이프라인 진행률

### 왜 필요했는가?

기존 폴링 방식(상태별 차등 간격)은 서버 부하와 지연이 문제였습니다.
SSE를 도입하여 서버 → 클라이언트 단방향 실시간 푸시로 전환했습니다.

### End-to-End 진행률 파이프라인

```
Worker (Redis Stream 결과 발행)
    ↓
StreamConsumer (PipelineProgress 호출)
    ↓
Redis Pub/Sub (events:file_progress:{file_id})
    ↓
EventsController (SSE 엔드포인트: GET /api/events/file-progress/stream)
    ↓
Frontend useFileProgressSSE (SSE 구독)
    ↓
useDisplayProgress (Mode A/B 보간)
    ↓
ContentCard / UploadProgress (UI)
```

### ProgressTracker (Backend)

**파일**: `backend/app/utils/progress_tracker.py`

각 파이프라인 단계에서 진행률 이벤트를 Redis Pub/Sub으로 발행:

```python
class PipelineProgress:
    def __init__(self, file_id: str):
        self.file_id = file_id

    async def asr_started(self): ...
    async def asr_completed(self): ...
    async def llm_started(self): ...
    async def llm_progress(self, progress: float, message: str): ...
    async def llm_completed(self): ...
```

StreamConsumer에서 Worker 결과 처리 시 PipelineProgress를 호출하여 각 단계별 진행률 발행.

### SSE 엔드포인트

**파일**: `backend/app/controllers/events_controller.py`

```
GET /api/events/file-progress/stream
```

- Redis Pub/Sub `events:file_progress:*` 패턴 구독
- 30초 keep-alive ping
- `asyncio.wait(FIRST_COMPLETED)` 패턴으로 ping과 메시지 번갈아 처리

**메시지 포맷**:
```json
{
  "type": "file_progress",
  "file_id": "uuid",
  "status": "PROCESSING",
  "progress": 45.0,
  "message": "ASR 처리 중..."
}
```

### Frontend 진행률 표시

**파일**: `frontend/src/features/content/hooks/useDisplayProgress.ts`

2가지 모드로 부드러운 진행률 표시:

| 모드 | 조건 | 동작 |
|------|------|------|
| **Mode A (Estimation)** | SSE progress = 0 | `updated_at` 기반 시간 추정. 상태별 `ESTIMATED_DURATION` 맵, ease-out 커브 (10~85%) |
| **Mode B (Interpolation)** | SSE progress > 0 | SSE 마일스톤 사이 0.3%/초 보간. anchor 기준 최대 8% 버퍼, 200ms 간격 tick |

**설계 원칙**:
- `highWaterMark` 단조증가 보장 (진행률 역행 방지)
- 상태 전환 시 ref 초기화
- 비처리 상태 즉시 반환 (COMPLETED → 100, FAILED → 0)

---

## V8.5: Frontend Vite 마이그레이션

### 아키텍처: Feature-based Architecture

```
frontend/src/
├── features/                  # 기능별 모듈
│   ├── asr/                  # 실시간 ASR (WebSocket)
│   ├── chat/                 # AI 채팅 인터페이스
│   ├── content/              # 콘텐츠 관리
│   ├── monitoring/           # 모니터링
│   ├── sidebar/              # 사이드바
│   └── upload/               # 파일 업로드
├── pages/                     # 페이지 컴포넌트
├── routes/                    # 라우팅 설정
├── shared/                    # 공유 리소스
│   ├── components/           # 공통 컴포넌트 (layout/, ui/)
│   ├── contexts/             # React Context
│   ├── hooks/                # 커스텀 훅
│   ├── services/             # API 서비스 (httpClient, endpoints)
│   ├── types/                # 공통 타입
│   └── utils/                # 유틸리티
└── styles/                    # 전역 스타일
```

### 라우팅

| 경로 | 페이지 | 기능 |
|------|--------|------|
| `/` | HomePage | 랜딩 |
| `/chat/:threadId` | ChatPage | AI 채팅 |
| `/contents` | ContentsPage | 콘텐츠 목록 |
| `/contents/:id` | ContentDetailPage | 콘텐츠 상세 |
| `/upload` | UploadPage | 파일 업로드 |
| `/monitoring` | MonitoringPage | 모니터링 |

### 상태 관리 패턴

```
API/SSE → Custom Hook → useState/Context → Component → UI
```

- **로컬 상태**: `useState` (컴포넌트 레벨)
- **전역 상태**: Context API (ThreadTitleContext 등)
- **서버 상태**: Custom Hooks (`useContents`, `useThreads`, `useThread`)
- **실시간 상태**: SSE/WebSocket → Hook → UI 자동 갱신

### 주요 Custom Hooks

| Hook | 기능 |
|------|------|
| `useContents(params?)` | 콘텐츠 목록 (페이지네이션 + SSE 실시간 갱신) |
| `useContent(id)` | 개별 콘텐츠 상세 |
| `useThreads()` | 스레드 목록 관리 |
| `useThread(threadId)` | 개별 스레드 |
| `useThreadChat({...})` | Vercel AI SDK 스타일 채팅 훅 (SSE 스트리밍) |
| `useContentChat(contentId)` | 콘텐츠별 RAG 채팅 |
| `useFileProgressSSE()` | SSE 파이프라인 진행률 구독 |
| `useDisplayProgress(content)` | Mode A/B 진행률 보간 |

### AI 채팅 인터페이스

5가지 AI 모드:

| 모드 | 설명 |
|------|------|
| `simple` | 일반 AI 대화 |
| `search` | 실시간 웹 검색 기반 답변 |
| `rag` | 저장된 콘텐츠에서 검색 (pgvector) |
| `reasoning` | 단계별 논리적 분석 |
| `hybrid` | 웹 + 내 문서 통합 검색 |

SSE 이벤트 타입: `thinking` | `source` | `sources` | `token` | `content` | `done` | `error` | `thread_created`

### API 통신

**HTTP Client**: Fetch 기반, `postStream()` SSE 지원

**프록시 설정 (Vite)**:
```
/api → http://localhost:8000   (FastAPI)
/ws  → http://localhost:8000   (WebSocket)
/media → http://localhost:9000 (MinIO)
/grafana → http://localhost:3002 (Grafana)
```

---

## V8.5: `conversation_id` → `thread_id` 통일

### 변경 범위

| 영역 | Before | After |
|------|--------|-------|
| API 파라미터/응답 | `conversation_id` | `thread_id` |
| Redis 키 | `ai:conversation:{id}` | `ai:thread:{id}` |
| 프론트엔드 상태 | `conversationId` | `threadId` |
| Langfuse | - | `session_id`로 매핑 |

### 스레드 저장 구조 (Redis)

```
키: "ai:thread:{thread_id}"
TTL: 7일

값: {
    "thread_id": "...",
    "messages": [
        {"role": "user", "content": "...", "timestamp": 123},
        {"role": "assistant", "content": "...", "metadata": {...}, "timestamp": 456}
    ],
    "created_at": 123,
    "updated_at": 456
}
```

---

## AI 채팅 워크플로우 (V8.5 통합)

### 전체 데이터 플로우

```
Client (React)
    ↓ POST /api/ai/threads/stream  (SSE)
FastAPI Router
    ↓
ai_chat_controller.py
    ├─ thread_service.get_or_create_thread()  (Redis)
    ├─ thread_service.add_message(role="user")
    └─ run_ai_agent()
         ↓
    LangGraph Workflow
         ├─ IntentParser
         │    └─ Query Transformation (V8.3)
         ├─ Searcher
         ├─ SearchEvaluator (V8.4)
         ├─ QueryRewriter (V8.4, 조건부)
         ├─ FallbackHandler (V8.4, 조건부)
         ├─ RAGRetriever (mode=rag)
         ├─ Reasoner (mode=reasoning)
         └─ Generator
              ↓ SSE 토큰 스트리밍
    thread_service.add_message(role="assistant")
         ↓
Client (실시간 렌더링)
```

### LangGraph 역할 비중

| LangGraph/LangChain (~10%) | 자체 구현 (~90%) |
|---|---|
| StateGraph 워크플로우 정의 | 비즈니스 로직 (Intent, 검색, RAG, 추론, 생성) |
| 노드 간 상태 전달 | LLM 호출 (llm_client → LiteLLM) |
| 조건부 라우팅 / 루프 | 스레드 히스토리 관리 (Redis) |
| 메시지 타입 (HumanMessage, AIMessage) | Query Transformation 플러그인 |
| | 웹 검색 (SearXNG) |
| | 텔레메트리 (OpenTelemetry, Langfuse) |

---

## Observability Stack (V8.2~)

### 이중 관측성 아키텍처

```
┌─────────────────────┐     ┌─────────────────────┐
│   OpenTelemetry     │     │     Langfuse        │
│   (Tempo)           │     │     (Self-hosted)   │
│                     │     │                     │
│  • 지연시간         │     │  • 프롬프트 전문    │
│  • 에러 추적        │     │  • 응답 전문        │
│  • 서비스 의존성    │     │  • 토큰 사용량      │
│  • Span 계층        │     │  • 비용 추적        │
│                     │     │  • 품질 평가        │
└─────────────────────┘     └─────────────────────┘
          │                           │
          └───────────┬───────────────┘
                      ▼
             ┌─────────────────┐
             │    Grafana      │
             │  (통합 대시보드) │
             └─────────────────┘
```

### Span Link 패턴

비동기 작업(StreamConsumer, AI Chat Streaming)을 원본 HTTP 요청과 OpenTelemetry Span Link로 연결.
Tempo UI에서 "Related Traces"로 확인 가능.

### OpenLLMetry (LLM 자동 계측)

traceloop-sdk 기반으로 LangGraph, OpenAI, LiteLLM 호출을 자동 계측.
Redis/urllib3 instrumentation은 노이즈 방지를 위해 비활성화.

---

## 콘텐츠 처리 파이프라인

### Backend-Worker 분리 아키텍처

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  FastAPI    │────▶│   Valkey    │────▶│ GPU Worker  │
│  Backend    │     │   (Celery)  │     │             │
│             │     │             │     │  ASR (FLM/  │
│  전처리     │     │  Task Queue │     │  whisper)   │
│  DB 저장    │     │             │     │  OCR (LLM   │
│  LLM 요약   │     │             │     │  Vision)    │
└──────┬──────┘     └─────────────┘     └──────┬──────┘
       │                                       │
       │          ┌─────────────┐              │
       │          │ Redis Stream│◀─────────────┘
       └─────────▶│  (결과 큐)  │  결과 발행
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │ Stream      │
                  │ Consumer    │──▶ PipelineProgress
                  │             │      ↓ Redis Pub/Sub
                  │ DB 저장     │      ↓ SSE
                  │ 후처리      │      ↓ Frontend
                  └─────────────┘
```

### 파이프라인 상태 전이 (ContentStateMachine)

```
QUEUED → PULLING → PROCESSING → OCR_PROCESSING → SUMMARY_QUEUED → SUMMARIZING → COMPLETED
           ↓          ↓             ↓                                  ↓
     DOWNLOAD_FAILED  ASR_FAILED   OCR_FAILED                   SUMMARY_FAILED
```

### 처리 기능

| 기능 | 엔진 | 가속 |
|------|------|------|
| 실시간 ASR | FLM (FastFlowLM) | AMD NPU (Ryzen AI) |
| 정확도 ASR | whisper.cpp | Vulkan GPU |
| 화자 분리 | PyAnnote 3.1 | AMD GPU (ROCm) |
| OCR | LLM Vision Model | FLM/llama.cpp |
| LLM 요약 | 3단계 파이프라인 (LangGraph SectionGraph) | LiteLLM 라우팅 |
| 임베딩 | embeddinggemma:300m (768d) | - |

### LLM 3단계 요약 파이프라인

```
Phase 1: 구조 분석 (제목, 키워드, 목차 추출)
Phase 2: 핵심 요약 (bullet points)
Phase 3: 상세 섹션 생성 (LangGraph Dynamic Fan-out, 병렬)
         → 50~300자 검증 및 자동 재시도
```

---

## 품질 테스트 프레임워크 (V8.5 신규)

```
quality_testing/
├── core/
│   ├── config.py          # 테스트 설정
│   ├── interfaces.py      # 추상 인터페이스
│   └── orchestrator.py    # 테스트 오케스트레이터
├── evaluators/
│   ├── citation_evaluator.py   # 인용 정확도
│   ├── intent_evaluator.py     # 의도 분류
│   ├── quality_evaluator.py    # 응답 품질
│   └── search_evaluator.py     # 검색 품질
├── loaders/
│   └── redis_loader.py    # 테스트 데이터 로더
├── maskers/
│   └── pii_masker.py      # PII 마스킹
└── reporters/
    ├── json_reporter.py   # JSON 리포트
    └── markdown_reporter.py # MD 리포트
```

---

## 주요 파일 맵

### Backend

| 계층 | 파일 | 역할 |
|------|------|------|
| **진입점** | `app/main.py` | FastAPI 앱 |
| **Controller** | `controllers/content_controller.py` | 파일 업로드, 목록/상세, 재처리 |
| **Controller** | `controllers/ai_chat_controller.py` | AI 에이전트 채팅 (SSE) |
| **Controller** | `controllers/events_controller.py` | SSE 파이프라인 진행률 |
| **Controller** | `controllers/websocket_controller.py` | 실시간 ASR (WebSocket) |
| **Service** | `services/content_service.py` | 콘텐츠 비즈니스 로직 |
| **Service** | `services/thread_service.py` | 스레드 히스토리 (Redis) |
| **Service** | `services/stream_consumer.py` | Redis Stream 소비자 |
| **Service** | `services/llm_summary_service.py` | LLM 요약 파이프라인 |
| **Agent** | `agents/graph.py` | LangGraph 워크플로우 정의 |
| **Agent** | `agents/state.py` | GraphState 타입 정의 |
| **Node** | `agents/nodes/intent_parser.py` | Intent 분석 + Query Transformation |
| **Node** | `agents/nodes/searcher.py` | 웹 검색 |
| **Node** | `agents/nodes/search_evaluator.py` | 검색 품질 평가 (V8.4) |
| **Node** | `agents/nodes/query_rewriter.py` | 쿼리 재작성 (V8.4) |
| **Node** | `agents/nodes/fallback_handler.py` | 폴백 처리 (V8.4) |
| **Node** | `agents/nodes/generator.py` | LLM 응답 생성 |
| **Node** | `agents/nodes/rag_retriever.py` | 벡터 검색 (pgvector) |
| **Node** | `agents/nodes/reasoner.py` | 단계별 추론 |
| **Util** | `utils/progress_tracker.py` | SSE 진행률 발행 (V8.5) |
| **State** | `state_machines/machines/content_machine.py` | FSM 상태 전이 검증 |
| **Core** | `core/telemetry.py` | OpenTelemetry + OpenLLMetry |
| **Core** | `core/langfuse.py` | Langfuse 클라이언트 |

### Frontend

| 파일 | 역할 |
|------|------|
| `features/chat/components/ChatInterface.tsx` | AI 채팅 전체 인터페이스 |
| `features/chat/components/AIModeSelector.tsx` | AI 모드 선택기 |
| `features/chat/components/ThinkingDisplay.tsx` | AI 사고 과정 표시 |
| `features/content/components/ContentList.tsx` | 콘텐츠 목록 |
| `features/content/components/ContentDetailLayout.tsx` | 상세 레이아웃 |
| `features/content/hooks/useDisplayProgress.ts` | 진행률 보간 (V8.5) |
| `features/upload/components/FileUploader.tsx` | 파일 업로드 |
| `shared/hooks/useContents.ts` | 콘텐츠 서버 상태 |
| `shared/hooks/useFileProgressSSE.ts` | SSE 진행률 구독 |
| `shared/hooks/useThreadChat.ts` | AI 채팅 SSE 스트리밍 |
| `shared/services/api/httpClient.ts` | HTTP/SSE 클라이언트 |

---

## 로드맵

| 버전 | 상태 | 주요 기능 |
|------|------|----------|
| V7.4 | ✅ 완료 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager |
| V7.5 | ✅ 완료 | Redis → Valkey 마이그레이션 |
| V7.6 | ✅ 완료 | FLM Thinking Model 지원, Client AI 모드 UI 전면 개편 |
| V8.0 | ✅ 완료 | LangGraph AI Agent, Multi-Agent Workflow, Tier 라우팅 |
| V8.1 | ✅ 완료 | Tempo 기반 트레이스 저장소, End-to-End Trace 전파 |
| V8.2 | ✅ 완료 | Langfuse LLM Observability, Span Link 패턴 |
| V8.3 | ✅ 완료 | Query Transformation 플러그인 아키텍처 |
| V8.4 | ✅ 완료 | 검색 재시도 메커니즘 (LangGraph 루프) |
| **V8.5** | ✅ **완료** | **SSE 파이프라인 진행률, Frontend Vite 마이그레이션** |
| V9.0 | 예정 | 초개인화 레이어 (User Profiling, Adaptive Prompt, Memory System) |
| V9.1 | 예정 | LlamaIndex RAG 고도화 (pgvector, Re-ranking, RAGAS 평가) |
| V10.0 | 예정 | Temporal 워크플로우 엔진, 멀티 호스트 지원 |
