# 검색 재시도 메커니즘: "실패를 인지하고 스스로 복구하는 시스템" 설계

> **프로젝트**: SearXNG + LangGraph 기반 AI 채팅 서비스
> **기간**: 2026년 1월 ~ 2월
> **역할**: 백엔드 아키텍처 설계 및 구현 (단독)

---

## 1. Situation — 어떤 상황이었나

SearXNG 기반 웹 검색과 LangGraph 에이전트를 결합한 AI 채팅 서비스를 개발하고 있었다. 사용자가 질문하면 에이전트가 실시간으로 웹을 검색하고, 그 결과를 바탕으로 LLM이 답변을 생성한다.

서비스의 핵심 가치는 **"최신 정보 기반 신뢰도 높은 답변"**이었다. 그러나 서비스를 운영하면서 이 가치가 흔들리는 상황이 반복됐다.

---

## 2. Problem — 구체적으로 뭐가 문제였나

검색 성공률이 약 **60% 수준**에 머물렀다. 검색에 실패하면 에이전트는 단순히 "정보를 찾을 수 없습니다"를 반환하고 종료됐다.

문제의 본질을 분석했을 때 두 가지 실패 패턴이 있었다:

1. **쿼리 구성 실패**: 너무 구체적이거나 모호한 검색어로 인해 관련 결과가 0개 반환
2. **품질 기준 미달**: 결과는 나왔지만 질문과 관련성이 낮은 노이즈 데이터

이 두 경우 모두 에이전트는 "검색 시도 1회 → 실패 → 종료"의 동일한 경로를 밟았다. **시스템이 자신의 실패를 인지하지 못하고 있었다.**

---

## 3. Attempted Solution — 처음에 어떻게 시도했나

첫 번째 접근은 단순 재시도(retry)였다.

```python
# 초기 시도: 동일 쿼리로 N회 재시도
for attempt in range(MAX_RETRY):
    results = await searx.search(original_query)
    if results:
        break
```

결과는 예상대로였다: **같은 쿼리로 같은 검색을 반복하면 같은 실패가 반복된다.** 네트워크 타임아웃이 원인인 경우를 제외하면, 재시도는 의미가 없었다.

핵심적으로 부족했던 것은 두 가지였다:

- **실패 진단 없음**: "왜 실패했는지"를 시스템이 파악하지 못함
- **전략 변경 없음**: 실패 원인에 따라 다른 쿼리 전략을 사용하지 않음

---

## 4. Final Solution — 최종적으로 어떻게 해결했나

### 설계 원칙: "실패 원인 진단 → 전략 변경 → 재시도"

LangGraph의 **순환 그래프(Cyclic Graph)** 구조를 활용해 적응형 검색 재시도 메커니즘을 설계했다.

```
Searcher → Evaluator → [통과/실패 분기]
                           ↓ 실패
                    QueryRewriter
                           ↓
                       Searcher (재시도)
                           ↓ 최종 실패
                    FallbackHandler
```

### 핵심 컴포넌트

**① SearchEvaluatorNode: 3가지 기준으로 자동 평가 (100점 만점)**

| 기준 | 배점 | 세부 내용 |
|------|------|-----------|
| 결과 개수 | 40점 | 최소 3개 이상이면 만점 |
| 품질 점수 | 40점 | 각 결과의 snippet 길이, 도메인 신뢰도 |
| 관련성 | 20점 | 쿼리 키워드와 결과 제목/snippet 매칭 |

**50점 미만이면 재시도를 트리거한다.**

**② QueryRewriterNode: 실패 원인별 3단계 전략**

```python
def get_rewrite_strategy(retry_count: int, failure_reason: str) -> str:
    strategies = {
        1: "broaden",   # 1차: 검색 범위 확장 (구체적 표현 제거)
        2: "narrow",    # 2차: 핵심 키워드만 (노이즈 제거)
        3: "synonym",   # 3차: 동의어/관련어로 대체
    }
    return strategies.get(retry_count, "fallback")
```

실패 이유를 State에 기록하고, LLM이 해당 전략에 맞는 새로운 쿼리를 생성하게 했다.

**③ FallbackHandlerNode: Graceful Degradation**

최대 3회 재시도 후에도 실패하면, 검색 결과 없이 LLM 내부 지식으로 답변을 생성하되 "검색 기반 정보가 아닐 수 있음"을 사용자에게 명시한다.

**④ 실시간 진행 표시: SSE `search_retry` 이벤트**

재시도 중 사용자에게 진행 상황을 실시간으로 전달했다. 단순히 "로딩 중"이 아니라 "다른 방식으로 재검색 중"임을 알려 UX를 보호했다.

```python
yield {
    "event": "search_retry",
    "data": {
        "retry_count": state["search_retry_count"],
        "reason": state["retry_reason"],
        "new_query": state["rewritten_query"]
    }
}
```

### State 설계: 실패 이력 추적

```python
class SearchRetryState(TypedDict):
    search_retry_count: int          # 현재 재시도 횟수
    search_quality_score: float      # 마지막 평가 점수
    original_search_queries: list    # 최초 쿼리 보존
    failed_queries: list             # 실패한 쿼리 이력
    retry_reason: str                # 실패 원인 분류
```

실패 이력을 State에 축적함으로써 QueryRewriter가 이전에 실패한 방식을 반복하지 않도록 했다.

---

## 5. Result — 결과와 임팩트

| 지표 | Before | After |
|------|--------|-------|
| 검색 성공률 | ~60% | ~85% |
| 빈 결과 반환 빈도 | 높음 | 거의 제거 |
| 최악의 경우 UX | "정보를 찾을 수 없습니다" + 종료 | LLM 지식 기반 답변 + 명시적 안내 |

특히 **"빈 결과로 인한 완전 실패"가 거의 사라진 것**이 핵심 성과다. 사용자가 어떤 질문을 해도 시스템이 何らかの 답변을 제공할 수 있게 됐다.

---

## 6. Lessons Learned — 이 경험에서 배운 것

**"실패하지 않는 시스템"이 아니라 "실패를 인지하고 스스로 복구하는 시스템"의 가치**

LLM 기반 서비스에서 실패는 피할 수 없다. 외부 검색 엔진의 결과, 사용자 쿼리의 다양성, 모델의 비결정성 — 모두 실패 요인이 될 수 있다. 중요한 것은 시스템이 실패를 인식하고 다른 전략으로 전환할 수 있어야 한다는 것이다.

이 프로젝트를 통해 세 가지를 깨달았다:

1. **단순 재시도는 의미 없다**: 같은 입력으로 같은 작업을 반복하면 같은 결과가 나온다. 재시도가 유효하려면 반드시 전략 변경이 함께해야 한다.

2. **실패 진단이 먼저다**: 무엇이 잘못됐는지를 시스템이 스스로 파악해야 올바른 복구 전략을 선택할 수 있다.

3. **UX 보호는 기술적 문제다**: 사용자에게 "다시 검색 중"이라는 피드백을 주는 것은 UX의 문제이지만, 이를 구현하는 것은 SSE 이벤트 설계 같은 기술적 결정이다.

---

## 7. 기술 스택

- **LangGraph**: 순환 그래프로 재시도 루프 구현 (조건부 엣지)
- **SearXNG**: Self-hosted 메타 검색 엔진 (외부 의존성 제거)
- **FastAPI + SSE**: 실시간 재시도 상태 스트리밍
- **Python**: `TypedDict` 기반 State 관리

---

*관련 문서: `docs/v8.4-search-retry-implementation.md`, `docs/search-retry-architecture.md`, `docs/search-quality-tuning.md`*
