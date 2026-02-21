"""Query Contextualization 테스트 스크립트

컨텍스트 의존적인 질문 시퀀스를 보내서 재작성이 제대로 되는지 확인
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
from app.services.conversation_service import get_conversation_service


async def test_contextualization():
    """컨텍스트 의존적 질문 시퀀스 테스트"""
    print("=" * 80)
    print("Query Contextualization 테스트")
    print("=" * 80)

    settings = get_settings()
    conv_service = get_conversation_service()

    # 테스트 질문 시퀀스
    questions = [
        "파이썬 웹 프레임워크 비교해줘",
        "그 중에서 가장 빠른 건?",
        "그거 배우려면 얼마나 걸려?",
    ]

    conversation_id = None

    for i, query in enumerate(questions, 1):
        print(f"\n{'=' * 80}")
        print(f"[질문 {i}] {query}")
        print('=' * 80)

        # 사용자 메시지 저장
        if conversation_id:
            await conv_service.add_message(
                conversation_id,
                role="user",
                content=query,
            )

        # AI Agent 실행
        start_time = time.time()
        result = await run_ai_agent(
            settings=settings,
            query=query,
            conversation_id=conversation_id,
            mode="search",  # 강제로 SEARCH 모드 사용
        )
        elapsed = time.time() - start_time

        # 첫 실행에서 conversation_id 저장
        if not conversation_id:
            # 대화 생성
            conversation = await conv_service.create_conversation()
            conversation_id = conversation.conversation_id
            # 사용자 메시지 저장
            await conv_service.add_message(
                conversation_id,
                role="user",
                content=query,
            )

        # AI 응답 저장
        await conv_service.add_message(
            conversation_id,
            role="assistant",
            content=result.get("response", ""),
            metadata={
                "mode": str(result.get("mode", "simple")),
                "sources": result.get("sources", []),
                "citations": result.get("citations", []),
                "intent": result.get("query_analysis"),
                "search_queries": result.get("search_queries", []),
                "search_results": result.get("search_results", []),
                "thinking_steps": result.get("thinking_steps", []),
            },
        )

        # 결과 출력
        print(f"\n[실행 시간] {elapsed:.2f}초")
        print(f"[모드] {result.get('mode')}")

        # Intent 분석
        query_analysis = result.get("query_analysis")
        if query_analysis:
            print(f"\n[Intent 분석]")
            if isinstance(query_analysis, dict):
                intent_val = query_analysis.get("intent", "N/A")
                mode_val = query_analysis.get("mode", "N/A")
                print(f"  의도: {intent_val}")
                print(f"  모드: {mode_val}")

        # 검색 쿼리
        search_queries = result.get("search_queries", [])
        print(f"\n[생성된 검색 쿼리] (총 {len(search_queries)}개)")
        for j, sq in enumerate(search_queries, 1):
            is_original = sq == query
            marker = " [원본]" if is_original else " [재작성/변환]"
            print(f"  {j}. {sq}{marker}")

        # 검색 결과
        search_results = result.get("search_results", [])
        if search_results:
            print(f"\n[검색 결과] (총 {len(search_results)}개)")
            for j, sr in enumerate(search_results[:3], 1):
                if isinstance(sr, dict):
                    title = sr.get("title", "N/A")[:60]
                    quality = sr.get("quality_score", 0)
                    url = sr.get("url", "N/A")[:50]
                    print(f"  {j}. [{quality:.1f}점] {title}")
                    print(f"     {url}")

        # Citations
        citations = result.get("citations", [])
        if citations:
            print(f"\n[Citations] (총 {len(citations)}개)")
            for cite in citations[:3]:
                if isinstance(cite, dict):
                    cid = cite.get("id", "?")
                    title = cite.get("title", "N/A")[:50]
                    verified = cite.get("verified", False)
                    print(f"  [{cid}] {title} {'✓' if verified else '✗'}")

        # 응답 미리보기
        response = result.get("response", "")
        preview = response[:200].replace('\n', ' ')
        print(f"\n[응답 미리보기]")
        print(f"  {preview}...")
        print(f"  (전체 길이: {len(response)}자)")

        # 잠시 대기 (rate limit 방지)
        if i < len(questions):
            print("\n다음 질문까지 2초 대기...")
            await asyncio.sleep(2)

    # 최종 요약
    print("\n" + "=" * 80)
    print("테스트 완료")
    print("=" * 80)
    print(f"\n대화 ID: {conversation_id}")
    print(f"총 질문: {len(questions)}개")
    print("\n상세 분석을 위해 다음 명령을 실행하세요:")
    print(f"  uv run python scripts/analyze_recent_chat.py")

    return conversation_id


if __name__ == "__main__":
    conv_id = asyncio.run(test_contextualization())
