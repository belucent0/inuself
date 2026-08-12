"""E2E 테스트용 HTTP + SSE 클라이언트 래퍼.

백엔드 API 흐름:
  login()          → POST /api/auth/login      → access_token
  create_thread()  → POST /api/threads        → {thread_id, message_id}
  add_message()    → POST /api/threads/{id}/messages → {thread_id, message_id}
  stream_response()→ GET  /api/threads/{id}/messages/{mid}/stream → TurnResult
  cleanup_all()    → DELETE /api/threads/{id} (테스트 종료 후 정리)
"""

from __future__ import annotations

import json
import time

import httpx

from .models import TurnResult


class ChatClientError(Exception):
    """ChatClient 에러 기반 클래스."""


class AuthError(ChatClientError):
    """인증 실패."""


class APIError(ChatClientError):
    """API 호출 실패."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class ChatClient:
    """백엔드 API를 래핑하는 비동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._access_token: str | None = None
        self._created_thread_ids: list[str] = []
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout),
        )

    # ------------------------------------------------------------------
    # 인증
    # ------------------------------------------------------------------

    async def signup(
        self, login_id: str, password: str, signup_code: str = "zoo", name: str | None = None
    ) -> bool:
        """회원가입을 시도하고, 신규 계정이면 True, 기존 계정이면 False를 반환한다."""
        payload = {
            "login_id": login_id,
            "password": password,
            "signup_code": signup_code,
            "name": name or login_id,
        }
        resp = await self._client.post(
            "/api/auth/signup", json=payload
        )
        if resp.status_code == 200:
            return True
        if resp.status_code in (400, 409):
            return False
        raise APIError(resp.status_code, resp.text)

    async def login(self, login_id: str, password: str) -> None:
        """이메일/비밀번호로 로그인하고 access_token을 저장합니다."""
        resp = await self._client.post(
            "/api/auth/login",
            json={"login_id": login_id, "password": password},
        )
        if resp.status_code != 200:
            raise AuthError(
                f"로그인 실패 (HTTP {resp.status_code}): {resp.text}"
            )
        data = resp.json()
        self._access_token = data["access_token"]

    def _auth_headers(self) -> dict[str, str]:
        if not self._access_token:
            raise AuthError("로그인이 필요합니다.")
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------------
    # 스레드 / 메시지 생성
    # ------------------------------------------------------------------

    async def create_thread(self, query: str, mode: str = "auto") -> tuple[str, str]:
        """새 스레드와 첫 번째 사용자 메시지를 생성합니다.

        Returns:
            (thread_id, ai_message_id) — SSE 스트리밍에 사용
        """
        resp = await self._client.post(
            "/api/threads",
            json={"query": query, "mode": mode},
            headers=self._auth_headers(),
        )
        if resp.status_code != 200:
            raise APIError(resp.status_code, resp.text)

        data = resp.json()
        thread_id: str = data["thread_id"]
        message_id: str = data["message_id"]
        self._created_thread_ids.append(thread_id)
        return thread_id, message_id

    async def add_message(
        self, thread_id: str, query: str, mode: str = "auto"
    ) -> tuple[str, str]:
        """기존 스레드에 사용자 메시지를 추가합니다.

        Returns:
            (thread_id, ai_message_id)
        """
        resp = await self._client.post(
            f"/api/threads/{thread_id}/messages",
            json={"query": query, "mode": mode},
            headers=self._auth_headers(),
        )
        if resp.status_code != 200:
            raise APIError(resp.status_code, resp.text)

        data = resp.json()
        return data["thread_id"], data["message_id"]

    # ------------------------------------------------------------------
    # SSE 스트리밍
    # ------------------------------------------------------------------

    async def stream_response(
        self,
        thread_id: str,
        message_id: str,
        cancel_after_events: int | None = None,
    ) -> TurnResult | tuple[TurnResult, int, bool]:
        """SSE 스트림을 소비하고 TurnResult를 반환합니다.

        cancel_after_events가 지정되면 지정한 data 이벤트 수만큼 받았을 때
        즉시 cancel API를 호출하고 스트림을 종료한다.

        Returns:
            cancel_after_events가 None일 때:
                TurnResult
            그 외:
                (TurnResult, received_data_events, cancel_requested)
        """
        if not self._access_token:
            raise AuthError("로그인이 필요합니다.")

        url = (
            f"{self._base_url}/api/threads/{thread_id}"
            f"/messages/{message_id}/stream"
        )
        start = time.monotonic()

        full_content = ""
        mode_used = "simple"
        had_error = False
        received_data_events = 0
        cancel_requested = False

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout)
        ) as stream_client:
            async with stream_client.stream(
                "GET",
                url,
                headers={
                    **self._auth_headers(),
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise APIError(response.status_code, body.decode())

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue

                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue

                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")
                    event_data = event.get("data")
                    received_data_events += 1

                    if event_type == "token":
                        token = event_data if isinstance(event_data, str) else ""
                        full_content += token

                    elif event_type == "content":
                        # 전체 content 교체 (token 누적보다 우선)
                        if isinstance(event_data, str):
                            full_content = event_data

                    elif event_type == "thinking":
                        # mode 정보 추출
                        if isinstance(event_data, dict) and "mode" in event_data:
                            raw_mode = event_data["mode"]
                            # AIMode.SEARCH → search
                            if isinstance(raw_mode, str):
                                if raw_mode.startswith("AIMode."):
                                    raw_mode = raw_mode.split(".", 1)[1]
                                mode_used = raw_mode.lower()

                    elif event_type == "error":
                        had_error = True

                    elif event_type == "done":
                        break

                    if (
                        (not cancel_requested)
                        and cancel_after_events is not None
                        and received_data_events >= cancel_after_events
                    ):
                        cancel_requested = await self.cancel_stream(thread_id, message_id)
                        if cancel_requested:
                            break

        elapsed = time.monotonic() - start
        result = TurnResult(
            full_content=full_content,
            mode_used=mode_used,
            had_error=had_error,
            elapsed_seconds=elapsed,
        )

        if cancel_after_events is None:
            return result

        return result, received_data_events, cancel_requested

    # ------------------------------------------------------------------
    # 기타 API
    # ------------------------------------------------------------------

    async def cancel_stream(
        self, thread_id: str, message_id: str
    ) -> bool:
        """스트리밍 취소 API를 호출해 사용자가 시작한 생성 작업을 취소합니다."""
        resp = await self._client.post(
            f"/api/threads/{thread_id}/messages/{message_id}/cancel",
            headers=self._auth_headers(),
        )
        return resp.status_code == 200

    async def get_thread(self, thread_id: str) -> dict:
        """스레드 상세 조회."""
        resp = await self._client.get(
            f"/api/threads/{thread_id}", headers=self._auth_headers()
        )
        if resp.status_code != 200:
            raise APIError(resp.status_code, resp.text)
        return resp.json()

    async def cleanup_all(self) -> None:
        """생성한 모든 스레드를 삭제합니다."""
        for thread_id in list(self._created_thread_ids):
            try:
                resp = await self._client.delete(
                    f"/api/threads/{thread_id}",
                    headers=self._auth_headers(),
                )
                if resp.status_code in (200, 404):
                    self._created_thread_ids.remove(thread_id)
            except Exception:  # noqa: BLE001
                pass  # 정리 실패는 무시

    async def close(self) -> None:
        """httpx 클라이언트를 닫습니다."""
        await self._client.aclose()

    async def __aenter__(self) -> "ChatClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
