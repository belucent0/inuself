"""메타데이터 수정 테스트 스크립트.

search_queries와 search_results가 올바르게 메타데이터에 저장되는지 확인합니다.
"""
import requests
import json
import time
import redis
import sys

# UTF-8 출력 설정 (Windows 콘솔 호환)
sys.stdout.reconfigure(encoding='utf-8')


def test_ai_chat_metadata():
    """AI 채팅 API 호출 후 메타데이터 검증."""

    # API 엔드포인트
    api_url = "http://localhost:8000/api/ai/chat"

    # 테스트 쿼리 (검색 모드를 유발)
    test_query = "TSMC의 최신 뉴스"

    print(f"Testing query: '{test_query}'")
    print("=" * 80)

    # API 요청
    payload = {
        "query": test_query,
        "mode": "auto"  # auto로 설정하여 IntentParser가 SEARCH 모드 선택하도록
    }

    print("[1] Sending request to API...")
    try:
        response = requests.post(api_url, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()

        conversation_id = result.get("conversation_id")
        response_text = result.get("response", "")

        print(f"✓ Request successful")
        print(f"  Conversation ID: {conversation_id}")
        print(f"  Response length: {len(response_text)} chars")
        print()

    except Exception as e:
        print(f"❌ API request failed: {e}")
        return 1

    # Redis에서 메타데이터 확인
    print("[2] Checking metadata in Redis...")

    try:
        client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

        # 대화 가져오기
        conv_key = f"ai:conversation:{conversation_id}"
        conv_data = client.get(conv_key)

        if not conv_data:
            print(f"❌ Conversation not found in Redis: {conversation_id}")
            return 1

        conversation = json.loads(conv_data)
        messages = conversation.get("messages", [])

        if len(messages) < 2:
            print(f"❌ Expected at least 2 messages, got {len(messages)}")
            return 1

        # 마지막 assistant 메시지 (방금 생성된 응답)
        assistant_msg = messages[-1]

        if assistant_msg.get("role") != "assistant":
            print(f"❌ Last message is not from assistant: {assistant_msg.get('role')}")
            return 1

        metadata = assistant_msg.get("metadata", {})

        print(f"✓ Metadata retrieved")
        print()

    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        return 1

    # 메타데이터 검증
    print("[3] Validating metadata...")
    print("-" * 80)

    success = True

    # Mode 확인
    mode = metadata.get("mode", "")
    print(f"Mode: {mode}")
    if "SEARCH" not in mode:
        print(f"  ⚠️  WARNING: Expected SEARCH mode, got {mode}")
    else:
        print(f"  ✓ Correct mode")
    print()

    # Intent 확인
    intent = metadata.get("intent", {})
    intent_search_queries = intent.get("search_queries", []) if intent else []
    print(f"Intent search_queries: {len(intent_search_queries)} queries")
    if intent_search_queries:
        for idx, q in enumerate(intent_search_queries, 1):
            print(f"  {idx}. {q}")
        print(f"  ✓ Intent has search queries")
    else:
        print(f"  ⚠️  WARNING: Intent has no search queries")
    print()

    # search_queries 확인 (수정사항 검증 - 핵심!)
    search_queries = metadata.get("search_queries", [])
    print(f"Metadata search_queries: {len(search_queries)} queries")
    if not search_queries:
        print(f"  ❌ FAIL: search_queries is empty!")
        print(f"  Expected: queries from IntentParser")
        print(f"  Got: {search_queries}")
        success = False
    else:
        for idx, q in enumerate(search_queries, 1):
            print(f"  {idx}. {q}")
        print(f"  ✓ PASS: search_queries populated correctly")
    print()

    # search_results 확인 (수정사항 검증 - 핵심!)
    search_results = metadata.get("search_results", [])
    print(f"Metadata search_results: {len(search_results)} results")
    if not search_results:
        print(f"  ❌ FAIL: search_results is empty!")
        print(f"  Expected: results from Searcher")
        print(f"  Got: {search_results}")
        success = False
    else:
        for idx, r in enumerate(search_results[:3], 1):  # 처음 3개만 출력
            title = r.get("title", "") if isinstance(r, dict) else ""
            print(f"  {idx}. {title[:60]}...")
        if len(search_results) > 3:
            print(f"  ... and {len(search_results) - 3} more")
        print(f"  ✓ PASS: search_results populated correctly")
    print()

    # sources 확인 (기존 동작)
    sources = metadata.get("sources", [])
    print(f"Metadata sources: {len(sources)} sources")
    if sources:
        for idx, s in enumerate(sources[:3], 1):
            title = s.get("title", "") if isinstance(s, dict) else ""
            print(f"  {idx}. {title[:60]}...")
        if len(sources) > 3:
            print(f"  ... and {len(sources) - 3} more")
        print(f"  ✓ Sources present")
    print()

    # thinking_steps 확인
    thinking_steps = metadata.get("thinking_steps", [])
    print(f"Thinking steps: {len(thinking_steps)} steps")
    if thinking_steps:
        print(f"  ✓ Thinking steps recorded")
    else:
        print(f"  ⚠️  WARNING: No thinking steps recorded")
    print()

    # 최종 결과
    print("=" * 80)
    if success:
        print("✓ 모든 테스트 통과!")
        print("  - search_queries가 메타데이터에 올바르게 저장됨")
        print("  - search_results가 메타데이터에 올바르게 저장됨")
        print("  - 수정사항이 정상 작동함")
        return 0
    else:
        print("❌ 일부 테스트 실패")
        print("  - search_queries 또는 search_results가 비어있음")
        print("  - 수정사항이 적용되지 않았거나 다른 문제 발생")
        return 1


if __name__ == "__main__":
    exit_code = test_ai_chat_metadata()
    sys.exit(exit_code)
