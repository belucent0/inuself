from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request, Response

from app.controllers import auth_controller
from app.core import auth
from app.core.auth import hash_password
from app.db.models import User
from app.schemas.auth import AuthLoginRequest, AuthSignupRequest


def make_request(cookie: str | None = None) -> Request:
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def make_user(password: str = "Correct1!") -> User:
    now = datetime.now(timezone.utc)
    return User(
        id=uuid4(),
        email="test-user",
        password=hash_password(password),
        name="Tester",
        is_active=True,
        is_super=False,
        created_at=now,
    )


class FakeSession:
    def __init__(self, user=None):
        self.user = user
        self.added = None

    async def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.user)

    def add(self, user):
        self.added = user
        if user.id is None:
            user.id = uuid4()
        if user.created_at is None:
            user.created_at = datetime.now(timezone.utc)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_signup_sets_opaque_cookie_and_returns_user_only(monkeypatch):
    token = "opaque-session-token"
    absolute_expires_at = int(datetime.now(timezone.utc).timestamp()) + 86_400
    create = AsyncMock(
        return_value=(token, {"absolute_expires_at": absolute_expires_at})
    )
    delete = AsyncMock()
    monkeypatch.setattr(auth_controller, "create_session", create)
    monkeypatch.setattr(auth_controller, "delete_session", delete)

    response = Response()
    result = await auth_controller.signup(
        AuthSignupRequest(
            login_id="new-user",
            password="Password1!",
            signup_code="test-only-signup-access-code",
            name="New User",
        ),
        make_request(),
        response,
        FakeSession(),
    )

    body = result.model_dump(mode="json")
    assert set(body) == {"user"}
    assert "token" not in str(body).lower()
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("timblo_session=opaque-session-token")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "max-age=" in cookie
    assert response.headers["cache-control"] == "no-store"
    delete.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_login_uses_same_generic_error_for_missing_and_wrong_password(
    monkeypatch,
):
    monkeypatch.setattr(auth_controller, "ensure_login_allowed", AsyncMock())
    record_failure = AsyncMock()
    monkeypatch.setattr(auth_controller, "record_login_failure", record_failure)

    details = []
    for user in (None, make_user()):
        with pytest.raises(HTTPException) as exc:
            await auth_controller.login(
                AuthLoginRequest(login_id="test-user", password="WrongPass1!"),
                make_request(),
                Response(),
                FakeSession(user),
            )
        assert exc.value.status_code == 401
        details.append(exc.value.detail)

    assert details[0] == details[1]
    assert record_failure.await_count == 2


@pytest.mark.asyncio
async def test_successful_login_resets_limit_and_replaces_existing_session(
    monkeypatch,
):
    user = make_user()
    create = AsyncMock(
        return_value=(
            "new-token",
            {
                "absolute_expires_at": int(datetime.now(timezone.utc).timestamp())
                + 86_400
            },
        )
    )
    delete = AsyncMock()
    clear_failures = AsyncMock()
    monkeypatch.setattr(auth_controller, "ensure_login_allowed", AsyncMock())
    monkeypatch.setattr(auth_controller, "clear_login_failures", clear_failures)
    monkeypatch.setattr(auth_controller, "create_session", create)
    monkeypatch.setattr(auth_controller, "delete_session", delete)

    response = Response()
    result = await auth_controller.login(
        AuthLoginRequest(login_id="test-user", password="Correct1!"),
        make_request("timblo_session=old-token"),
        response,
        FakeSession(user),
    )

    assert result.user.id == user.id
    clear_failures.assert_awaited_once_with("test-user")
    delete.assert_awaited_once_with("old-token")
    create.assert_awaited_once_with(str(user.id))
    assert "timblo_session=new-token" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_logout_is_idempotent_and_clears_cookie(monkeypatch):
    delete = AsyncMock()
    monkeypatch.setattr(auth_controller, "delete_session", delete)
    response = Response()

    result = await auth_controller.logout(make_request(), response)

    assert result is None
    delete.assert_awaited_once_with(None)
    cookie = response.headers["set-cookie"].lower()
    assert "timblo_session=" in cookie
    assert "max-age=0" in cookie
    assert response.headers["cache-control"] == "no-store"


def test_refresh_route_is_removed():
    paths = {route.path for route in auth_controller.router.routes}
    assert "/auth/refresh" not in paths


def test_production_cookie_is_secure_expiring_and_host_only(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(
            session_absolute_ttl_days=14,
            session_cookie_secure=True,
        ),
    )
    response = Response()

    auth.set_session_cookie(
        response,
        "opaque-token",
        int(datetime.now(timezone.utc).timestamp()) + 86_400,
    )

    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "expires=" in cookie
    assert "domain=" not in cookie
