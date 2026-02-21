"""검색 재시도 기능 테스트 스크립트

V8.4: 검색 결과가 불충분할 때 자동 재시도하는지 검증
"""
import sys
import os
import asyncio
import json
import time
from datetime import datetime

# Windows 인코딩 설정
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Backend 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.agents import run_ai_agent
from app.core.config import get_settings


async def test_search_retry():
    """검색 재시도 기능 테스트"""
    print("=" * 80)
    print("검색 재시도 기능 테스트 (V8.4)")
    print("=" * 80)

    settings = get_settings()

    # 테스트 케이스: 검색 결과가 없거나 부족할 가능성이 높은 쿼리
    test_cases = [
        {
            "query": "xyzabc12345 최신 정보",  # 존재하지 않는 키워드
            "expected": "no_results or low_quality",
            "description": "존재하지 않는 키워드 (재시도 예상)",
        },
        {
            "query": "파이썬 웹 프레임워크 비교",  # 정상적인 쿼리
            "expected": "sufficient_results",
            "description": "정상 쿼리 (재시도 불필요 예상)",
        },
    ]

    for i, test_case in enumerate(test_cases, 1):
        query = test_case["query"]
        expected = test_case["expected"]
        description = test_case["description"]

        print(f"\n{'=' * 80}")
        print(f"[테스트 케이스 {i}] {description}")
        print(f"질문: {query}")
        print(f"예상: {expected}")
        print('=' * 80)

        # AI Agent 실행 (재시도 활성화)
        start_time = time.time()
        result = await run_ai_agent(
            settings=settings,
            query=query,
            mode="search",  # 강제로 SEARCH 모드
            enable_retry=True,  # V8.4 재시도 활성화
            max_retries=3,
        )
        elapsed = time.time() - start_time

        # 결과 분석
        print(f"\n[실행 시간] {elapsed:.2f}초")
        print(f"[모드] {result.get('mode')}")

        # 재시도 정보
        retry_count = result.get("search_retry_count", 0)
        quality_score = result.get("search_quality_score", 0)
        retry_reason = result.get("retry_reason", "")

        print(f"\n[재시도 정보]")
        print(f"  - 재시도 횟수: {retry_count}")
        print(f"  - 품질 점수: {quality_score:.1f}/100")
        print(f"  - 재시도 이유: {retry_reason if retry_reason else '없음 (충분)'}")

        # 실패한 쿼리
        failed_queries = result.get("failed_queries", [])
        if failed_queries:
            print(f"\n[실패한 쿼리] (총 {len(failed_queries)}개)")
            for j, fq in enumerate(failed_queries[:5], 1):
                print(f"  {j}. {fq}")

        # 최종 검색 쿼리
        search_queries = result.get("search_queries", [])
        print(f"\n[최종 검색 쿼리] (총 {len(search_queries)}개)")
        for j, sq in enumerate(search_queries, 1):
            print(f"  {j}. {sq}")

        # 검색 결과
        search_results = result.get("search_results", [])
        print(f"\n[검색 결과] (총 {len(search_results)}개)")
        if search_results:
            for j, sr in enumerate(search_results[:3], 1):
                if isinstance(sr, dict):
                    title = sr.get("title", "N/A")[:60]
                    quality = sr.get("quality_score", 0)
                    print(f"  {j}. [{quality:.1f}점] {title}")
        else:
            print("  (검색 결과 없음)")

        # 사고 과정
        thinking_steps = result.get("thinking_steps", [])
        retry_steps = [
            s for s in thinking_steps
            if "query_rewrite" in s.get("step", "") or "evaluation" in s.get("step", "")
        ]
        if retry_steps:
            print(f"\n[재시도 과정] (총 {len(retry_steps)}단계)")
            for step in retry_steps:
                content = step.get("content", "")
                print(f"  - {content}")

        # 응답 미리보기
        response = result.get("response", "")
        preview = response[:200].replace('\n', ' ')
        print(f"\n[응답 미리보기]")
        print(f"  {preview}...")
        print(f"  (전체 길이: {len(response)}자)")

        # 평가
        print(f"\n[평가]")
        if retry_count == 0 and quality_score >= 50:
            print("  ✓ 첫 시도 성공 (재시도 불필요)")
        elif retry_count > 0 and quality_score >= 50:
            print(f"  ✓ {retry_count}회 재시도 후 성공")
        elif retry_count >= 3:
            print(f"  ! 최대 재시도 ({retry_count}회) 도달, 폴백 사용")
        else:
            print(f"  ? 예상치 못한 상태 (retry={retry_count}, quality={quality_score})")

        # 대기
        if i < len(test_cases):
            print("\n다음 테스트까지 3초 대기...")
            await asyncio.sleep(3)

    # 최종 요약
    print("\n" + "=" * 80)
    print("테스트 완료")
    print("=" * 80)
    print("\n[검증 포인트]")
    print("1. 테스트 1: 재시도가 발생했는가? (retry_count > 0)")
    print("2. 테스트 1: 쿼리가 재작성되었는가? (failed_queries 존재)")
    print("3. 테스트 1: 폴백이 작동했는가? (retry_count >= 3)")
    print("4. 테스트 2: 재시도 없이 성공했는가? (retry_count == 0, quality >= 50)")
    print("\n상세 분석을 위해 로그를 확인하세요.")


if __name__ == "__main__":
    asyncio.run(test_search_retry())
