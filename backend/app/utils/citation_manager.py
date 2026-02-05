"""Citation Manager - 출처 표시 관리 (Phase 4)

LLM 응답에서 Citation을 추출, 검증하고 출처 목록을 생성합니다.
"""
from __future__ import annotations

import re
from typing import Any
from loguru import logger


class Citation:
    """인용 정보."""

    def __init__(
        self,
        id: int,
        title: str,
        url: str,
        snippet: str = "",
        verified: bool = True,
    ):
        """Initialize citation.

        Args:
            id: Citation 번호 [1], [2], ...
            title: 출처 제목
            url: 출처 URL
            snippet: 인용된 부분 (선택)
            verified: 검증 여부
        """
        self.id = id
        self.title = title
        self.url = url
        self.snippet = snippet
        self.verified = verified

    def to_dict(self) -> dict[str, Any]:
        """Dict 형식으로 변환."""
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "verified": self.verified,
        }


class CitationExtractor:
    """LLM 응답에서 Citation 번호를 추출."""

    # Citation 패턴: [1], [2], [10] 등
    CITATION_PATTERN = r"\[(\d+)\]"

    @classmethod
    def extract(cls, text: str) -> list[int]:
        """텍스트에서 Citation 번호 추출.

        Args:
            text: LLM 응답 텍스트

        Returns:
            Citation 번호 리스트 (중복 제거, 정렬됨)
        """
        matches = re.findall(cls.CITATION_PATTERN, text)
        citation_ids = sorted(set(int(m) for m in matches))

        logger.debug(f"[Citation] Extracted {len(citation_ids)} citations: {citation_ids}")
        return citation_ids


class CitationVerifier:
    """Citation 검증 (출처가 실제로 존재하는지 확인)."""

    @classmethod
    def verify(
        cls,
        citation_ids: list[int],
        sources: list[dict[str, Any]],
    ) -> dict[int, bool]:
        """Citation ID가 실제 출처에 해당하는지 검증.

        Args:
            citation_ids: 추출된 Citation ID 목록
            sources: 검색 결과 목록 (1-indexed)

        Returns:
            {citation_id: is_valid} 매핑
        """
        max_source_id = len(sources)
        verification = {}

        for cid in citation_ids:
            # 출처 범위 내에 있는지 확인
            is_valid = 1 <= cid <= max_source_id
            verification[cid] = is_valid

            if not is_valid:
                logger.warning(
                    f"[Citation] Invalid citation [{cid}]: "
                    f"Only {max_source_id} sources available"
                )

        valid_count = sum(verification.values())
        logger.info(
            f"[Citation] Verified {valid_count}/{len(citation_ids)} citations"
        )

        return verification


class ReferenceBuilder:
    """최종 출처 목록 생성."""

    @classmethod
    def build(
        cls,
        citation_ids: list[int],
        sources: list[dict[str, Any]],
        verification: dict[int, bool],
    ) -> list[Citation]:
        """사용된 Citation에 대한 출처 목록 생성.

        Args:
            citation_ids: 추출된 Citation ID 목록
            sources: 검색 결과 목록 (1-indexed)
            verification: Citation 검증 결과

        Returns:
            Citation 객체 리스트
        """
        citations = []

        for cid in sorted(citation_ids):
            is_valid = verification.get(cid, False)

            if not is_valid:
                # 유효하지 않은 Citation은 더미로 표시
                citations.append(
                    Citation(
                        id=cid,
                        title="[출처 없음]",
                        url="",
                        snippet="",
                        verified=False,
                    )
                )
                continue

            # sources는 0-indexed이므로 cid-1
            source = sources[cid - 1]

            citations.append(
                Citation(
                    id=cid,
                    title=source.get("title", "제목 없음"),
                    url=source.get("url", ""),
                    snippet=source.get("snippet", "")[:200],  # 최대 200자
                    verified=True,
                )
            )

        logger.info(f"[Citation] Built {len(citations)} reference entries")
        return citations


class CitationManager:
    """Citation 관리 통합 클래스."""

    def __init__(self):
        """Initialize citation manager."""
        self.extractor = CitationExtractor()
        self.verifier = CitationVerifier()
        self.builder = ReferenceBuilder()

    def process(
        self, response_text: str, sources: list[dict[str, Any]]
    ) -> tuple[str, list[Citation]]:
        """LLM 응답에서 Citation 처리.

        Args:
            response_text: LLM 생성 응답
            sources: 검색 결과 목록

        Returns:
            (원본 텍스트, Citation 목록) 튜플
        """
        # 1. Citation 번호 추출
        citation_ids = self.extractor.extract(response_text)

        if not citation_ids:
            logger.info("[Citation] No citations found in response")
            return response_text, []

        # 2. Citation 검증
        verification = self.verifier.verify(citation_ids, sources)

        # 3. 출처 목록 생성
        citations = self.builder.build(citation_ids, sources, verification)

        # 4. 검증 실패한 Citation 경고
        invalid_count = sum(1 for c in citations if not c.verified)
        if invalid_count > 0:
            logger.warning(
                f"[Citation] {invalid_count} unverified citations in response"
            )

        return response_text, citations

    def format_references(self, citations: list[Citation]) -> str:
        """출처 목록을 마크다운 형식으로 포맷팅.

        Args:
            citations: Citation 목록

        Returns:
            마크다운 형식 출처 목록
        """
        if not citations:
            return ""

        lines = ["\n## 출처\n"]

        for cite in citations:
            if cite.verified:
                lines.append(f"[{cite.id}] **{cite.title}**")
                lines.append(f"    {cite.url}\n")
            else:
                lines.append(f"[{cite.id}] [!] 출처 확인 불가\n")

        return "\n".join(lines)
