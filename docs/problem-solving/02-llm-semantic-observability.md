# LLM Semantic Observability: "HTTP 200인데 왜 사용자는 불만인가"

> **프로젝트**: LLM 기반 문서요약 + AI 채팅 서비스
> **기간**: 2026년 1월 ~ 2월
> **역할**: 인프라 설계 및 구현 (단독)

---

## 1. Situation — 어떤 상황이었나

문서 업로드 요약과 AI 채팅 기능을 제공하는 서비스를 운영하고 있었다. LLM이 핵심 가치를 만들어내는 구조였다: 사용자가 문서를 올리면 LLM이 요약하고, 대화하면 LLM이 검색 결과를 기반으로 답변을 생성한다.

기존 모니터링 스택은 일반적인 웹 서비스 수준이었다:
- **Prometheus**: CPU, 메모리, 응답시간 메트릭
- **Loki + Promtail**: 컨테이너 로그 수집
- **Jaeger + Tempo**: HTTP 요청 분산 추적
- **Grafana**: 전체 대시보드

서버는 안정적으로 운영됐다. 모든 지표가 정상 범위였다.

---

## 2. Problem — 구체적으로 뭐가 문제였나

**"HTTP 200 OK, 응답 시간 < 3초"** — 모든 지표가 녹색인데 사용자 피드백은 달랐다.

- "요약 품질이 낮아요"
- "엉뚱한 내용을 말해요" (환각)
- "같은 질문인데 답변이 매번 달라요"

이 피드백이 왔을 때, 나는 아무것도 할 수 없었다.

APM 도구로 볼 수 있는 것들이었다:
- ✅ 서버가 살아있나?
- ✅ 응답 속도가 빠른가?
- ✅ 에러가 발생했나?

볼 수 **없는** 것들이었다:
- ❌ LLM에 어떤 프롬프트가 들어갔나?
- ❌ LLM이 어떤 응답을 생성했나?
- ❌ 검색 결과 중 어떤 것이 실제로 사용됐나?
- ❌ 이번 요청에 토큰이 얼마나 쓰였나?

전통적인 APM은 **서버의 건강**을 볼 수 있지만, **LLM의 품질**은 볼 수 없었다. AI 서비스에는 새로운 종류의 관측성이 필요했다.

---

## 3. Attempted Solution — 처음에 어떻게 시도했나

첫 번째 시도는 **OpenLLMetry + Grafana** 조합이었다.

OpenLLMetry는 LLM 호출을 자동으로 계측하는 오픈소스 라이브러리다. Grafana와 연동하면 기존 대시보드에 LLM 메트릭을 추가할 수 있었다.

```python
# 초기 시도
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
OpenAIInstrumentor().instrument()
```

셋업은 쉬웠다. 결과도 나쁘지 않아 보였다. Grafana에 LLM 관련 패널이 생겼다.

그런데 실제로 환각 이슈를 디버깅하려고 했을 때 문제가 드러났다:

- **Input/Output 확인 불가**: 어떤 프롬프트가 어떤 응답을 만들었는지 볼 수 없었다
- **단계 추적 불가**: "검색 → 컨텍스트 구성 → 생성" 각 단계의 데이터를 볼 수 없었다
- **비용 불투명**: 토큰 사용량이 집계되지 않았다

한 마디로: **예쁜 대시보드는 있었지만, 실제 디버깅에 필요한 정보가 없었다.** 숫자가 보이는 것처럼 보였지만 의미를 해석할 수 없었다.

---

## 4. Final Solution — 최종적으로 어떻게 해결했나

### 핵심 인사이트: Semantic Observability

기존 APM이 "서버 관점의 관측성"이라면, LLM 서비스에는 **"의미론적 관측성(Semantic Observability)"**이 필요하다는 결론에 도달했다.

의미론적 관측성이란:
- 어떤 의도(Intent)가 입력됐는가
- 어떤 정보를 검색했는가
- LLM이 어떤 컨텍스트로 추론했는가
- 최종적으로 어떤 응답을 생성했는가

이를 구현하기 위해 **Langfuse**를 선택했다.

### Langfuse 선택 이유

| 기준 | 결정 |
|------|------|
| 데이터 보안 | Self-hosted로 사내 인프라에 배포 → 프롬프트/응답 외부 유출 없음 |
| LangGraph 통합 | 공식 Callback 지원 → 기존 코드 수정 최소화 |
| 데이터 구조 | Trace/Span 계층으로 AI 워크플로우 표현에 최적화 |
| 비용 가시성 | 토큰 사용량 및 비용 자동 집계 |

### 비침투적 연동 설계

핵심 원칙은 **기존 비즈니스 로직을 건드리지 않는 것**이었다. LangGraph의 Callback 메커니즘을 활용해 Langfuse 관측 코드를 완전히 분리했다.

```python
# app/core/langfuse.py
from langfuse.callback import CallbackHandler

def get_langfuse_handler(trace_id: str, user_id: str) -> CallbackHandler:
    return CallbackHandler(
        trace_id=trace_id,
        user_id=user_id,
        session_id=session_id,
    )

# 에이전트 실행 시 - 기존 로직 변경 없이 핸들러만 추가
result = await agent.ainvoke(
    input_state,
    config={"callbacks": [get_langfuse_handler(trace_id, user_id)]}
)
```

### Trace/Span 계층 설계

LangGraph의 각 노드가 자동으로 Span으로 기록된다:

```
Trace: user_query="RAG 패턴 설명해줘"
  ├── Span: IntentClassifier (50ms)
  │     Input: 원본 쿼리
  │     Output: intent=SEARCH, mode=HYBRID
  ├── Span: SearchNode (1.2s)
  │     Input: search_queries=["RAG pattern", "Retrieval Augmented Generation"]
  │     Output: 12개 웹 결과
  ├── Span: ReasoningNode (800ms)
  │     Input: 검색 결과 + 사용자 쿼리
  │     Output: reasoning_steps
  └── Span: GenerationNode (2.1s)
        Input: 최종 프롬프트 (전문)
        Output: 생성된 응답 (전문)
        Tokens: input=3,200, output=850
        Cost: $0.012
```

환각이 발생한 경우, `trace_id`로 즉시 해당 요청을 찾아 각 단계의 입출력을 확인할 수 있게 됐다.

### 수집 데이터 명세

| 데이터 | 용도 |
|--------|------|
| Input/Output 전문 | 환각 원인 추적, 프롬프트 품질 평가 |
| 단계별 레이턴시 | 병목 구간 식별 |
| 토큰 수 / 비용 | 비용 최적화, 이상 요청 감지 |
| 검색 결과 메타데이터 | 검색 품질과 응답 품질의 상관관계 분석 |
| User ID + Session ID | 사용자별 패턴 분석 |

---

## 5. Result — 결과와 임팩트

| 지표 | Before | After |
|------|--------|-------|
| 환각 이슈 디버깅 시간 | ~10분 (로그 추적, 재현 시도) | ~30초 (trace_id → Langfuse UI) |
| 프롬프트 개선 방식 | 추측 기반 | 데이터 기반 (실제 실패 케이스 분석) |
| 비용 가시성 | 불투명 (월말 청구서까지 모름) | 요청별 실시간 확인 |
| 신규 개발자 온보딩 | "어떻게 동작하는지 설명" 필요 | Langfuse에서 실제 실행 흐름 확인 |

가장 큰 변화는 **프롬프트 엔지니어링 방식**이다. 이전에는 "아마도 이 표현이 환각을 유발할 것 같다"는 추측으로 프롬프트를 수정했다. 이후에는 실제로 환각이 발생한 요청들의 공통 패턴을 분석해 구체적인 원인을 찾을 수 있게 됐다.

---

## 6. Lessons Learned — 이 경험에서 배운 것

**"APM은 청진기, Langfuse는 통역사"**

전통적인 APM은 서버의 심박수를 측정한다. 살아있는지, 숨을 잘 쉬는지. 그러나 AI 서비스에서 진짜 문제는 서버가 아니라 **서버가 무슨 말을 하고 있는가**에 있다.

Langfuse는 LLM이 생성하는 내용을 읽을 수 있게 해줬다. 통역사처럼.

이 경험에서 세 가지를 배웠다:

1. **AI 서비스는 새로운 종류의 관측성이 필요하다**: HTTP 상태 코드는 LLM 품질을 말해주지 않는다. "기능적 정확성(Functional Correctness)"과 "의미적 품질(Semantic Quality)"은 다른 측정 도구가 필요하다.

2. **Self-hosted 선택이 중요하다**: 프롬프트와 사용자 응답에는 민감한 정보가 포함될 수 있다. 외부 SaaS 관측성 도구를 쓰면 데이터가 외부로 나간다. 처음부터 Self-hosted로 설계해야 한다.

3. **비침투적 설계가 유지보수를 결정한다**: 관측 코드를 비즈니스 로직과 분리하지 않으면, 관측 기능을 끄거나 교체할 때 전체 코드를 수정해야 한다. Callback 패턴으로 완전 분리한 덕분에 필요 시 Langfuse를 다른 도구로 교체할 수 있다.

---

## 7. 기술 스택

- **Langfuse**: LLM Observability 플랫폼 (Self-hosted, Docker Compose)
- **LangGraph Callback**: 비침투적 트레이싱 연동
- **PostgreSQL**: Langfuse 데이터 스토어
- **Prometheus + Loki + Grafana**: 기존 인프라 레이어 (병행 운영)
- **FastAPI**: 백엔드 서비스 (Python)

---

*관련 문서: `docs/plans/llm-observability-langfuse.md`, `docs/observability-v1.0.md`*
