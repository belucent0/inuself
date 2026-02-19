"""IntentParserNode._quick_classify 라우팅 로직 단위 테스트.

이 테스트는 LLM 호출 없이 패턴 매칭 로직만 검증합니다.
"안녕하세요" 같은 명확한 입력이 올바른 모드로 라우팅되는지 확인하여
에이전트 코딩으로 인한 라우팅 로직 회귀를 PR 단계에서 차단합니다.

의존성: pytest, pydantic, langchain-core, langgraph, loguru, openai, httpx
(kiwipiepy는 conftest.py에서 mock 처리)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# conftest.py가 kiwipiepy mock과 sys.path를 먼저 설정합니다.
from app.agents.nodes.intent_parser import IntentParserNode
from app.agents.state import AIMode


@pytest.fixture
def node() -> IntentParserNode:
    """최소 설정으로 IntentParserNode 생성 (LLM 호출 없음)."""
    settings = MagicMock()
    return IntentParserNode(settings)


class TestQuickClassifySimple:
    """인사 / 간단한 대화 → SIMPLE 모드 검증."""

    def test_korean_greeting_simple(self, node: IntentParserNode) -> None:
        assert node._quick_classify("안녕하세요") == AIMode.SIMPLE

    def test_korean_greeting_short(self, node: IntentParserNode) -> None:
        assert node._quick_classify("안녕") == AIMode.SIMPLE

    def test_english_hi(self, node: IntentParserNode) -> None:
        assert node._quick_classify("hi") == AIMode.SIMPLE

    def test_english_hello(self, node: IntentParserNode) -> None:
        assert node._quick_classify("hello") == AIMode.SIMPLE

    def test_thanks(self, node: IntentParserNode) -> None:
        assert node._quick_classify("고마워") == AIMode.SIMPLE

    def test_thanks_formal(self, node: IntentParserNode) -> None:
        assert node._quick_classify("감사합니다") == AIMode.SIMPLE

    def test_long_greeting_not_simple(self, node: IntentParserNode) -> None:
        """20자 이상 인사는 SIMPLE 패스스루 아님 → LLM 판단."""
        long = "안녕하세요 오늘 날씨가 정말 좋네요 무엇을 도와드릴까요"
        # 길이 제한(20자) 때문에 SIMPLE이 아닌 None 반환
        assert node._quick_classify(long) != AIMode.SIMPLE


class TestQuickClassifySearch:
    """검색 필요 쿼리 → SEARCH 모드 검증."""

    def test_latest_keyword(self, node: IntentParserNode) -> None:
        assert node._quick_classify("최신 AI 트렌드") == AIMode.SEARCH

    def test_news_keyword(self, node: IntentParserNode) -> None:
        assert node._quick_classify("오늘 IT 뉴스") == AIMode.SEARCH

    def test_today_keyword(self, node: IntentParserNode) -> None:
        assert node._quick_classify("오늘 코스피 지수는?") == AIMode.SEARCH

    def test_realtime_keyword(self, node: IntentParserNode) -> None:
        assert node._quick_classify("실시간 환율 알려줘") == AIMode.SEARCH

    def test_search_command(self, node: IntentParserNode) -> None:
        assert node._quick_classify("파이썬 최신 버전 검색해줘") == AIMode.SEARCH


class TestQuickClassifyRag:
    """내부 문서 참조 쿼리 → RAG 모드 검증."""

    def test_my_document(self, node: IntentParserNode) -> None:
        assert node._quick_classify("내 문서 보여줘") == AIMode.RAG

    def test_my_content(self, node: IntentParserNode) -> None:
        assert node._quick_classify("내 콘텐츠 목록") == AIMode.RAG

    def test_uploaded(self, node: IntentParserNode) -> None:
        # "찾아줘"는 search_patterns에도 있으므로 search trigger 없는 쿼리 사용
        assert node._quick_classify("내가 올린 파일 보여줘") == AIMode.RAG


class TestQuickClassifyReasoning:
    """추론/분석 쿼리 → REASONING 모드 검증."""

    def test_analyze(self, node: IntentParserNode) -> None:
        assert node._quick_classify("이 코드 분석해줘") == AIMode.REASONING

    def test_compare(self, node: IntentParserNode) -> None:
        assert node._quick_classify("A와 B 비교해줘") == AIMode.REASONING

    def test_why(self, node: IntentParserNode) -> None:
        assert node._quick_classify("왜 이런 현상이 생기나요") == AIMode.REASONING

    def test_step_by_step(self, node: IntentParserNode) -> None:
        assert node._quick_classify("단계별로 설명해줘") == AIMode.REASONING

    def test_difference(self, node: IntentParserNode) -> None:
        assert node._quick_classify("두 방식의 차이점은?") == AIMode.REASONING


class TestQuickClassifyFallthrough:
    """패턴 매칭 없음 → None 반환 (LLM이 결정)."""

    def test_factual_question_no_pattern(self, node: IntentParserNode) -> None:
        """팩트 질문이지만 검색 트리거 키워드 없음 → LLM에게 위임."""
        assert node._quick_classify("대한민국의 수도는?") is None

    def test_capital_question(self, node: IntentParserNode) -> None:
        assert node._quick_classify("프랑스 수도가 어디야?") is None

    def test_general_question(self, node: IntentParserNode) -> None:
        assert node._quick_classify("파이썬이란 무엇인가요?") is None


class TestQuickClassifySearchPatternIntegrity:
    """검색 패턴 목록의 핵심 키워드 존재 여부 — 회귀 방지.

    에이전트가 실수로 키워드를 삭제하거나 오타를 내면 이 테스트가 실패합니다.
    """

    REQUIRED_SEARCH_TRIGGERS: list[str] = ["최신", "뉴스", "오늘", "실시간"]
    REQUIRED_GREETING_TRIGGERS: list[str] = ["안녕", "hi", "hello", "감사"]

    @pytest.mark.parametrize("keyword", REQUIRED_SEARCH_TRIGGERS)
    def test_search_trigger_present(
        self, node: IntentParserNode, keyword: str
    ) -> None:
        result = node._quick_classify(keyword)
        assert result == AIMode.SEARCH, (
            f"'{keyword}'가 SEARCH를 트리거해야 합니다. "
            f"search_patterns 목록이 변경되었을 수 있습니다."
        )

    @pytest.mark.parametrize("keyword", REQUIRED_GREETING_TRIGGERS)
    def test_greeting_trigger_present(
        self, node: IntentParserNode, keyword: str
    ) -> None:
        result = node._quick_classify(keyword)
        assert result == AIMode.SIMPLE, (
            f"'{keyword}'가 SIMPLE을 트리거해야 합니다. "
            f"greetings 목록이 변경되었을 수 있습니다."
        )
