"""Fallback Handler 노드.

V8.4: 모든 검색 시도가 실패했을 때 대안을 제공합니다.
"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger

from ..state import GraphState, ThinkingStep, AIMode


class FallbackHandlerNode:
    """검색 실패 시 폴백 처리 노드."""

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings

    async def __call__(self, state: GraphState) -> dict:
        """검색 실패 시 대안 제공.

        대안:
        1. 내장 지식으로 답변 (기본)
        2. RAG로 전환 (내부 문서 검색)
        3. 사용자에게 검색 실패 명시적 안내

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        query = state["query"]
        retry_count = state.get("search_retry_count", 0)
        failed_queries = state.get("failed_queries", [])
        retry_reason = state.get("retry_reason", "")
        thinking_steps = list(state.get("thinking_steps", []))

        logger.warning(
            f"[FallbackHandler] All search attempts failed for: '{query}' "
            f"(tried {retry_count} times, reason={retry_reason})"
        )

        # 폴백 전략 선택
        fallback_strategy = self._select_fallback_strategy(state)

        if fallback_strategy == "rag":
            # RAG로 전환 (내부 문서 검색)
            logger.info("[FallbackHandler] Fallback: Switching to RAG mode")

            thinking_steps.append(ThinkingStep(
                step="fallback_rag",
                content="웹 검색 실패, 내부 문서 검색으로 전환",
                timestamp=time.time()
            ))

            return {
                "mode": AIMode.RAG,
                "search_results": [],
                "thinking_steps": thinking_steps,
            }

        elif fallback_strategy == "llm_only":
            # LLM 지식만으로 답변
            logger.info("[FallbackHandler] Fallback: Using LLM knowledge only")

            fallback_message = self._create_fallback_message(query, retry_count, retry_reason)

            thinking_steps.append(ThinkingStep(
                step="fallback_llm",
                content="웹 검색 실패, 내장 지식으로 답변",
                timestamp=time.time()
            ))

            return {
                "search_results": [],
                "requires_clarification": False,  # 경고 표시하지만 계속 진행
                "clarification_question": None,
                "thinking_steps": thinking_steps,
                # Generator에게 폴백 메시지 전달 (응답 앞부분에 추가)
                "metadata": {
                    **state.get("metadata", {}),
                    "fallback_message": fallback_message,
                }
            }

        else:  # explicit_error
            # 사용자에게 명시적으로 실패 알림
            logger.info("[FallbackHandler] Fallback: Explicit error message")

            error_message = self._create_error_message(query, retry_count, failed_queries)

            thinking_steps.append(ThinkingStep(
                step="fallback_error",
                content="검색 실패, 사용자에게 안내",
                timestamp=time.time()
            ))

            return {
                "search_results": [],
                "requires_clarification": True,
                "clarification_question": error_message,
                "thinking_steps": thinking_steps,
                "error": "Search failed after all retry attempts",
            }

    def _select_fallback_strategy(self, state: GraphState) -> str:
        """폴백 전략 선택.

        Args:
            state: 현재 그래프 상태

        Returns:
            전략명 ("rag", "llm_only", "explicit_error")
        """
        retry_reason = state.get("retry_reason", "")
        mode = state.get("mode")

        # 1. 내부 문서가 있을 가능성이 높으면 RAG로 전환
        query = state["query"].lower()
        if any(keyword in query for keyword in ["내 문서", "내 콘텐츠", "저장된", "업로드한"]):
            return "rag"

        # 2. HYBRID 모드였다면 이미 RAG도 시도했을 것이므로 LLM만 사용
        if mode == AIMode.HYBRID:
            return "llm_only"

        # 3. 특정 최신 정보가 필요한 경우 명시적 에러
        if any(keyword in query for keyword in ["최신", "현재", "오늘", "실시간", "2026", "2025"]):
            return "explicit_error"

        # 4. 기본: LLM 지식으로 답변 시도
        return "llm_only"

    def _create_fallback_message(self, query: str, retry_count: int, retry_reason: str) -> str:
        """폴백 메시지 생성.

        Args:
            query: 원본 질문
            retry_count: 재시도 횟수
            retry_reason: 재시도 이유

        Returns:
            폴백 메시지
        """
        reason_messages = {
            "no_results": "관련 검색 결과를 찾지 못했습니다",
            "insufficient_results": "충분한 검색 결과를 찾지 못했습니다",
            "low_quality": "신뢰할 만한 검색 결과를 찾지 못했습니다",
            "low_relevance": "관련성 높은 검색 결과를 찾지 못했습니다",
        }

        reason_msg = reason_messages.get(retry_reason, "검색 결과를 찾지 못했습니다")

        return (
            f"⚠️ {retry_count}번의 검색 시도 후에도 {reason_msg}. "
            f"제 내장 지식을 바탕으로 답변드리겠습니다. "
            f"최신 정보가 아닐 수 있으니 참고해 주세요."
        )

    def _create_error_message(
        self, query: str, retry_count: int, failed_queries: list[str]
    ) -> str:
        """명시적 에러 메시지 생성.

        Args:
            query: 원본 질문
            retry_count: 재시도 횟수
            failed_queries: 실패한 쿼리 목록

        Returns:
            에러 메시지
        """
        return (
            f"죄송합니다. '{query}'에 대한 검색 결과를 찾을 수 없었습니다.\n\n"
            f"**시도한 검색**: {retry_count}회\n"
            f"**검색 키워드**: {', '.join(failed_queries[:5])}\n\n"
            f"다음과 같은 방법을 시도해 보세요:\n"
            f"- 더 일반적인 용어로 질문해 주세요\n"
            f"- 다른 표현이나 동의어를 사용해 주세요\n"
            f"- 질문을 더 구체적으로 작성해 주세요"
        )
