"""Generator 노드.

최종 응답을 생성하는 노드입니다.
V8.3 Phase 4: Citation (출처 표시) 추가
"""
from __future__ import annotations

from loguru import logger
import time
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage

from ..state import GraphState, AIMode, ThinkingStep, SearchResult
from ..tools.llm_client import async_llm_completion_stream
from ...utils.citation_manager import CitationManager



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
        self.citation_manager = CitationManager()

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

            # [Phase 4] Citation 처리
            citations = []
            if search_results:
                # 검색 결과를 dict 형식으로 변환
                sources_for_citation = [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("snippet", ""),
                    }
                    for r in search_results[:5]
                ]

                # Citation 추출 및 검증
                _, citation_objects = self.citation_manager.process(
                    response, sources_for_citation
                )
                citations = [c.to_dict() for c in citation_objects]

                if citations:
                    logger.info(f"[Generator] Extracted {len(citations)} citations")

            thinking_steps.append(ThinkingStep(
                step="generation_complete",
                content=f"응답 생성 완료 ({len(response)} 글자, {len(citations)}개 출처)",
                timestamp=time.time()
            ))

            # 소스 정보 추출
            sources = self._extract_sources(search_results)

            return {
                "response": response,
                "sources": sources,
                "citations": citations,  # Phase 4: Citation 추가
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

        # 완료 시 소스 정보 및 Citation 전송
        sources = self._extract_sources(search_results)
        if sources:
            yield {"type": "sources", "data": sources}

        # [Phase 4] Citation 처리
        full_response = "".join(response_chunks)
        if search_results:
            sources_for_citation = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("snippet", ""),
                }
                for r in search_results[:5]
            ]

            _, citation_objects = self.citation_manager.process(
                full_response, sources_for_citation
            )
            citations = [c.to_dict() for c in citation_objects]

            if citations:
                yield {"type": "citations", "data": citations}
                logger.info(f"[Generator] Stream: Extracted {len(citations)} citations")

        yield {"type": "done", "data": full_response}

    def _build_context(self, mode: AIMode, search_results: list[SearchResult]) -> str:
        """검색 결과로부터 컨텍스트 문자열 생성.

        Phase 4: 출처 번호를 명확히 표시하여 LLM이 인용하도록 유도

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
            # Phase 4: 출처 번호를 명확히 표시
            context_parts.append(
                f"출처 [{i}] ({source_type})\n"
                f"제목: {result.get('title', '제목 없음')}\n"
                f"URL: {result.get('url', '')}\n"
                f"내용: {result.get('snippet', '')}\n"
            )

        header = "## 제공된 출처 정보\n\n"
        header += "답변 시 반드시 [번호] 형식으로 출처를 인용하세요.\n\n"

        return header + "\n---\n".join(context_parts)

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
