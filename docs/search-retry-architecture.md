# 검색 재시도 아키텍처 설계

**작성일**: 2026-02-06
**목적**: 검색 결과가 불충분하거나 부적합할 때 자동 재시도 메커니즘 구현

---

## 1. 문제 정의

### 현재 상황
```python
# Searcher 노드에서 검색 실패 시
except WebSearchError as e:
    logger.warning(f"[Searcher] Search failed: {e}")
    return {
        "search_results": [],  # 빈 결과 반환
        "error": f"검색 실패: {e}",
    }
```

**문제점**:
- ❌ 검색 실패 시 그냥 빈 결과 반환
- ❌ 재시도 로직 없음
- ❌ 대체 쿼리 생성 없음
- ❌ 사용자에게 무의미한 응답 전달

### 필요한 기능
1. **검색 품질 평가**: 결과가 충분한가? 관련성이 있는가?
2. **재시도 결정**: 재시도가 필요한가?
3. **쿼리 재작성**: 다른 키워드로 다시 검색
4. **최대 재시도 제한**: 무한 루프 방지
5. **폴백 전략**: 모든 시도 실패 시 대안

---

## 2. LangGraph의 루프 메커니즘

### 2.1 조건부 엣지 (Conditional Edges)

LangGraph는 **조건부 엣지**를 통해 동적 라우팅을 지원합니다:

```python
def route_after_search(state: GraphState) -> str:
    """검색 후 다음 노드 결정"""
    search_results = state.get("search_results", [])
    retry_count = state.get("search_retry_count", 0)
    max_retries = 3

    # 1. 충분한 결과가 있으면 → Generator로
    if len(search_results) >= 3:
        return "generator"

    # 2. 재시도 가능하면 → Query Rewriter로
    if retry_count < max_retries:
        return "query_rewriter"

    # 3. 모든 시도 실패 → Fallback Handler로
    return "fallback_handler"

workflow.add_conditional_edges(
    "searcher",
    route_after_search,
    {
        "generator": "generator",
        "query_rewriter": "query_rewriter",
        "fallback_handler": "fallback_handler",
    }
)
```

### 2.2 루프 구조

```
┌─────────────┐
│ IntentParser│
└──────┬──────┘
       ↓
┌──────────────┐
│   Searcher   │ ←─────────┐
└──────┬───────┘           │
       ↓                   │
   [검색 결과 평가]         │
       ↓                   │
   충분? ──NO→ ┌───────────┴────┐
    │          │ Query Rewriter │
   YES         └────────────────┘
    ↓
┌──────────┐
│Generator │
└──────────┘
```

**핵심**: Searcher → Query Rewriter → Searcher 순환

---

## 3. 구현 설계

### 3.1 State 확장

먼저 `GraphState`에 재시도 관련 필드 추가:

```python
# backend/app/agents/state.py

class GraphState(TypedDict):
    # ... 기존 필드들 ...

    # 검색 재시도 관련
    search_retry_count: int              # 현재 재시도 횟수
    search_quality_score: float          # 검색 결과 품질 점수 (0-100)
    original_search_queries: list[str]   # 원본 검색 쿼리 (재작성 참고용)
    failed_queries: list[str]            # 실패한 쿼리 목록 (중복 방지)
```

### 3.2 SearchQualityEvaluator 노드

검색 결과 품질을 평가하는 전용 노드:

```python
# backend/app/agents/nodes/search_evaluator.py

class SearchQualityEvaluator:
    """검색 결과 품질 평가"""

    def evaluate(self, state: GraphState) -> dict:
        """검색 결과 품질 평가

        Returns:
            {
                "search_quality_score": float,  # 0-100
                "needs_retry": bool,
                "retry_reason": str,
            }
        """
        search_results = state.get("search_results", [])
        query = state["query"]
        keywords = state.get("query_analysis", {}).get("keywords", [])

        # 1. 결과 개수 평가
        count_score = min(len(search_results) * 20, 40)  # 최대 40점

        # 2. 품질 점수 평가 (QualityAssessor 결과 사용)
        if search_results:
            avg_quality = sum(r.get("quality_score", 0) for r in search_results) / len(search_results)
            quality_score = avg_quality * 0.4  # 최대 40점
        else:
            quality_score = 0

        # 3. 관련성 평가 (키워드 매칭)
        relevance_score = self._evaluate_relevance(search_results, keywords)  # 최대 20점

        total_score = count_score + quality_score + relevance_score

        # 재시도 필요 판단
        needs_retry = total_score < 50  # 50점 미만이면 재시도

        retry_reason = ""
        if len(search_results) == 0:
            retry_reason = "no_results"
        elif len(search_results) < 3:
            retry_reason = "insufficient_results"
        elif avg_quality < 40:
            retry_reason = "low_quality"
        elif relevance_score < 10:
            retry_reason = "low_relevance"

        return {
            "search_quality_score": total_score,
            "needs_retry": needs_retry,
            "retry_reason": retry_reason,
        }
```

### 3.3 QueryRewriter 노드

검색 결과가 불충분할 때 쿼리를 재작성:

```python
# backend/app/agents/nodes/query_rewriter.py

class QueryRewriter:
    """검색 실패 시 쿼리 재작성"""

    async def __call__(self, state: GraphState) -> dict:
        """실패 원인 기반 쿼리 재작성"""
        query = state["query"]
        retry_count = state.get("search_retry_count", 0)
        retry_reason = state.get("retry_reason", "")
        failed_queries = state.get("failed_queries", [])
        original_queries = state.get("original_search_queries", [])

        # 재작성 전략 선택
        if retry_count == 0:
            # 첫 재시도: 더 일반적인 용어로
            strategy = "broaden"
        elif retry_count == 1:
            # 두 번째 재시도: 더 구체적인 용어로
            strategy = "narrow"
        else:
            # 세 번째 재시도: 동의어/관련어 사용
            strategy = "synonym"

        # LLM으로 쿼리 재작성
        rewrite_prompt = f"""검색 실패 원인: {retry_reason}
원본 질문: {query}
실패한 쿼리들: {failed_queries}

전략: {strategy}
- broaden: 더 넓은 범위의 일반적인 용어 사용
- narrow: 더 구체적이고 특정한 용어 사용
- synonym: 동의어나 관련어 사용

새로운 검색 쿼리 3개를 생성하세요 (한 줄에 하나씩):"""

        response = await async_llm_completion(
            settings=self.settings,
            messages=[{"role": "user", "content": rewrite_prompt}],
            temperature=0.7,  # 다양성 확보
            max_tokens=200,
        )

        # 새 쿼리 추출
        new_queries = [q.strip() for q in response.strip().split('\n') if q.strip()]
        new_queries = [q for q in new_queries if q not in failed_queries][:3]

        logger.info(f"[QueryRewriter] Retry #{retry_count+1}, strategy={strategy}, new_queries={new_queries}")

        return {
            "search_queries": new_queries,
            "search_retry_count": retry_count + 1,
            "failed_queries": failed_queries + state.get("search_queries", []),
        }
```

### 3.4 FallbackHandler 노드

모든 검색 시도가 실패했을 때 대안 제공:

```python
# backend/app/agents/nodes/fallback_handler.py

class FallbackHandler:
    """검색 실패 시 폴백 처리"""

    async def __call__(self, state: GraphState) -> dict:
        """검색 실패 시 대안 제공"""
        query = state["query"]
        failed_queries = state.get("failed_queries", [])

        logger.warning(f"[FallbackHandler] All search attempts failed for: {query}")

        # 옵션 1: RAG로 전환 (내부 문서 검색)
        # 옵션 2: LLM 지식만으로 답변
        # 옵션 3: 사용자에게 검색 실패 안내

        fallback_message = (
            f"죄송합니다. '{query}'에 대한 최신 검색 결과를 찾지 못했습니다. "
            f"제 내장 지식을 바탕으로 답변드리겠습니다."
        )

        return {
            "search_results": [],
            "requires_clarification": True,
            "clarification_question": fallback_message,
            "thinking_steps": state.get("thinking_steps", []) + [
                ThinkingStep(
                    step="fallback",
                    content="검색 실패, 내장 지식으로 대체",
                    timestamp=time.time()
                )
            ]
        }
```

### 3.5 그래프 통합

```python
# backend/app/agents/graph.py

def create_ai_graph_with_retry(settings: Any) -> StateGraph:
    """검색 재시도 기능이 있는 그래프"""

    # 노드 추가
    workflow.add_node("intent_parser", IntentParserNode(settings))
    workflow.add_node("searcher", SearcherNode(settings))
    workflow.add_node("search_evaluator", SearchQualityEvaluator())
    workflow.add_node("query_rewriter", QueryRewriter(settings))
    workflow.add_node("fallback_handler", FallbackHandler())
    workflow.add_node("generator", GeneratorNode(settings))

    # 엔트리 포인트
    workflow.set_entry_point("intent_parser")

    # IntentParser → Searcher
    workflow.add_conditional_edges(
        "intent_parser",
        route_by_mode,
        {"searcher": "searcher", "generator": "generator"}
    )

    # Searcher → SearchEvaluator (항상)
    workflow.add_edge("searcher", "search_evaluator")

    # SearchEvaluator → 조건부 라우팅
    def route_after_evaluation(state: GraphState) -> str:
        """평가 후 라우팅"""
        needs_retry = state.get("needs_retry", False)
        retry_count = state.get("search_retry_count", 0)
        max_retries = 3

        # 충분한 결과 → Generator
        if not needs_retry:
            return "generator"

        # 재시도 가능 → QueryRewriter
        if retry_count < max_retries:
            return "query_rewriter"

        # 모든 시도 실패 → Fallback
        return "fallback_handler"

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

    # FallbackHandler → Generator
    workflow.add_edge("fallback_handler", "generator")

    # Generator → END
    workflow.add_edge("generator", END)

    return workflow.compile()
```

---

## 4. 실행 흐름 예시

### 시나리오: "TSMC 2026 투자 계획"

```
1. IntentParser
   ↓ [SEARCH 모드]

2. Searcher (시도 1)
   검색 쿼리: ["TSMC 2026 투자 계획"]
   결과: 2개 (불충분)
   ↓

3. SearchEvaluator
   품질 점수: 45/100
   needs_retry: True
   retry_reason: "insufficient_results"
   ↓

4. QueryRewriter (재시도 #1)
   전략: broaden
   새 쿼리: ["TSMC 투자", "반도체 투자 2026", "TSMC 설비 투자"]
   ↓

5. Searcher (시도 2)
   검색 쿼리: ["TSMC 투자", ...]
   결과: 5개
   ↓

6. SearchEvaluator
   품질 점수: 72/100
   needs_retry: False
   ↓

7. Generator
   5개 결과 기반 답변 생성
   ↓ END
```

### 시나리오: 모든 시도 실패

```
1-3. (위와 동일)
   ↓

4. QueryRewriter (재시도 #1)
   ↓
5. Searcher (시도 2) → 결과: 1개
   ↓
6. SearchEvaluator → needs_retry: True
   ↓

7. QueryRewriter (재시도 #2)
   ↓
8. Searcher (시도 3) → 결과: 0개
   ↓
9. SearchEvaluator → needs_retry: True
   ↓

10. QueryRewriter (재시도 #3)
   ↓
11. Searcher (시도 4) → 결과: 1개
   ↓
12. SearchEvaluator → needs_retry: True, retry_count=3 (한계)
   ↓

13. FallbackHandler
    "검색 결과를 찾지 못했습니다. 내장 지식으로 답변합니다."
   ↓
14. Generator
    LLM 지식만으로 답변
   ↓ END
```

---

## 5. 고급 전략

### 5.1 적응형 재시도 전략

```python
class AdaptiveRetryStrategy:
    """재시도 전략을 동적으로 선택"""

    def select_strategy(self, state: GraphState) -> str:
        """실패 원인에 따른 전략 선택"""
        retry_reason = state.get("retry_reason", "")

        if retry_reason == "no_results":
            # 결과 없음 → 더 넓은 범위
            return "broaden"
        elif retry_reason == "low_quality":
            # 품질 낮음 → 신뢰도 높은 도메인 강제
            return "trusted_domains"
        elif retry_reason == "low_relevance":
            # 관련성 낮음 → 키워드 강화
            return "keyword_boost"
        else:
            return "synonym"
```

### 5.2 검색 엔진 전환

```python
def route_to_alternative_search(state: GraphState) -> str:
    """검색 엔진 전환"""
    retry_count = state.get("search_retry_count", 0)

    if retry_count == 0:
        return "searcher_primary"    # SearXNG
    elif retry_count == 1:
        return "searcher_google"     # Google Custom Search
    elif retry_count == 2:
        return "searcher_bing"       # Bing Search
    else:
        return "fallback_handler"
```

### 5.3 병렬 검색

```python
# 여러 쿼리를 동시에 실행
async def parallel_search(queries: list[str]) -> list[dict]:
    """병렬 검색"""
    tasks = [search_web(q, ...) for q in queries]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 실패한 것 제외
    valid_results = [r for r in results if not isinstance(r, Exception)]
    return valid_results
```

---

## 6. 구현 우선순위

### Phase 1: 기본 재시도 (즉시 구현 가능)
1. ✅ State에 `search_retry_count` 추가
2. ✅ SearchEvaluator 노드 구현
3. ✅ 조건부 엣지로 재시도 로직 추가
4. ✅ 최대 3회 재시도

### Phase 2: 쿼리 재작성 (중요도 높음)
1. ✅ QueryRewriter 노드 구현
2. ✅ 재작성 전략 (broaden/narrow/synonym)
3. ✅ 실패 쿼리 추적

### Phase 3: 폴백 처리 (안정성)
1. ✅ FallbackHandler 구현
2. ✅ RAG 전환 옵션
3. ✅ 사용자 피드백

### Phase 4: 고급 기능 (최적화)
1. ⏳ 적응형 전략 선택
2. ⏳ 검색 엔진 전환
3. ⏳ 병렬 검색

---

## 7. 예상 효과

### Before (현재)
```
검색 실패 → 빈 결과 → "정보가 없습니다" 응답
성공률: ~60%
```

### After (재시도 구현)
```
검색 실패 → 쿼리 재작성 → 재검색 → 성공
실패 → 다른 전략 → 재검색 → 성공
모든 시도 실패 → 내장 지식 사용
성공률: ~85% (예상)
```

### 사용자 경험 개선
- ❌ Before: "죄송합니다, 정보를 찾을 수 없습니다"
- ✅ After: "최초 검색에서 결과가 부족하여 다른 키워드로 재검색했습니다. 총 7개의 관련 자료를 찾았습니다."

---

## 8. 참고 자료

- [LangGraph Conditional Edges](https://langchain-ai.github.io/langgraph/concepts/low_level/#conditional-edges)
- [LangGraph Cycles](https://langchain-ai.github.io/langgraph/concepts/low_level/#cycles)
- [Perplexity Search Quality](https://www.perplexity.ai/hub/blog/how-perplexity-works)

---

**작성자**: Claude (AI Agent)
**다음 단계**: Phase 1 기본 재시도 구현 시작
