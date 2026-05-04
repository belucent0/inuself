"""Query Transformation 플러그인 단위 테스트 (V8.3).

ContextualizeTransformer와 DecomposeTransformer의 동작을 검증합니다.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage

from app.agents.nodes.intent_parser import (
    ContextualizeTransformer,
    DecomposeTransformer,
)
from app.agents.state import GraphState, AIMode


# =============================================================================
# ContextualizeTransformer 테스트
# =============================================================================

class TestContextualizeTransformer:
    """대화 맥락 기반 쿼리 재작성 테스트."""

    @pytest.fixture
    def transformer(self):
        """Transformer 인스턴스 생성."""
        return ContextualizeTransformer()

    @pytest.fixture
    def mock_settings(self):
        """Mock 설정 객체."""
        settings = MagicMock()
        settings.ai_gateway_url = "http://localhost:8003/v1"
        return settings

    def test_should_apply_with_conversation_history(self, transformer):
        """대화 히스토리가 있으면 True 반환."""
        state: GraphState = {
            "query": "그것의 주요 용도는?",
            "messages": [
                HumanMessage(content="파이썬이란?"),
                AIMessage(content="파이썬은 고수준 프로그래밍 언어입니다."),
            ],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        assert transformer.should_apply("그것의 주요 용도는?", state) is True

    def test_should_not_apply_without_history(self, transformer):
        """대화 히스토리가 없으면 False 반환."""
        state: GraphState = {
            "query": "파이썬이란?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        assert transformer.should_apply("파이썬이란?", state) is False

    def test_should_not_apply_with_only_user_message(self, transformer):
        """User 메시지만 있으면 False (AI 응답 필요)."""
        state: GraphState = {
            "query": "두 번째 질문",
            "messages": [
                HumanMessage(content="첫 번째 질문"),
            ],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        assert transformer.should_apply("두 번째 질문", state) is False

    @pytest.mark.asyncio
    async def test_transform_with_reference(self, transformer, mock_settings):
        """참조가 있는 질문 → 재작성."""
        state: GraphState = {
            "query": "그것의 용도는?",
            "messages": [
                HumanMessage(content="파이썬이란?"),
                AIMessage(content="파이썬은 고수준 프로그래밍 언어입니다."),
            ],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        # LLM 응답 모킹
        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = "파이썬의 용도는?"

            result = await transformer.transform("그것의 용도는?", state, mock_settings)

            assert len(result) == 1
            assert "파이썬" in result[0]
            assert mock_llm.called

    @pytest.mark.asyncio
    async def test_transform_without_reference(self, transformer, mock_settings):
        """참조가 없는 질문 → 그대로 반환."""
        state: GraphState = {
            "query": "자바란?",
            "messages": [
                HumanMessage(content="파이썬이란?"),
                AIMessage(content="파이썬은 고수준 프로그래밍 언어입니다."),
            ],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = "자바란?"

            result = await transformer.transform("자바란?", state, mock_settings)

            assert len(result) == 1
            assert result[0] == "자바란?"

    @pytest.mark.asyncio
    async def test_transform_llm_failure(self, transformer, mock_settings):
        """LLM 실패 시 원본 반환."""
        state: GraphState = {
            "query": "그것의 장점은?",
            "messages": [
                HumanMessage(content="파이썬이란?"),
                AIMessage(content="파이썬은 고수준 프로그래밍 언어입니다."),
            ],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.side_effect = Exception("LLM connection error")

            result = await transformer.transform("그것의 장점은?", state, mock_settings)

            # 에러 시 원본 반환
            assert len(result) == 1
            assert result[0] == "그것의 장점은?"

    def test_format_conversation(self, transformer):
        """대화 히스토리 포맷팅 테스트."""
        messages = [
            HumanMessage(content="파이썬이란?"),
            AIMessage(content="파이썬은 고수준 프로그래밍 언어입니다."),
            HumanMessage(content="장점은?"),
        ]

        formatted = transformer._format_conversation(messages)

        assert "사용자: 파이썬이란?" in formatted
        assert "AI: 파이썬은 고수준 프로그래밍 언어입니다." in formatted
        assert "사용자: 장점은?" in formatted

    def test_format_conversation_truncation(self, transformer):
        """긴 메시지 잘림 테스트."""
        long_content = "A" * 300
        messages = [
            HumanMessage(content=long_content),
        ]

        formatted = transformer._format_conversation(messages)

        # 200자로 제한 + "..."
        assert len(formatted) <= 215  # "사용자: " + 200 + "..."


# =============================================================================
# DecomposeTransformer 테스트
# =============================================================================

class TestDecomposeTransformer:
    """질의 분해 테스트."""

    @pytest.fixture
    def transformer(self):
        """Transformer 인스턴스 생성."""
        return DecomposeTransformer()

    @pytest.fixture
    def mock_settings(self):
        """Mock 설정 객체."""
        settings = MagicMock()
        settings.ai_gateway_url = "http://localhost:8003/v1"
        return settings

    def test_should_apply_complex_query(self, transformer):
        """복잡한 질문은 True."""
        state: GraphState = {
            "query": "2024년 AI 트렌드와 미래 산업 영향은?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        assert transformer.should_apply("2024년 AI 트렌드와 미래 산업 영향은?", state) is True

    def test_should_not_apply_simple_query(self, transformer):
        """간단한 질문은 False."""
        state: GraphState = {
            "query": "파이썬이란?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        assert transformer.should_apply("파이썬이란?", state) is False

    def test_is_complex_query_with_markers(self, transformer):
        """복잡도 마커가 있으면 True."""
        assert transformer._is_complex_query("파이썬과 자바의 비교") is True
        assert transformer._is_complex_query("AI가 산업에 미치는 영향") is True
        assert transformer._is_complex_query("2024년 기술 트렌드 분석") is True

    def test_is_complex_query_too_short(self, transformer):
        """짧은 질문은 False."""
        assert transformer._is_complex_query("안녕") is False
        assert transformer._is_complex_query("뭐해") is False

    @pytest.mark.asyncio
    async def test_transform_complex_query(self, transformer, mock_settings):
        """복잡한 질문 → 분해."""
        state: GraphState = {
            "query": "2024년 AI 트렌드와 미래 영향은?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        llm_response = """2024 AI breakthrough technologies
Generative AI trends 2024
AI impact on industries
AI market forecast 2024"""

        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = llm_response

            result = await transformer.transform(
                "2024년 AI 트렌드와 미래 영향은?",
                state,
                mock_settings
            )

            assert len(result) >= 2
            assert len(result) <= 5
            assert "2024" in result[0] or "AI" in result[0]

    @pytest.mark.asyncio
    async def test_transform_simple_query_not_decomposed(self, transformer, mock_settings):
        """간단한 질문은 분해 안 함."""
        state: GraphState = {
            "query": "파이썬이란?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        # LLM이 1개만 반환 (분해 안 함)
        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = "파이썬이란?"

            result = await transformer.transform("파이썬이란?", state, mock_settings)

            # 1개 → 원본 반환
            assert len(result) == 1
            assert result[0] == "파이썬이란?"

    @pytest.mark.asyncio
    async def test_transform_too_many_sub_queries(self, transformer, mock_settings):
        """너무 많은 분해 → 원본 반환."""
        state: GraphState = {
            "query": "복잡한 질문",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        # 8개 반환 (너무 많음)
        llm_response = "\n".join([f"Query {i}" for i in range(8)])

        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = llm_response

            result = await transformer.transform("복잡한 질문", state, mock_settings)

            # 너무 많으면 원본 반환
            assert len(result) == 1
            assert result[0] == "복잡한 질문"

    def test_parse_numbered_list(self, transformer):
        """번호 매겨진 목록 파싱 테스트."""
        text = """1. First query
2. Second query
3) Third query
- Fourth query"""

        result = transformer._parse_numbered_list(text)

        assert len(result) == 4
        assert "First query" in result
        assert "Second query" in result
        assert "Third query" in result
        assert "Fourth query" in result

    def test_parse_numbered_list_filters_short(self, transformer):
        """짧은 항목 필터링."""
        text = """1. OK query
2. NO
3. Another good query"""

        result = transformer._parse_numbered_list(text)

        assert len(result) == 2
        assert "OK query" in result
        assert "Another good query" in result
        assert "NO" not in result  # 너무 짧음


# =============================================================================
# HyDETransformer 테스트
# =============================================================================

class TestHyDETransformer:
    """HyDE (Hypothetical Document Embeddings) 기반 쿼리 재작성 테스트."""

    @pytest.fixture
    def transformer(self):
        """Transformer 인스턴스 생성."""
        from app.agents.nodes.intent_parser import HyDETransformer
        return HyDETransformer()

    @pytest.fixture
    def mock_settings(self):
        """Mock 설정 객체."""
        settings = MagicMock()
        settings.ai_gateway_url = "http://localhost:8003/v1"
        return settings

    def test_should_apply_factoid_query(self, transformer):
        """사실적 질문(factoid)에는 True."""
        state: GraphState = {
            "query": "파이썬이란?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "search_queries": [],  # 기존 쿼리 없음
            "conversation_id": "test-001",
        }

        assert transformer.should_apply("파이썬이란?", state) is True

    def test_should_not_apply_when_enough_queries(self, transformer):
        """이미 충분한 쿼리가 있으면 False."""
        state: GraphState = {
            "query": "파이썬이란?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "search_queries": ["query1", "query2", "query3"],  # 3개 이상
            "conversation_id": "test-001",
        }

        assert transformer.should_apply("파이썬이란?", state) is False

    def test_should_not_apply_too_short(self, transformer):
        """너무 짧은 질문은 False."""
        state: GraphState = {
            "query": "안녕",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "search_queries": [],
            "conversation_id": "test-001",
        }

        assert transformer.should_apply("안녕", state) is False

    def test_should_not_apply_too_long(self, transformer):
        """너무 긴 질문은 False."""
        long_query = "A" * 150
        state: GraphState = {
            "query": long_query,
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "search_queries": [],
            "conversation_id": "test-001",
        }

        assert transformer.should_apply(long_query, state) is False

    @pytest.mark.asyncio
    async def test_transform_factoid_query(self, transformer, mock_settings):
        """사실적 질문 → HyDE 쿼리 생성."""
        state: GraphState = {
            "query": "파이썬 웹 프레임워크 비교",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "search_queries": [],
            "conversation_id": "test-001",
        }

        # LLM 응답 모킹 (가설적 답변)
        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = "Django와 Flask는 파이썬의 대표적인 웹 프레임워크입니다."

            result = await transformer.transform("파이썬 웹 프레임워크 비교", state, mock_settings)

            # HyDE 쿼리가 생성되어야 함
            assert len(result) == 1
            assert len(result[0]) > 0
            # 키워드가 포함되어야 함
            assert any(kw in result[0] for kw in ["Django", "Flask", "프레임", "웹"])

    @pytest.mark.asyncio
    async def test_transform_llm_failure(self, transformer, mock_settings):
        """LLM 실패 시 빈 목록 반환."""
        state: GraphState = {
            "query": "파이썬이란?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "search_queries": [],
            "conversation_id": "test-001",
        }

        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.side_effect = Exception("LLM connection error")

            result = await transformer.transform("파이썬이란?", state, mock_settings)

            # 에러 시 빈 목록 반환
            assert len(result) == 0

    @pytest.mark.asyncio
    async def test_transform_too_few_keywords(self, transformer, mock_settings):
        """키워드가 너무 적으면 빈 목록."""
        state: GraphState = {
            "query": "파이썬이란?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "search_queries": [],
            "conversation_id": "test-001",
        }

        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = "언어입니다"  # 키워드 1개만 추출 가능

            result = await transformer.transform("파이썬이란?", state, mock_settings)

            # 키워드 부족으로 빈 목록
            assert len(result) == 0


# =============================================================================
# 통합 시나리오 테스트
# =============================================================================

class TestQueryTransformationIntegration:
    """실제 사용 시나리오 테스트."""

    @pytest.fixture
    def contextualize(self):
        return ContextualizeTransformer()

    @pytest.fixture
    def decompose(self):
        return DecomposeTransformer()

    @pytest.fixture
    def hyde(self):
        from app.agents.nodes.intent_parser import HyDETransformer
        return HyDETransformer()

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.ai_gateway_url = "http://localhost:8003/v1"
        return settings

    @pytest.mark.asyncio
    async def test_scenario_followup_question(
        self,
        contextualize,
        decompose,
        mock_settings
    ):
        """시나리오: 후속 질문.

        User: "파이썬이란?"
        AI: "..."
        User: "그것의 용도는?" ← 이 질문 처리
        """
        state: GraphState = {
            "query": "그것의 용도는?",
            "messages": [
                HumanMessage(content="파이썬이란?"),
                AIMessage(content="파이썬은 고수준 프로그래밍 언어입니다."),
            ],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        # 1. Contextualize 적용
        assert contextualize.should_apply("그것의 용도는?", state) is True

        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = "파이썬의 용도는?"

            contextualized = await contextualize.transform(
                "그것의 용도는?",
                state,
                mock_settings
            )

            assert "파이썬" in contextualized[0]

        # 2. Decompose는 적용 안 함 (간단한 질문)
        assert decompose.should_apply(contextualized[0], state) is False

    @pytest.mark.asyncio
    async def test_scenario_complex_question(
        self,
        contextualize,
        decompose,
        mock_settings
    ):
        """시나리오: 복잡한 질문 (분해 필요).

        User: "2024년 AI 트렌드와 미래 영향은?"
        """
        state: GraphState = {
            "query": "2024년 AI 트렌드와 미래 영향은?",
            "messages": [],
            "mode": AIMode.SEARCH,
            "thinking_steps": [],
            "search_results": [],
            "conversation_id": "test-001",
        }

        # 1. Contextualize는 적용 안 함 (히스토리 없음)
        assert contextualize.should_apply("2024년 AI 트렌드와 미래 영향은?", state) is False

        # 2. Decompose 적용
        assert decompose.should_apply("2024년 AI 트렌드와 미래 영향은?", state) is True

        llm_response = """2024 AI breakthrough
Generative AI trends
AI industry impact"""

        with patch("app.agents.nodes.intent_parser.async_llm_completion") as mock_llm:
            mock_llm.return_value = llm_response

            decomposed = await decompose.transform(
                "2024년 AI 트렌드와 미래 영향은?",
                state,
                mock_settings
            )

            assert len(decomposed) >= 2
            assert len(decomposed) <= 5
