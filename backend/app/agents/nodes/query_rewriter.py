"""Query Rewriter 노드.

V8.4: 검색 실패 시 쿼리를 재작성하여 재시도합니다.
"""

from __future__ import annotations

import copy
import time
from datetime import datetime
from typing import Any

from loguru import logger

from ..state import GraphState, ThinkingStep
from ..tools.llm_client import async_llm_completion
from ..tools.datetime_tool import get_current_datetime


class QueryRewriterNode:
    """검색 실패 시 쿼리 재작성 노드."""

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings

    async def __call__(self, state: GraphState) -> dict:
        """실패 원인 기반 쿼리 재작성.

        재작성 전략:
        - 1차 시도: broaden (더 넓은 범위, 일반적인 용어)
        - 2차 시도: narrow (더 구체적, 특정한 용어)
        - 3차 시도: synonym (동의어, 관련어 사용)

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        query = state["query"]
        retry_count = state.get("search_retry_count", 0)
        retry_reason = state.get("retry_reason", "")
        failed_queries = state.get("failed_queries", [])
        original_queries = state.get("original_search_queries", [])
        query_analysis = state.get("query_analysis") or {}
        keyword_hints = query_analysis.get("keywords", [])
        domain_allowlist = query_analysis.get("domain_allowlist", [])
        search_recency = query_analysis.get("search_recency")
        search_language = query_analysis.get("search_language")
        thinking_steps = list(state.get("thinking_steps", []))

        # 원본 쿼리 저장 (첫 재시도 시)
        if retry_count == 0:
            original_queries = state.get("search_queries", [query])

        # 재작성 전략 선택
        strategy = self._select_strategy(retry_count, retry_reason)

        # 교차 언어 fallback: 한국어 중심 검색이 낮은 품질일 때 영어 검색 재시도
        language_strategy = query_analysis.get("language_strategy")
        effective_search_language = search_language
        if self._should_enable_cross_language_fallback(
            retry_reason=retry_reason,
            search_language=search_language,
            retry_count=retry_count,
            language_strategy=language_strategy,
        ):
            effective_search_language = "en-US"
            logger.info(
                "[QueryRewriter] Cross-language fallback enabled: "
                f"{search_language or 'auto'} -> en-US"
            )

            thinking_steps.append(
                ThinkingStep(
                    step="cross_language_fallback",
                    content="한국어 검색 품질이 낮아 영어 검색도 병행합니다.",
                    timestamp=time.time(),
                )
            )

        logger.info(
            f"[QueryRewriter] Retry #{retry_count + 1}, "
            f"reason={retry_reason}, strategy={strategy}"
        )

        # LLM으로 쿼리 재작성
        new_queries = await self._rewrite_queries(
            query,
            original_queries,
            failed_queries,
            strategy,
            retry_reason,
            keyword_hints=keyword_hints,
            domain_allowlist=domain_allowlist,
            search_recency=search_recency,
            search_language=effective_search_language,
        )

        logger.info(
            f"[QueryRewriter] Generated {len(new_queries)} new queries: {new_queries}"
        )

        # 사고 과정 기록
        thinking_steps.append(
            ThinkingStep(
                step="query_rewrite",
                content=f"검색 재시도 #{retry_count + 1} - 전략: {strategy}",
                timestamp=time.time(),
            )
        )

        updated_query_analysis = copy.deepcopy(query_analysis)
        if isinstance(updated_query_analysis, dict):
            if effective_search_language:
                updated_query_analysis["search_language"] = effective_search_language

        return {
            "search_queries": new_queries,
            "search_retry_count": retry_count + 1,
            "failed_queries": failed_queries + state.get("search_queries", []),
            "original_search_queries": original_queries,
            "query_analysis": updated_query_analysis,
            "thinking_steps": thinking_steps,
        }

    def _should_enable_cross_language_fallback(
        self,
        *,
        retry_reason: str,
        search_language: str | None,
        retry_count: int,
        language_strategy: str | None = None,
    ) -> bool:
        """교차 언어 fallback 적용 여부를 판단한다."""
        # 이미 이중 언어 전략이면 fallback 불필요 (Phase 3.5에서 선제적으로 처리됨)
        if language_strategy in (
            "ko_primary_en_secondary",
            "en_primary_ko_secondary",
        ):
            return False

        # 첫 실패에서 바로 전환하지 않고, 한 번 실패한 뒤부터 적용
        if retry_count < 1:
            return False

        if retry_reason not in {
            "low_quality",
            "low_relevance",
            "low_content_coverage",
        }:
            return False

        normalized = (search_language or "").lower()
        return normalized in {"", "ko-kr", "ko"}

    def _select_strategy(self, retry_count: int, retry_reason: str) -> str:
        """재작성 전략 선택.

        Args:
            retry_count: 현재 재시도 횟수
            retry_reason: 재시도 이유

        Returns:
            전략명
        """
        # 실패 원인에 따른 적응형 전략
        if retry_reason == "no_results":
            # 결과 없음 → 더 넓은 범위
            return "broaden"
        elif retry_reason == "low_quality":
            # 품질 낮음 → 신뢰도 높은 도메인 명시
            return "trusted_domains"
        elif retry_reason == "low_relevance":
            # 관련성 낮음 → 키워드 강화
            return "keyword_boost"
        elif retry_reason == "low_content_coverage":
            # 본문 근거 부족 → 더 해설형/문서형 쿼리
            return "source_depth"

        # 기본 순환 전략
        if retry_count == 0:
            return "broaden"
        elif retry_count == 1:
            return "narrow"
        else:
            return "synonym"

    async def _rewrite_queries(
        self,
        query: str,
        original_queries: list[str],
        failed_queries: list[str],
        strategy: str,
        retry_reason: str,
        keyword_hints: list[str] | None = None,
        domain_allowlist: list[str] | None = None,
        search_recency: str | None = None,
        search_language: str | None = None,
    ) -> list[str]:
        """쿼리 재작성.

        Args:
            query: 원본 사용자 질문
            original_queries: 최초 생성된 쿼리들
            failed_queries: 실패한 쿼리들
            strategy: 재작성 전략
            retry_reason: 재시도 이유
            keyword_hints: 핵심 키워드 힌트
            domain_allowlist: 도메인 제한 힌트
            search_recency: 최신성 힌트
            search_language: 언어 힌트

        Returns:
            새로운 검색 쿼리 목록
        """
        # 전략별 프롬프트
        current_year = str(datetime.now().year)
        strategy_instructions = {
            "broaden": f"""더 넓은 범위의 일반적인 용어를 사용하세요.
예: "FastAPI 성능 벤치마크" → "파이썬 웹 프레임워크 성능 비교"
예: "TSMC {current_year} 투자 계획" → "TSMC 투자", "반도체 투자 {current_year}" """,
            "narrow": f"""더 구체적이고 특정한 용어를 사용하세요.
예: "파이썬 웹 프레임워크" → "FastAPI vs Flask 성능"
예: "TSMC 투자" → "TSMC {current_year}년 설비 투자 계획 82조" """,
            "synonym": """동의어나 관련어를 사용하세요.
예: "투자 계획" → "투자 전략", "자본 지출 계획"
예: "성능" → "속도", "처리량", "응답 시간" """,
            "trusted_domains": """신뢰도 높은 출처를 명시하세요.
예: "AI 뉴스" → "AI 뉴스 site:techcrunch.com OR site:venturebeat.com"
예: "파이썬 튜토리얼" → "python tutorial site:python.org OR site:realpython.com" """,
            "keyword_boost": """핵심 키워드를 강조하고 명확히 하세요.
예: "그거" → 원본 주제를 명확히 명시
예: "빠른 것" → "성능이 빠른 프레임워크" """,
            "source_depth": f"""본문이 풍부한 문서/리포트/공식 자료를 찾도록 쿼리를 만드세요.
예: "AI 동향" → "AI 동향 보고서 분석", "AI 산업 리포트 {current_year}"
예: "성능 비교" → "공식 벤치마크 보고서", "technical deep dive" """,
        }

        instruction = strategy_instructions.get(
            strategy, strategy_instructions["broaden"]
        )

        keyword_hints = keyword_hints or []
        domain_allowlist = domain_allowlist or []

        constraint_lines = []
        if keyword_hints:
            constraint_lines.append(
                f"- 핵심 키워드 유지: {', '.join(keyword_hints[:5])}"
            )
        if domain_allowlist:
            constraint_lines.append(
                f"- 도메인 제한 유지: {', '.join(domain_allowlist[:3])}"
            )
        if search_recency:
            constraint_lines.append(f"- 최신성 필터 유지: {search_recency}")
        if search_language:
            constraint_lines.append(f"- 언어 필터 유지: {search_language}")

        constraints_text = (
            "\n".join(constraint_lines) if constraint_lines else "- 제약 없음"
        )

        rewrite_prompt = f"""오늘 날짜: {get_current_datetime()}

검색 결과가 만족스럽지 않아 쿼리를 재작성해야 합니다.

**원본 질문**: {query}

**최초 검색 쿼리들**: {", ".join(original_queries)}

**실패한 쿼리들**: {", ".join(failed_queries) if failed_queries else "없음"}

**실패 이유**: {self._translate_retry_reason(retry_reason)}

**재작성 전략**: {strategy}
{instruction}

**검색 제약(가능하면 유지)**
{constraints_text}

**중요**: 실패한 쿼리와 중복되지 않도록 하세요.
**중요**: 단어만 나열하지 말고 실제 검색 가능한 쿼리 문장으로 작성하세요.

새로운 검색 쿼리 3개를 생성하세요 (한 줄에 하나씩, 번호나 기호 없이):"""

        try:
            response = await async_llm_completion(
                settings=self.settings,
                messages=[{"role": "user", "content": rewrite_prompt}],
                temperature=0.7,  # 다양성 확보
                max_tokens=200,
            )

            # 쿼리 추출
            new_queries = []
            for line in response.strip().split("\n"):
                line = line.strip()
                # 번호나 기호 제거
                line = line.lstrip("0123456789.-•* ")
                if line and line not in failed_queries and line not in original_queries:
                    new_queries.append(line)

            # 최소 1개, 최대 3개
            new_queries = new_queries[:3]

            if not new_queries:
                # 생성 실패 시 폴백: 원본 쿼리 약간 변형
                logger.warning(
                    "[QueryRewriter] Failed to generate new queries, using fallback"
                )
                new_queries = [f"{query} 정보", f"{query} 자세히", f"{query} 관련"]

            return new_queries

        except Exception as e:
            logger.error(f"[QueryRewriter] Rewrite failed: {e}")
            # 에러 시 폴백
            return [f"{query} 검색", f"{query} 정보"]

    def _translate_retry_reason(self, reason: str) -> str:
        """재시도 이유를 한국어로 번역.

        Args:
            reason: 재시도 이유 코드

        Returns:
            한국어 설명
        """
        translations = {
            "no_results": "검색 결과가 없음",
            "insufficient_results": "검색 결과가 부족함 (3개 미만)",
            "low_quality": "검색 결과의 품질이 낮음",
            "low_relevance": "검색 결과의 관련성이 낮음",
            "low_content_coverage": "검색 결과 본문 근거가 부족함",
            "overall_low_score": "전체 품질 점수가 낮음",
        }
        return translations.get(reason, reason)
