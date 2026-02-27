"""스트리밍 취소 동작 E2E 테스트.

요구 사항:
- 스트림 진행 중 취소 API를 호출했을 때
- 메시지 상태가 cancelled로 바뀌어야 함
- 추가 에러가 없어야 함
"""

from __future__ import annotations

import uuid

import pytest

from .chat_client import ChatClient
from .conftest import E2E_BASE_URL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_after_events",
    [1, 3],
)
async def test_stream_cancel_stops_generation(cancel_after_events: int) -> None:
    """스트림이 시작된 직후/짧은 토큰 수신 뒤 취소했을 때 상태가 cancelled인지 확인한다."""
    client = ChatClient(base_url=E2E_BASE_URL)

    user = f"stream-cancel-{uuid.uuid4().hex[:10]}"
    password = "Test1234!"

    # 신규 유저 생성(이미 존재하면 로그인 fallback)
    is_new = await client.signup(user, password, signup_code="zoo", name="CI Stream Test")
    if not is_new:
        await client.login(user, password)
    else:
        await client.login(user, password)

    try:
        # 긴 답변이 생성되어 취소 타이밍까지 도달하도록 질의 길게 구성
        thread_id, message_id = await client.create_thread(
            """
            Explain in detail how a modern recommendation system is built,
            including data pipelines, feature engineering, model training,
            A/B testing, edge cases, safety, and cost optimization.
            Include concrete examples and formulas when needed.
            """,
            mode="simple",
        )

        _, received_data_events, cancel_requested = await client.stream_response(
            thread_id,
            message_id,
            cancel_after_events=cancel_after_events,
        )

        assert cancel_requested, (
            f"취소 API를 {cancel_after_events}번째 data 이벤트에서 호출하지 못했습니다."
        )
        assert received_data_events >= cancel_after_events, (
            f"수신한 data 이벤트 수가 기대치보다 적습니다. expected>={cancel_after_events},"
            f" actual={received_data_events}"
        )

        thread_data = await client.get_thread(thread_id)
        messages = thread_data.get("messages", [])

        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        assert assistant_msgs, "assistant 응답 메시지가 생성되지 않았습니다."
        latest = assistant_msgs[-1]

        assert latest.get("status") == "cancelled", (
            f"예상은 cancelled, 실제={latest.get('status')} (message_id={message_id})"
        )

    finally:
        await client.cleanup_all()
        await client.close()
