"""Reasoner 노드.

복잡한 질문에 대해 Chain-of-Thought 추론을 수행하는 노드입니다.
"""
from __future__ import annotations

from loguru import logger
import time
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage

from ..state import GraphState, AIMode, ThinkingStep
from ..tools.llm_client import async_llm_completion, async_llm_completion_stream
from ..tools.datetime_tool import get_current_datetime


# Reasoning 시스템 프롬프트 베이스 (날짜는 _get_reasoning_system_prompt()에서 동적 주입)
_REASONING_SYSTEM_PROMPT_BASE = """당신은 복잡한 문제를 단계별로 분석하고 추론하는 AI 어시스턴트입니다.

추론 방식:
1. 먼저 문제를 이해하고 핵심 요소를 파악하세요
2. 단계별로 논리적으로 분석하세요
3. 각 단계에서 근거를 제시하세요
4. 최종 결론을 명확하게 제시하세요

응답 형식:
## 문제 분석
[문제의 핵심 요소 파악]

## 추론 과정
### 단계 1: [제목]
[분석 내용]

### 단계 2: [제목]
[분석 내용]

...

## 결론
[최종 결론]

한국어로 답변하세요."""


def _get_reasoning_system_prompt() -> str:
    """현재 날짜가 포함된 Reasoning 시스템 프롬프트를 반환합니다."""
    current_date = get_current_datetime()
    return f"오늘 날짜: {current_date}\n\n{_REASONING_SYSTEM_PROMPT_BASE}"


class ReasonerNode:
    """Chain-of-Thought 추론을 수행하는 노드."""

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings

    async def __call__(self, state: GraphState) -> dict:
        """추론 실행.

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        query = state["query"]
        search_results = state.get("search_results", [])
        thinking_steps = list(state.get("thinking_steps", []))

        # 사고 과정 기록
        thinking_steps.append(ThinkingStep(
            step="reasoning_start",
            content="복잡한 추론 시작...",
            timestamp=time.time()
        ))

        # 컨텍스트 구성
        context = self._build_context(search_results)

        # 메시지 구성
        messages = [
            {"role": "system", "content": _get_reasoning_system_prompt()},
        ]

        if context:
            messages.append({
                "role": "user",
                "content": f"참고 자료:\n{context}\n\n질문: {query}\n\n위 질문에 대해 단계별로 분석하고 추론해주세요."
            })
        else:
            messages.append({
                "role": "user",
                "content": f"질문: {query}\n\n위 질문에 대해 단계별로 분석하고 추론해주세요."
            })

        try:
            # LLM 호출 (추론은 더 많은 토큰 필요) - 동적 모델 라우팅
            selected_model = state.get("selected_model")
            response = await async_llm_completion(
                settings=self.settings,
                messages=messages,
                model=selected_model,
                temperature=0.3,  # 일관성을 위해 낮은 온도
                max_tokens=4096,  # 긴 추론을 위해 더 많은 토큰
            )

            logger.info(f"[Reasoner] Reasoning completed: {len(response)} chars")

            # 추론 단계 추출
            reasoning_steps = self._extract_reasoning_steps(response)
            thinking_steps.extend(reasoning_steps)

            thinking_steps.append(ThinkingStep(
                step="reasoning_complete",
                content=f"추론 완료: {len(reasoning_steps)}개 단계",
                timestamp=time.time()
            ))

            return {
                "response": response,
                "thinking_steps": thinking_steps,
                "messages": [AIMessage(content=response)],
            }

        except Exception as e:
            logger.error(f"[Reasoner] Reasoning failed: {e}")
            thinking_steps.append(ThinkingStep(
                step="reasoning_error",
                content=f"추론 실패: {str(e)}",
                timestamp=time.time()
            ))
            return {
                "thinking_steps": thinking_steps,
                "error": f"추론 실패: {e}",
            }

    def _build_context(self, search_results: list) -> str:
        """검색 결과로부터 컨텍스트 구성."""
        if not search_results:
            return ""

        context_parts = []
        for i, result in enumerate(search_results[:5], 1):
            title = result.get("title", "") if isinstance(result, dict) else getattr(result, "title", "")
            snippet = result.get("snippet", "") if isinstance(result, dict) else getattr(result, "snippet", "")
            source = result.get("source", "web") if isinstance(result, dict) else getattr(result, "source", "web")

            source_type = "웹" if source == "web" else "내부 문서"
            context_parts.append(f"[{source_type} {i}] {title}\n{snippet}")

        return "\n\n".join(context_parts)

    async def stream(self, state: GraphState) -> AsyncIterator[dict]:
        """스트리밍 추론 응답 생성.

        Args:
            state: 현재 그래프 상태

        Yields:
            스트리밍 청크 딕셔너리
        """
        query = state["query"]
        search_results = state.get("search_results", [])

        # 컨텍스트 구성
        context = self._build_context(search_results)

        # 메시지 구성
        messages = [
            {"role": "system", "content": _get_reasoning_system_prompt()},
        ]

        if context:
            messages.append({
                "role": "user",
                "content": f"참고 자료:\n{context}\n\n질문: {query}\n\n위 질문에 대해 단계별로 분석하고 추론해주세요."
            })
        else:
            messages.append({
                "role": "user",
                "content": f"질문: {query}\n\n위 질문에 대해 단계별로 분석하고 추론해주세요."
            })

        # 스트리밍 응답 생성 - 동적 모델 라우팅
        selected_model = state.get("selected_model")
        response_chunks = []
        async for chunk in async_llm_completion_stream(
            settings=self.settings,
            messages=messages,
            model=selected_model,
            temperature=0.3,
            max_tokens=4096,
        ):
            response_chunks.append(chunk)
            yield {"type": "content", "data": chunk}

        yield {"type": "done", "data": "".join(response_chunks)}

    def _extract_reasoning_steps(self, response: str) -> list[ThinkingStep]:
        """응답에서 추론 단계 추출."""
        steps = []
        current_time = time.time()

        # "### 단계" 패턴으로 단계 추출
        import re
        step_pattern = r'###\s*단계\s*\d+[:\s]*(.+?)(?=###|##|$)'
        matches = re.findall(step_pattern, response, re.DOTALL)

        for i, match in enumerate(matches[:5], 1):  # 최대 5단계
            content = match.strip()[:200]  # 200자로 제한
            steps.append(ThinkingStep(
                step=f"reasoning_step_{i}",
                content=content,
                timestamp=current_time + i * 0.1,
            ))

        return steps
