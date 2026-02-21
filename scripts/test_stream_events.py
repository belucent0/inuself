"""stream_ai_agent의 이벤트 전송 테스트.

search_queries와 search_results 이벤트가 올바르게 yield되는지 확인합니다.
"""
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "backend"))

from app.agents.graph import stream_ai_agent
from app.core.config import get_settings


async def test_stream_events():
    """스트리밍 이벤트 테스트."""
    settings = get_settings()

    # 검색 모드를 유발하는 쿼리
    query = "TSMC의 최신 뉴스"

    print(f"Testing query: {query}")
    print("=" * 80)

    events_received = {
        "thinking": 0,
        "query_analysis": 0,
        "search_queries": 0,
        "sources": 0,
        "search_results": 0,
        "token": 0,
        "done": 0,
    }

    search_queries_data = None
    search_results_data = None

    async for event in stream_ai_agent(
        settings=settings,
        query=query,
        conversation_id=None,
        mode="auto",
        metadata=None,
        enable_reflection=False,
        enable_retry=True,
        max_retries=3,
        user_id=None,
    ):
        event_type = event.get("type", "")
        event_data = event.get("data")

        if event_type in events_received:
            events_received[event_type] += 1

        # 중요한 이벤트 출력
        if event_type == "thinking":
            step = event_data.get("step", "") if isinstance(event_data, dict) else ""
            content = event_data.get("content", "") if isinstance(event_data, dict) else ""
            print(f"[THINKING] {step}: {content}")

        elif event_type == "query_analysis":
            print(f"[QUERY_ANALYSIS] {event_data}")

        elif event_type == "search_queries":
            search_queries_data = event_data
            print(f"[SEARCH_QUERIES] Received: {len(event_data) if isinstance(event_data, list) else 0} queries")
            print(f"  Queries: {event_data}")

        elif event_type == "sources":
            print(f"[SOURCES] Received: {len(event_data) if isinstance(event_data, list) else 0} sources")

        elif event_type == "search_results":
            search_results_data = event_data
            print(f"[SEARCH_RESULTS] Received: {len(event_data) if isinstance(event_data, list) else 0} results")

        elif event_type == "token":
            pass  # 토큰은 너무 많아서 생략

        elif event_type == "done":
            print("[DONE] Stream completed")

    print("\n" + "=" * 80)
    print("Event Summary:")
    for event_type, count in events_received.items():
        print(f"  {event_type}: {count}")

    print("\n" + "=" * 80)
    print("Validation:")

    success = True

    # search_queries 이벤트 검증
    if events_received["search_queries"] == 0:
        print("❌ FAIL: search_queries 이벤트가 전송되지 않았습니다!")
        success = False
    elif not search_queries_data or len(search_queries_data) == 0:
        print("❌ FAIL: search_queries 데이터가 비어있습니다!")
        success = False
    else:
        print(f"✓ PASS: search_queries 이벤트 전송됨 ({len(search_queries_data)} queries)")

    # search_results 이벤트 검증
    if events_received["search_results"] == 0:
        print("❌ FAIL: search_results 이벤트가 전송되지 않았습니다!")
        success = False
    elif not search_results_data or len(search_results_data) == 0:
        print("⚠️  WARN: search_results 데이터가 비어있습니다 (검색 결과가 없을 수 있음)")
    else:
        print(f"✓ PASS: search_results 이벤트 전송됨 ({len(search_results_data)} results)")

    # done 이벤트 검증
    if events_received["done"] != 1:
        print("❌ FAIL: done 이벤트가 정확히 1번 전송되지 않았습니다!")
        success = False
    else:
        print("✓ PASS: done 이벤트 전송됨")

    print("\n" + "=" * 80)
    if success:
        print("✓ 모든 테스트 통과!")
        return 0
    else:
        print("❌ 일부 테스트 실패")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(test_stream_events())
    sys.exit(exit_code)
