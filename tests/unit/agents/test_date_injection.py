"""날짜 주입 통합 테스트.

Generator, Reasoner, IntentParser, QueryRewriter, FallbackHandler 각 컴포넌트에
현재 날짜가 올바르게 주입되는지 검증합니다.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

# conftest.py가 sys.path와 mock 설정을 먼저 처리합니다.
from app.agents.nodes.generator import get_system_prompt
from app.agents.nodes.reasoner import _get_reasoning_system_prompt
from app.agents.nodes.intent_parser import INTENT_ANALYSIS_PROMPT
from app.agents.state import AIMode


# 테스트에서 공통으로 사용할 고정 날짜
FIXED_DATE_STR = "2026년 02월 21일 (토요일)"
FIXED_DATETIME = datetime(2026, 2, 21, 12, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))


class TestGeneratorDateInjection:
    """GeneratorNode get_system_prompt()의 날짜 주입 검증."""

    @pytest.mark.parametrize(
        "mode",
        [AIMode.SIMPLE, AIMode.SEARCH, AIMode.RAG, AIMode.REASONING, AIMode.HYBRID],
    )
    def test_system_prompt_contains_date_prefix(self, mode: AIMode) -> None:
        """모든 AIMode에서 시스템 프롬프트가 '오늘 날짜:' 접두사를 포함하는지 확인합니다."""
        with patch("app.agents.nodes.generator.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR
            result = get_system_prompt(mode)

        assert result.startswith("오늘 날짜:"), (
            f"AIMode.{mode.name}의 시스템 프롬프트가 '오늘 날짜:'로 시작해야 합니다.\n"
            f"실제 시작: '{result[:30]}'"
        )

    @pytest.mark.parametrize(
        "mode",
        [AIMode.SIMPLE, AIMode.SEARCH, AIMode.RAG, AIMode.REASONING, AIMode.HYBRID],
    )
    def test_system_prompt_contains_fixed_date(self, mode: AIMode) -> None:
        """mock된 날짜가 프롬프트에 포함되는지 확인합니다."""
        with patch("app.agents.nodes.generator.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR
            result = get_system_prompt(mode)

        assert FIXED_DATE_STR in result, (
            f"AIMode.{mode.name}의 시스템 프롬프트에 날짜 '{FIXED_DATE_STR}'가 없습니다."
        )

    def test_system_prompt_calls_get_current_datetime(self) -> None:
        """get_system_prompt()가 get_current_datetime()을 호출하는지 확인합니다."""
        with patch("app.agents.nodes.generator.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR
            get_system_prompt(AIMode.SIMPLE)

        mock_dt.assert_called_once()

    def test_system_prompt_base_content_preserved(self) -> None:
        """날짜 주입 후 기존 시스템 프롬프트 내용이 보존되는지 확인합니다."""
        with patch("app.agents.nodes.generator.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR
            result = get_system_prompt(AIMode.SIMPLE)

        # 기본 SIMPLE 프롬프트 내용이 남아있는지 확인
        assert "AI 어시스턴트" in result


class TestReasonerDateInjection:
    """ReasonerNode _get_reasoning_system_prompt()의 날짜 주입 검증."""

    def test_reasoning_prompt_starts_with_date(self) -> None:
        """Reasoning 프롬프트가 '오늘 날짜:'로 시작하는지 확인합니다."""
        with patch("app.agents.nodes.reasoner.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR
            result = _get_reasoning_system_prompt()

        assert result.startswith("오늘 날짜:"), (
            f"Reasoning 시스템 프롬프트가 '오늘 날짜:'로 시작해야 합니다.\n"
            f"실제 시작: '{result[:30]}'"
        )

    def test_reasoning_prompt_contains_fixed_date(self) -> None:
        """mock된 날짜가 Reasoning 프롬프트에 포함되는지 확인합니다."""
        with patch("app.agents.nodes.reasoner.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR
            result = _get_reasoning_system_prompt()

        assert FIXED_DATE_STR in result

    def test_reasoning_prompt_contains_base_content(self) -> None:
        """날짜 주입 후 기존 Reasoning 프롬프트 내용이 보존되는지 확인합니다."""
        with patch("app.agents.nodes.reasoner.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR
            result = _get_reasoning_system_prompt()

        assert "단계별로" in result
        assert "결론" in result

    def test_reasoning_prompt_calls_get_current_datetime(self) -> None:
        """_get_reasoning_system_prompt()가 get_current_datetime()을 호출하는지 확인합니다."""
        with patch("app.agents.nodes.reasoner.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR
            _get_reasoning_system_prompt()

        mock_dt.assert_called_once()


class TestIntentParserDateInjection:
    """IntentParser INTENT_ANALYSIS_PROMPT의 날짜 주입 검증."""

    def test_intent_prompt_template_has_current_date_placeholder(self) -> None:
        """INTENT_ANALYSIS_PROMPT에 {current_date} 플레이스홀더가 있는지 확인합니다."""
        assert "{current_date}" in INTENT_ANALYSIS_PROMPT, (
            "INTENT_ANALYSIS_PROMPT에 '{current_date}' 플레이스홀더가 있어야 합니다."
        )

    def test_intent_prompt_formatted_contains_date(self) -> None:
        """포맷된 프롬프트에 날짜가 포함되는지 확인합니다."""
        formatted = INTENT_ANALYSIS_PROMPT.format(
            query="테스트 질문",
            current_date=FIXED_DATE_STR,
        )

        assert FIXED_DATE_STR in formatted

    def test_intent_prompt_formatted_contains_query(self) -> None:
        """포맷된 프롬프트에 쿼리가 포함되는지 확인합니다."""
        test_query = "오늘 날씨 어때요?"
        formatted = INTENT_ANALYSIS_PROMPT.format(
            query=test_query,
            current_date=FIXED_DATE_STR,
        )

        assert test_query in formatted

    def test_intent_prompt_date_label(self) -> None:
        """포맷된 프롬프트에 '오늘 날짜:' 라벨이 포함되는지 확인합니다."""
        formatted = INTENT_ANALYSIS_PROMPT.format(
            query="test",
            current_date=FIXED_DATE_STR,
        )

        assert "오늘 날짜:" in formatted


class TestQueryRewriterDateInjection:
    """QueryRewriterNode _rewrite_queries()의 날짜 주입 검증."""

    def test_rewrite_prompt_calls_get_current_datetime(self) -> None:
        """_rewrite_queries()가 get_current_datetime()을 호출하는지 확인합니다."""
        from app.agents.nodes.query_rewriter import QueryRewriterNode

        settings = MagicMock()
        node = QueryRewriterNode(settings)

        # LLM 호출을 mock
        async def mock_llm(*args, **kwargs):
            return "query1\nquery2\nquery3"

        with patch("app.agents.nodes.query_rewriter.async_llm_completion", mock_llm), \
             patch("app.agents.nodes.query_rewriter.get_current_datetime") as mock_dt:
            mock_dt.return_value = FIXED_DATE_STR

            import asyncio
            asyncio.get_event_loop().run_until_complete(
                node._rewrite_queries(
                    query="테스트",
                    original_queries=["original"],
                    failed_queries=[],
                    strategy="broaden",
                    retry_reason="no_results",
                )
            )

        mock_dt.assert_called()


class TestFallbackHandlerDynamicYear:
    """FallbackHandlerNode의 동적 연도 계산 검증."""

    def test_current_year_triggers_explicit_error(self) -> None:
        """현재 연도 키워드가 explicit_error 전략을 트리거하는지 확인합니다."""
        from app.agents.nodes.fallback_handler import FallbackHandlerNode

        settings = MagicMock()
        node = FallbackHandlerNode(settings)

        current_year = str(datetime.now().year)
        state = MagicMock()
        state.__getitem__ = lambda self, key: f"{current_year}년 최신 AI 뉴스"
        state.get = lambda key, default=None: {
            "retry_reason": "no_results",
            "mode": None,
        }.get(key, default)

        # GraphState처럼 동작하는 dict 사용
        graph_state = {
            "query": f"{current_year}년 최신 AI 뉴스",
            "retry_reason": "no_results",
            "mode": None,
        }

        strategy = node._select_fallback_strategy(graph_state)  # type: ignore[arg-type]
        assert strategy == "explicit_error", (
            f"'{current_year}'이 포함된 쿼리는 'explicit_error' 전략이어야 합니다. "
            f"실제: '{strategy}'"
        )

    def test_previous_year_triggers_explicit_error(self) -> None:
        """이전 연도 키워드도 explicit_error 전략을 트리거하는지 확인합니다."""
        from app.agents.nodes.fallback_handler import FallbackHandlerNode

        settings = MagicMock()
        node = FallbackHandlerNode(settings)

        previous_year = str(datetime.now().year - 1)
        graph_state = {
            "query": f"{previous_year}년 경제 전망",
            "retry_reason": "no_results",
            "mode": None,
        }

        strategy = node._select_fallback_strategy(graph_state)  # type: ignore[arg-type]
        assert strategy == "explicit_error", (
            f"'{previous_year}'이 포함된 쿼리는 'explicit_error' 전략이어야 합니다. "
            f"실제: '{strategy}'"
        )
