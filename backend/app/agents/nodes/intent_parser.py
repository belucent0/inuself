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
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from kiwipiepy import Kiwi
from langchain_core.messages import HumanMessage
from loguru import logger

from ..state import GraphState, AIMode, ThinkingStep, QueryAnalysis
from ..tools.llm_client import async_llm_completion
from ..tools.model_router import TierRouter

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
    logger.info(f"[IntentParser] Kiwi warmed up in {time.time()-start:.2f}s")


# 코드/에러 패턴 정규식
CODE_PATTERNS = [
    r'[{}\[\]();]',              # 괄호, 세미콜론
    r'^\s*(def|class|import|from|const|let|var|function)\s',  # 함수/클래스 선언
    r'(Error|Exception|Traceback|TypeError|ValueError)',      # 에러 타입
    r'(\.py|\.js|\.ts|\.java|\.cpp|\.go)[:"\']',              # 파일 확장자
    r'(https?://|localhost:\d+)',                             # URL
    r'(\w+\.\w+\()',                                          # 메서드 호출
    r'(=>|->|::)',                                            # 연산자
]

# 의도 분석 프롬프트
INTENT_ANALYSIS_PROMPT = """당신은 사용자 질문의 의도를 분석하는 전문가입니다.

다음 질문을 분석하여 가장 적합한 모드를 선택하세요:

사용자 질문: {query}

모드 선택 기준:
- simple: 인사, 일반 대화, 간단한 질문 (최신 정보나 검색이 필요 없는 경우)
- search: 최신 뉴스, 실시간 정보, 특정 웹사이트 검색이 필요한 경우
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


class IntentParserNode:
    """사용자 쿼리의 의도를 분석하여 적절한 모드를 결정하는 노드.

    또한 임베딩 기반 Tier 라우팅을 수행합니다.
    선택된 tier는 인프라 레이어(StreamProcessor)에서 실제 모델로 매핑됩니다.
    """

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정 (LiteLLM 연결 정보 등)
        """
        self.settings = settings
        self.tier_router = TierRouter(settings)

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
            logger.info(f"[IntentParser] Using explicitly specified mode: {current_mode}")
            thinking_steps.append(ThinkingStep(
                step="intent_analysis",
                content="질문 분석 중...",
                timestamp=time.time()
            ))

            # Tier 기반 라우팅
            selected_tier = await self.tier_router.select_tier(
                query=query,
                mode=current_mode.value,
                context_size=0
            )
            logger.info(f"[IntentParser] Tier routing: {selected_tier}")

            # 검색 모드면 쿼리 재정의 수행
            query_analysis = None
            search_queries = [query]  # 기본값: 원본 쿼리
            if current_mode in (AIMode.SEARCH, AIMode.HYBRID):
                query_analysis = await self.reformulate_query(query)
                if query_analysis:
                    search_queries = query_analysis.get("sub_queries", [query])
                    thinking_steps.append(ThinkingStep(
                        step="query_reformulation",
                        content=f"검색 쿼리 재정의: {query_analysis.get('search_focus', '')}",
                        timestamp=time.time()
                    ))

            thinking_steps.append(ThinkingStep(
                step="intent_result",
                content=f"모드: {current_mode}, 티어: {selected_tier}",
                timestamp=time.time()
            ))
            return {
                "mode": current_mode,
                "selected_model": selected_tier,  # tier명이 LiteLLM으로 전달됨
                "intent_confidence": 1.0,
                "requires_clarification": False,
                "query_analysis": query_analysis,
                "search_queries": search_queries,
                "thinking_steps": thinking_steps,
            }

        # 사고 과정 기록
        thinking_steps.append(ThinkingStep(
            step="intent_analysis",
            content=f"사용자 질문 분석 중: '{query[:50]}...'",
            timestamp=time.time()
        ))

        # 간단한 패턴 매칭으로 빠른 분류 (LLM 호출 최소화)
        quick_mode = self._quick_classify(query)
        if quick_mode:
            logger.info(f"[IntentParser] Quick classification: mode={quick_mode}")

            # Tier 기반 라우팅
            selected_tier = await self.tier_router.select_tier(
                query=query,
                mode=quick_mode.value,
                context_size=0
            )
            logger.info(f"[IntentParser] Tier routing: {selected_tier}")

            # 검색 모드면 쿼리 재정의 수행
            query_analysis = None
            search_queries = [query]
            if quick_mode in (AIMode.SEARCH, AIMode.HYBRID):
                query_analysis = await self.reformulate_query(query)
                if query_analysis:
                    search_queries = query_analysis.get("sub_queries", [query])
                    thinking_steps.append(ThinkingStep(
                        step="query_reformulation",
                        content=f"검색 쿼리 재정의: {query_analysis.get('search_focus', '')}",
                        timestamp=time.time()
                    ))

            thinking_steps.append(ThinkingStep(
                step="intent_result",
                content=f"빠른 분류: {quick_mode} 모드, 티어: {selected_tier}",
                timestamp=time.time()
            ))
            return {
                "mode": quick_mode,
                "selected_model": selected_tier,  # tier명이 LiteLLM으로 전달됨
                "intent_confidence": 0.9,
                "requires_clarification": False,
                "query_analysis": query_analysis,
                "search_queries": search_queries,
                "thinking_steps": thinking_steps,
            }

        # LLM을 사용한 정밀 분류
        try:
            prompt = INTENT_ANALYSIS_PROMPT.format(query=query)
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

            logger.info(f"[IntentParser] LLM classification: mode={mode}, confidence={confidence}")

            # Tier 기반 라우팅
            selected_tier = await self.tier_router.select_tier(
                query=query,
                mode=mode.value,
                context_size=0
            )
            logger.info(f"[IntentParser] Tier routing: {selected_tier}")

            # 검색 모드면 쿼리 재정의 수행
            query_analysis = None
            search_queries = [query]
            if mode in (AIMode.SEARCH, AIMode.HYBRID):
                query_analysis = await self.reformulate_query(query)
                if query_analysis:
                    search_queries = query_analysis.get("sub_queries", [query])
                    thinking_steps.append(ThinkingStep(
                        step="query_reformulation",
                        content=f"검색 쿼리 재정의: {query_analysis.get('search_focus', '')}",
                        timestamp=time.time()
                    ))

            thinking_steps.append(ThinkingStep(
                step="intent_result",
                content=f"의도 분석: {mode} 모드, 티어: {selected_tier} (신뢰도: {confidence:.0%})",
                timestamp=time.time()
            ))

            return {
                "mode": mode,
                "selected_model": selected_tier,  # tier명이 LiteLLM으로 전달됨
                "intent_confidence": confidence,
                "requires_clarification": confidence < 0.7,
                "query_analysis": query_analysis,
                "search_queries": search_queries,
                "thinking_steps": thinking_steps,
            }

        except Exception as e:
            logger.warning(f"[IntentParser] LLM classification failed: {e}, falling back to simple mode")
            thinking_steps.append(ThinkingStep(
                step="intent_error",
                content=f"의도 분석 실패, 기본 모드 사용: {str(e)}",
                timestamp=time.time()
            ))
            return {
                "mode": AIMode.SIMPLE,
                "selected_model": "tier-simple",  # 기본 티어
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
        greetings = ["안녕", "반가워", "hi", "hello", "ㅎㅇ", "하이", "뭐해", "고마워", "감사"]
        if any(g in query_lower for g in greetings) and len(query) < 20:
            return AIMode.SIMPLE

        # 웹 검색이 필요한 패턴
        search_patterns = ["최신", "뉴스", "오늘", "현재", "실시간", "검색해", "찾아줘", "알려줘"]
        if any(p in query_lower for p in search_patterns):
            return AIMode.SEARCH

        # 내부 문서 참조 패턴
        rag_patterns = ["내 문서", "내 콘텐츠", "저장된", "업로드한", "내가 올린"]
        if any(p in query_lower for p in rag_patterns):
            return AIMode.RAG

        # 추론이 필요한 패턴
        reasoning_patterns = ["분석해", "비교해", "왜", "어떻게", "설명해", "단계별", "차이점"]
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

    async def reformulate_query(self, query: str) -> QueryAnalysis | None:
        """형태소 분석 + 임베딩 하이브리드 쿼리 추출.

        LLM 의존 없이 형태소 분석으로 키워드를 추출하고,
        코드/에러 쿼리는 원본을 유지합니다.

        Args:
            query: 원본 사용자 쿼리

        Returns:
            QueryAnalysis
        """
        search_queries = []
        keywords = []

        # 1. 쿼리 타입 감지
        query_type = self._detect_query_type(query)
        logger.info(f"[IntentParser] Query type detected: {query_type}")

        # 2. 타입별 처리
        if query_type == "code_error":
            # 코드/에러: 원본 쿼리 앞부분 사용 (에러 메시지 핵심 부분)
            truncated = self._extract_error_core(query)
            if truncated:
                search_queries.append(truncated)
            keywords = self._extract_code_keywords(query)

        elif query_type == "natural":
            # 자연어 질문: 형태소 분석으로 키워드 추출
            keywords, pos_map = self._extract_keywords_with_pos(query)
            if keywords:
                # 키워드 조합으로 검색 쿼리 생성 (고유명사/외국어는 따옴표)
                keyword_query = self._build_search_query(keywords, pos_map)
                search_queries.append(keyword_query)

            # 원본 쿼리도 추가 (적절한 길이면)
            if len(query) <= 80:
                search_queries.insert(0, query)

        else:  # mixed
            # 혼합: 둘 다 시도
            keywords, pos_map = self._extract_keywords_with_pos(query)
            code_keywords = self._extract_code_keywords(query)
            # 코드 키워드 추가 (품사는 SL로 처리 - 정확한 매칭 필요)
            for ck in code_keywords:
                if ck not in pos_map:
                    keywords.append(ck)
                    pos_map[ck] = 'SL'

            if keywords:
                keyword_query = self._build_search_query(keywords, pos_map)
                search_queries.append(keyword_query)

            # 원본 쿼리 앞부분
            if len(query) <= 120:
                search_queries.insert(0, query)
            else:
                search_queries.insert(0, query[:120] + "...")

        # 3. 임베딩 기반 의미 확장 (현재 비활성화 - 지연 방지)
        # TODO: 임베딩 서버가 안정화되면 다시 활성화
        # if len(search_queries) < 2 and keywords:
        #     try:
        #         expanded = await self._expand_with_embedding(keywords)
        #         if expanded:
        #             search_queries.append(expanded)
        #     except Exception as e:
        #         logger.debug(f"[IntentParser] Embedding expansion failed: {e}")

        # 4. 중복 제거 및 정리
        seen = set()
        unique_queries = []
        for q in search_queries:
            q_clean = q.strip()
            q_normalized = q_clean.lower()
            if q_normalized and q_normalized not in seen and len(q_clean) >= 3:
                seen.add(q_normalized)
                unique_queries.append(q_clean)

        # 최소 1개 쿼리 보장
        if not unique_queries:
            unique_queries = [query[:100] if len(query) > 100 else query]

        query_analysis = QueryAnalysis(
            original_query=query,
            reformulated_query=unique_queries[0],
            sub_queries=unique_queries[:4],
            keywords=keywords[:10],
            search_focus=f"키워드: {', '.join(keywords[:3])}" if keywords else "원본 쿼리 검색",
        )

        logger.info(f"[IntentParser] Final search queries: {query_analysis['sub_queries']}")
        logger.info(f"[IntentParser] Extracted keywords: {keywords[:10]}")
        return query_analysis

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
        special_chars = sum(1 for c in query if c in '{}[]();=<>|&^%$#@!')
        special_ratio = special_chars / max(len(query), 1)

        # 줄바꿈 개수 (코드는 여러 줄)
        newline_count = query.count('\n')

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
            r'(Error|Exception|Traceback)[:\s].*',
            r'(TypeError|ValueError|KeyError|AttributeError|ImportError)[:\s].*',
            r'failed.*',
            r'cannot.*',
            r'unable to.*',
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
            r'(TypeError|ValueError|KeyError|AttributeError|ImportError|'
            r'RuntimeError|SyntaxError|NameError|IndexError|ModuleNotFoundError)',
            query
        )
        keywords.extend(error_types)

        # 모듈/패키지명 추출 (import xxx, from xxx)
        imports = re.findall(r'(?:import|from)\s+(\w+)', query)
        keywords.extend(imports)

        # 함수/메서드명 추출
        methods = re.findall(r'(\w+)\s*\(', query)
        # 너무 일반적인 것 제외
        common_funcs = {'print', 'len', 'str', 'int', 'list', 'dict', 'set', 'range'}
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

    def _extract_keywords_with_pos(self, query: str) -> tuple[list[str], dict[str, str]]:
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

            # 추출할 품사: NNG(일반명사), NNP(고유명사), VV(동사), VA(형용사), SL(외국어)
            target_pos = {'NNG', 'NNP', 'VV', 'VA', 'SL', 'SH'}  # SH: 한자

            for token in tokens:
                form = token.form
                tag = token.tag

                # 불용어 필터링
                if len(form) < 2:
                    continue
                if form in {'것', '수', '때', '등', '중', '내', '더', '안'}:
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
        exact_match_pos = {'NNP', 'SL', 'SH'}  # 고유명사, 외국어, 한자

        for kw in keywords[:5]:  # 최대 5개
            pos = pos_map.get(kw, 'NNG')
            if pos in exact_match_pos and len(kw) >= 2:
                # 고유명사/외국어는 따옴표로 감싸기
                query_parts.append(f'"{kw}"')
            else:
                query_parts.append(kw)

        return " ".join(query_parts)

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
        embedding_url = getattr(self.settings, 'EMBEDDING_URL', 'http://localhost:11435/v1/embeddings')

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # 키워드 조합 텍스트의 임베딩 생성
                text = " ".join(keywords[:5])
                response = await client.post(
                    embedding_url,
                    json={
                        "model": "embeddinggemma:300m",
                        "input": text,
                    }
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
