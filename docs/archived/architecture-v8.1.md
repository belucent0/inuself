# Architecture V8.1

> Observability 고도화 + LLM Semantic Observability + Span Link 패턴

## V8.1 업데이트 요약 (2026-02-05)

이번 업데이트에서 추가된 주요 기능:

| 기능 | 설명 |
|------|------|
| **Langfuse LLM Observability** | LLM 호출의 프롬프트/응답/비용 추적 (Semantic Layer) |
| **OpenLLMetry** | traceloop-sdk 기반 LLM 자동 계측 |
| **Span Link 패턴** | 비동기 작업(StreamConsumer, AI Chat)을 원본 요청과 연결 |
| **노이즈 필터링** | urllib3/Redis instrumentation 비활성화로 orphan trace 제거 |

## 버전 히스토리

| 버전 | 변경 내용 |
|------|----------|
| V6.6 | Redis Stream 기반 메시징 (Docker Desktop 크래시 해결) |
| V7.0 | Provider Manager 통합 프로세스 관리 |
| V7.3 | Provider Manager 패키지 구조화 + Consumer Group 자동 복구 |
| V7.4 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager, audio_gateway 제거 |
| V7.5 | Redis -> Valkey 마이그레이션 (라이선스/성능 이슈 대응) |
| V7.6 | FLM Thinking Model 지원, Client AI 모드 UI 전면 개편 |
| V8.0 | LangGraph 기반 AI Agent 시스템, Multi-Agent Workflow, Tier 기반 모델 라우팅 |
| **V8.1** | **Observability 고도화, Langfuse LLM Observability, Span Link 패턴** |

---

## V8.1 주요 변경 사항

### 1. Tempo 기반 트레이스 저장소

**변경**: Jaeger → Grafana Tempo

| 항목 | Jaeger (V8.0) | Tempo (V8.1) |
|------|--------------|--------------|
| 저장소 | In-memory | S3 호환 (MinIO) |
| 보관 기간 | 휘발성 | 장기 보관 가능 |
| Grafana 연동 | 외부 링크 | 네이티브 통합 |
| 리소스 | 메모리 집약 | 효율적 |

```yaml
# docker-compose.yml
tempo:
  image: grafana/tempo:latest
  ports:
    - "3200:3200"   # tempo query
    - "4317:4317"   # OTLP gRPC
    - "4318:4318"   # OTLP HTTP
  volumes:
    - ./tempo/tempo.yaml:/etc/tempo.yaml
    - tempo-data:/var/tempo
```

### 2. End-to-End Trace Context 전파

**문제**: Worker에서 Backend로 Redis Stream을 통해 결과 전송 시 trace_id가 00_00으로 끊김

**해결**: traceparent 헤더 주입/복원 패턴

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    End-to-End Trace Context 전파 (V8.1)                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Frontend                                                                   │
│     │ traceparent: 00-{trace_id}-{span_id}-01                              │
│     ▼                                                                       │
│  Nginx ──────────────────────────────────────────────────────────────┐      │
│     │ proxy_set_header traceparent $http_traceparent                 │      │
│     ▼                                                                │      │
│  Backend (FastAPI)                                                   │      │
│     │ OpenTelemetryMiddleware                                        │      │
│     │ trace_id = f12af25b42e7740e962ce86450949b63                   │      │
│     ▼                                                                │      │
│  Celery Task (.apply_async)                                          │      │
│     │ Redis Stream + traceparent in message headers                  │      │
│     ▼                                                                │      │
│  Worker (Celery)                                                     │      │
│     │ ✅ 동일 trace_id 유지                                          │      │
│     │                                                                │      │
│     ├─► ASR/OCR 처리                                                │      │
│     │                                                                │      │
│     └─► result_publisher.py                                         │      │
│           │ inject_trace_context(carrier)                            │      │
│           │ data["traceparent"] = carrier["traceparent"]             │      │
│           ▼                                                          │      │
│        Redis Stream (stream:worker:results)                          │      │
│           │ {"type": "asr", "traceparent": "00-{trace_id}-..."}     │      │
│           ▼                                                          │      │
│  Backend StreamConsumer                                              │      │
│     │ ctx = extract_trace_context({"traceparent": ...})             │      │
│     │ otel_context.attach(ctx)                                       │      │
│     │ ✅ trace_id 복원: f12af25b42e7740e962ce86450949b63            │      │
│     ▼                                                                │      │
│  DB 업데이트 + WebSocket 알림                                         │      │
│     │                                                                │      │
│     ▼                                                                │      │
│  Tempo (수집) ◄─────────────────────────────────────────────────────┘      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3. run_in_executor 글로벌 패치

**문제**: `asyncio.run_in_executor()`로 실행되는 동기 함수에서 OTEL 컨텍스트 손실

**해결**: 글로벌 패치로 자동 컨텍스트 전파

```python
# backend/app/core/telemetry.py

def patch_run_in_executor():
    """asyncio.AbstractEventLoop.run_in_executor를 패치하여 OTEL 컨텍스트 자동 전파."""
    global _original_run_in_executor
    import asyncio
    from opentelemetry import context as otel_context

    if _original_run_in_executor is not None:
        return  # 이미 패치됨

    _original_run_in_executor = asyncio.AbstractEventLoop.run_in_executor

    def patched_run_in_executor(self, executor, func, *args):
        current_ctx = otel_context.get_current()

        @functools.wraps(func)
        def wrapper():
            token = otel_context.attach(current_ctx)
            try:
                return func(*args) if args else func()
            finally:
                otel_context.detach(token)

        return _original_run_in_executor(self, executor, wrapper)

    asyncio.AbstractEventLoop.run_in_executor = patched_run_in_executor
```

**적용 위치**: `setup_telemetry()` 함수 내에서 자동 호출

**효과**: 모든 `run_in_executor` 호출에서 trace_id 유지
- LLM 요약 (`extract_metadata`, `generate_core_summary`)
- 동기 라이브러리 호출
- CPU-bound 작업

### 4. State Machine 중앙화

**변경**: 콘텐츠 상태 관리 로직을 중앙화하여 일관성 및 유지보수성 향상

```python
# backend/app/services/state_machine.py

class ContentStateMachine:
    """콘텐츠 처리 상태 머신.

    상태 전이 규칙:
    - pending → processing (작업 시작)
    - processing → completed (정상 완료)
    - processing → failed (오류 발생)
    - failed → processing (재시도, validate=False)
    - completed → processing (재처리)
    """

    VALID_TRANSITIONS = {
        "pending": ["processing"],
        "processing": ["completed", "failed"],
        "failed": ["processing"],      # 재시도 허용
        "completed": ["processing"],   # 재처리 허용
    }

    @classmethod
    def can_transition(cls, from_state: str, to_state: str) -> bool:
        return to_state in cls.VALID_TRANSITIONS.get(from_state, [])

    @classmethod
    def transition(cls, content: Content, to_state: str, validate: bool = True) -> bool:
        if validate and not cls.can_transition(content.status, to_state):
            raise InvalidStateTransitionError(
                f"Cannot transition from {content.status} to {to_state}"
            )
        content.status = to_state
        return True
```

**상태 다이어그램**:

```
                    ┌─────────────────────────────────────┐
                    │                                     │
                    ▼                                     │
              ┌──────────┐                                │
              │ pending  │                                │
              └────┬─────┘                                │
                   │ start()                              │
                   ▼                                      │
              ┌──────────┐     fail()     ┌──────────┐   │
              │processing│───────────────▶│  failed  │   │
              └────┬─────┘                └────┬─────┘   │
                   │ complete()                │         │
                   ▼                           │ retry() │
              ┌──────────┐                     │         │
              │completed │─────────────────────┴─────────┘
              └──────────┘     reprocess()
```

### 5. 동적 Logging 포맷

**문제**: `from loguru import logger` 직접 import 시 `KeyError: 'trace_id'` 발생

**해결**: 동적 포맷 함수로 런타임에 trace_id 조회

```python
# backend/app/core/logging.py

def get_trace_id_safe() -> str:
    """OpenTelemetry trace_id를 안전하게 가져옴. 없으면 00_00 반환."""
    try:
        from app.core.telemetry import get_trace_id
        trace_id = get_trace_id()
        if trace_id:
            return trace_id
    except Exception:
        pass
    return "00_00"

def format_with_trace_id(record):
    """trace_id를 포함한 로그 포맷 생성."""
    trace_id = get_trace_id_safe()
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        f"trace_id={trace_id} | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>\n"
    )

logger.add(sys.stderr, format=format_with_trace_id, level="INFO", ...)
```

**이점**:
- 어떤 파일에서 `from loguru import logger`를 사용해도 동작
- 싱글톤 logger 인스턴스 공유
- 런타임에 현재 trace context에서 trace_id 조회

### 6. Langfuse LLM Semantic Observability

**목적**: OpenTelemetry는 기술적 trace(지연시간, 에러)를 제공하지만, LLM의 **프롬프트/응답 전문, 토큰 사용량, 비용**은 추적하지 않음. Langfuse로 Semantic Layer 추가.

**아키텍처**:
```
┌─────────────────────────────────────────────────────────────────────┐
│                    Observability Stack (V8.1)                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────┐     ┌─────────────────────┐               │
│  │   OpenTelemetry     │     │     Langfuse        │               │
│  │   (Tempo)           │     │     (Self-hosted)   │               │
│  │                     │     │                     │               │
│  │  • 지연시간         │     │  • 프롬프트 전문    │               │
│  │  • 에러 추적        │     │  • 응답 전문        │               │
│  │  • 서비스 의존성    │     │  • 토큰 사용량      │               │
│  │  • Span 계층        │     │  • 비용 추적        │               │
│  │                     │     │  • 품질 평가        │               │
│  └─────────────────────┘     └─────────────────────┘               │
│           │                           │                             │
│           └───────────┬───────────────┘                             │
│                       ▼                                             │
│              ┌─────────────────┐                                    │
│              │    Grafana      │                                    │
│              │  (통합 대시보드) │                                    │
│              └─────────────────┘                                    │
└─────────────────────────────────────────────────────────────────────┘
```

**설정**:
```yaml
# docker-compose.yml
langfuse:
  image: langfuse/langfuse:2
  environment:
    - DATABASE_URL=postgresql://...
    - LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-torch-dev
    - LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-torch-dev
  ports:
    - "3001:3000"

backend:
  environment:
    - LANGFUSE_HOST=http://langfuse:3000/langfuse
    - LANGFUSE_PUBLIC_KEY=pk-lf-torch-dev
    - LANGFUSE_SECRET_KEY=sk-lf-torch-dev
```

**사용 예시 (AI Chat)**:
```python
# backend/app/agents/graph.py
from ..core.langfuse import get_langfuse_client

langfuse_client = get_langfuse_client()
if langfuse_client:
    langfuse_trace = langfuse_client.trace(
        name="ai-chat-stream",
        user_id=user_id,
        session_id=conversation_id,
        input={"query": query, "mode": mode},
        tags=["ai-chat-mode", "streaming"],
    )
    # 하위 span 생성
    intent_span = langfuse_trace.span(name="intent_parser", input={"query": query})
    generation_span = langfuse_trace.span(name="llm_generation", ...)
```

### 7. OpenLLMetry (LLM 자동 계측)

**목적**: LangChain, LangGraph, OpenAI 등 LLM 라이브러리 호출을 **자동으로 계측**

**구성**:
```python
# backend/app/core/telemetry.py

def _init_openllmetry(service_name: str) -> bool:
    from traceloop.sdk import Traceloop

    Traceloop.init(
        app_name=service_name,
        disable_batch=True,  # 기존 OTEL exporter 사용
    )

    # 노이즈 제거: Redis/urllib3 instrumentation 비활성화
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    RedisInstrumentor().uninstrument()

    from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
    URLLib3Instrumentor().uninstrument()
```

**자동 계측 대상**:
| 라이브러리 | 계측 내용 |
|-----------|----------|
| OpenAI | chat.completions, embeddings |
| LangChain | chains, agents, tools |
| LangGraph | nodes, edges, state |
| LiteLLM | completion, embedding |

### 8. Span Link 패턴 (비동기 작업 연결)

**문제**: 비동기 작업(Redis Stream Consumer, AI Chat Streaming)이 독립적인 trace로 생성되어 원본 요청과 연결이 끊김

**해결**: OpenTelemetry **Span Link**로 독립 trace를 원본 요청에 연결

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Span Link 패턴 (V8.1)                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  [Trace A: HTTP 요청]                                               │
│     │                                                                │
│     └─► POST /api/files/upload                                      │
│           └─► enqueue_asr_task                                      │
│                 │                                                    │
│                 │ (traceparent 전달)                                │
│                 ▼                                                    │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ [Trace B: Background Processing]                              │   │
│  │    ◄───── Span Link ─────►  Trace A                          │   │
│  │                                                               │   │
│  │    stream_consumer.process_result                             │   │
│  │      └─► post_processing                                      │   │
│  │      └─► db_update                                            │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Tempo UI에서 "Related Traces"로 연결 확인 가능                      │
└─────────────────────────────────────────────────────────────────────┘
```

**구현 (StreamConsumer)**:
```python
# backend/app/services/stream_consumer.py
from opentelemetry.trace import Link

def _restore_trace_context(self, message: dict):
    traceparent = message.get("traceparent")
    carrier = {"traceparent": traceparent}
    parent_ctx = extract_trace_context(carrier)
    parent_span_ctx = trace.get_current_span(parent_ctx).get_span_context()

    # Span Link로 연결된 독립 trace 생성
    links = [Link(parent_span_ctx)] if parent_span_ctx.is_valid else []

    with tracer.start_as_current_span(
        "stream_consumer.process_result",
        kind=trace.SpanKind.CONSUMER,
        links=links,  # 원본 trace와 링크!
    ) as span:
        # 검색 편의를 위한 attribute
        span.set_attribute("link.trace_id", format(parent_span_ctx.trace_id, '032x'))
        yield
```

**구현 (AI Chat)**:
```python
# backend/app/agents/graph.py
from opentelemetry.trace import Link, get_current_span

# FastAPI 요청의 span context 추출
parent_span = get_current_span()
parent_span_ctx = parent_span.get_span_context()

# Span Link 생성
links = [Link(parent_span_ctx)] if parent_span_ctx.is_valid else []

# 독립 trace 시작 (Span Link로 연결)
otel_span = tracer.start_span(
    "ai-chat-stream",
    kind=SpanKind.INTERNAL,
    links=links,
    attributes={"ai.query": query[:200], "ai.mode": mode}
)
```

### 9. 노이즈 필터링 (Orphan Trace 제거)

**문제**: 내부 HTTP 호출(MinIO health check)과 Redis 폴링(XREADGROUP)이 orphan trace로 Tempo에 노이즈 생성

**해결**: 해당 instrumentation 비활성화

```python
# backend/app/core/telemetry.py

def _init_openllmetry(service_name: str):
    Traceloop.init(...)

    # Redis instrumentation 비활성화 (XREADGROUP 노이즈)
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    redis_instrumentor = RedisInstrumentor()
    if redis_instrumentor.is_instrumented_by_opentelemetry:
        redis_instrumentor.uninstrument()

    # urllib3 instrumentation 비활성화 (MinIO health check 노이즈)
    from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
    urllib3_instrumentor = URLLib3Instrumentor()
    if urllib3_instrumentor.is_instrumented_by_opentelemetry:
        urllib3_instrumentor.uninstrument()
```

**제거된 노이즈 trace**:
| 이전 | 설명 | 처리 |
|------|------|------|
| XREADGROUP | Redis Stream 폴링 | Redis uninstrument |
| GET http://minio:9000 | MinIO health check | urllib3 uninstrument |
| connect | 내부 TCP 연결 | urllib3 uninstrument |

### 10. Observability 통합 UI

**변경**: 사이드바 메뉴 통합 + 탭 기반 대시보드 뷰어

```
기존 (V8.0):                    변경 후 (V8.1):
├── 모니터링 → /monitoring       ├── Observability → /logs
├── 분산 추적 → /tracing              │
└── 로그 → /logs                      └─ 탭: [로그] [트레이스] [메트릭]
```

**구현**:

```tsx
// client/app/logs/page.tsx
const DASHBOARD_TABS = [
  {
    id: 'logs',
    label: '로그',
    path: '/grafana/d/docker-logs/docker-logs?kiosk=true&orgId=1&from=now-15m&to=now&refresh=5s'
  },
  {
    id: 'traces',
    label: '트레이스',
    path: '/grafana/d/tempo/tempo-search?kiosk=true&orgId=1'  // Tempo 대시보드
  },
  {
    id: 'metrics',
    label: '메트릭',
    path: '/grafana/d/windows-metrics/windows-system-metrics?kiosk=true&orgId=1&from=now-15m&to=now&refresh=5s'
  },
];
```

---

## 개괄 아키텍처 (V8.1)

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
│  │                      관 측 성   스 택  (V8.1)                         │      │
│  │  ┌────────┐  ┌────────────┐  ┌──────────┐  ┌──────────┐             │      │
│  │  │ Tempo  │  │ Prometheus │  │ Grafana  │  │  Flower  │             │      │
│  │  │ :3200  │  │   :9090    │  │  :3002   │  │  :5555   │             │      │
│  │  │ :4317  │  │            │  │          │  │          │             │      │
│  │  └────────┘  └────────────┘  └──────────┘  └──────────┘             │      │
│  └─────────────────────────────────────────────────────────────────────┘      │
│                                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐                    │
│  │  Nginx   │   │  MinIO   │   │PostgreSQL│   │ SearXNG  │                    │
│  │   :80    │   │  :9000   │   │  :5432   │   │  :8080   │                    │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘                    │
│                                                                                 │
└───────────────────────────────────────────────────────────────────────────────┘
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

## 분산 추적 (V8.1)

### 컴포넌트별 trace 설정

| 컴포넌트 | 역할 | trace 전파 방식 |
|---------|------|----------------|
| **Frontend** | trace 시작 | W3C Trace Context 헤더 생성 |
| **Nginx** | 헤더 전달 | `proxy_set_header traceparent $http_traceparent` |
| **Backend** | trace 수신/생성 | OpenTelemetryMiddleware, 자체 span 생성 |
| **Celery** | trace 전파 | `CeleryInstrumentor` (자동 헤더 전파) |
| **Worker** | trace 유지 | 작업 결과에 traceparent 주입 |
| **StreamConsumer** | trace 복원 | traceparent에서 context 복원 |
| **LiteLLM** | trace 연결 | `LiteLLMInstrumentor` |
| **Provider Manager** | trace 전파 | HTTP 요청에 traceparent 추가 |

### OTLP 엔드포인트 (V8.1)

| 서비스 | 환경변수 | 엔드포인트 | 변경 |
|--------|---------|-----------|------|
| backend | OTEL_EXPORTER_OTLP_ENDPOINT | http://tempo:4317 | Jaeger → Tempo |
| worker | OTEL_EXPORTER_OTLP_ENDPOINT | http://tempo:4317 | Jaeger → Tempo |
| litellm | OTEL_EXPORTER_OTLP_ENDPOINT | http://tempo:4317 | Jaeger → Tempo |
| provider-manager | OTEL_EXPORTER_OTLP_ENDPOINT | http://localhost:4317 | 유지 |
| frontend | /otlp/v1/traces | Nginx → Tempo:4318 | Jaeger → Tempo |

### trace context 전파 코드

**Worker (result_publisher.py)**:
```python
from worker.telemetry import inject_trace_context

def _publish_result(data: dict[str, Any]) -> str:
    # traceparent 주입 (Backend에서 trace context 복원용)
    trace_carrier = {}
    inject_trace_context(trace_carrier)
    if trace_carrier.get("traceparent"):
        data["traceparent"] = trace_carrier["traceparent"]

    entry_id = redis.xadd(RESULT_STREAM, {"data": json.dumps(data)})
    return entry_id
```

**Backend (stream_consumer.py)**:
```python
from opentelemetry import context as otel_context
from ..core.telemetry import extract_trace_context, get_tracer

@contextmanager
def _restore_trace_context(self, message: dict[str, Any]):
    traceparent = message.get("traceparent")
    if not traceparent:
        yield
        return

    carrier = {"traceparent": traceparent}
    ctx = extract_trace_context(carrier)
    token = otel_context.attach(ctx)
    try:
        tracer = get_tracer("stream-consumer")
        with tracer.start_as_current_span("stream_consumer.handle_message", context=ctx) as span:
            span.set_attribute("message.type", message.get("type", "unknown"))
            span.set_attribute("file.id", str(message.get("file_id", "")))
            yield
    finally:
        otel_context.detach(token)
```

---

## 서비스 목록 (V8.1)

### Docker Compose 서비스

| 서비스 | 컨테이너명 | 포트 | 역할 | V8.1 변경사항 |
|--------|-----------|------|------|--------------|
| **nginx** | asr-nginx | 80, 8080 | Reverse Proxy | Tempo 프록시 추가 |
| **frontend** | asr-frontend | 3000 | Next.js 웹 UI | Observability 통합 UI |
| **backend** | asr-backend | 8000 | FastAPI 메인 API | **run_in_executor 패치, 동적 로깅** |
| **worker** | asr-worker-unified | - | Celery 워커 | **traceparent 주입** |
| **litellm** | asr-litellm-proxy | 4000 | LLM 라우팅 프록시 | - |
| **valkey** | asr-valkey | 6379 | 캐시 + Celery Broker | - |
| **postgres** | asr-postgres | 5432 | 메인 DB | - |
| **minio** | asr-minio | 9000, 9001 | S3 호환 스토리지 | **Tempo 데이터 저장** |
| **searxng** | asr-searxng | 8080 | 메타 검색 엔진 | - |
| **tempo** | asr-tempo | 3200, 4317, 4318 | 분산 추적 저장소 | **신규 (Jaeger 대체)** |
| **prometheus** | asr-prometheus | 9090 | 메트릭 수집 | - |
| **grafana** | asr-grafana | 3002 | 대시보드 | **Tempo 데이터소스 추가** |
| **flower** | asr-flower | 5555 | Celery 모니터링 | - |

---

## Redis/Valkey 인터페이스 (V8.1)

### Streams

| Stream | 용도 | V8.1 변경 |
|--------|------|----------|
| `stream:gpu:requests` | GPU 작업 요청 | - |
| `stream:gpu:responses` | GPU 작업 결과 | - |
| `stream:provider:requests` | 제어 요청 | - |
| `stream:provider:responses` | 제어 응답 | - |
| `stream:provider:events` | 상태 변경 이벤트 | - |
| `stream:worker:results` | Worker → Backend 결과 | **traceparent 포함** |

### Message Format (stream:worker:results)

```json
{
  "type": "asr",
  "event": "completed",
  "file_id": 123,
  "result_s3_key": "results/asr/123.json",
  "duration_seconds": 120.5,
  "num_speakers": 2,
  "speaker_labels": ["SPEAKER_00", "SPEAKER_01"],
  "timestamp": "2024-01-15T10:30:00.000Z",
  "traceparent": "00-f12af25b42e7740e962ce86450949b63-abc123def456-01"
}
```

---

## 관측성 및 복구 기능 (V8.1)

### Backend

| 컴포넌트 | 파일 | 역할 | V8.1 변경 |
|---------|------|------|----------|
| **StateWatchdog** | state_watchdog.py | STUCK 상태 감지 | - |
| **StateReconciler** | state_reconciler.py | DB 상태 복구 | - |
| **StateMachine** | state_machine.py | 상태 전이 중앙화 | **신규** |
| **StreamConsumer** | stream_consumer.py | Worker 결과 수신 | **trace context 복원** |
| **Telemetry** | telemetry.py | OTEL 설정 | **run_in_executor 패치** |
| **Logging** | logging.py | 로그 포맷 | **동적 trace_id 포맷** |

### 핵심 함수 (telemetry.py)

| 함수 | 역할 |
|-----|------|
| `setup_telemetry()` | OTEL 초기화 + 계측 + 패치 |
| `patch_run_in_executor()` | run_in_executor 글로벌 패치 |
| `get_trace_id()` | 현재 trace_id 반환 |
| `inject_trace_context(carrier)` | traceparent 주입 |
| `extract_trace_context(carrier)` | traceparent에서 context 복원 |
| `get_tracer(name)` | named tracer 반환 |

---

## Tempo 설정

### tempo.yaml

```yaml
server:
  http_listen_port: 3200

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318

storage:
  trace:
    backend: s3
    s3:
      bucket: tempo
      endpoint: minio:9000
      access_key: ${MINIO_ACCESS_KEY}
      secret_key: ${MINIO_SECRET_KEY}
      insecure: true

compactor:
  compaction:
    block_retention: 168h  # 7일
```

### Grafana Tempo 데이터소스

```yaml
datasources:
  - name: Tempo
    type: tempo
    access: proxy
    url: http://tempo:3200
    jsonData:
      httpMethod: GET
      tracesToLogs:
        datasourceUid: loki
        tags: ['service.name']
      serviceMap:
        datasourceUid: prometheus
```

---

## 로드맵

| 버전 | 상태 | 계획 |
|------|------|------|
| V7.4 | ✅ 완료 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager |
| V7.5 | ✅ 완료 | Redis -> Valkey 마이그레이션 |
| V7.6 | ✅ 완료 | FLM Thinking Model 지원, Client UI 전면 개편 |
| V8.0 | ✅ 완료 | LangGraph AI Agent, Multi-Agent Workflow, Tier 라우팅 |
| **V8.1** | ✅ **완료** | **Observability 고도화, State Machine 중앙화, End-to-End Trace** |
| V8.2 | 예정 | LlamaIndex RAG (pgvector), Vector Store, 시맨틱 검색 |
| V9.0 | 예정 | Temporal 워크플로우 엔진, 멀티 호스트 지원 |

---

## 기술 스택 (V8.1)

| 구성요소 | 기술 | V8.1 변경 |
|----------|------|----------|
| AI Agent Framework | LangGraph | - |
| 형태소 분석 | kiwipiepy (Kiwi) | - |
| 웹 검색 | SearXNG | - |
| RAG 검색 | PostgreSQL (ILIKE) | - |
| LLM Backend | LiteLLM | - |
| Message Queue | Redis Stream | - |
| Cache | Valkey | - |
| Database | PostgreSQL | - |
| Storage | MinIO (S3 호환) | - |
| **Tracing** | **Grafana Tempo** | Jaeger → Tempo |
| Metrics | Prometheus + Grafana | - |
| Process Manager | PM2 (Host) | - |

---

## V8.1 상세 변경 사항 요약

### 1. Observability 고도화
- Jaeger → Grafana Tempo 전환 (S3 백엔드, 장기 보관)
- Grafana 네이티브 Tempo 통합
- Observability 통합 UI (탭 기반)

### 2. End-to-End Trace 전파
- Worker → Backend Redis Stream traceparent 주입/복원
- run_in_executor 글로벌 패치로 동기 함수 trace 유지
- 동적 로깅 포맷으로 모든 로그에 trace_id 포함

### 3. State Machine 중앙화
- ContentStateMachine 클래스로 상태 전이 규칙 중앙화
- 터미널 상태(failed, completed)에서도 재시도 가능 (validate=False)
- 일관된 상태 관리 API

### 4. 코드 품질 개선
- telemetry.py: 모듈화된 계측 함수
- logging.py: KeyError 방지 동적 포맷
- stream_consumer.py: context manager 기반 trace 복원
