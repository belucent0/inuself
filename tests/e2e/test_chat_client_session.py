import httpx
import pytest

from .chat_client import ChatClient


@pytest.mark.asyncio
async def test_login_cookie_is_reused_for_json_and_sse_requests() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"user": {"login_id": "tester"}},
                headers={"Set-Cookie": "timblo_session=session; Path=/; HttpOnly"},
            )
        assert request.headers.get("cookie") == "timblo_session=session"
        assert "authorization" not in request.headers
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"thread_id": "thread", "message_id": "answer"},
            )
        return httpx.Response(
            200,
            text='data: {"type":"done","data":{"content":"ok"}}\n\n',
        )

    client = ChatClient("https://example.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.login("tester", "password")
        thread_id, message_id = await client.create_thread("hello")
        result = await client.stream_response(thread_id, message_id)
    finally:
        await client.close()

    assert result.full_content == "ok"
    assert [request.url.path for request in requests] == [
        "/api/auth/login",
        "/api/threads",
        "/api/threads/thread/messages/answer/stream",
    ]
