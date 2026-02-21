"""Quality Assessor 테스트"""
from datetime import datetime, timedelta, timezone

import pytest

from app.utils.quality_assessor import QualityAssessor


@pytest.fixture
def assessor():
    """QualityAssessor 인스턴스."""
    return QualityAssessor()


def test_domain_assessment_official_docs(assessor):
    """공식 문서 도메인은 높은 점수를 받아야 함."""
    result = {"url": "https://docs.python.org/3/tutorial.html", "title": "Python Tutorial", "snippet": "..."}
    assessed = assessor.assess(result)

    assert assessed["trust_score"] >= 90
    assert "quality_score" in assessed


def test_domain_assessment_low_trust(assessor):
    """낮은 신뢰도 도메인은 감점되어야 함."""
    result = {"url": "https://blog.naver.com/some-post", "title": "블로그 글", "snippet": "..."}
    assessed = assessor.assess(result)

    assert assessed["trust_score"] < 60


def test_freshness_recent(assessor):
    """최근 콘텐츠는 높은 최신성 점수를 받아야 함."""
    recent_date = datetime.now(timezone.utc) - timedelta(days=15)
    result = {
        "url": "https://example.com",
        "title": "Recent Article",
        "snippet": "...",
        "published_date": recent_date.isoformat(),
    }
    assessed = assessor.assess(result)

    assert assessed["freshness_score"] >= 90


def test_freshness_old(assessor):
    """오래된 콘텐츠는 낮은 최신성 점수를 받아야 함."""
    old_date = datetime.now(timezone.utc) - timedelta(days=365 * 6)
    result = {
        "url": "https://example.com",
        "title": "Old Article",
        "snippet": "...",
        "published_date": old_date.isoformat(),
    }
    assessed = assessor.assess(result)

    assert assessed["freshness_score"] < 40


def test_content_quality_short_snippet(assessor):
    """짧은 스니펫은 감점되어야 함."""
    result = {"url": "https://example.com", "title": "Title", "snippet": "Too short"}
    assessed = assessor.assess(result)

    # 짧은 콘텐츠로 인해 기본값(70)보다 낮아야 함
    assert assessed["content_score"] < 70


def test_content_quality_spam(assessor):
    """스팸 키워드가 많으면 감점되어야 함."""
    result = {
        "url": "https://example.com",
        "title": "광고 클릭 이벤트",
        "snippet": "무료다운로드 지금바로 클릭하세요! 할인 프로모션!",
    }
    assessed = assessor.assess(result)

    # 스팸 키워드로 인해 크게 감점
    assert assessed["content_score"] < 50


def test_filter_low_quality(assessor):
    """낮은 품질 결과는 필터링되어야 함."""
    results = [
        {"url": "https://docs.python.org", "title": "Python Docs", "snippet": "Official documentation"},
        {"url": "https://spam.com", "title": "광고", "snippet": "클릭"},
    ]

    # 평가
    for r in results:
        assessor.assess(r)

    # 필터링 (최소 점수 50)
    filtered = assessor.filter_low_quality(results, min_score=50.0)

    # 공식 문서만 남아야 함
    assert len(filtered) == 1
    assert "python.org" in filtered[0]["url"]


def test_rerank_by_quality(assessor):
    """품질 점수 기준으로 재정렬되어야 함."""
    results = [
        {
            "url": "https://blog.naver.com/post",
            "title": "블로그",
            "snippet": "...",
        },
        {
            "url": "https://docs.python.org/tutorial",
            "title": "Python Tutorial",
            "snippet": "Official Python documentation for beginners",
        },
    ]

    reranked = assessor.rerank_by_quality(results)

    # 공식 문서가 첫 번째여야 함
    assert "python.org" in reranked[0]["url"]
    assert reranked[0]["quality_score"] > reranked[1]["quality_score"]


def test_overall_quality_score(assessor):
    """종합 품질 점수가 올바르게 계산되어야 함."""
    # 우수한 결과
    excellent_result = {
        "url": "https://docs.python.org/3/tutorial.html",
        "title": "Python Tutorial - Official Documentation",
        "snippet": "Learn Python programming with this comprehensive tutorial. "
        "Covers basic syntax, data structures, and more. Over 500 words of quality content.",
        "published_date": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
    }

    assessed = assessor.assess(excellent_result)

    # 높은 종합 점수
    assert assessed["quality_score"] >= 80
    assert assessed["trust_score"] >= 90
    assert assessed["freshness_score"] >= 90


def test_no_date_provided(assessor):
    """날짜 정보가 없어도 평가가 가능해야 함."""
    result = {"url": "https://example.com", "title": "Article", "snippet": "Content here"}
    assessed = assessor.assess(result)

    # 기본값 사용
    assert assessed["freshness_score"] == 50.0
    assert "quality_score" in assessed
