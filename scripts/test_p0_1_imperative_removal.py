"""P0-1: 명령형/요청형 제거 기능 테스트

V8.5 Query Refinement 개선 사항을 테스트합니다.
"""
import sys
import os
import asyncio
import json
from datetime import datetime

# Windows 인코딩 설정
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Backend 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.agents import run_ai_agent
from app.core.config import get_settings


# 테스트 케이스
TEST_CASES = [
    {
        "name": "TSMC 케이스 재현 #1",
        "query": "tsmc 2026년에 발표된 투자 근황",
        "expected_cleaned": "tsmc 2026년에 발표된 투자 근황",  # 명령형 없음
        "expected_keywords_include": ["tsmc", "2026", "투자"],
        "expected_keywords_exclude": [],
    },
    {
        "name": "TSMC 케이스 재현 #2 (치명적 오류)",
        "query": "2030년까지의 투자계획을 안내바람",
        "expected_cleaned": "2030년까지의 투자계획",  # "안내바람" 제거
        "expected_keywords_include": ["2030", "투자", "계획"],
        "expected_keywords_exclude": ["바람"],  # ❌ "바람"이 없어야 함
    },
    {
        "name": "알려줘 패턴",
        "query": "파이썬 웹 프레임워크 비교 알려줘",
        "expected_cleaned": "파이썬 웹 프레임워크 비교",
        "expected_keywords_include": ["파이썬", "웹", "프레임워크", "비교"],
        "expected_keywords_exclude": ["알려", "줘"],
    },
    {
        "name": "해주세요 패턴",
        "query": "두바이 쫀득 쿠키 레시피를 설명해주세요",
        "expected_cleaned": "두바이 쫀득 쿠키 레시피",
        "expected_keywords_include": ["두바이", "쿠키", "레시피"],
        "expected_keywords_exclude": ["설명", "주세요"],
    },
    {
        "name": "조사바람 패턴",
        "query": "SK하이닉스 2026년 투자 계획 조사바람",
        "expected_cleaned": "SK하이닉스 2026년 투자 계획",
        "expected_keywords_include": ["SK", "하이닉스", "2026", "투자", "계획"],
        "expected_keywords_exclude": ["조사", "바람"],
    },
    {
        "name": "복합 패턴",
        "query": "최근 AI 트렌드에 대해 자세히 알려달라",
        "expected_cleaned": "최근 AI 트렌드",
        "expected_keywords_include": ["최근", "AI", "트렌드"],
        "expected_keywords_exclude": ["알려", "달라"],
    },
]


async def test_intent_parser_only():
    """IntentParser만 테스트 (빠른 검증)"""
    print("=" * 80)
    print("P0-1: 명령형/요청형 제거 기능 테스트")
    print("=" * 80)
    print()

    settings = get_settings()

    # IntentParser 직접 테스트
    from app.agents.nodes.intent_parser import IntentParserNode
    parser = IntentParserNode(settings)

    results = []

    for i, test_case in enumerate(TEST_CASES, 1):
        name = test_case["name"]
        query = test_case["query"]
        expected_cleaned = test_case["expected_cleaned"]
        expected_include = test_case["expected_keywords_include"]
        expected_exclude = test_case["expected_keywords_exclude"]

        print(f"\n{'=' * 80}")
        print(f"[테스트 {i}] {name}")
        print(f"{'=' * 80}")
        print(f"입력: {query}")
        print(f"예상 정제: {expected_cleaned}")

        # 1. 명령형 제거 테스트
        cleaned = parser._clean_imperative_forms(query)
        print(f"\n[1단계: 명령형 제거]")
        print(f"  결과: {cleaned}")
        print(f"  예상: {expected_cleaned}")

        cleaned_ok = cleaned == expected_cleaned
        print(f"  ✅ 성공" if cleaned_ok else f"  ❌ 실패")

        # 2. reformulate_query 전체 테스트
        print(f"\n[2단계: 전체 파이프라인]")
        try:
            query_analysis = await parser.reformulate_query(query, state=None)

            reformulated = query_analysis.get("reformulated_query", "")
            search_queries = query_analysis.get("sub_queries", [])
            keywords = query_analysis.get("keywords", [])

            print(f"  Reformulated: {reformulated}")
            print(f"  Search Queries: {search_queries}")
            print(f"  Keywords: {keywords}")

            # 키워드 검증
            include_ok = all(
                any(exp.lower() in kw.lower() for kw in keywords)
                for exp in expected_include
            )
            exclude_ok = all(
                not any(exp.lower() in kw.lower() for kw in keywords)
                for exp in expected_exclude
            )

            print(f"\n  [키워드 검증]")
            for exp in expected_include:
                found = any(exp.lower() in kw.lower() for kw in keywords)
                print(f"    {'✅' if found else '❌'} 포함: {exp}")

            for exp in expected_exclude:
                not_found = not any(exp.lower() in kw.lower() for kw in keywords)
                print(f"    {'✅' if not_found else '❌'} 제외: {exp}")

            overall_ok = cleaned_ok and include_ok and exclude_ok
            print(f"\n  {'✅ 전체 성공' if overall_ok else '❌ 일부 실패'}")

            results.append({
                "name": name,
                "query": query,
                "cleaned": cleaned,
                "cleaned_ok": cleaned_ok,
                "keywords": keywords,
                "include_ok": include_ok,
                "exclude_ok": exclude_ok,
                "overall_ok": overall_ok,
            })

        except Exception as e:
            print(f"  ❌ 에러: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "name": name,
                "query": query,
                "error": str(e),
                "overall_ok": False,
            })

    # 최종 결과
    print(f"\n\n{'=' * 80}")
    print("최종 결과")
    print(f"{'=' * 80}")

    success_count = sum(1 for r in results if r.get("overall_ok", False))
    total_count = len(results)

    print(f"\n성공: {success_count}/{total_count} ({success_count/total_count*100:.1f}%)")
    print()

    for r in results:
        status = "✅" if r.get("overall_ok", False) else "❌"
        print(f"  {status} {r['name']}")

    print(f"\n{'=' * 80}")

    return results


async def test_full_pipeline():
    """전체 AI Agent 파이프라인 테스트 (실제 검증)"""
    print("\n\n" + "=" * 80)
    print("전체 파이프라인 테스트 (실제 검색 포함)")
    print("=" * 80)
    print()

    settings = get_settings()

    # TSMC 케이스만 전체 파이프라인 테스트
    test_case = TEST_CASES[1]  # "2030년까지의 투자계획을 안내바람"

    name = test_case["name"]
    query = test_case["query"]

    print(f"[테스트] {name}")
    print(f"질문: {query}")
    print()

    result = await run_ai_agent(
        settings=settings,
        query=query,
        mode="search",
        enable_retry=False,  # 재시도 비활성화 (빠른 테스트)
    )

    print(f"[결과]")
    print(f"  Mode: {result.get('mode')}")
    print(f"  Intent: {result.get('query_analysis')}")
    print(f"  Keywords: {result.get('query_analysis', {}).get('keywords', [])}")
    print(f"  Search Queries: {result.get('query_analysis', {}).get('sub_queries', [])}")
    print(f"  Search Results: {len(result.get('search_results', []))}개")

    # 키워드에 "바람" 없는지 확인
    keywords = result.get('query_analysis', {}).get('keywords', [])
    has_bad_keyword = any("바람" in kw for kw in keywords)

    print(f"\n  {'❌ 실패: 키워드에 \"바람\" 포함됨' if has_bad_keyword else '✅ 성공: \"바람\" 제거됨'}")

    print(f"\n  응답 미리보기: {result.get('response', '')[:200]}...")


if __name__ == "__main__":
    print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 1. IntentParser만 테스트 (빠름)
    results = asyncio.run(test_intent_parser_only())

    # 2. 전체 파이프라인 테스트 (느림, 선택적)
    # asyncio.run(test_full_pipeline())

    print(f"\n종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
