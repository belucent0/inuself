"""Quality Assessor - 검색 결과 신뢰도 평가 (Phase 3)

규칙 기반 품질 평가:
1. 도메인 신뢰도 (화이트리스트)
2. 최신성 체크 (날짜 파싱)
3. 콘텐츠 품질 (휴리스틱)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
import re

from loguru import logger


# 신뢰할 수 있는 도메인 화이트리스트 (점수: 0-100)
TRUSTED_DOMAINS = {
    # 공식 문서 (최고 신뢰도)
    "python.org": 95,
    "docs.python.org": 95,
    "pytorch.org": 95,
    "tensorflow.org": 95,
    "numpy.org": 95,
    "pandas.pydata.org": 95,
    "fastapi.tiangolo.com": 95,
    "flask.palletsprojects.com": 95,
    "django-doc.readthedocs.io": 95,
    # 개발 플랫폼
    "github.com": 90,
    "gitlab.com": 85,
    # 기술 커뮤니티
    "stackoverflow.com": 88,
    "stackexchange.com": 85,
    # 뉴스/미디어
    "techcrunch.com": 80,
    "arstechnica.com": 80,
    "wired.com": 75,
    # 위키
    "en.wikipedia.org": 75,
    "ko.wikipedia.org": 75,
    # 블로그 플랫폼 (중간 신뢰도)
    "medium.com": 65,
    "dev.to": 70,
    "velog.io": 65,
    # 교육
    "coursera.org": 80,
    "udacity.com": 80,
    "edx.org": 80,
    # 한국 기술 블로그
    "tech.kakao.com": 85,
    "engineering.linecorp.com": 85,
    "techblog.woowahan.com": 85,
    "d2.naver.com": 85,
}

# 낮은 신뢰도 도메인 (자동 감점)
LOW_TRUST_DOMAINS = {
    "namu.wiki": -10,  # 나무위키
    "blog.naver.com": -5,  # 개인 블로그
    "tistory.com": -5,  # 티스토리
}

# 스팸 키워드 (광고 감지)
SPAM_KEYWORDS = [
    "광고",
    "클릭",
    "무료다운로드",
    "지금바로",
    "할인",
    "프로모션",
    "이벤트",
]


class QualityAssessor:
    """검색 결과 품질 평가기."""

    def __init__(self):
        """Initialize quality assessor."""
        pass

    def assess(self, result: dict[str, Any]) -> dict[str, Any]:
        """검색 결과의 품질을 평가하고 점수를 부여.

        Args:
            result: 검색 결과
                - url: URL
                - title: 제목
                - snippet: 스니펫
                - (선택) published_date: 발행일

        Returns:
            평가된 검색 결과 (quality_score, trust_score, freshness_score 추가)
        """
        url = result.get("url", "")
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        published_date = result.get("published_date")

        # 1. 도메인 신뢰도
        domain_score = self._assess_domain(url)

        # 2. 최신성
        freshness_score = self._assess_freshness(published_date)

        # 3. 콘텐츠 품질
        content_score = self._assess_content_quality(title, snippet)

        # 4. 종합 점수 (0-100)
        quality_score = self._calculate_final_score(
            domain_score, freshness_score, content_score
        )

        # 결과에 점수 추가
        result["quality_score"] = round(quality_score, 2)
        result["trust_score"] = round(domain_score, 2)
        result["freshness_score"] = round(freshness_score, 2)
        result["content_score"] = round(content_score, 2)

        logger.debug(
            f"[Quality] {url[:50]} - Quality: {quality_score:.1f} "
            f"(Trust: {domain_score:.1f}, Fresh: {freshness_score:.1f}, Content: {content_score:.1f})"
        )

        return result

    def _assess_domain(self, url: str) -> float:
        """도메인 신뢰도 평가.

        Args:
            url: URL

        Returns:
            도메인 점수 (0-100)
        """
        if not url:
            return 50.0  # 기본값

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # www 제거
            if domain.startswith("www."):
                domain = domain[4:]

            # 화이트리스트 확인
            if domain in TRUSTED_DOMAINS:
                return float(TRUSTED_DOMAINS[domain])

            # 저신뢰 도메인 확인
            for low_domain, penalty in LOW_TRUST_DOMAINS.items():
                if low_domain in domain:
                    return max(50.0 + penalty, 0.0)

            # 공식 문서 패턴 감지
            if any(
                pattern in domain
                for pattern in ["docs.", "documentation.", "official.", "reference."]
            ):
                return 90.0

            # .org, .edu 도메인 (일반적으로 신뢰도 높음)
            if domain.endswith(".org") or domain.endswith(".edu"):
                return 80.0

            # .gov 도메인 (정부 기관)
            if domain.endswith(".gov"):
                return 95.0

            # 기본값
            return 60.0

        except Exception as e:
            logger.warning(f"[Quality] Failed to parse domain from {url}: {e}")
            return 50.0

    def _assess_freshness(self, published_date: str | datetime | None) -> float:
        """최신성 평가.

        Args:
            published_date: 발행일 (ISO 형식 문자열 또는 datetime)

        Returns:
            최신성 점수 (0-100)
        """
        if not published_date:
            return 50.0  # 날짜 정보 없음

        try:
            # 문자열을 datetime으로 변환
            if isinstance(published_date, str):
                # ISO 형식 파싱
                pub_date = datetime.fromisoformat(published_date.replace("Z", "+00:00"))
            else:
                pub_date = published_date

            now = datetime.now(timezone.utc)

            # timezone-naive datetime을 timezone-aware로 변환
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)

            age = now - pub_date

            # 최신성 점수 계산
            if age < timedelta(days=30):  # 1개월 이내
                return 100.0
            elif age < timedelta(days=180):  # 6개월 이내
                return 90.0
            elif age < timedelta(days=365):  # 1년 이내
                return 80.0
            elif age < timedelta(days=365 * 2):  # 2년 이내
                return 70.0
            elif age < timedelta(days=365 * 3):  # 3년 이내
                return 60.0
            elif age < timedelta(days=365 * 5):  # 5년 이내
                return 40.0
            else:  # 5년 이상
                return 20.0

        except Exception as e:
            logger.warning(f"[Quality] Failed to parse date {published_date}: {e}")
            return 50.0

    def _assess_content_quality(self, title: str, snippet: str) -> float:
        """콘텐츠 품질 평가 (휴리스틱).

        Args:
            title: 제목
            snippet: 스니펫

        Returns:
            콘텐츠 점수 (0-100)
        """
        score = 70.0  # 기본값

        content = (title + " " + snippet).lower()

        # 1. 길이 체크
        if len(snippet) < 50:
            score -= 10  # 너무 짧음
        elif len(snippet) > 200:
            score += 5  # 충분한 내용

        # 2. 스팸 키워드 감지
        spam_count = sum(1 for kw in SPAM_KEYWORDS if kw in content)
        if spam_count > 0:
            score -= min(spam_count * 10, 30)  # 최대 -30점

        # 3. 코드 블록 포함 (기술 문서)
        if any(pattern in snippet for pattern in ["```", "```python", "```javascript"]):
            score += 10

        # 4. 질문 포함 (Stack Overflow 스타일)
        if "?" in title or "how to" in title.lower():
            score += 5

        # 5. 제목 품질
        if len(title) < 10:
            score -= 5  # 제목이 너무 짧음
        if title.isupper():
            score -= 10  # 전부 대문자 (스팸 가능성)

        # 6. 특수문자 과다 사용
        special_chars = re.findall(r"[!@#$%^&*(){}\[\]]", title)
        if len(special_chars) > 3:
            score -= 10

        return max(min(score, 100.0), 0.0)  # 0-100 범위

    def _calculate_final_score(
        self, domain_score: float, freshness_score: float, content_score: float
    ) -> float:
        """종합 품질 점수 계산.

        가중치:
        - 도메인: 50%
        - 최신성: 30%
        - 콘텐츠: 20%

        Args:
            domain_score: 도메인 점수
            freshness_score: 최신성 점수
            content_score: 콘텐츠 점수

        Returns:
            종합 점수 (0-100)
        """
        final = (
            domain_score * 0.5 + freshness_score * 0.3 + content_score * 0.2
        )
        return max(min(final, 100.0), 0.0)

    def filter_low_quality(
        self, results: list[dict[str, Any]], min_score: float = 40.0
    ) -> list[dict[str, Any]]:
        """낮은 품질의 결과를 필터링.

        Args:
            results: 검색 결과 목록
            min_score: 최소 품질 점수

        Returns:
            필터링된 결과
        """
        filtered = []
        removed_count = 0

        for result in results:
            # 평가되지 않은 결과는 평가 먼저
            if "quality_score" not in result:
                result = self.assess(result)

            if result.get("quality_score", 0) >= min_score:
                filtered.append(result)
            else:
                removed_count += 1
                logger.debug(
                    f"[Quality] Filtered out: {result.get('url', 'N/A')} "
                    f"(score: {result.get('quality_score', 0):.1f})"
                )

        logger.info(
            f"[Quality] Filtered {removed_count}/{len(results)} low-quality results "
            f"(min_score={min_score})"
        )

        return filtered

    def rerank_by_quality(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """품질 점수 기준으로 재정렬.

        Args:
            results: 검색 결과 목록

        Returns:
            재정렬된 결과
        """
        # 평가되지 않은 결과는 평가 먼저
        for result in results:
            if "quality_score" not in result:
                self.assess(result)

        # 품질 점수 기준 내림차순 정렬
        sorted_results = sorted(
            results, key=lambda x: x.get("quality_score", 0), reverse=True
        )

        logger.info(
            f"[Quality] Reranked {len(sorted_results)} results by quality score"
        )

        return sorted_results
