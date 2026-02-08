# Architecture V8.2

> LLM Semantic Observability + Span Link 패턴 + 노이즈 필터링

## V8.2 업데이트 요약 (2026-02-05)

### 왜 V8.2로 버전업했는가?

V8.1에서 Tempo 기반 분산 추적을 구축했으나, **LLM 호출의 의미론적 관측**이 누락되었습니다.
OpenTelemetry는 지연시간/에러를 추적하지만, **프롬프트 전문, 응답 내용, 토큰 사용량, 비용**은 추적하지 않습니다.

V8.2에서는 **Langfuse**를 도입하여 LLM Semantic Observability를 완성했습니다.

### Opik 도입 시도 및 포기 이유

**Opik**(Comet ML)을 먼저 검토했으나 다음 이유로 포기:

| 항목 | Opik | Langfuse | 결정 |
|------|------|----------|------|
| **Self-hosted 안정성** | ClickHouse 의존, 복잡한 설정 | PostgreSQL만 필요, 단순 | Langfuse |
| **리소스 요구사항** | ClickHouse + Redis + MySQL | PostgreSQL 1개 | Langfuse |
| **Docker Compose 지원** | 공식 지원 미흡, 수동 구성 필요 | 공식 docker-compose 제공 | Langfuse |
| **SDK 성숙도** | 신규, API 변경 빈번 | 안정적인 v2 API | Langfuse |
| **문서화** | Self-hosted 문서 부족 | 상세한 Self-hosted 가이드 | Langfuse |

**결론**: Opik은 강력하지만 Self-hosted 환경에서 운영 부담이 큼. Langfuse가 경량화된 대안으로 적합.

### V8.2 주요 변경 사항

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
| V8.1 | Tempo 기반 트레이스 저장소, End-to-End Trace 전파, State Machine 중앙화 |
| **V8.2** | **Langfuse LLM Observability, Span Link 패턴, 노이즈 필터링** |

---

## V8.2 주요 변경 사항

### 1. Langfuse LLM Semantic Observability

**목적**: OpenTelemetry는 기술적 trace(지연시간, 에러)를 제공하지만, LLM의 **프롬프트/응답 전문, 토큰 사용량, 비용**은 추적하지 않음. Langfuse로 Semantic Layer 추가.

**아키텍처**:
```
┌─────────────────────────────────────────────────────────────────────┐
│                    Observability Stack (V8.2)                        │
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
    - LANGFUSE_HOST=http://langfuse:3000/langfuse  # base path 포함!
    - LANGFUSE_PUBLIC_KEY=pk-lf-torch-dev
    - LANGFUSE_SECRET_KEY=sk-lf-torch-dev
```

**SDK 버전 제한** (중요):
```toml
# pyproject.toml
"langfuse>=2.0.0,<3.0.0"  # v3 API 호환성 문제로 v2 고정
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

### 2. OpenLLMetry (LLM 자동 계측)

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

### 3. Span Link 패턴 (비동기 작업 연결)

**문제**: 비동기 작업(Redis Stream Consumer, AI Chat Streaming)이 독립적인 trace로 생성되어 원본 요청과 연결이 끊김

**해결**: OpenTelemetry **Span Link**로 독립 trace를 원본 요청에 연결

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Span Link 패턴 (V8.2)                             │
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
from opentelemetry.trace import Link, get_current_span, SpanKind
from opentelemetry import trace as otel_trace, context as otel_context

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

# Context 활성화 (child span 연결)
ctx = otel_trace.set_span_in_context(otel_span)
token = otel_context.attach(ctx)

try:
    # ... AI 처리 ...
finally:
    otel_context.detach(token)
    otel_span.end()
```

### 4. 노이즈 필터링 (Orphan Trace 제거)

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
        logger.info("[OpenLLMetry] Redis instrumentation disabled")

    # urllib3 instrumentation 비활성화 (MinIO health check 노이즈)
    from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
    urllib3_instrumentor = URLLib3Instrumentor()
    if urllib3_instrumentor.is_instrumented_by_opentelemetry:
        urllib3_instrumentor.uninstrument()
        logger.info("[OpenLLMetry] urllib3 instrumentation disabled")
```

**제거된 노이즈 trace**:
| 이전 | 설명 | 처리 |
|------|------|------|
| XREADGROUP | Redis Stream 폴링 | Redis uninstrument |
| GET http://minio:9000 | MinIO health check | urllib3 uninstrument |
| connect | 내부 TCP 연결 | urllib3 uninstrument |
| HEAD | S3 버킷 체크 | urllib3 uninstrument |

---

## 개괄 아키텍처 (V8.2)

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
│  │                      관 측 성   스 택  (V8.2)                         │      │
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
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘                    │
│                                                                                 │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 기술 스택 (V8.2)

| 구성요소 | 기술 | V8.2 변경 |
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
| Tracing | Grafana Tempo | - |
| **LLM Observability** | **Langfuse v2** | **신규** |
| **LLM Auto-Instrumentation** | **OpenLLMetry (traceloop-sdk)** | **신규** |
| Metrics | Prometheus + Grafana | - |
| Process Manager | PM2 (Host) | - |

---

## 로드맵

| 버전 | 상태 | 계획 |
|------|------|------|
| V7.4 | ✅ 완료 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager |
| V7.5 | ✅ 완료 | Redis -> Valkey 마이그레이션 |
| V7.6 | ✅ 완료 | FLM Thinking Model 지원, Client UI 전면 개편 |
| V8.0 | ✅ 완료 | LangGraph AI Agent, Multi-Agent Workflow, Tier 라우팅 |
| V8.1 | ✅ 완료 | Tempo 기반 트레이스 저장소, End-to-End Trace 전파 |
| **V8.2** | ✅ **완료** | **Langfuse LLM Observability, Span Link 패턴** |
| V8.3 | 예정 | LlamaIndex RAG (pgvector), Vector Store, 시맨틱 검색 |
| V9.0 | 예정 | Temporal 워크플로우 엔진, 멀티 호스트 지원 |

---

## V8.2 상세 변경 사항 요약

### 1. LLM Semantic Observability
- Langfuse v2 Self-hosted 도입
- AI Chat 모드에 trace/span 계측 추가
- 프롬프트/응답/토큰 사용량 추적

### 2. LLM 자동 계측
- OpenLLMetry (traceloop-sdk) 통합
- LangGraph, OpenAI, LiteLLM 자동 계측
- 기존 Tempo exporter 활용

### 3. Span Link 패턴
- StreamConsumer: 백그라운드 처리를 원본 요청과 링크
- AI Chat: 스트리밍 처리를 HTTP 요청과 링크
- Tempo UI에서 "Related Traces" 확인 가능

### 4. 노이즈 필터링
- Redis instrumentation 비활성화 (XREADGROUP 폴링)
- urllib3 instrumentation 비활성화 (MinIO health check)
- Orphan trace 제거로 Tempo UI 가독성 향상
