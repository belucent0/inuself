"""Phase 4 Citation Manager 수동 테스트"""
import sys
import os

# backend 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.utils.citation_manager import CitationManager, CitationExtractor, CitationVerifier, ReferenceBuilder


def test_citation_manager():
    """CitationManager 기본 테스트."""
    print("=" * 60)
    print("Phase 4 Citation Manager 테스트")
    print("=" * 60)

    manager = CitationManager()

    # 테스트 데이터
    sources = [
        {"title": "Python 공식 문서", "url": "https://docs.python.org", "snippet": "Python은 1991년..."},
        {"title": "Stack Overflow", "url": "https://stackoverflow.com", "snippet": "파이썬 설치 방법..."},
        {"title": "나무위키", "url": "https://namu.wiki", "snippet": "파이썬은 프로그래밍 언어..."},
    ]

    # 테스트 1: Citation 추출
    print("\n[Test 1] Citation 추출")
    response1 = "파이썬은 1991년에 개발되었습니다[1]. 가장 인기있는 언어입니다[2]."
    _, citations1 = manager.process(response1, sources)
    print(f"  Response: {response1}")
    print(f"  Citations: {[c.id for c in citations1]}")
    assert len(citations1) == 2, "2개 Citation이 추출되어야 함"
    assert citations1[0].id == 1 and citations1[1].id == 2
    print("  [PASS]")

    # 테스트 2: Citation 없음
    print("\n[Test 2] Citation 없는 경우")
    response2 = "파이썬은 프로그래밍 언어입니다."
    _, citations2 = manager.process(response2, sources)
    print(f"  Response: {response2}")
    print(f"  Citations: {len(citations2)}")
    assert len(citations2) == 0, "Citation이 없어야 함"
    print("  [PASS]")

    # 테스트 3: 유효하지 않은 Citation
    print("\n[Test 3] 유효하지 않은 Citation")
    response3 = "파이썬은 1991년에 개발되었습니다[1]. 매우 인기있습니다[10]."
    _, citations3 = manager.process(response3, sources)
    print(f"  Response: {response3}")
    print(f"  Citations: {[(c.id, c.verified) for c in citations3]}")
    assert len(citations3) == 2, "2개 Citation이 추출되어야 함"
    assert citations3[0].verified == True, "[1]은 유효해야 함"
    assert citations3[1].verified == False, "[10]은 유효하지 않아야 함"
    print("  [PASS]")

    # 테스트 4: 중복 Citation
    print("\n[Test 4] 중복 Citation")
    response4 = "파이썬[1]은 인기있습니다[1]. 또한 배우기 쉽습니다[1]."
    _, citations4 = manager.process(response4, sources)
    print(f"  Response: {response4}")
    print(f"  Citations: {[c.id for c in citations4]}")
    assert len(citations4) == 1, "중복 제거되어 1개만 남아야 함"
    assert citations4[0].id == 1
    print("  [PASS]")

    # 테스트 5: Reference Formatting
    print("\n[Test 5] Reference Formatting")
    response5 = "파이썬[1]은 1991년에 개발되었고[2], 인기있는 언어입니다[3]."
    _, citations5 = manager.process(response5, sources)
    formatted = manager.format_references(citations5)
    print(f"  Response: {response5}")
    print(f"  Formatted References:")
    print(formatted)
    assert "Python 공식 문서" in formatted
    assert "Stack Overflow" in formatted
    assert "나무위키" in formatted
    print("  [PASS]")

    # 테스트 6: Extractor 단독 테스트
    print("\n[Test 6] CitationExtractor 단독 테스트")
    text = "정보[1]입니다. 추가[2], 더[3] 많은 정보[100]"
    extracted = CitationExtractor.extract(text)
    print(f"  Text: {text}")
    print(f"  Extracted: {extracted}")
    assert extracted == [1, 2, 3, 100], "모든 번호가 추출되어야 함"
    print("  [PASS]")

    # 테스트 7: Verifier 단독 테스트
    print("\n[Test 7] CitationVerifier 단독 테스트")
    citation_ids = [1, 2, 5, 100]
    verification = CitationVerifier.verify(citation_ids, sources)
    print(f"  Citation IDs: {citation_ids}")
    print(f"  Sources: {len(sources)} available")
    print(f"  Verification: {verification}")
    assert verification[1] == True, "[1]은 유효해야 함"
    assert verification[2] == True, "[2]는 유효해야 함"
    assert verification[5] == False, "[5]는 유효하지 않아야 함"
    assert verification[100] == False, "[100]은 유효하지 않아야 함"
    print("  [PASS]")

    # 테스트 8: Builder 단독 테스트
    print("\n[Test 8] ReferenceBuilder 단독 테스트")
    citation_ids = [1, 3, 5]
    verification = {1: True, 3: True, 5: False}
    citations = ReferenceBuilder.build(citation_ids, sources, verification)
    print(f"  Citation IDs: {citation_ids}")
    print(f"  Built Citations:")
    for c in citations:
        status = "OK" if c.verified else "Invalid"
        print(f"    [{c.id}] {c.title} ({status})")
    assert len(citations) == 3, "3개 Citation이 생성되어야 함"
    assert citations[0].verified == True
    assert citations[1].verified == True
    assert citations[2].verified == False
    assert citations[2].title == "[출처 없음]"
    print("  [PASS]")

    print("\n" + "=" * 60)
    print("[SUCCESS] All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    test_citation_manager()
