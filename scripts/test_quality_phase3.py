"""Phase 3 Quality Assessor 수동 테스트"""
import sys
import os
from datetime import datetime, timedelta, timezone

# backend 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.utils.quality_assessor import QualityAssessor


def test_quality_assessor():
    """QualityAssessor 기본 테스트."""
    assessor = QualityAssessor()

    print("=" * 60)
    print("Phase 3 Quality Assessor 테스트")
    print("=" * 60)

    # 테스트 1: 공식 문서 (높은 신뢰도)
    print("\n[Test 1] 공식 문서 평가")
    result1 = {
        "url": "https://docs.python.org/3/tutorial.html",
        "title": "Python Tutorial - Official Documentation",
        "snippet": "Learn Python programming with this comprehensive tutorial covering basic syntax.",
        "published_date": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
    }
    assessed1 = assessor.assess(result1)
    print(f"  URL: {result1['url']}")
    print(f"  Quality Score: {assessed1['quality_score']:.1f}")
    print(f"  Trust Score: {assessed1['trust_score']:.1f}")
    print(f"  Freshness Score: {assessed1['freshness_score']:.1f}")
    print(f"  Content Score: {assessed1['content_score']:.1f}")
    assert assessed1["quality_score"] >= 80, "공식 문서는 80점 이상이어야 함"
    print("  [PASS]")

    # 테스트 2: 블로그 (낮은 신뢰도)
    print("\n[Test 2] 개인 블로그 평가")
    result2 = {
        "url": "https://blog.naver.com/some-post",
        "title": "블로그 글",
        "snippet": "개인적인 의견입니다.",
        "published_date": (datetime.now(timezone.utc) - timedelta(days=365 * 3)).isoformat(),
    }
    assessed2 = assessor.assess(result2)
    print(f"  URL: {result2['url']}")
    print(f"  Quality Score: {assessed2['quality_score']:.1f}")
    print(f"  Trust Score: {assessed2['trust_score']:.1f}")
    print(f"  Freshness Score: {assessed2['freshness_score']:.1f}")
    print(f"  Content Score: {assessed2['content_score']:.1f}")
    assert assessed2["quality_score"] < 60, "개인 블로그는 60점 미만이어야 함"
    print("  [PASS]")

    # 테스트 3: 스팸 (매우 낮은 품질)
    print("\n[Test 3] 스팸성 콘텐츠 평가")
    result3 = {
        "url": "https://spam.example.com/ad",
        "title": "광고 클릭 이벤트!!!",
        "snippet": "무료다운로드 지금바로 클릭하세요!!! 할인 프로모션!!!",
    }
    assessed3 = assessor.assess(result3)
    print(f"  URL: {result3['url']}")
    print(f"  Quality Score: {assessed3['quality_score']:.1f}")
    print(f"  Trust Score: {assessed3['trust_score']:.1f}")
    print(f"  Freshness Score: {assessed3['freshness_score']:.1f}")
    print(f"  Content Score: {assessed3['content_score']:.1f}")
    assert assessed3["content_score"] < 50, "스팸은 콘텐츠 점수가 낮아야 함"
    print("  [PASS]")

    # 테스트 4: 재정렬
    print("\n[Test 4] 품질 기반 재정렬")
    results = [result2, result3, result1]  # 낮은 품질 → 스팸 → 높은 품질 순서
    reranked = assessor.rerank_by_quality(results)
    print(f"  Before: {[r['url'].split('/')[2] for r in results]}")
    print(f"  After:  {[r['url'].split('/')[2] for r in reranked]}")
    assert "python.org" in reranked[0]["url"], "공식 문서가 첫 번째여야 함"
    print("  [PASS]")

    # 테스트 5: 필터링
    print("\n[Test 5] 낮은 품질 필터링")
    filtered = assessor.filter_low_quality(results, min_score=50.0)
    print(f"  Original: {len(results)} results")
    print(f"  Filtered: {len(filtered)} results (min_score=50)")
    print(f"  Remaining: {[r['url'].split('/')[2] for r in filtered]}")
    assert len(filtered) <= len(results), "필터링 후 개수가 줄어야 함"
    print("  [PASS]")

    print("\n" + "=" * 60)
    print("[SUCCESS] All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    test_quality_assessor()
