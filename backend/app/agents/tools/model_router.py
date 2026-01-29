"""Tier 기반 라우터.

임베딩 유사도 기반으로 쿼리의 복잡도를 판단하여 적절한 "능력 티어"를 결정합니다.
실제 모델 선택은 인프라 레이어(stream_processor.py)에서 담당합니다.

설계 원칙:
- Backend(LangGraph): WHAT - "이 쿼리에 어떤 능력이 필요한가?" (tier 결정)
- Infrastructure(StreamProcessor): HOW - "그 능력을 어떤 모델로 제공할 것인가?" (model 결정)
"""
from __future__ import annotations

import json
from loguru import logger
from pathlib import Path
from typing import Any, Optional
from functools import lru_cache

import math
import httpx



# 라우팅 규칙 파일 경로
ROUTING_RULES_PATH = Path(__file__).parent.parent / "routing_rules.json"


@lru_cache(maxsize=1)
def _load_routing_rules() -> dict:
    """라우팅 규칙 로드 (캐싱)."""
    try:
        with open(ROUTING_RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[TierRouter] Failed to load routing rules: {e}")
        return {"default_tier": "tier-simple", "rules": []}


class TierRouter:
    """임베딩 기반 Tier 라우터.

    쿼리와 라우팅 규칙의 예시 문장들 간 유사도를 계산하여
    적합한 능력 티어를 결정합니다.

    Tiers:
    - tier-simple: 간단한 작업 (인사, 짧은 질문)
    - tier-complex: 복잡한 분석, 추론
    - tier-reasoning: REASONING 모드 전용
    """

    def __init__(self, settings: Any):
        """초기화.

        Args:
            settings: 애플리케이션 설정
        """
        self.settings = settings
        self.rules = _load_routing_rules()
        self.default_tier = self.rules.get("default_tier", "tier-simple")
        self._rule_embeddings: dict[str, list[list[float]]] = {}
        self._embeddings_initialized = False

        # FLM 임베딩 서버 URL (flm-llm 서버에서 제공)
        self.embedding_url = "http://localhost:11435/v1/embeddings"

    async def select_tier(self, query: str, mode: str = None, context_size: int = 0) -> str:
        """쿼리에 적합한 능력 티어 선택.

        Args:
            query: 사용자 쿼리
            mode: AI 모드 (참고용)
            context_size: 컨텍스트 크기 (토큰 수 추정)

        Returns:
            선택된 티어명 (tier-simple, tier-complex, tier-reasoning)
        """
        # 1. 모드 기반 빠른 판단 (REASONING은 항상 tier-reasoning)
        if mode == "reasoning":
            logger.info(f"[TierRouter] REASONING mode -> tier-reasoning")
            return "tier-reasoning"

        # 2. 컨텍스트 크기 기반 판단 (많은 문서 = 복잡한 작업)
        if context_size > 3000:
            logger.info(f"[TierRouter] Large context ({context_size}) -> tier-complex")
            return "tier-complex"

        # 3. 임베딩 기반 유사도 매칭 (현재 비활성화 - Docker 환경에서 localhost 접근 불가)
        # TODO: 임베딩 서버를 Docker 네트워크로 노출하거나 설정으로 URL 변경
        # try:
        #     selected = await self._embedding_based_routing(query)
        #     if selected:
        #         return selected
        # except Exception as e:
        #     logger.warning(f"[TierRouter] Embedding routing failed: {e}, using rule-based fallback")

        # 4. 규칙 기반 폴백
        return self._rule_based_routing(query)

    async def _embedding_based_routing(self, query: str) -> Optional[str]:
        """임베딩 기반 라우팅.

        Args:
            query: 사용자 쿼리

        Returns:
            선택된 티어명 또는 None
        """
        # 규칙 임베딩 초기화 (최초 1회)
        if not self._embeddings_initialized:
            await self._initialize_rule_embeddings()

        if not self._rule_embeddings:
            return None

        # 쿼리 임베딩 생성
        query_embedding = await self._get_embedding(query)
        if query_embedding is None:
            return None

        # 각 규칙과 유사도 계산
        best_match = None
        best_score = 0.0

        for rule in self.rules.get("rules", []):
            rule_name = rule["name"]
            threshold = rule.get("score_threshold", 0.7)

            if rule_name not in self._rule_embeddings:
                continue

            # 규칙의 모든 예시와 유사도 계산, 최대값 사용
            rule_embs = self._rule_embeddings[rule_name]
            similarities = [
                self._cosine_similarity(query_embedding, emb)
                for emb in rule_embs
            ]
            max_similarity = max(similarities) if similarities else 0.0

            logger.debug(f"[TierRouter] Rule '{rule_name}': max_similarity={max_similarity:.3f}, threshold={threshold}")

            if max_similarity >= threshold and max_similarity > best_score:
                best_score = max_similarity
                best_match = rule

        if best_match:
            tier = best_match["tier"]
            logger.info(f"[TierRouter] Embedding match: rule='{best_match['name']}', score={best_score:.3f} -> {tier}")
            return tier

        return None

    async def _initialize_rule_embeddings(self):
        """라우팅 규칙의 예시 문장들을 임베딩으로 변환."""
        logger.info("[TierRouter] Initializing rule embeddings...")

        for rule in self.rules.get("rules", []):
            rule_name = rule["name"]
            utterances = rule.get("utterances", [])

            if not utterances:
                continue

            embeddings = []
            for utterance in utterances:
                emb = await self._get_embedding(utterance)
                if emb is not None:
                    embeddings.append(emb)

            if embeddings:
                self._rule_embeddings[rule_name] = embeddings
                logger.debug(f"[TierRouter] Rule '{rule_name}': {len(embeddings)} embeddings loaded")

        self._embeddings_initialized = True
        logger.info(f"[TierRouter] Rule embeddings initialized: {len(self._rule_embeddings)} rules")

    async def _get_embedding(self, text: str) -> Optional[list[float]]:
        """텍스트의 임베딩 벡터 생성.

        Args:
            text: 임베딩할 텍스트

        Returns:
            임베딩 벡터 또는 None
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.embedding_url,
                    json={
                        "input": text,
                        "model": "embeddinggemma:300m"  # FLM 내장 임베딩 모델
                    }
                )

                if response.status_code != 200:
                    logger.warning(f"[TierRouter] Embedding API error: {response.status_code}")
                    return None

                data = response.json()
                # OpenAI 호환 응답 형식
                if "data" in data and len(data["data"]) > 0:
                    return data["data"][0]["embedding"]

                return None

        except httpx.ConnectError:
            logger.debug("[TierRouter] Embedding server not available")
            return None
        except Exception as e:
            logger.warning(f"[TierRouter] Embedding request failed: {e}")
            return None

    def _cosine_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        """코사인 유사도 계산 (순수 Python, numpy 불필요).

        Args:
            vec1: 벡터 1
            vec2: 벡터 2

        Returns:
            유사도 (0.0 ~ 1.0)
        """
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm_a = math.sqrt(sum(a * a for a in vec1))
        norm_b = math.sqrt(sum(b * b for b in vec2))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    def _rule_based_routing(self, query: str) -> str:
        """규칙 기반 라우팅 (폴백).

        Args:
            query: 사용자 쿼리

        Returns:
            선택된 티어명
        """
        query_lower = query.lower()

        # 복잡한 분석이 필요한 키워드
        complex_keywords = [
            "분석", "비교", "왜", "어떻게", "설명", "단계별",
            "차이점", "장단점", "원인", "이유", "전략", "계획",
            "추론", "판단", "평가", "예측"
        ]

        if any(kw in query_lower for kw in complex_keywords):
            logger.info(f"[TierRouter] Rule-based: complex keywords detected -> tier-complex")
            return "tier-complex"

        # 긴 질문은 복잡한 것으로 간주
        if len(query) > 100:
            logger.info(f"[TierRouter] Rule-based: long query ({len(query)} chars) -> tier-complex")
            return "tier-complex"

        logger.info(f"[TierRouter] Rule-based: default -> {self.default_tier}")
        return self.default_tier


# 하위 호환성을 위한 별칭 (기존 코드에서 ModelRouter로 import하는 경우)
ModelRouter = TierRouter
