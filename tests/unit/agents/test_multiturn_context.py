"""멀티턴 대화 맥락 처리 단위 테스트.

다음 두 가지를 검증합니다:
1. ContextualizeTransformer.should_apply — 멀티턴 여부 감지 로직
2. ContextualizeTransformer._format_conversation — 대화 이력 포맷팅

에이전트 코딩으로 인해 대화 히스토리 전달 로직이 깨지는 회귀를 방지합니다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# conftest.py가 kiwipiepy mock과 sys.path를 먼저 설정합니다.
from app.agents.nodes.intent_parser import ContextualizeTransformer
from langchain_core.messages import AIMessage, HumanMessage


@pytest.fixture
def transformer() -> ContextualizeTransformer:
    return ContextualizeTransformer()


class TestShouldApply:
    """멀티턴 감지: 메시지 수에 따른 should_apply 반환값 검증."""

    def test_no_messages_returns_false(
        self, transformer: ContextualizeTransformer
    ) -> None:
        state: dict = {"messages": []}
        assert transformer.should_apply("그게 뭔가요?", state) is False

    def test_single_message_returns_false(
        self, transformer: ContextualizeTransformer
    ) -> None:
        state: dict = {"messages": [HumanMessage(content="파이썬이란?")]}
        assert transformer.should_apply("그게 뭔가요?", state) is False

    def test_two_messages_returns_true(
        self, transformer: ContextualizeTransformer
    ) -> None:
        """User + AI 1턴 → 맥락 참조 가능."""
        state: dict = {
            "messages": [
                HumanMessage(content="파이썬이란?"),
                AIMessage(content="파이썬은 고수준 프로그래밍 언어입니다."),
            ]
        }
        assert transformer.should_apply("그게 뭔가요?", state) is True

    def test_four_messages_returns_true(
        self, transformer: ContextualizeTransformer
    ) -> None:
        """2턴 대화 → 맥락 참조 가능."""
        state: dict = {
            "messages": [
                HumanMessage(content="서울은 어떤 도시인가요?"),
                AIMessage(content="서울은 대한민국의 수도입니다."),
                HumanMessage(content="그 도시의 인구는?"),
                AIMessage(content="서울의 인구는 약 950만 명입니다."),
            ]
        }
        assert transformer.should_apply("그 도시에 유명한 관광지는?", state) is True

    def test_missing_messages_key_returns_false(
        self, transformer: ContextualizeTransformer
    ) -> None:
        """messages 키 자체가 없으면 False."""
        state: dict = {}
        assert transformer.should_apply("질문", state) is False


class TestFormatConversation:
    """대화 이력 포맷팅 — 출력 형식 회귀 방지."""

    def test_human_message_prefix(
        self, transformer: ContextualizeTransformer
    ) -> None:
        messages = [HumanMessage(content="파이썬이란?")]
        result = transformer._format_conversation(messages)
        assert "사용자" in result
        assert "파이썬이란?" in result

    def test_ai_message_prefix(
        self, transformer: ContextualizeTransformer
    ) -> None:
        messages = [AIMessage(content="파이썬은 언어입니다.")]
        result = transformer._format_conversation(messages)
        assert "AI" in result
        assert "파이썬은 언어입니다." in result

    def test_long_content_truncated(
        self, transformer: ContextualizeTransformer
    ) -> None:
        """200자 초과 메시지는 잘려야 함."""
        long_content = "A" * 300
        messages = [HumanMessage(content=long_content)]
        result = transformer._format_conversation(messages)
        # 200자 제한 + "..." 이어붙임
        assert "..." in result
        assert len(result) < 300

    def test_multiple_turns_ordered(
        self, transformer: ContextualizeTransformer
    ) -> None:
        """여러 턴 순서대로 포맷팅."""
        messages = [
            HumanMessage(content="첫 번째 질문"),
            AIMessage(content="첫 번째 응답"),
            HumanMessage(content="두 번째 질문"),
        ]
        result = transformer._format_conversation(messages)
        first_pos = result.index("첫 번째 질문")
        second_pos = result.index("두 번째 질문")
        assert first_pos < second_pos, "대화 순서가 유지되어야 합니다."


class TestContextualizeTransformerIntegration:
    """LLM mock으로 transform() 호출 경로 검증."""

    @pytest.mark.asyncio
    async def test_single_turn_returns_original_query(
        self, transformer: ContextualizeTransformer
    ) -> None:
        """첫 번째 턴은 LLM 호출 없이 원본 쿼리 그대로 반환."""
        state: dict = {"messages": []}
        settings = MagicMock()

        result = await transformer.transform("파이썬이란?", state, settings)

        assert result == ["파이썬이란?"]

    @pytest.mark.asyncio
    async def test_multiturn_calls_llm_for_contextualization(
        self, transformer: ContextualizeTransformer
    ) -> None:
        """멀티턴에서 LLM을 호출해 쿼리를 문맥화한다."""
        state: dict = {
            "messages": [
                HumanMessage(content="서울은 어떤 도시인가요?"),
                AIMessage(content="서울은 대한민국의 수도입니다."),
            ]
        }
        settings = MagicMock()
        contextualized_query = "서울의 인구는 얼마인가요?"

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "app.agents.nodes.intent_parser.async_llm_completion",
                AsyncMock(return_value=contextualized_query),
            )
            result = await transformer.transform("그 도시의 인구는?", state, settings)

        assert result == [contextualized_query]

    @pytest.mark.asyncio
    async def test_multiturn_llm_failure_returns_original(
        self, transformer: ContextualizeTransformer
    ) -> None:
        """LLM 호출 실패 시 원본 쿼리를 반환해야 한다 (폴백 로직 검증)."""
        state: dict = {
            "messages": [
                HumanMessage(content="파이썬이란?"),
                AIMessage(content="파이썬은 언어입니다."),
            ]
        }
        settings = MagicMock()
        original_query = "그것의 버전은?"

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "app.agents.nodes.intent_parser.async_llm_completion",
                AsyncMock(side_effect=Exception("LLM 오류")),
            )
            result = await transformer.transform(original_query, state, settings)

        assert result == [original_query]
