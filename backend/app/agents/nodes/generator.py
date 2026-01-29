"""Generator 노드.

최종 응답을 생성하는 노드입니다.
"""
from __future__ import annotations

from loguru import logger
import time
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage

from ..state import GraphState, AIMode, ThinkingStep, SearchResult
from ..tools.llm_client import async_llm_completion_stream



# 모드별 시스템 프롬프트
SYSTEM_PROMPTS = {
    AIMode.SIMPLE: """당신은 친절하고 도움이 되는 AI 어시스턴트입니다.
사용자의 질문에 명확하고 간결하게 답변하세요.
한국어로 자연스럽게 대화하세요.""",

    AIMode.SEARCH: """당신은 웹 검색 결과를 바탕으로 정보를 제공하는 AI 어시스턴트입니다.
제공된 검색 결과를 참고하여 정확한 정보를 전달하세요.
출처를 명시하고, 검색 결과에 없는 정보는 추측하지 마세요.
한국어로 답변하세요.""",

    AIMode.RAG: """당신은 사용자의 문서를 기반으로 답변하는 AI 어시스턴트입니다.
제공된 문서 내용을 참고하여 정확한 정보를 전달하세요.
문서에 없는 내용은 "해당 정보가 문서에 없습니다"라고 안내하세요.
한국어로 답변하세요.""",

    AIMode.REASONING: """당신은 복잡한 문제를 분석하고 추론하는 AI 어시스턴트입니다.
단계별로 논리적으로 설명하세요.
필요하면 여러 관점에서 분석하세요.
한국어로 답변하세요.""",

    AIMode.HYBRID: """당신은 웹 검색과 내부 문서를 종합하여 답변하는 AI 어시스턴트입니다.
외부 정보와 내부 문서를 비교하고 종합하여 최선의 답변을 제공하세요.
출처를 명확히 구분하여 표시하세요.
한국어로 답변하세요.""",
}


class GeneratorNode:
    """최종 응답을 생성하는 노드."""

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings

    async def __call__(self, state: GraphState) -> dict:
        """응답 생성 실행.

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        query = state["query"]
        mode = state["mode"]
        search_results = state.get("search_results", [])
        thinking_steps = list(state.get("thinking_steps", []))

        # 사고 과정 기록
        thinking_steps.append(ThinkingStep(
            step="generation_start",
            content=f"응답 생성 시작 (모드: {mode})",
            timestamp=time.time()
        ))

        # 컨텍스트 구성
        context = self._build_context(mode, search_results)
        system_prompt = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS[AIMode.SIMPLE])

        # 메시지 구성
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # 컨텍스트가 있으면 추가
        if context:
            messages.append({
                "role": "user",
                "content": f"참고 자료:\n{context}\n\n질문: {query}"
            })
        else:
            messages.append({"role": "user", "content": query})

        try:
            # LLM 호출 (스트리밍) - 동적 모델 라우팅
            selected_model = state.get("selected_model")
            response_chunks = []
            async for chunk in async_llm_completion_stream(
                settings=self.settings,
                messages=messages,
                model=selected_model,
            ):
                response_chunks.append(chunk)

            response = "".join(response_chunks)

            logger.info(f"[Generator] Response generated: {len(response)} chars")

            thinking_steps.append(ThinkingStep(
                step="generation_complete",
                content=f"응답 생성 완료 ({len(response)} 글자)",
                timestamp=time.time()
            ))

            # 소스 정보 추출
            sources = self._extract_sources(search_results)

            return {
                "response": response,
                "sources": sources,
                "thinking_steps": thinking_steps,
                "messages": [AIMessage(content=response)],
            }

        except Exception as e:
            logger.error(f"[Generator] Generation failed: {e}")
            thinking_steps.append(ThinkingStep(
                step="generation_error",
                content=f"응답 생성 실패: {str(e)}",
                timestamp=time.time()
            ))
            return {
                "response": "죄송합니다. 응답 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                "sources": [],
                "thinking_steps": thinking_steps,
                "error": str(e),
            }

    async def stream(self, state: GraphState) -> AsyncIterator[dict]:
        """스트리밍 응답 생성.

        Args:
            state: 현재 그래프 상태

        Yields:
            스트리밍 청크 딕셔너리
        """
        query = state["query"]
        mode = state["mode"]
        search_results = state.get("search_results", [])

        # 컨텍스트 구성
        context = self._build_context(mode, search_results)
        system_prompt = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS[AIMode.SIMPLE])

        # 메시지 구성
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        if context:
            messages.append({
                "role": "user",
                "content": f"참고 자료:\n{context}\n\n질문: {query}"
            })
        else:
            messages.append({"role": "user", "content": query})

        # 스트리밍 응답 생성 - 동적 모델 라우팅
        selected_model = state.get("selected_model")
        response_chunks = []
        async for chunk in async_llm_completion_stream(
            settings=self.settings,
            messages=messages,
            model=selected_model,
        ):
            response_chunks.append(chunk)
            yield {"type": "content", "data": chunk}

        # 완료 시 소스 정보 전송
        sources = self._extract_sources(search_results)
        if sources:
            yield {"type": "sources", "data": sources}

        yield {"type": "done", "data": "".join(response_chunks)}

    def _build_context(self, mode: AIMode, search_results: list[SearchResult]) -> str:
        """검색 결과로부터 컨텍스트 문자열 생성.

        Args:
            mode: AI 모드
            search_results: 검색 결과 목록

        Returns:
            컨텍스트 문자열
        """
        if not search_results:
            return ""

        context_parts = []
        for i, result in enumerate(search_results[:5], 1):  # 최대 5개
            source_type = "웹" if result.get("source") == "web" else "문서"
            context_parts.append(
                f"[{source_type} {i}] {result.get('title', '제목 없음')}\n"
                f"URL: {result.get('url', '')}\n"
                f"내용: {result.get('snippet', '')}\n"
            )

        return "\n---\n".join(context_parts)

    def _extract_sources(self, search_results: list[SearchResult]) -> list[SearchResult]:
        """검색 결과에서 출처 정보 추출.

        Args:
            search_results: 검색 결과 목록

        Returns:
            출처 정보 목록
        """
        if not search_results:
            return []

        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("snippet", "")[:200],  # 요약 제한
                source=r.get("source", "web"),
            )
            for r in search_results[:5]
        ]
