"""Entity Disambiguator 노드.

동일한 이름/약어를 가진 여러 개체 중 컨텍스트에 맞는 것을 선택합니다.

예시:
- TSMC: Taiwan Semiconductor (대만 반도체) vs 트랜스미션 소프트코리아
- Apple: Apple Inc. (회사) vs 사과 (과일)
- Jaguar: 자동차 브랜드 vs 동물
- AWS: Amazon Web Services vs 다른 의미

V9.0: Entity Disambiguation 추가
- 검색 결과에서 올바른 개체 선택
- 다수 의견(Majority Consensus) 기반 필터링
- 컨텍스트 키워드 매칭으로 신뢰도 증가
"""
from __future__ import annotations

from typing import List, Dict, Any
from loguru import logger


class EntityDisambiguator:
    """
    개체 명확화 노드

    검색 결과에서 다수 의견과 컨텍스트 키워드를 기반으로
    올바른 개체를 선택하고 신뢰도를 조정합니다.
    """

    # 알려진 고빈도 개체 매핑
    # 새로운 개체는 동적으로 추가 가능
    KNOWN_ENTITIES = {
        "TSMC": {
            "primary": "Taiwan Semiconductor Manufacturing Company",
            "description": "대만 반도체 파운드리 (세계 최대)",
            "aliases": ["대만 반도체", "TSMC 파운드리", "타이완 반도체", "台積電"],
            "keywords": [
                # 영문 키워드
                "semiconductor", "foundry", "chip", "taiwan", "fab",
                "wafer", "nanometer", "nm", "process", "manufacturing",
                # 한글 키워드
                "파운드리", "반도체", "웨이퍼", "공정", "제조",
                # 관련 인물/지역
                "morris chang", "hsinchu", "신주", "대만"
            ],
            "confidence_boost": 0.4  # 검색 결과에 이 키워드가 있으면 신뢰도 증가
        },
        "Apple": {
            "primary": "Apple Inc.",
            "description": "미국 IT 기업",
            "aliases": ["애플", "애플 컴퍼니", "애플사"],
            "keywords": [
                "iPhone", "iPad", "Mac", "iOS", "macOS", "Steve Jobs",
                "Tim Cook", "Cupertino", "쿠퍼티노", "아이폰", "맥북",
                "애플워치", "App Store"
            ],
            "confidence_boost": 0.3
        },
        "AWS": {
            "primary": "Amazon Web Services",
            "description": "아마존 클라우드 서비스",
            "aliases": ["아마존 웹 서비스", "AWS 클라우드"],
            "keywords": [
                "cloud", "EC2", "S3", "Lambda", "Amazon", "클라우드",
                "인스턴스", "버킷", "리전", "region"
            ],
            "confidence_boost": 0.3
        },
        "Jaguar": {
            "primary": "Jaguar Cars (자동차 브랜드)",
            "description": "영국 럭셔리 자동차 브랜드",
            "aliases": ["재규어", "재규어 자동차"],
            "keywords": [
                "car", "vehicle", "luxury", "XE", "XF", "F-Type",
                "자동차", "차량", "럭셔리", "영국"
            ],
            "confidence_boost": 0.3
        },
        # 추가 개체는 여기에 등록
        # 동적 확장 가능
    }

    def __init__(self):
        """초기화"""
        pass

    def clarify_query(self, query: str) -> str:
        """
        쿼리 명확화 (Phase 3)

        동일한 약어가 여러 의미를 가질 때, 일반적인 의미로 확장합니다.

        Args:
            query: 원본 쿼리

        Returns:
            명확화된 쿼리

        Examples:
            >>> clarify_query("TSMC의 최신 뉴스")
            "TSMC Taiwan Semiconductor 대만 반도체의 최신 뉴스"

            >>> clarify_query("Apple 주가")
            "Apple Inc 애플 주가"
        """
        clarified = query

        for entity, info in self.KNOWN_ENTITIES.items():
            if entity.upper() in query.upper():
                # 개체 이름 + primary + 대표 별칭 1-2개 추가
                primary = info["primary"]
                aliases = info.get("aliases", [])[:2]  # 상위 2개 별칭만

                # "TSMC" → "TSMC Taiwan Semiconductor 대만 반도체"
                replacement = f"{entity} {primary} {' '.join(aliases)}"
                clarified = clarified.replace(entity, replacement)

                logger.info(f"[EntityDisambiguator] Query clarified: '{entity}' → '{replacement}'")
                break  # 첫 번째 매칭만 적용

        return clarified

    async def disambiguate(
        self,
        query: str,
        search_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        검색 결과에서 컨텍스트에 맞는 개체 선택

        Args:
            query: 사용자 쿼리
            search_results: 검색 결과 리스트

        Returns:
            필터링 및 재정렬된 검색 결과
        """
        if not search_results:
            return search_results

        # 1. 쿼리에서 개체 추출
        detected_entities = self._detect_entities(query)

        if not detected_entities:
            # 알려진 개체가 없으면 그대로 반환
            return search_results

        logger.info(f"[EntityDisambiguator] Detected entities: {detected_entities}")

        # 2. 각 검색 결과에 명확화 점수 부여
        scored_results = []
        for result in search_results:
            disambiguation_score = self._calculate_disambiguation_score(
                result, detected_entities, query
            )
            result["disambiguation_score"] = disambiguation_score
            scored_results.append(result)

        # 3. 명확화 점수 + 기존 quality_score 결합
        for result in scored_results:
            original_score = result.get("quality_score", 50.0)
            disambiguation_score = result.get("disambiguation_score", 0.0)

            # 가중 평균 (기존 70% + 명확화 30%)
            result["quality_score"] = original_score * 0.7 + disambiguation_score * 0.3

        # 4. 재정렬
        scored_results.sort(key=lambda x: x.get("quality_score", 0), reverse=True)

        logger.info(f"[EntityDisambiguator] Reordered {len(scored_results)} results")

        # 5. 디버그 로그 (상위 3개)
        for i, result in enumerate(scored_results[:3]):
            logger.debug(
                f"  #{i+1}: {result.get('title', 'N/A')[:50]}... "
                f"(Quality: {result.get('quality_score', 0):.1f}, "
                f"Disambiguation: {result.get('disambiguation_score', 0):.1f})"
            )

        return scored_results

    def _detect_entities(self, query: str) -> List[str]:
        """
        쿼리에서 알려진 개체 탐지

        Args:
            query: 사용자 쿼리

        Returns:
            탐지된 개체 리스트
        """
        detected = []
        query_upper = query.upper()

        for entity in self.KNOWN_ENTITIES:
            # 개체 이름 매칭
            if entity.upper() in query_upper:
                detected.append(entity)
                continue

            # 별칭 매칭
            entity_info = self.KNOWN_ENTITIES[entity]
            for alias in entity_info.get("aliases", []):
                if alias.upper() in query_upper:
                    detected.append(entity)
                    break

        return detected

    def _calculate_disambiguation_score(
        self,
        result: Dict[str, Any],
        entities: List[str],
        query: str
    ) -> float:
        """
        검색 결과의 명확화 점수 계산

        각 개체의 키워드가 얼마나 포함되어 있는지 확인

        Args:
            result: 검색 결과
            entities: 탐지된 개체 리스트
            query: 사용자 쿼리

        Returns:
            명확화 점수 (0-100)
        """
        score = 50.0  # 기본 점수

        title = result.get("title", "").lower()
        snippet = result.get("snippet", "").lower()
        url = result.get("url", "").lower()
        content = f"{title} {snippet} {url}"

        for entity in entities:
            if entity not in self.KNOWN_ENTITIES:
                continue

            entity_info = self.KNOWN_ENTITIES[entity]
            keywords = entity_info["keywords"]
            boost = entity_info["confidence_boost"]

            # 키워드 매칭 개수
            matched_keywords = sum(
                1 for kw in keywords if kw.lower() in content
            )

            if matched_keywords > 0:
                # 키워드 매칭 비율에 따라 점수 증가
                match_ratio = matched_keywords / len(keywords)
                score += boost * match_ratio * 100

                logger.debug(
                    f"[EntityDisambiguator] {entity}: "
                    f"{matched_keywords}/{len(keywords)} keywords matched "
                    f"(+{boost * match_ratio * 100:.1f} points)"
                )

        return min(100.0, score)

    @classmethod
    def register_entity(
        cls,
        name: str,
        primary: str,
        description: str,
        aliases: List[str],
        keywords: List[str],
        confidence_boost: float = 0.3
    ):
        """
        새로운 개체 등록 (동적 확장)

        Args:
            name: 개체 이름 (예: "TSMC")
            primary: 주요 의미 (예: "Taiwan Semiconductor Manufacturing Company")
            description: 설명
            aliases: 별칭 리스트
            keywords: 관련 키워드 리스트
            confidence_boost: 신뢰도 증가 비율 (기본값: 0.3)
        """
        cls.KNOWN_ENTITIES[name] = {
            "primary": primary,
            "description": description,
            "aliases": aliases,
            "keywords": keywords,
            "confidence_boost": confidence_boost
        }
        logger.info(f"[EntityDisambiguator] Registered new entity: {name} ({primary})")
