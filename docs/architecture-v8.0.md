# Architecture V8.0

> LangGraph 기반 AI Agent 시스템 + Multi-Agent Workflow

## 버전 히스토리

| 버전 | 변경 내용 |
|------|----------|
| V6.6 | Redis Stream 기반 메시징 (Docker Desktop 크래시 해결) |
| V7.0 | Provider Manager 통합 프로세스 관리 |
| V7.3 | Provider Manager 패키지 구조화 + Consumer Group 자동 복구 |
| V7.4 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager, audio_gateway 제거 |
| V7.5 | Redis -> Valkey 마이그레이션 (라이선스/성능 이슈 대응) |
| V7.6 | FLM Thinking Model 지원, Client AI 모드 UI 전면 개편 |
| **V8.0** | **LangGraph 기반 AI Agent 시스템, Multi-Agent Workflow, Tier 기반 모델 라우팅** |

---

## 개괄 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Docker                                                                          │
│                                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ Frontend │──▶│ Backend  │──▶│  Valkey  │──▶│  Worker  │──▶│ LiteLLM  │     │
│  │  :3000   │   │  :8000   │   │  :6379   │   │ (Celery) │   │  :4000   │     │
│  │          │   │          │   │          │   │          │   │          │     │
│  │ AI Chat  │   │ LangGraph│   │  Cache   │   │  ASR/OCR │   │  Router  │     │
│  │   UI     │   │  Agents  │   │ + Stream │   │   Tasks  │   │          │     │
│  └──────────┘   └────┬─────┘   └──────────┘   └────┬─────┘   └────┬─────┘     │
│        │             │              │              │              │            │
│        │             │              │              │              │            │
│  ┌─────┴─────────────┴──────────────┴──────────────┴──────────────┴─────┐      │
│  │                      관 측 성   스 택                                 │      │
│  │  ┌────────┐  ┌────────────┐  ┌──────────┐  ┌──────────┐             │      │
│  │  │ Jaeger │  │ Prometheus │  │ Grafana  │  │  Flower  │             │      │
│  │  │ :16686 │  │   :9090    │  │  :3002   │  │  :5555   │             │      │
│  │  └────────┘  └────────────┘  └──────────┘  └──────────┘             │      │
│  └─────────────────────────────────────────────────────────────────────┘      │
│                                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐                    │
│  │  Nginx   │   │  MinIO   │   │PostgreSQL│   │ SearXNG  │                    │
│  │   :80    │   │  :9000   │   │  :5432   │   │  :8080   │                    │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘                    │
│                                                                                 │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │
                                        │ Redis Stream (TCP)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Host (Windows)                                                                  │
│                                                                                 │
│            ┌────────────────────────────────────────┐                          │
│            │      Provider Manager (V7.4+)          │                          │
│            │  - Redis Stream Consumer               │                          │
│            │  - HTTP API (:9998)                    │                          │
│            │  - Idle Timeout Manager                │                          │
│            │  - OpenTelemetry trace 전파            │                          │
│            │  - Tier → Model 매핑 (V8.0)           │                          │
│            └───────────────┬────────────────────────┘                          │
│                            │                                                    │
│         ┌──────────────────┼──────────────────┐                                │
│         ▼                  ▼                  ▼                                │
│   ┌───────────┐     ┌───────────┐     ┌───────────┐                           │
│   │ GPU (LLM) │     │ GPU (ASR) │     │ NPU (FLM) │                           │
│   │ :8080-81  │     │ :8001-03  │     │ :11434-37 │                           │
│   └───────────┘     └───────────┘     └───────────┘                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## AI Agent 시스템 (V8.0 핵심)

### LangGraph 기반 Multi-Agent Workflow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          사용자 쿼리 입력                                        │
└────────────────────────────────┬────────────────────────────────────────────────┘
                                 │
                                 ▼
                        ┌─────────────────┐
                        │ Intent Parser   │
                        │ (의도 분석)      │
                        │                 │
                        │ - Kiwi 형태소    │
                        │ - LLM 분석      │
                        │ - Mode 결정     │
                        └────────┬────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            │                    │                    │
            ▼                    ▼                    ▼
    ┌───────────────┐    ┌───────────────┐    ┌───────────────┐
    │    SEARCH     │    │   RESEARCH    │    │   ANALYSIS    │
    │   (웹 검색)    │    │  (웹+RAG)     │    │  (RAG 검색)   │
    └───────┬───────┘    └───────┬───────┘    └───────┬───────┘
            │                    │                    │
            │              ┌─────┴─────┐              │
            │              ▼           ▼              │
            │      ┌──────────┐  ┌──────────┐         │
            │      │ SearXNG  │  │ RAG DB   │         │
            │      │ (Web)    │  │(Summary) │         │
            │      └────┬─────┘  └────┬─────┘         │
            │           │             │               │
            │           └──────┬──────┘               │
            └──────────────────┼──────────────────────┘
                               │
                               ▼
                      ┌─────────────────┐
                      │    Searcher     │
                      │ (검색 실행)      │
                      │                 │
                      │ - 웹/RAG 검색    │
                      │ - RRF 스코어링   │
                      │ - 소스 수집     │
                      └────────┬────────┘
                               │
                      ┌────────┴────────┐
                      │   REASONING?    │
                      │                 │
                      └────┬──────┬─────┘
                          No    Yes
                           │     │
                           │     ▼
                           │  ┌─────────────────┐
                           │  │    Reasoner     │
                           │  │ (추론/분석)      │
                           │  │                 │
                           │  │ - Chain-of-     │
                           │  │   Thought       │
                           │  │ - 단계별 추론    │
                           │  └────────┬────────┘
                           │           │
                           └───────────┘
                                 │
                                 ▼
                        ┌─────────────────┐
                        │   Generator     │
                        │ (답변 생성)      │
                        │                 │
                        │ - LLM 호출      │
                        │ - 마크다운 생성  │
                        │ - 소스 인용     │
                        └────────┬────────┘
                                 │
                                 ▼
                        ┌─────────────────┐
                        │   Reflector     │
                        │ (품질 검증)      │
                        │                 │
                        │ - 정확성 평가    │
                        │ - 출처 확인     │
                        │ - 완성도 체크   │
                        └────────┬────────┘
                                 │
                                 ▼
                        ┌─────────────────┐
                        │  최종 응답 반환  │
                        │  + 소스 인용    │
                        │  + 사고 과정    │
                        └─────────────────┘
```

### AI 모드 4가지

| 모드 | 설명 | 워크플로우 | 사용 사례 |
|------|------|----------|----------|
| **SEARCH** | 웹 검색 기반 답변 | IntentParser → SearXNG → Generator | 최신 뉴스, 실시간 정보 |
| **RESEARCH** | 웹+RAG 하이브리드 | IntentParser → SearXNG + RAG → Generator | 포괄적 조사, 심층 분석 |
| **ANALYSIS** | 내부 문서 분석 | IntentParser → RAG → Generator | 업로드한 콘텐츠 질문 |
| **REASONING** | 추론 모드 | IntentParser → Reasoner → Generator | 복잡한 분석, 비교, 추론 |

### Tier 기반 모델 라우팅 (V8.0)

**설계 원칙: 관심사의 분리**

| 레이어 | 컴포넌트 | 역할 | 결정 내용 |
|--------|---------|------|----------|
| **Backend** | TierRouter | WHAT - "어떤 능력이 필요한가?" | tier-simple, tier-complex, tier-reasoning |
| **Infrastructure** | StreamProcessor | HOW - "어떤 모델로 제공할 것인가?" | lfm2:2.6b, qwen3:4b, qwen3-tk:4b |

**Tier 결정 흐름:**

```
사용자 쿼리
    │
    ▼
┌────────────────────────┐
│  Backend (TierRouter)  │
│                        │
│  1. 모드 확인:         │
│     reasoning → tier-reasoning
│                        │
│  2. 컨텍스트 크기:     │
│     >3000 → tier-complex
│                        │
│  3. 규칙 기반:         │
│     복잡 키워드 → tier-complex
│     기본 → tier-simple │
└───────────┬────────────┘
            │ tier 결정
            ▼
┌────────────────────────────────┐
│ Infrastructure (StreamProcessor)│
│                                │
│  tier-simple → lfm2:2.6b       │
│  tier-complex → qwen3:4b       │
│  tier-reasoning → qwen3-tk:4b  │
└────────────────────────────────┘
```

---

## 세부 아키텍처

### Backend AI Agent 구조

```
backend/app/
├── agents/                         # LangGraph AI Agent 시스템 (V8.0)
│   ├── graph.py                    # 메인 LangGraph 정의
│   ├── state.py                    # GraphState 타입 정의
│   ├── routing_rules.json          # Tier 라우팅 규칙
│   ├── nodes/
│   │   ├── intent_parser.py        # 의도 분석 (Kiwi + LLM)
│   │   ├── searcher.py             # 검색 실행 (Web + RAG)
│   │   ├── reasoner.py             # 추론 (Chain-of-Thought)
│   │   ├── generator.py            # 답변 생성
│   │   ├── reflector.py            # 품질 검증
│   │   └── rag_retriever.py        # RAG 검색 (선택적)
│   └── tools/
│       ├── web_search.py           # SearXNG 클라이언트
│       ├── rag_search.py           # 내부 콘텐츠 검색
│       ├── llm_client.py           # LiteLLM 클라이언트
│       └── model_router.py         # Tier 라우터 (TierRouter)
├── controllers/
│   ├── ai_chat_controller.py       # AI 채팅 API (V8.0)
│   ├── search_controller.py        # 기존 Deep Search
│   └── admin_controller.py         # 시스템 관리
└── services/
    └── conversation_service.py     # 대화 히스토리 (Valkey)
```

### Frontend AI Chat UI

```
client/
├── components/
│   ├── AIModeSelector.tsx          # AI 모드 선택 UI (V8.0)
│   ├── ChatInterface.tsx           # AI 채팅 인터페이스
│   ├── ChatPrompt.tsx              # 입력 폼
│   ├── MarkdownContent.tsx         # 답변 렌더링
│   ├── SourceCarousel.tsx          # 소스 인용 표시
│   └── ThinkingProcessAccordion.tsx # 사고 과정 시각화
```

---

## 서비스 목록

### Docker Compose 서비스

| 서비스 | 컨테이너명 | 포트 | 역할 | V8.0 변경사항 |
|--------|-----------|------|------|--------------|
| **nginx** | asr-nginx | 80, 8080 | Reverse Proxy | traceparent 헤더 전달 |
| **frontend** | asr-frontend | 3000 | Next.js 웹 UI | AI Chat UI 추가 |
| **backend** | asr-backend | 8000 | FastAPI 메인 API | **LangGraph Agent 추가** |
| **worker** | asr-worker-unified | - | Celery 워커 | ASR/LLM/OCR 통합 태스크 |
| **litellm** | asr-litellm-proxy | 4000 | LLM 라우팅 프록시 | **Tier 기반 라우팅** |
| **valkey** | asr-valkey | 6379 | 캐시 + Celery Broker | **대화 히스토리 저장** |
| **postgres** | asr-postgres | 5432 | 메인 DB | 콘텐츠/트랜스크립트 저장 |
| **minio** | asr-minio | 9000, 9001 | S3 호환 스토리지 | 미디어 파일 저장 |
| **searxng** | asr-searxng | 8080 | 메타 검색 엔진 | **웹 검색 통합 (V8.0)** |
| **jaeger** | asr-jaeger | 16686, 4317, 4318 | 분산 추적 | OTLP gRPC/HTTP |
| **prometheus** | asr-prometheus | 9090 | 메트릭 수집 | GPU/NPU 모니터링 |
| **grafana** | asr-grafana | 3002 | 대시보드 | 관측성 시각화 |
| **flower** | asr-flower | 5555 | Celery 모니터링 | 읽기 전용 모드 |

### Host Providers (PM2 관리)

| 프로바이더 | 포트 | 모델/역할 | 관리 방식 | Tier 매핑 (V8.0) |
|-----------|------|----------|----------|------------------|
| **provider-manager** | 9998 | Redis Stream 브릿지 | PM2 항상 실행 | - |
| **npu-exporter** | 9183 | NPU 메트릭 수집 | PM2 항상 실행 | - |
| **llama-server** | 8080 | LLM 요약 (Qwen3-4B) | On-Demand | tier-complex |
| **llama-ocr-server** | 8081 | OCR Vision (Qwen3-VL-8B) | On-Demand | - |
| **whisper-server** | 8001 | ASR Speed Mode | On-Demand | - |
| **insanely-fast** | 8002 | ASR Accuracy Mode | On-Demand | - |
| **diarization** | 8003 | Speaker Diarization | On-Demand | - |
| **flm-asr** | 11434 | NPU ASR (whisper-v3:turbo) | Always-On | - |
| **flm-llm** | 11435 | NPU LLM (lfm2:2.6b) | Always-On | tier-simple |
| **flm-llm-thinking** | 11437 | NPU LLM (qwen3-tk:4b) | On-Demand | tier-reasoning |
| **flm-ocr** | 11436 | NPU OCR (qwen3vl-it:4b) | Always-On | - |

---

## AI Agent 노드 상세

### 1. IntentParser (의도 분석)

**역할**: 사용자 쿼리 의도 분석 및 AI 모드 결정

**기능**:
- Kiwi 형태소 분석기를 사용한 키워드 추출
- LLM 기반 의도 분석
- 검색 키워드 생성 (웹 검색용)
- AI 모드 결정 (search, research, analysis, reasoning)

**입력**: 사용자 쿼리, 대화 히스토리
**출력**: `ai_mode`, `requires_search`, `search_queries`, `thinking_steps`

### 2. Searcher (검색 실행)

**역할**: 웹 검색 및 RAG 검색 실행

**기능**:
- **웹 검색**: SearXNG를 통한 메타 검색
  - 캐싱 지원 (Valkey, TTL 1시간)
  - 카테고리 자동 감지 (general, news, videos, images)
- **RAG 검색**: PostgreSQL ILIKE 기반 키워드 검색
  - 콘텐츠 요약(summary_md)에서 검색
  - 관련 스니펫 추출
- **RRF 스코어링**: Reciprocal Rank Fusion
  - 웹/RAG 결과 통합 순위 매김
  - 스코어 = Σ 1/(k + rank), k=60

**입력**: `search_queries`, `ai_mode`, `content_ids` (선택)
**출력**: `search_results`, `sources`, `thinking_steps`

### 3. Reasoner (추론)

**역할**: REASONING 모드 전용 Chain-of-Thought 추론

**기능**:
- 단계별 추론 수행
- 사고 과정 기록
- 중간 결론 도출

**입력**: `search_results`, `query`, `conversation_history`
**출력**: `reasoning_steps`, `intermediate_conclusions`, `thinking_steps`

### 4. Generator (답변 생성)

**역할**: 최종 답변 생성

**기능**:
- LiteLLM을 통한 LLM 호출
- Tier 기반 모델 선택 (TierRouter)
- 마크다운 형식 응답
- 소스 인용 포함
- SSE 스트리밍 지원

**입력**: `search_results`, `reasoning_steps`, `query`, `conversation_history`
**출력**: `answer`, `sources`, `thinking_steps`

### 5. Reflector (품질 검증)

**역할**: 생성된 답변의 품질 검증

**기능**:
- 정확성 평가
- 출처 확인
- 완성도 체크
- 사고 과정 요약

**입력**: `answer`, `sources`, `query`
**출력**: `reflection`, `quality_score`, `thinking_steps`

### 6. RAGRetriever (RAG 검색, 선택적)

**역할**: 특정 콘텐츠 ID 기반 상세 정보 조회

**기능**:
- 콘텐츠 상세 정보 조회
- 전사 텍스트 포함 (선택)
- 컨텍스트 확장

**입력**: `content_ids`
**출력**: `rag_context`, `thinking_steps`

---

## 핵심 기술 요소

### Kiwi 형태소 분석기 (V8.0)

**목적**: 한국어 쿼리에서 핵심 키워드 추출

**특징**:
- Singleton 패턴으로 초기화 1회만
- App 시작 시 Warmup (첫 요청 지연 ~1.8s 제거)
- 명사, 고유명사, 동사, 형용사 추출

**사용 위치**: `intent_parser.py`

```python
from kiwipiepy import Kiwi

_kiwi_instance: Kiwi | None = None

def get_kiwi() -> Kiwi:
    global _kiwi_instance
    if _kiwi_instance is None:
        _kiwi_instance = Kiwi()
    return _kiwi_instance

def warmup_kiwi() -> None:
    kiwi = get_kiwi()
    kiwi.analyze("워밍업 테스트 문장입니다")
```

### RRF (Reciprocal Rank Fusion)

**목적**: 웹 검색과 RAG 검색 결과 통합

**알고리즘**:
```
score(item) = Σ 1/(k + rank)
k = 60 (상수)
```

**예시**:
- 웹 검색 1위: 1/(60+1) = 0.0164
- RAG 검색 1위: 1/(60+1) = 0.0164
- 양쪽 모두 1위인 경우: 0.0164 + 0.0164 = 0.0328

### 대화 히스토리 (Valkey)

**구조**:
```
conversation:{conversation_id} = [
    {"role": "user", "content": "...", "timestamp": "..."},
    {"role": "assistant", "content": "...", "sources": [...], "timestamp": "..."}
]
```

**TTL**: 7일 (604800초)

---

## API 설계 (V8.0)

### AI Chat Endpoint

```python
# POST /api/ai/chat/stream
{
    "query": "string",
    "mode": "auto | search | research | analysis | reasoning",
    "conversation_id": "uuid (optional)",
    "content_ids": [1, 2, 3]  # ANALYSIS 모드용
}

# Response (SSE Stream)
data: {"type": "thinking", "data": {"step": "intent_analysis", "content": "의도 분석 중..."}}
data: {"type": "thinking", "data": {"step": "web_search", "content": "웹 검색 중..."}}
data: {"type": "source", "data": {"title": "...", "url": "...", "snippet": "..."}}
data: {"type": "content", "data": {"content": "답변 텍스트 조각"}}
data: {"type": "done", "data": {"conversation_id": "...", "total_time": 3.45}}
```

### Conversation History

```python
# GET /api/ai/conversations/{conversation_id}
{
    "conversation_id": "uuid",
    "messages": [
        {"role": "user", "content": "...", "timestamp": "..."},
        {"role": "assistant", "content": "...", "sources": [...], "timestamp": "..."}
    ],
    "created_at": "ISO 8601",
    "updated_at": "ISO 8601"
}

# GET /api/ai/conversations (목록)
# DELETE /api/ai/conversations/{conversation_id}
```

---

## 분산 추적 (OpenTelemetry)

### 트레이스 흐름 (V8.0 확장)

```
Frontend (asr-frontend)
    │ traceparent 헤더
    ▼
Nginx → Backend (asr-backend)
            │ LangGraph Span
            ├─ IntentParser Span
            ├─ Searcher Span
            ├─ Reasoner Span
            ├─ Generator Span
            │   │ Redis Stream + traceparent
            │   ▼
            │  Provider Manager
            │   │ HTTP + traceparent
            │   ▼
            │  GPU/NPU Server
            └─ Reflector Span
                │
                ▼
            Jaeger (수집)
```

### OTLP 엔드포인트

| 서비스 | 환경변수 | 엔드포인트 |
|--------|---------|-----------|
| backend | OTEL_EXPORTER_OTLP_ENDPOINT | http://jaeger:4317 |
| worker | OTEL_EXPORTER_OTLP_ENDPOINT | http://jaeger:4317 |
| litellm | OTEL_EXPORTER_OTLP_ENDPOINT | http://jaeger:4317 |
| provider-manager | OTEL_EXPORTER_OTLP_ENDPOINT | http://localhost:4317 |
| frontend | /otlp/v1/traces | Nginx → Jaeger:4318 |

---

## 관측성 및 복구 기능

### Backend

| 컴포넌트 | 파일 | 역할 |
|---------|------|------|
| **StateWatchdog** | state_watchdog.py | 타임아웃 기반 STUCK 상태 감지 |
| **StateReconciler** | state_reconciler.py | DB 상태 일관성 복구 |
| **WatchdogScheduler** | watchdog_scheduler.py | 주기적 상태 검증 |
| **AdminController** | admin_controller.py | 시스템 관리 API |

### Provider Manager

| 컴포넌트 | 파일 | 역할 |
|---------|------|------|
| **IdleManager** | idle_manager.py | On-Demand 프로바이더 Idle Timeout |
| **LogRotator** | log_rotator.py | 로그 파일 자동 로테이션 |
| **Telemetry** | telemetry.py | OpenTelemetry trace 전파 |
| **JobTracker** | job_tracker.py | 작업 진행 상태 추적 (Redis Hash) |
| **StreamProcessor** | stream_processor.py | **Tier → Model 매핑 (V8.0)** |

---

## Redis/Valkey 인터페이스

### Streams

| Stream | 용도 |
|--------|------|
| `stream:gpu:requests` | GPU 작업 요청 (Worker/LiteLLM → Provider Manager) |
| `stream:gpu:responses` | GPU 작업 결과 (Provider Manager → Worker/LiteLLM) |
| `stream:provider:requests` | 제어 요청 (상태 조회, 재시작) |
| `stream:provider:responses` | 제어 응답 |
| `stream:provider:events` | 상태 변경 이벤트 |

### Keys

| Key | 용도 | V8.0 추가 |
|-----|------|-----------|
| `providers:status` | 프로바이더 실시간 상태 | |
| `providers:jobs` | 프로바이더별 활성 작업 수 | |
| `job:{id}` | 개별 작업 상세 정보 | |
| `conversation:{id}` | 대화 히스토리 | ✅ |
| `ai:search:cache:{hash}` | 검색 결과 캐시 | ✅ |

---

## 성능 최적화 (V8.0)

### Kiwi Warmup

**문제**: 첫 요청 시 Kiwi 초기화 + 첫 분석 = ~1.8초 지연

**해결**: App 시작 시 warmup 수행 (main.py lifespan)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Kiwi 형태소 분석기 워밍업
    try:
        from .agents.nodes.intent_parser import warmup_kiwi
        warmup_kiwi()
    except Exception as e:
        logger.warning(f"[Lifespan] Kiwi warmup failed: {e}")

    yield
```

### 임베딩 라우팅 비활성화

**문제**: Docker 환경에서 localhost:11435 접근 불가로 10초 timeout

**해결**: 규칙 기반 라우팅으로 폴백 (model_router.py)

```python
# 3. 임베딩 기반 유사도 매칭 (현재 비활성화 - Docker 환경에서 localhost 접근 불가)
# TODO: 임베딩 서버를 Docker 네트워크로 노출하거나 설정으로 URL 변경
# try:
#     selected = await self._embedding_based_routing(query)
#     if selected:
#         return selected
# except Exception as e:
#     logger.warning(f"[TierRouter] Embedding routing failed: {e}, using rule-based fallback")

# 4. 규칙 기반 폴백
return self._rule_based_routing(query)
```

### 검색 캐싱

**구현**: Valkey 캐시 (TTL 1시간)

```python
SEARCH_CACHE_PREFIX = "ai:search:cache:"
SEARCH_CACHE_TTL = 3600  # 1시간

cache_key = _generate_cache_key(query, categories, language)
cached = await redis.get(cache_key)
if cached:
    return json.loads(cached)
```

---

## 로드맵

| 버전 | 상태 | 계획 |
|------|------|------|
| V7.4 | ✅ 완료 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager, audio_gateway 제거 |
| V7.5 | ✅ 완료 | Redis -> Valkey 마이그레이션 (라이선스/성능 대응) |
| V7.6 | ✅ 완료 | FLM Thinking Model 지원, Client UI 전면 개편 |
| V8.0 | ✅ 완료 | **LangGraph 기반 AI Agent 시스템, Multi-Agent Workflow, Tier 기반 라우팅** |
| V8.1 | 예정 | LlamaIndex RAG (pgvector), Vector Store, 시맨틱 검색 |
| V9.0 | 예정 | Temporal 워크플로우 엔진, 멀티 호스트 지원 |

---

## 기술 스택 (V8.0)

| 구성요소 | 기술 |
|----------|------|
| **AI Agent Framework** | **LangGraph** |
| **형태소 분석** | **kiwipiepy (Kiwi)** |
| **웹 검색** | **SearXNG** |
| **RAG 검색** | **PostgreSQL (ILIKE, 추후 pgvector)** |
| LLM Backend | LiteLLM (prometheus-router) |
| Message Queue | Redis Stream |
| Cache | Valkey (Redis 호환) |
| Database | PostgreSQL |
| Storage | MinIO (S3 호환) |
| Tracing | OpenTelemetry + Jaeger |
| Metrics | Prometheus + Grafana |
| Process Manager | PM2 (Host) |

---

## 주요 개선 사항 (V8.0)

### 1. LangGraph 기반 Multi-Agent 시스템
- 의도 분석 → 검색 → 추론 → 생성 → 검증의 체계적인 워크플로우
- 각 노드의 역할 명확화 및 재사용 가능한 구조

### 2. 4가지 AI 모드
- SEARCH (웹 검색), RESEARCH (웹+RAG), ANALYSIS (RAG), REASONING (추론)
- 사용자 의도에 맞는 최적화된 워크플로우 제공

### 3. Tier 기반 모델 라우팅
- Backend: WHAT (능력 티어 결정)
- Infrastructure: HOW (모델 선택)
- 관심사의 분리로 확장성 및 유지보수성 향상

### 4. 웹 검색(SearXNG) 통합
- 메타 검색 엔진으로 다양한 검색 소스 통합
- 캐싱 지원으로 응답 속도 개선

### 5. RRF 기반 결과 통합
- 웹 검색과 RAG 검색 결과를 공정하게 통합
- Reciprocal Rank Fusion 알고리즘 적용

### 6. 사고 과정 시각화
- 각 노드의 실행 과정을 사용자에게 실시간 표시
- 답변 생성 시작 시 자동 접힘 UX

### 7. 성능 최적화
- Kiwi warmup으로 첫 요청 지연 제거 (~1.8s)
- 임베딩 라우팅 비활성화로 timeout 방지 (10s)
- 검색 결과 캐싱 (1시간 TTL)
