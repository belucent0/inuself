"""Intent Parser 노드.

사용자 쿼리의 의도를 분석하여 적절한 AI 모드를 결정합니다.
또한 Tier 기반 라우팅을 수행하여 쿼리에 적합한 능력 티어를 선택합니다.

설계 원칙:
- Backend(LangGraph): WHAT - "이 쿼리에 어떤 능력이 필요한가?" (tier 결정)
- Infrastructure(StreamProcessor): HOW - "그 능력을 어떤 모델로 제공할 것인가?" (model 결정)

V8.1: 형태소 분석(kiwi) + 임베딩 하이브리드 쿼리 추출
- LLM 기반 쿼리 생성 대신 형태소 분석으로 키워드 추출
- 코드/에러 쿼리 자동 감지
- 임베딩 기반 의미적 확장 (선택적)

V8.3: 플러그인 기반 Query Transformation 아키텍처
- QueryTransformer 추상 인터페이스
- 대화 맥락 기반 쿼리 재작성 (ContextualizeTransformer)
- 질의 분해 (DecomposeTransformer)
- 모듈형 설계로 새 기법 추가 용이
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from kiwipiepy import Kiwi
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from loguru import logger

from ..state import GraphState, AIMode, ThinkingStep, QueryAnalysis
from ..tools.llm_client import async_llm_completion
from ..tools.model_router import TierRouter
from ..tools.datetime_tool import get_current_datetime
from ...core.llm_tier import LLMTier

# Kiwi 형태소 분석기 싱글톤 (로딩 시간 절약)
_kiwi_instance: Kiwi | None = None
_kiwi_warmed_up: bool = False


def get_kiwi() -> Kiwi:
    """Kiwi 형태소 분석기 싱글톤 반환."""
    global _kiwi_instance
    if _kiwi_instance is None:
        logger.info("[IntentParser] Initializing Kiwi morphological analyzer...")
        _kiwi_instance = Kiwi()
        logger.info("[IntentParser] Kiwi initialized successfully")
    return _kiwi_instance


def warmup_kiwi() -> None:
    """Kiwi 워밍업 (앱 시작 시 호출하여 첫 요청 지연 방지)."""
    global _kiwi_warmed_up
    if _kiwi_warmed_up:
        return

    import time

    start = time.time()
    kiwi = get_kiwi()
    # 첫 분석으로 내부 캐시 워밍업
    kiwi.analyze("워밍업 테스트 문장입니다")
    _kiwi_warmed_up = True
    logger.info(f"[IntentParser] Kiwi warmed up in {time.time() - start:.2f}s")


# 코드/에러 패턴 정규식
CODE_PATTERNS = [
    r"[{}\[\]();]",  # 괄호, 세미콜론
    r"^\s*(def|class|import|from|const|let|var|function)\s",  # 함수/클래스 선언
    r"(Error|Exception|Traceback|TypeError|ValueError)",  # 에러 타입
    r'(\.py|\.js|\.ts|\.java|\.cpp|\.go)[:"\']',  # 파일 확장자
    r"(https?://|localhost:\d+)",  # URL
    r"(\w+\.\w+\()",  # 메서드 호출
    r"(=>|->|::)",  # 연산자
]

# 검색 제약 힌트 패턴
RECENCY_HINT_PATTERNS: list[tuple[str, list[str]]] = [
    ("day", ["오늘", "금일", "실시간", "방금", "속보", "latest", "today", "breaking"]),
    ("week", ["이번주", "이번 주", "지난주", "지난 주", "주간", "weekly", "this week"]),
    (
        "month",
        ["이번달", "이번 달", "지난달", "지난 달", "월간", "month", "monthly", "최근"],
    ),
    ("year", ["올해", "작년", "연간", "year", "yearly"]),
]

LANGUAGE_HINT_PATTERNS: dict[str, list[str]] = {
    "ko-KR": ["한국어", "한글", "국문", "korean", "kor"],
    "en-US": ["영어", "영문", "english", "eng"],
    "ja-JP": ["일본어", "일문", "japanese", "jpn", "일본 기사"],
    "zh-CN": ["중국어", "중문", "chinese", "chn", "중국 기사"],
}

OFFICIAL_DOC_DOMAIN_HINTS: dict[str, list[str]] = {
    "python": ["python.org", "docs.python.org"],
    "fastapi": ["fastapi.tiangolo.com", "python.org"],
    "pytorch": ["pytorch.org"],
    "tensorflow": ["tensorflow.org"],
    "react": ["react.dev"],
    "django": ["docs.djangoproject.com"],
}

MIN_QUERY_COUNT_SEARCH = 2
MIN_QUERY_COUNT_HYBRID = 3
MAX_QUERY_COUNT = 5

FACTOID_QUERY_MARKERS = {
    "무엇",
    "어떤",
    "누구",
    "언제",
    "어디",
    "몇",
    "1위",
    "최고",
    "최초",
    "순위",
    "top",
}

# 검색 쿼리에서 의미가 약한 일반어
LOW_SIGNAL_NOUNS = {
    "정도",
    "수준",
    "부분",
    "관련",
    "기준",
    "의미",
    "내용",
    "정보",
    "결과",
}

# 의도 분석 프롬프트
INTENT_ANALYSIS_PROMPT = """당신은 사용자 질문의 의도를 분석하는 전문가입니다.

오늘 날짜: {current_date}

다음 질문을 분석하여 가장 적합한 모드를 선택하세요:

사용자 질문: {query}

모드 선택 기준:
- simple: 인사, 일반 대화, 간단한 질문 (최신 정보나 검색이 필요 없는 경우)
- search: 최신 뉴스, 실시간 정보, 특정 웹사이트 검색이 필요한 경우
  (오늘 날짜 기준으로 학습 데이터에 없을 가능성이 높은 정보 포함)
- rag: 사용자의 기존 콘텐츠나 문서를 참조해야 하는 경우
- reasoning: 복잡한 분석, 비교, 추론, 단계별 설명이 필요한 경우
- hybrid: 웹 검색과 내부 문서 검색을 모두 활용해야 하는 경우

다음 JSON 형식으로만 응답하세요:
{{"mode": "simple|search|rag|reasoning|hybrid", "confidence": 0.0~1.0, "reason": "선택 이유"}}"""

# Multi-Query 프롬프트 - 줄바꿈으로 구분된 검색어 목록
MULTI_QUERY_PROMPT = """다음 질문에 대해 웹 검색에 효과적인 검색어 3개를 생성하세요.
각 검색어는 한 줄에 하나씩, 다른 관점에서 작성하세요.

질문: {query}

검색어:"""


# =============================================================================
# Query Transformation Plugin Architecture (V8.3)
# =============================================================================


class QueryTransformer(ABC):
    """쿼리 변환 기법의 추상 인터페이스.

    새로운 쿼리 변환 기법을 추가하려면 이 클래스를 상속하고
    transform()과 should_apply() 메서드를 구현하세요.
    """

    @abstractmethod
    async def transform(
        self, query: str, state: GraphState, settings: Any
    ) -> list[str]:
        """쿼리를 변환하여 새 쿼리 목록 반환.

        Args:
            query: 원본 사용자 쿼리
            state: 현재 그래프 상태 (대화 히스토리 등)
            settings: 애플리케이션 설정

        Returns:
            변환된 쿼리 목록
        """
        pass

    @abstractmethod
    def should_apply(self, query: str, state: GraphState) -> bool:
        """이 기법을 적용해야 하는지 판단.

        Args:
            query: 원본 사용자 쿼리
            state: 현재 그래프 상태

        Returns:
            True면 이 transformer 적용
        """
        pass


class ContextualizeTransformer(QueryTransformer):
    """대화 맥락을 고려하여 쿼리를 독립적으로 재작성하는 transformer.

    문제:
    - User: "파이썬이란?"
    - AI: "파이썬은 고수준 프로그래밍 언어입니다..."
    - User: "그것의 주요 용도는?" ← 맥락 없이 검색하면 무의미

    해결:
    - 대화 히스토리 참조 → "파이썬의 주요 용도는?" 로 재작성
    """

    async def transform(
        self, query: str, state: GraphState, settings: Any
    ) -> list[str]:
        """대화 맥락을 반영하여 쿼리 재작성."""
        messages = state.get("messages", [])

        if len(messages) < 2:
            # 첫 질문이면 그대로 반환
            return [query]

        # 최근 3턴(User+AI 3쌍)만 사용 (너무 긴 히스토리는 노이즈)
        recent_messages = messages[-6:]

        # 대화 히스토리 포맷팅
        conversation_context = self._format_conversation(recent_messages)

        context_prompt = f"""이전 대화:
{conversation_context}

현재 질문: "{query}"

현재 질문이 이전 대화를 참조하고 있다면 (예: "그것", "그거", "그 사람", "더 자세히" 등),
독립적으로 이해 가능한 완전한 질문으로 재작성하세요.
참조가 없다면 원본 질문을 그대로 반환하세요.

예시:
- "그것의 장점은?" → "파이썬의 장점은?"
- "더 자세히" → "파이썬에 대해 더 자세히 설명해줘"
- "최신 버전은?" → "최신 파이썬 버전은?"
- "파이썬이란?" → "파이썬이란?" (참조 없음, 그대로)

재작성된 질문만 출력하세요 (추가 설명 없이):"""

        try:
            response = await async_llm_completion(
                settings=settings,
                messages=[{"role": "user", "content": context_prompt}],
                temperature=0.1,
                max_tokens=200,
            )

            contextualized = response.strip()

            # 유효성 검사: 너무 길거나 이상하면 원본 사용
            if len(contextualized) > 200 or len(contextualized) < 3:
                logger.warning(
                    f"[ContextualizeTransformer] Invalid output, using original: {contextualized[:50]}"
                )
                return [query]

            logger.info(f"[ContextualizeTransformer] '{query}' → '{contextualized}'")
            return [contextualized]

        except Exception as e:
            logger.error(f"[ContextualizeTransformer] Failed: {e}")
            return [query]

    def should_apply(self, query: str, state: GraphState) -> bool:
        """대화 히스토리가 있을 때만 적용."""
        messages = state.get("messages", [])
        # 최소 2개 메시지 (User 1개 + AI 1개) 있어야 맥락 참조 가능
        return len(messages) >= 2

    def _format_conversation(self, messages: list[BaseMessage]) -> str:
        """대화 히스토리 포맷팅.

        Args:
            messages: 대화 메시지 목록

        Returns:
            포맷팅된 대화 문자열
        """
        formatted = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                role = "사용자"
            elif isinstance(msg, AIMessage):
                role = "AI"
            else:
                role = msg.type

            # 최대 200자로 제한 (너무 긴 응답은 요약)
            content = msg.content[:200]
            if len(msg.content) > 200:
                content += "..."

            formatted.append(f"{role}: {content}")

        return "\n".join(formatted)


class DecomposeTransformer(QueryTransformer):
    """복잡한 질문을 하위 질문으로 분해하는 transformer.

    예시:
    질문: "2024년 AI 트렌드와 미래 산업 영향은?"
    분해:
    1. 2024 AI breakthrough technologies
    2. Generative AI trends 2024
    3. AI impact on industries future
    4. AI market forecast 2024-2025
    """

    async def transform(
        self, query: str, state: GraphState, settings: Any
    ) -> list[str]:
        """복잡한 질문을 3-5개 하위 질문으로 분해."""

        decomposition_prompt = f"""다음 질문을 3-5개의 독립적인 하위 질문으로 분해하세요.
각 하위 질문은 웹 검색에 적합한 형태여야 합니다.

질문: "{query}"

예시:
질문: "2024년 AI 트렌드와 미래 산업 영향은?"
분해:
1. 2024 AI breakthrough technologies
2. Generative AI trends 2024
3. AI impact on industries future
4. AI market forecast 2024-2025

간단한 질문(한 문장, 팩트 조회)이면 분해하지 말고 원본 반환하세요.

분해된 질문 (각 줄에 번호 없이 하나씩):"""

        try:
            response = await async_llm_completion(
                settings=settings,
                messages=[{"role": "user", "content": decomposition_prompt}],
                temperature=0.3,
                max_tokens=300,
            )

            # 줄바꿈으로 분리된 쿼리 파싱
            sub_queries = self._parse_numbered_list(response)

            # 유효성 검사: 너무 많거나 적으면 원본 사용
            if len(sub_queries) < 2 or len(sub_queries) > 7:
                logger.info(
                    f"[DecomposeTransformer] Not decomposed (count={len(sub_queries)}), using original"
                )
                return [query]

            logger.info(
                f"[DecomposeTransformer] Decomposed into {len(sub_queries)} sub-queries"
            )
            return sub_queries[:5]  # 최대 5개로 제한

        except Exception as e:
            logger.error(f"[DecomposeTransformer] Failed: {e}")
            return [query]

    def should_apply(self, query: str, state: GraphState) -> bool:
        """복잡한 질문인지 판단."""
        return self._is_complex_query(query)

    def _is_complex_query(self, query: str) -> bool:
        """복잡한 질문인지 판단.

        여러 절이 있거나, "와/과", "영향", "비교" 등 키워드 포함.

        Args:
            query: 사용자 쿼리

        Returns:
            True면 복잡한 질문
        """
        # 길이 체크
        if len(query) < 20:
            return False

        # 복잡도 마커
        complexity_markers = [
            "와",
            "과",
            "영향",
            "비교",
            "차이",
            "관계",
            "트렌드",
            "분석",
            "전망",
            "미래",
            "변화",
            "발전",
            "역사",
            "장단점",
            "장점과 단점",
            "어떻게 다른",
            "무엇이 다른",
        ]

        return any(marker in query for marker in complexity_markers)

    def _parse_numbered_list(self, text: str) -> list[str]:
        """번호 매겨진 목록 파싱.

        Args:
            text: LLM 응답 텍스트

        Returns:
            파싱된 항목 목록
        """
        lines = text.strip().split("\n")
        items = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 번호 제거: "1. ", "1) ", "- " 등
            cleaned = re.sub(r"^[\d\-\*\•]+[\.\)]\s*", "", line)
            if cleaned and len(cleaned) >= 5:  # 최소 5자
                items.append(cleaned)

        return items


class HyDETransformer(QueryTransformer):
    """HyDE (Hypothetical Document Embeddings) 기반 쿼리 재작성 transformer.

    핵심 아이디어:
    - 질문에 대한 "이상적인 답변"을 가설적으로 생성
    - 그 답변에서 키워드 추출 → 검색 쿼리로 사용
    - 검색 엔진이 "답변이 포함된 문서"를 찾도록 유도

    예시:
    질문: "파이썬 웹 프레임워크 비교"
    가설적 답변: "Django와 Flask는 파이썬의 대표적인 웹 프레임워크입니다.
                 Django는 Full-stack, Flask는 Micro framework입니다."
    HyDE 쿼리: "Django Flask 웹 프레임워크 Full-stack Micro"
    """

    async def transform(
        self, query: str, state: GraphState, settings: Any
    ) -> list[str]:
        """HyDE 기반 쿼리 재작성."""

        hyde_prompt = f"""질문: "{query}"

위 질문에 대한 이상적인 답변을 1-2문장으로 작성하세요.
(검색 엔진이 찾아야 할 답변의 예시입니다)

답변에는 핵심 사실, 용어, 개념을 포함하되, 간결하게 작성하세요.

답변:"""

        try:
            # 가설적 답변 생성
            hypothetical_answer = await async_llm_completion(
                settings=settings,
                messages=[{"role": "user", "content": hyde_prompt}],
                temperature=0.5,  # 약간의 창의성 허용
                max_tokens=200,
            )

            # 답변에서 키워드 추출 (Kiwi 형태소 분석 사용)
            hyde_keywords = self._extract_keywords_from_hyde(hypothetical_answer)

            # 키워드가 너무 적으면 HyDE 적용 안 함
            if len(hyde_keywords) < 2:
                logger.info(
                    f"[HyDETransformer] Too few keywords ({len(hyde_keywords)}), skipping"
                )
                return []

            # 키워드 기반 검색 쿼리 생성 (상위 5개)
            hyde_query = " ".join(hyde_keywords[:5])

            logger.info(f"[HyDETransformer] '{query}' → HyDE: '{hyde_query}'")
            return [hyde_query]

        except Exception as e:
            logger.error(f"[HyDETransformer] Failed: {e}")
            return []

    def should_apply(self, query: str, state: GraphState) -> bool:
        """HyDE를 적용해야 하는지 판단.

        HyDE는 사실적 질문(factoid queries)에 효과적입니다.
        대화 맥락이나 질의 분해보다 낮은 우선순위로 선택적 적용.

        적용 조건:
        - 검색 쿼리가 아직 3개 미만
        - 간단한 사실 질문 (정의, 비교, 방법 등)
        """
        # 이미 충분한 쿼리가 있으면 적용 안 함
        existing_queries = state.get("search_queries", [])
        if len(existing_queries) >= 3:
            return False

        # HyDE에 적합한 패턴: 정의, 비교, 방법, 특징
        hyde_patterns = [
            "이란",
            "무엇",
            "어떤",
            "어떻게",
            "방법",
            "비교",
            "차이",
            "특징",
            "장점",
            "단점",
            "종류",
            "예시",
        ]

        # 질문이 너무 짧거나 길면 제외
        if len(query) < 10 or len(query) > 100:
            return False

        return any(pattern in query for pattern in hyde_patterns)

    def _extract_keywords_from_hyde(self, hypothetical_answer: str) -> list[str]:
        """가설적 답변에서 키워드 추출.

        Kiwi 형태소 분석을 사용하여 명사, 외국어, 고유명사 추출.

        Args:
            hypothetical_answer: 생성된 가설적 답변

        Returns:
            키워드 목록
        """
        try:
            kiwi = get_kiwi()
            result = kiwi.analyze(hypothetical_answer)

            if not result:
                return []

            tokens = result[0][0]
            keywords = []

            # 추출할 품사: NNG(일반명사), NNP(고유명사), SL(외국어)
            target_pos = {"NNG", "NNP", "SL", "SH"}

            for token in tokens:
                form = token.form
                tag = token.tag

                # 최소 2글자
                if len(form) < 2:
                    continue

                # 불용어 제외
                stopwords = {
                    "것",
                    "수",
                    "때",
                    "등",
                    "중",
                    "내",
                    "더",
                    "안",
                    "및",
                    "또는",
                    "입니다",
                    "있습니다",
                }
                if form in stopwords:
                    continue

                if tag in target_pos:
                    if form not in keywords:  # 중복 방지
                        keywords.append(form)

            return keywords

        except Exception as e:
            logger.warning(f"[HyDETransformer] Kiwi analysis failed: {e}")
            # Fallback: 공백으로 단순 분리
            return [w for w in hypothetical_answer.split() if len(w) >= 2][:10]


# 명백히 한국어 전용 컨텍스트 마커 (LLM 호출 없이 사전 필터링)
_KO_ONLY_MARKERS = frozenset({
    "대통령", "국회", "선거", "코스피", "코스닥", "한국은행",
    "김치", "한복", "한옥", "불고기", "비빔밥", "된장",
    "서울 맛집", "국내 맛집", "동네 맛집",
    "지하철 노선", "버스 노선",
})

# LLM 판단 프롬프트
_LANGUAGE_ROUTER_PROMPT = """당신은 검색 쿼리 언어 전문가입니다.
사용자 질문을 분석하여, 영어 웹 검색이 더 좋은 품질의 문서를 줄 수 있는지 판단하세요.

**판단 기준**:
- 프로그래밍/기술 문서 → 영어 검색이 대부분 우수
- 학술 논문/연구 → 영어 검색 우수
- 글로벌 기업/제품 정보 → 영어 검색 병행이 유리
- 한국 로컬 뉴스/정치/문화 → 한국어 검색만으로 충분
- 한국 기업이지만 글로벌 관련 → 한국어 + 영어 병행

**사용자 질문**: {query}

다음 JSON 형식으로만 답하세요:
{{
  "needs_english": true/false,
  "reason": "판단 이유 (한 문장)",
  "english_queries": ["영어 쿼리 1", "영어 쿼리 2"]
}}

**중요**: english_queries는 needs_english=true일 때만 포함하며, 최대 2개.
사용자 질문의 핵심 의도를 영어로 자연스럽게 변환한 검색 쿼리여야 합니다."""


class LanguageRouterTransformer(QueryTransformer):
    """LLM 기반 검색 언어 지능형 결정 transformer.

    사용자 질문 의도를 분석하여 영어 검색이 더 나은 품질의 문서를 제공할지
    선제적으로 판단하고, 필요시 영어 쿼리를 자동 생성합니다.

    판단 결과는 self.last_decision에 저장되어 reformulate_query()의
    Phase 3.5에서 언어 태깅에 활용됩니다.
    """

    def __init__(self) -> None:
        self.last_decision: dict[str, Any] | None = None

    def should_apply(self, query: str, state: GraphState) -> bool:
        """저비용 사전 필터링 (LLM 호출 없음)."""
        mode = state.get("mode")
        if mode not in (AIMode.SEARCH, AIMode.HYBRID, None):
            return False

        # 명백한 한국어 전용 쿼리 스킵
        if any(marker in query for marker in _KO_ONLY_MARKERS):
            return False

        # 20자 미만 순수 한글 → 스킵 (짧고 단순한 국내 쿼리)
        if len(query) < 20 and not re.search(r"[a-zA-Z]{3,}", query):
            return False

        return True

    async def transform(
        self, query: str, state: GraphState, settings: Any
    ) -> list[str]:
        """단일 LLM 호출로 영어 필요 여부 판단 + 영어 쿼리 생성."""
        prompt = _LANGUAGE_ROUTER_PROMPT.format(query=query)

        try:
            response = await async_llm_completion(
                settings=settings,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )

            # JSON 파싱
            raw = response.strip()
            # 마크다운 코드블록 제거
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip("` \n")
            decision = json.loads(raw)

            self.last_decision = decision
            needs_english = bool(decision.get("needs_english", False))
            english_queries: list[str] = decision.get("english_queries", [])

            if needs_english and english_queries:
                valid_en = [
                    q for q in english_queries
                    if isinstance(q, str) and len(q) >= 3
                ][:2]
                logger.info(
                    f"[LanguageRouterTransformer] needs_english=True, "
                    f"reason={decision.get('reason', '')}, "
                    f"en_queries={valid_en}"
                )
                return valid_en
            else:
                logger.info(
                    f"[LanguageRouterTransformer] needs_english=False, "
                    f"reason={decision.get('reason', '')}"
                )
                return []

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[LanguageRouterTransformer] JSON parse failed: {e}")
            self.last_decision = None
            return []
        except Exception as e:
            logger.error(f"[LanguageRouterTransformer] Failed: {e}")
            self.last_decision = None
            return []


class IntentParserNode:
    """사용자 쿼리의 의도를 분석하여 적절한 모드를 결정하는 노드.

    또한 임베딩 기반 Tier 라우팅을 수행합니다.
    선택된 tier는 인프라 레이어(StreamProcessor)에서 실제 모델로 매핑됩니다.

    V8.3: 플러그인 기반 Query Transformation
    - QueryTransformer 플러그인들을 순차 적용
    - 대화 맥락 반영, 질의 분해 등
    """

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정 (AI Gateway 연결 정보 등)
        """
        self.settings = settings
        self.tier_router = TierRouter(settings)

        # Query Transformation 플러그인 등록 (순서대로 실행)
        self.transformers: list[QueryTransformer] = [
            ContextualizeTransformer(),    # 1순위: 대화 맥락 반영
            DecomposeTransformer(),        # 2순위: 질의 분해
            HyDETransformer(),             # 3순위: HyDE (Phase 1B)
            LanguageRouterTransformer(),   # 4순위: 영어 검색 판단 + 쿼리 생성
        ]

        logger.info(
            f"[IntentParser] Initialized with {len(self.transformers)} query transformers"
        )

    def _resolve_selected_model(self, state: GraphState, fallback_tier: str) -> str:
        """요청 메타데이터 모델 오버라이드를 우선 적용한다.

        우선순위:
        1. metadata.model 또는 metadata.llm_model
        2. tier router가 선택한 fallback_tier
        """
        metadata = state.get("metadata")
        if isinstance(metadata, dict):
            requested = metadata.get("model") or metadata.get("llm_model")
            if isinstance(requested, str):
                normalized = requested.strip()
                if normalized:
                    logger.info(
                        f"[IntentParser] Using requested model override: {normalized}"
                    )
                    return normalized
        return fallback_tier

    async def __call__(self, state: GraphState) -> dict:
        """의도 분석 실행.

        Args:
            state: 현재 그래프 상태

        Returns:
            업데이트할 상태 딕셔너리
        """
        query = state["query"]
        current_mode = state.get("mode")
        thinking_steps = list(state.get("thinking_steps", []))

        # 명시적으로 모드가 지정된 경우 (SIMPLE이 아닌 경우) 분석 건너뛰기
        # SIMPLE은 기본값이므로 auto로 간주
        if current_mode and current_mode != AIMode.SIMPLE:
            logger.info(
                f"[IntentParser] Using explicitly specified mode: {current_mode}"
            )
            thinking_steps.append(
                ThinkingStep(
                    step="intent_analysis",
                    content="질문 분석 중...",
                    timestamp=time.time(),
                )
            )

            # Tier 기반 라우팅
            selected_tier = await self.tier_router.select_tier(
                query=query, mode=current_mode.value, context_size=0
            )
            logger.info(f"[IntentParser] Tier routing: {selected_tier}")

            # 검색 모드면 쿼리 재정의 수행
            query_analysis = None
            search_queries = [query]  # 기본값: 원본 쿼리
            if current_mode in (AIMode.SEARCH, AIMode.HYBRID):
                query_analysis = await self.reformulate_query(
                    query,
                    state,
                    mode=current_mode,
                )
                if query_analysis:
                    search_queries = query_analysis.get("sub_queries", [query])
                    thinking_steps.append(
                        ThinkingStep(
                            step="query_reformulation",
                            content=f"검색 쿼리 재정의: {query_analysis.get('search_focus', '')}",
                            timestamp=time.time(),
                        )
                    )

            thinking_steps.append(
                ThinkingStep(
                    step="intent_result",
                    content=f"모드: {current_mode}, 티어: {selected_tier}",
                    timestamp=time.time(),
                )
            )
            selected_model = self._resolve_selected_model(state, selected_tier)
            return {
                "mode": current_mode,
                "selected_model": selected_model,
                "intent_confidence": 1.0,
                "requires_clarification": False,
                "query_analysis": query_analysis,
                "search_queries": search_queries,
                "thinking_steps": thinking_steps,
            }

        # content_context가 있으면 RAG 우선 (auto/simple 모드에서만)
        content_context = state.get("content_context", "")
        if content_context and (not current_mode or current_mode == AIMode.SIMPLE):
            logger.info("[IntentParser] content_context detected → RAG mode")
            thinking_steps.append(
                ThinkingStep(
                    step="intent_analysis",
                    content="콘텐츠 컨텍스트 감지 → 내 문서 검색 모드",
                    timestamp=time.time(),
                )
            )
            selected_tier = await self.tier_router.select_tier(
                query=query, mode="rag", context_size=len(content_context)
            )
            thinking_steps.append(
                ThinkingStep(
                    step="intent_result",
                    content=f"RAG 모드 (콘텐츠 우선), 티어: {selected_tier}",
                    timestamp=time.time(),
                )
            )
            selected_model = self._resolve_selected_model(state, selected_tier)
            return {
                "mode": AIMode.RAG,
                "selected_model": selected_model,
                "intent_confidence": 1.0,
                "requires_clarification": False,
                "query_analysis": None,
                "search_queries": [query],
                "thinking_steps": thinking_steps,
            }

        # 사고 과정 기록
        thinking_steps.append(
            ThinkingStep(
                step="intent_analysis",
                content=f"사용자 질문 분석 중: '{query[:50]}...'",
                timestamp=time.time(),
            )
        )

        # 간단한 패턴 매칭으로 빠른 분류 (LLM 호출 최소화)
        quick_mode = self._quick_classify(query)
        if quick_mode:
            logger.info(f"[IntentParser] Quick classification: mode={quick_mode}")

            # Tier 기반 라우팅
            selected_tier = await self.tier_router.select_tier(
                query=query, mode=quick_mode.value, context_size=0
            )
            logger.info(f"[IntentParser] Tier routing: {selected_tier}")

            # 검색 모드면 쿼리 재정의 수행
            query_analysis = None
            search_queries = [query]
            if quick_mode in (AIMode.SEARCH, AIMode.HYBRID):
                query_analysis = await self.reformulate_query(
                    query,
                    state,
                    mode=quick_mode,
                )
                if query_analysis:
                    search_queries = query_analysis.get("sub_queries", [query])
                    thinking_steps.append(
                        ThinkingStep(
                            step="query_reformulation",
                            content=f"검색 쿼리 재정의: {query_analysis.get('search_focus', '')}",
                            timestamp=time.time(),
                        )
                    )

            thinking_steps.append(
                ThinkingStep(
                    step="intent_result",
                    content=f"빠른 분류: {quick_mode} 모드, 티어: {selected_tier}",
                    timestamp=time.time(),
                )
            )
            selected_model = self._resolve_selected_model(state, selected_tier)
            return {
                "mode": quick_mode,
                "selected_model": selected_model,
                "intent_confidence": 0.9,
                "requires_clarification": False,
                "query_analysis": query_analysis,
                "search_queries": search_queries,
                "thinking_steps": thinking_steps,
            }

        # LLM을 사용한 정밀 분류
        try:
            prompt = INTENT_ANALYSIS_PROMPT.format(
                query=query,
                current_date=get_current_datetime(),
            )
            response = await async_llm_completion(
                settings=self.settings,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,  # 일관된 결과를 위해 낮은 온도
                max_tokens=200,
            )

            # JSON 파싱
            result = self._parse_intent_response(response)
            mode = AIMode(result.get("mode", "simple"))
            confidence = float(result.get("confidence", 0.7))
            reason = result.get("reason", "")

            logger.info(
                f"[IntentParser] LLM classification: mode={mode}, confidence={confidence}"
            )

            # Tier 기반 라우팅
            selected_tier = await self.tier_router.select_tier(
                query=query, mode=mode.value, context_size=0
            )
            logger.info(f"[IntentParser] Tier routing: {selected_tier}")

            # 검색 모드면 쿼리 재정의 수행
            query_analysis = None
            search_queries = [query]
            if mode in (AIMode.SEARCH, AIMode.HYBRID):
                query_analysis = await self.reformulate_query(
                    query,
                    state,
                    mode=mode,
                )
                if query_analysis:
                    search_queries = query_analysis.get("sub_queries", [query])
                    thinking_steps.append(
                        ThinkingStep(
                            step="query_reformulation",
                            content=f"검색 쿼리 재정의: {query_analysis.get('search_focus', '')}",
                            timestamp=time.time(),
                        )
                    )

            thinking_steps.append(
                ThinkingStep(
                    step="intent_result",
                    content=f"의도 분석: {mode} 모드, 티어: {selected_tier} (신뢰도: {confidence:.0%})",
                    timestamp=time.time(),
                )
            )

            selected_model = self._resolve_selected_model(state, selected_tier)
            return {
                "mode": mode,
                "selected_model": selected_model,
                "intent_confidence": confidence,
                "requires_clarification": confidence < 0.7,
                "query_analysis": query_analysis,
                "search_queries": search_queries,
                "thinking_steps": thinking_steps,
            }

        except Exception as e:
            logger.warning(
                f"[IntentParser] LLM classification failed: {e}, falling back to simple mode"
            )
            thinking_steps.append(
                ThinkingStep(
                    step="intent_error",
                    content=f"의도 분석 실패, 기본 모드 사용: {str(e)}",
                    timestamp=time.time(),
                )
            )
            selected_model = self._resolve_selected_model(state, LLMTier.SIMPLE)
            return {
                "mode": AIMode.SIMPLE,
                "selected_model": selected_model,
                "intent_confidence": 0.5,
                "requires_clarification": False,
                "query_analysis": None,
                "search_queries": [query],
                "thinking_steps": thinking_steps,
                "error": f"Intent parsing failed: {e}",
            }

    def _quick_classify(self, query: str) -> AIMode | None:
        """빠른 패턴 매칭으로 의도 분류.

        Args:
            query: 사용자 쿼리

        Returns:
            분류된 모드 또는 None (LLM 분류 필요)
        """
        query_lower = query.lower()

        # 인사/간단한 대화 패턴
        greetings = [
            "안녕",
            "반가워",
            "hi",
            "hello",
            "ㅎㅇ",
            "하이",
            "뭐해",
            "고마워",
            "감사",
        ]
        if any(g in query_lower for g in greetings) and len(query) < 20:
            return AIMode.SIMPLE

        # 웹 검색이 필요한 패턴
        search_patterns = [
            "최신",
            "뉴스",
            "오늘",
            "현재",
            "실시간",
            "검색해",
            "찾아줘",
            "알려줘",
        ]
        if any(p in query_lower for p in search_patterns):
            return AIMode.SEARCH

        # 내부 문서 참조 패턴
        rag_patterns = ["내 문서", "내 콘텐츠", "저장된", "업로드한", "내가 올린"]
        if any(p in query_lower for p in rag_patterns):
            return AIMode.RAG

        # 추론이 필요한 패턴
        reasoning_patterns = [
            "분석해",
            "비교해",
            "왜",
            "어떻게",
            "설명해",
            "단계별",
            "차이점",
        ]
        if any(p in query_lower for p in reasoning_patterns):
            return AIMode.REASONING

        return None

    def _parse_intent_response(self, response: str) -> dict:
        """LLM 응답에서 의도 정보 파싱.

        Args:
            response: LLM 응답 문자열

        Returns:
            파싱된 의도 정보 딕셔너리
        """
        try:
            # JSON 블록 추출
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()
            else:
                # 중괄호로 시작하는 부분 찾기
                start = response.find("{")
                end = response.rfind("}") + 1
                if start >= 0 and end > start:
                    json_str = response[start:end]
                else:
                    json_str = response.strip()

            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"[IntentParser] Failed to parse JSON: {response[:100]}")
            return {"mode": "simple", "confidence": 0.5, "reason": "파싱 실패"}

    def _clean_imperative_forms(self, query: str) -> str:
        """명령형/요청형 표현 제거 (V8.5: Query Refinement).

        "안내바람", "알려줘", "조사해" 등의 표현을 제거하여
        검색 쿼리 품질을 향상시킵니다.

        Args:
            query: 원본 쿼리

        Returns:
            정제된 쿼리

        Examples:
            >>> _clean_imperative_forms("2030년까지의 투자계획을 안내바람")
            "2030년까지의 투자계획"
            >>> _clean_imperative_forms("파이썬 웹 프레임워크 비교해줘")
            "파이썬 웹 프레임워크 비교"
        """
        # 명령형/요청형 패턴 (순서 중요: 긴 패턴부터 매칭)
        patterns = [
            # "-해주세요" 계열
            (
                r"(을|를|에|에서|로|과|와)\s*(알려주세요|설명해주세요|안내해주세요|조사해주세요|찾아주세요|검색해주세요|분석해주세요|비교해주세요)",
                "",
            ),
            (
                r"\s*(알려주세요|설명해주세요|안내해주세요|조사해주세요|찾아주세요|검색해주세요|분석해주세요|비교해주세요)",
                "",
            ),
            # "-해줘" 계열
            (
                r"(을|를|에|에서|로|과|와)\s*(알려줘|설명해줘|안내해줘|조사해줘|찾아줘|검색해줘|분석해줘|비교해줘)",
                "",
            ),
            (
                r"\s*(알려줘|설명해줘|안내해줘|조사해줘|찾아줘|검색해줘|분석해줘|비교해줘)",
                "",
            ),
            # "-해달라" 계열
            (
                r"(을|를|에|에서|로|과|와)\s*(알려달라|설명해달라|안내해달라|조사해달라|찾아달라|검색해달라|분석해달라|비교해달라)",
                "",
            ),
            (
                r"\s*(알려달라|설명해달라|안내해달라|조사해달라|찾아달라|검색해달라|분석해달라|비교해달라)",
                "",
            ),
            # "-바람" 계열 (가장 문제되는 패턴)
            (r"(을|를|에|에서|로|과|와)\s*(안내바람|알림바람|조사바람|검색바람)", ""),
            (r"\s*(안내바람|알림바람|조사바람|검색바람)", ""),
            # "-해" 단순 명령형
            (
                r"(을|를|에|에서|로|과|와)\s*(알려|설명해|안내해|조사해|찾아|검색해|분석해|비교해)$",
                "",
            ),
        ]

        cleaned = query
        for pattern, replacement in patterns:
            cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

        # 연속 공백 제거 및 trim
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        # 결과가 너무 짧아지면 원본 반환
        if len(cleaned) < 2:
            return query

        logger.debug(f"[IntentParser] Query cleaned: '{query}' → '{cleaned}'")
        return cleaned

    async def reformulate_query(
        self,
        query: str,
        state: GraphState | None = None,
        mode: AIMode | None = None,
    ) -> QueryAnalysis | None:
        """플러그인 기반 Query Transformation + 형태소 분석 하이브리드 쿼리 추출.

        V8.3: 플러그인 아키텍처 적용
        1. QueryTransformer 플러그인들을 순차 적용 (대화 맥락, 질의 분해 등)
        2. 기존 형태소 분석으로 키워드 추출
        3. 최종 검색 쿼리 목록 생성

        V8.5: Query Refinement 추가
        0. 명령형/요청형 표현 제거 (전처리)

        Args:
            query: 원본 사용자 쿼리
            state: 현재 그래프 상태 (대화 히스토리 등)
            mode: 현재 선택된 모드 (검색 쿼리 최소 개수 결정)

        Returns:
            QueryAnalysis
        """
        # ===== Phase 0: Query Refinement (V8.5) =====
        refined_query = self._clean_imperative_forms(query)

        search_queries = []
        keywords = []
        pos_map: dict[str, str] = {}

        # ===== Phase 1: Query Transformation Plugins =====
        if state:
            logger.info(
                f"[IntentParser] Applying {len(self.transformers)} query transformers..."
            )
            for transformer in self.transformers:
                # Transformer는 원본 쿼리 사용 (컨텍스트 이해 위해)
                if transformer.should_apply(query, state):
                    try:
                        transformed = await transformer.transform(
                            query, state, self.settings
                        )
                        search_queries.extend(transformed)
                        logger.info(
                            f"[IntentParser] {transformer.__class__.__name__} applied: {len(transformed)} queries"
                        )
                    except Exception as e:
                        logger.error(
                            f"[IntentParser] {transformer.__class__.__name__} failed: {e}"
                        )

        # ===== Phase 2: 기존 형태소 분석 기반 키워드 추출 =====
        # 1. 쿼리 타입 감지 (정제된 쿼리 사용)
        query_type = self._detect_query_type(refined_query)
        logger.info(f"[IntentParser] Query type detected: {query_type}")

        # 2. 타입별 처리 (정제된 쿼리 사용)
        if query_type == "code_error":
            # 코드/에러: 원본 쿼리 앞부분 사용 (에러 메시지 핵심 부분)
            truncated = self._extract_error_core(refined_query)
            if truncated:
                search_queries.append(truncated)
            keywords = self._extract_code_keywords(refined_query)

        elif query_type == "natural":
            # 자연어 질문: 형태소 분석으로 키워드 추출 (정제된 쿼리)
            keywords, pos_map = self._extract_keywords_with_pos(refined_query)
            if keywords:
                # 키워드 조합으로 검색 쿼리 생성 (고유명사/외국어는 따옴표)
                keyword_query = self._build_search_query(keywords, pos_map)
                if keyword_query:
                    search_queries.append(keyword_query)

            # 정제된 쿼리 추가 (적절한 길이면)
            if len(refined_query) <= 80:
                search_queries.insert(0, refined_query)

        else:  # mixed
            # 혼합: 둘 다 시도 (정제된 쿼리)
            keywords, pos_map = self._extract_keywords_with_pos(refined_query)
            code_keywords = self._extract_code_keywords(refined_query)
            # 코드 키워드 추가 (품사는 SL로 처리 - 정확한 매칭 필요)
            for ck in code_keywords:
                if ck not in pos_map:
                    keywords.append(ck)
                    pos_map[ck] = "SL"

            if keywords:
                keyword_query = self._build_search_query(keywords, pos_map)
                if keyword_query:
                    search_queries.append(keyword_query)

            # 정제된 쿼리 앞부분
            if len(refined_query) <= 120:
                search_queries.insert(0, refined_query)
            else:
                search_queries.insert(0, refined_query[:120] + "...")

        # 3. 임베딩 기반 의미 확장 (현재 비활성화 - 지연 방지)
        # TODO: 임베딩 서버가 안정화되면 다시 활성화
        # if len(search_queries) < 2 and keywords:
        #     try:
        #         expanded = await self._expand_with_embedding(keywords)
        #         if expanded:
        #             search_queries.append(expanded)
        #     except Exception as e:
        #         logger.debug(f"[IntentParser] Embedding expansion failed: {e}")

        # ===== Phase 3: 중복 제거 및 최종 쿼리 목록 생성 =====
        seen = set()
        unique_queries = []
        for q in search_queries:
            q_clean = q.strip()
            q_normalized = q_clean.lower()
            if q_normalized and q_normalized not in seen and len(q_clean) >= 3:
                seen.add(q_normalized)
                unique_queries.append(q_clean)

        # 최소 1개 쿼리 보장 (정제된 쿼리 사용)
        if not unique_queries:
            unique_queries = [
                refined_query[:100] if len(refined_query) > 100 else refined_query
            ]

        target_min_queries = self._determine_target_min_queries(
            mode=mode,
            query_type=query_type,
            refined_query=refined_query,
            keywords=keywords,
        )
        if (
            query_type in {"natural", "mixed"}
            and len(unique_queries) < target_min_queries
        ):
            diversified_queries = self._build_diversified_queries(
                refined_query=refined_query,
                keywords=keywords,
                pos_map=pos_map,
            )
            for candidate in diversified_queries:
                candidate_clean = candidate.strip()
                normalized = candidate_clean.lower()
                if (
                    candidate_clean
                    and len(candidate_clean) >= 3
                    and normalized not in seen
                ):
                    seen.add(normalized)
                    unique_queries.append(candidate_clean)
                if len(unique_queries) >= target_min_queries:
                    break

        # 사실 확인형 질문은 과도한 분해를 피하고 핵심 쿼리 1~2개를 유지
        if (
            mode == AIMode.HYBRID
            and query_type == "natural"
            and self._is_factoid_query(refined_query, keywords)
            and len(unique_queries) > 2
        ):
            unique_queries = unique_queries[:2]

        # 최대 5개로 제한 (너무 많으면 검색 시간 증가)
        unique_queries = unique_queries[:MAX_QUERY_COUNT]

        # ===== Phase 3.5: 쿼리별 언어 태깅 (LanguageRouterTransformer 결과 반영) =====
        language_router = next(
            (t for t in self.transformers if isinstance(t, LanguageRouterTransformer)),
            None,
        )
        lang_decision = getattr(language_router, "last_decision", None)

        if lang_decision and lang_decision.get("needs_english"):
            language_strategy = "ko_primary_en_secondary"
            tagged_queries: list[dict[str, str]] = []
            for q in unique_queries:
                has_korean = bool(re.search(r"[가-힣]", q))
                lang = "ko-KR" if has_korean else "en-US"
                tagged_queries.append({"query": q, "language": lang})
            logger.info(
                f"[IntentParser] Language strategy: {language_strategy}, "
                f"tagged_queries: {tagged_queries}"
            )
        else:
            language_strategy = "ko_only"
            tagged_queries = [{"query": q, "language": "ko-KR"} for q in unique_queries]

        # ===== Phase 4: 검색 제약 힌트 추출 =====
        search_constraints = self._extract_search_constraints(
            original_query=query,
            refined_query=refined_query,
            keywords=keywords,
        )

        query_analysis = QueryAnalysis(
            original_query=query,
            reformulated_query=refined_query
            if refined_query != query
            else unique_queries[0],  # V8.5: 정제된 쿼리 표시
            sub_queries=unique_queries,
            keywords=keywords[:10],
            search_focus=f"키워드: {', '.join(keywords[:3])}"
            if keywords
            else "원본 쿼리 검색",
        )

        recency_hint = search_constraints.get("search_recency")
        if recency_hint:
            query_analysis["search_recency"] = recency_hint

        language_hint = search_constraints.get("search_language")
        if language_hint:
            query_analysis["search_language"] = language_hint

        domain_allowlist = search_constraints.get("domain_allowlist", [])
        if domain_allowlist:
            query_analysis["domain_allowlist"] = domain_allowlist

        # Phase 3.5 결과 저장
        query_analysis["language_strategy"] = language_strategy
        query_analysis["tagged_queries"] = tagged_queries

        focus_parts = []
        if keywords:
            focus_parts.append(f"키워드: {', '.join(keywords[:3])}")
        if recency_hint:
            focus_parts.append(f"최신성: {recency_hint}")
        if language_hint:
            focus_parts.append(f"언어: {language_hint}")
        if domain_allowlist:
            focus_parts.append(f"도메인: {', '.join(domain_allowlist[:2])}")

        query_analysis["search_focus"] = (
            " / ".join(focus_parts) if focus_parts else "원본 쿼리 검색"
        )

        logger.info(
            f"[IntentParser] Final search queries: {query_analysis.get('sub_queries', [])}"
        )
        logger.info(f"[IntentParser] Extracted keywords: {keywords[:10]}")
        if search_constraints:
            logger.info(f"[IntentParser] Search constraints: {search_constraints}")
        return query_analysis

    def _build_diversified_queries(
        self,
        refined_query: str,
        keywords: list[str],
        pos_map: dict[str, str],
    ) -> list[str]:
        """검색 다양성을 위한 보조 쿼리 후보를 생성한다."""
        candidates: list[str] = []
        normalized_query = refined_query.strip()

        if keywords:
            exact_match_pos = {"NNP", "SL", "SH"}
            quoted_keywords: list[str] = []
            for kw in keywords[:4]:
                pos = pos_map.get(kw, "NNG")
                if pos in exact_match_pos:
                    quoted_keywords.append(f'"{kw}"')
                else:
                    quoted_keywords.append(kw)

            keyword_core = " ".join(quoted_keywords).strip()
            expansion_terms = self._extract_query_expansion_terms(normalized_query)
            if expansion_terms:
                core_terms = [kw for kw in keywords[:3] if len(kw) >= 2]
                if core_terms:
                    candidates.append(" ".join(core_terms + expansion_terms))

            if keyword_core:
                candidates.append(keyword_core)

        if 4 <= len(normalized_query) <= 90:
            candidates.append(f'"{normalized_query}"')

        if len(candidates) < 3:
            condensed = re.sub(r"[?!.]", " ", normalized_query)
            condensed_tokens = [t for t in condensed.split() if len(t) >= 2]
            if condensed_tokens:
                candidates.append(" ".join(condensed_tokens[:5]))

        return candidates[:3]

    def _extract_query_expansion_terms(self, query: str) -> list[str]:
        """질문 의도에 맞는 확장 토픽 키워드를 추출한다."""
        query_lower = query.lower()

        if any(
            token in query_lower
            for token in ["흥행", "박스오피스", "box office", "매출"]
        ):
            return ["박스오피스", "흥행 기록", "box office"]

        if any(
            token in query_lower
            for token in ["인식", "평가", "이미지", "브랜드", "평판", "여론"]
        ):
            return ["평가", "평판", "여론"]

        if any(
            token in query_lower
            for token in ["맞나", "맞나요", "사실", "팩트", "진짜", "아닌가", "아닌지"]
        ):
            return ["사실", "근거", "검증"]

        if any(token in query_lower for token in ["비교", "차이", "vs"]):
            return ["비교", "차이", "장단점"]

        if any(token in query_lower for token in ["왜", "이유", "원인"]):
            return ["원인", "배경", "이유"]

        if any(token in query_lower for token in ["방법", "어떻게", "가이드", "절차"]):
            return ["방법", "절차", "가이드"]

        return []

    def _determine_target_min_queries(
        self,
        mode: AIMode | None,
        query_type: str,
        refined_query: str,
        keywords: list[str],
    ) -> int:
        """질문 유형에 따라 최소 쿼리 개수를 결정한다."""
        default_count = (
            MIN_QUERY_COUNT_HYBRID if mode == AIMode.HYBRID else MIN_QUERY_COUNT_SEARCH
        )

        if mode == AIMode.HYBRID and query_type == "natural":
            if self._is_factoid_query(refined_query, keywords):
                return 2

        return default_count

    def _is_factoid_query(self, query: str, keywords: list[str]) -> bool:
        """단일 사실 확인형 질문 여부를 판단한다."""
        normalized = query.lower().strip()

        if len(normalized) > 70:
            return False
        if len(keywords) > 6:
            return False

        if any(marker in normalized for marker in FACTOID_QUERY_MARKERS):
            return True

        if normalized.endswith("?") and len(normalized) <= 45:
            return True

        return False

    def _detect_query_type(self, query: str) -> str:
        """쿼리 타입 감지: code_error, natural, mixed.

        Args:
            query: 사용자 쿼리

        Returns:
            "code_error" | "natural" | "mixed"
        """
        # 코드/에러 패턴 점수
        code_score = 0
        for pattern in CODE_PATTERNS:
            if re.search(pattern, query, re.MULTILINE | re.IGNORECASE):
                code_score += 1

        # 특수문자 비율
        special_chars = sum(1 for c in query if c in "{}[]();=<>|&^%$#@!")
        special_ratio = special_chars / max(len(query), 1)

        # 줄바꿈 개수 (코드는 여러 줄)
        newline_count = query.count("\n")

        # 판정
        if code_score >= 2 or special_ratio > 0.1 or newline_count >= 3:
            return "code_error"
        elif code_score == 0 and special_ratio < 0.03:
            return "natural"
        else:
            return "mixed"

    def _extract_error_core(self, query: str) -> str | None:
        """에러 메시지에서 핵심 부분 추출.

        Args:
            query: 에러가 포함된 쿼리

        Returns:
            핵심 에러 메시지 (150자 이내)
        """
        # 에러 타입 라인 찾기
        error_patterns = [
            r"(Error|Exception|Traceback)[:\s].*",
            r"(TypeError|ValueError|KeyError|AttributeError|ImportError)[:\s].*",
            r"failed.*",
            r"cannot.*",
            r"unable to.*",
        ]

        for pattern in error_patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                error_line = match.group(0)[:150]
                return error_line.strip()

        # 패턴 못 찾으면 첫 150자
        return query[:150].strip() if len(query) > 150 else query.strip()

    def _extract_code_keywords(self, query: str) -> list[str]:
        """코드/에러에서 키워드 추출.

        Args:
            query: 코드가 포함된 쿼리

        Returns:
            키워드 목록
        """
        keywords = []

        # 에러 타입 추출
        error_types = re.findall(
            r"(TypeError|ValueError|KeyError|AttributeError|ImportError|"
            r"RuntimeError|SyntaxError|NameError|IndexError|ModuleNotFoundError)",
            query,
        )
        keywords.extend(error_types)

        # 모듈/패키지명 추출 (import xxx, from xxx)
        imports = re.findall(r"(?:import|from)\s+(\w+)", query)
        keywords.extend(imports)

        # 함수/메서드명 추출
        methods = re.findall(r"(\w+)\s*\(", query)
        # 너무 일반적인 것 제외
        common_funcs = {"print", "len", "str", "int", "list", "dict", "set", "range"}
        keywords.extend([m for m in methods if m not in common_funcs and len(m) > 2])

        # 중복 제거
        return list(dict.fromkeys(keywords))[:10]

    def _extract_keywords_kiwi(self, query: str) -> list[str]:
        """Kiwi 형태소 분석으로 키워드 추출.

        Args:
            query: 자연어 쿼리

        Returns:
            키워드 목록 (명사, 동사 어간 등)
        """
        keywords, _ = self._extract_keywords_with_pos(query)
        return keywords

    def _extract_keywords_with_pos(
        self, query: str
    ) -> tuple[list[str], dict[str, str]]:
        """Kiwi 형태소 분석으로 키워드와 품사 정보 추출.

        Args:
            query: 자연어 쿼리

        Returns:
            (키워드 목록, {키워드: 품사} 딕셔너리)
        """
        try:
            kiwi = get_kiwi()
            result = kiwi.analyze(query)

            if not result:
                return [], {}

            # 첫 번째 분석 결과 사용
            tokens = result[0][0]

            keywords = []
            pos_map = {}  # 키워드 -> 품사 매핑

            # 추출할 품사: 명사/고유명사/외국어 중심
            # VV/VA(용언 어간)는 "어쩌", "되" 같은 저품질 토큰을 유발해 제외
            target_pos = {"NNG", "NNP", "SL", "SH"}  # SH: 한자

            for token in tokens:
                form = token.form
                tag = token.tag

                # 불용어 필터링
                if len(form) < 2:
                    continue
                if form in {"것", "수", "때", "등", "중", "내", "더", "안"}:
                    continue

                if tag in target_pos:
                    if form not in pos_map:  # 중복 방지
                        keywords.append(form)
                        pos_map[form] = tag

            return keywords, pos_map

        except Exception as e:
            logger.warning(f"[IntentParser] Kiwi analysis failed: {e}")
            return [], {}

    def _build_search_query(self, keywords: list[str], pos_map: dict[str, str]) -> str:
        """키워드와 품사 정보로 검색 쿼리 생성.

        고유명사(NNP)와 외국어(SL)는 따옴표로 감싸서 정확한 문구 검색.

        Args:
            keywords: 키워드 목록
            pos_map: {키워드: 품사} 딕셔너리

        Returns:
            검색 쿼리 문자열
        """
        query_parts = []
        # 정확한 매칭이 필요한 품사
        exact_match_pos = {"NNP", "SL", "SH"}  # 고유명사, 외국어, 한자

        filtered_keywords: list[str] = []
        for kw in keywords:
            pos = pos_map.get(kw, "NNG")
            if self._is_low_signal_keyword(kw, pos):
                continue
            filtered_keywords.append(kw)
            if len(filtered_keywords) >= 5:
                break

        for kw in filtered_keywords:
            pos = pos_map.get(kw, "NNG")
            if pos in exact_match_pos and len(kw) >= 2:
                # 고유명사/외국어는 따옴표로 감싸기
                query_parts.append(f'"{kw}"')
            else:
                query_parts.append(kw)

        # 의미 있는 토큰이 너무 적으면 키워드 쿼리 생성 생략
        if len(query_parts) < 2:
            return ""

        return " ".join(query_parts)

    def _is_low_signal_keyword(self, keyword: str, pos: str) -> bool:
        """검색 품질이 낮은 키워드를 필터링한다."""
        token = keyword.strip().lower()
        if len(token) < 2:
            return True

        if pos == "NNG" and token in LOW_SIGNAL_NOUNS:
            return True

        # 한글 2글자 일반명사 중 일반어는 제거
        if pos == "NNG" and len(token) == 2 and token in {"관련", "내용", "정보"}:
            return True

        return False

    def _extract_search_constraints(
        self,
        original_query: str,
        refined_query: str,
        keywords: list[str],
    ) -> dict[str, Any]:
        """검색 필터 힌트 추출.

        의도 분석 결과를 검색 옵션으로 매핑하기 위한 최소 제약을 반환합니다.
        """
        combined_text = f"{original_query} {refined_query}".lower()
        constraints: dict[str, Any] = {}

        recency = self._extract_recency_hint(combined_text)
        if recency:
            constraints["search_recency"] = recency

        language = self._extract_language_hint(combined_text)
        if language:
            constraints["search_language"] = language

        domain_allowlist = self._extract_domain_allowlist(original_query)
        if not domain_allowlist:
            domain_allowlist = self._infer_domain_allowlist(combined_text, keywords)

        if domain_allowlist:
            constraints["domain_allowlist"] = domain_allowlist[:5]

        return constraints

    def _extract_recency_hint(self, text: str) -> str | None:
        """질문에서 최신성 힌트를 추출."""
        for recency, markers in RECENCY_HINT_PATTERNS:
            if any(marker in text for marker in markers):
                return recency

        # 연도 단위 질의는 기본적으로 year 힌트 부여
        if re.search(r"\b20\d{2}\b", text):
            return "year"

        return None

    def _extract_language_hint(self, text: str) -> str | None:
        """질문에서 언어 힌트를 추출."""
        for language, markers in LANGUAGE_HINT_PATTERNS.items():
            if any(marker in text for marker in markers):
                return language

        # 한글이 거의 없고 영문 토큰 위주면 영문 검색으로 힌트
        if not re.search(r"[가-힣]", text) and re.search(r"[a-z]{4,}", text):
            return "en-US"

        return None

    def _extract_domain_allowlist(self, text: str) -> list[str]:
        """질문에 명시된 site: 도메인 추출."""
        matches = re.findall(
            r"\bsite:([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b", text, flags=re.IGNORECASE
        )

        normalized: list[str] = []
        seen = set()
        for domain in matches:
            norm = self._normalize_domain(domain)
            if norm and norm not in seen:
                seen.add(norm)
                normalized.append(norm)

        return normalized

    def _infer_domain_allowlist(self, text: str, keywords: list[str]) -> list[str]:
        """질문 의미에서 도메인 허용 목록을 추론."""
        inferred: list[str] = []
        text_lower = text.lower()

        if "arxiv" in text_lower:
            inferred.append("arxiv.org")

        if "위키" in text_lower or "wikipedia" in text_lower:
            inferred.append("wikipedia.org")

        needs_official_docs = "공식" in text_lower and (
            "문서" in text_lower or "docs" in text_lower
        )
        if needs_official_docs:
            combined_tokens = " ".join(keywords).lower()
            for token, domains in OFFICIAL_DOC_DOMAIN_HINTS.items():
                if token in text_lower or token in combined_tokens:
                    inferred.extend(domains)

        normalized: list[str] = []
        seen = set()
        for domain in inferred:
            norm = self._normalize_domain(domain)
            if norm and norm not in seen:
                seen.add(norm)
                normalized.append(norm)

        return normalized

    def _normalize_domain(self, domain: str) -> str:
        """도메인 문자열 정규화."""
        normalized = domain.strip().lower()
        normalized = re.sub(r"^https?://", "", normalized)
        normalized = normalized.strip("/")
        if normalized.startswith("www."):
            normalized = normalized[4:]
        return normalized

    async def _expand_with_embedding(self, keywords: list[str]) -> str | None:
        """임베딩 모델로 의미적 확장 쿼리 생성.

        키워드들의 임베딩을 구하고 관련 용어를 찾아 확장합니다.
        (현재는 단순히 키워드 조합, 추후 유사어 확장 가능)

        Args:
            keywords: 추출된 키워드

        Returns:
            확장된 검색 쿼리 또는 None
        """
        if not keywords:
            return None

        # 임베딩 서버 URL (settings에서 가져오거나 기본값)
        embedding_url = getattr(
            self.settings, "EMBEDDING_URL", "http://localhost:11435/v1/embeddings"
        )

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # 키워드 조합 텍스트의 임베딩 생성
                text = " ".join(keywords[:5])
                response = await client.post(
                    embedding_url,
                    json={
                        "model": "embeddinggemma:300m",
                        "input": text,
                    },
                )

                if response.status_code == 200:
                    # 임베딩 성공 - 현재는 추가 처리 없이 키워드 재조합
                    # 추후: 유사 문서/용어 검색 후 확장
                    logger.debug(f"[IntentParser] Embedding generated for: {text}")

                    # 다른 조합으로 검색 쿼리 생성
                    if len(keywords) > 3:
                        return " ".join(keywords[2:6])

        except Exception as e:
            logger.debug(f"[IntentParser] Embedding request failed: {e}")

        return None
