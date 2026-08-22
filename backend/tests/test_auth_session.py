import base64
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.core import auth
from app.core import config


class MemoryRedis:
    def __init__(self):
        self.data: dict[str, object] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            deleted += int(key in self.data)
            self.data.pop(key, None)
            self.expirations.pop(key, None)
        return deleted

    async def expire(self, key, ttl):
        if key not in self.data:
            return False
        self.expirations[key] = ttl
        return True

    async def ttl(self, key):
        return self.expirations.get(key, -1 if key in self.data else -2)

    async def incr(self, key):
        value = int(self.data.get(key, 0)) + 1
        self.data[key] = str(value)
        return value

    async def eval(self, script, key_count, key, window):
        assert script == auth._LOGIN_FAILURE_SCRIPT
        assert key_count == 1
        count = await self.incr(key)
        ttl = await self.ttl(key)
        if count == 1 or ttl <= 0:
            await self.expire(key, window)
            ttl = window
        return [count, ttl]


@pytest.fixture
def redis_store(monkeypatch):
    store = MemoryRedis()
    monkeypatch.setattr(auth, "get_redis_client", lambda: store)
    return store


@pytest.mark.asyncio
async def test_session_token_entropy_digest_and_no_plaintext(redis_store):
    user_id = str(uuid4())
    token, record = await auth.create_session(user_id)

    padded = token + "=" * (-len(token) % 4)
    assert len(base64.urlsafe_b64decode(padded)) == 32
    key = auth.session_key(token)
    assert key in redis_store.data
    assert token not in key
    assert token not in str(redis_store.data[key])
    assert len(key.removeprefix(auth.SESSION_KEY_PREFIX)) == 64
    assert record["user_id"] == user_id
    assert redis_store.expirations[key] == 12 * 60 * 60


@pytest.mark.asyncio
async def test_missing_malformed_and_absolute_expired_sessions_are_401(
    redis_store, monkeypatch
):
    with pytest.raises(HTTPException) as missing:
        await auth.validate_session_token(None, touch=True)
    assert missing.value.status_code == 401

    malformed_token = "malformed"
    redis_store.data[auth.session_key(malformed_token)] = "not-json"
    with pytest.raises(HTTPException) as malformed:
        await auth.validate_session_token(malformed_token, touch=True)
    assert malformed.value.status_code == 401
    assert auth.session_key(malformed_token) not in redis_store.data

    monkeypatch.setattr(auth.time, "time", lambda: 1_000)
    expired_token = "expired"
    redis_store.data[auth.session_key(expired_token)] = json.dumps(
        {
            "user_id": str(uuid4()),
            "version": 0,
            "created_at": 900,
            "absolute_expires_at": 999,
        }
    )
    with pytest.raises(HTTPException) as expired:
        await auth.validate_session_token(expired_token, touch=True)
    assert expired.value.status_code == 401
    assert auth.session_key(expired_token) not in redis_store.data


@pytest.mark.asyncio
async def test_touch_slides_idle_ttl_but_caps_at_absolute_expiry(
    redis_store, monkeypatch
):
    monkeypatch.setattr(auth.time, "time", lambda: 1_000)
    user_id = str(uuid4())
    token = "near-absolute-limit"
    key = auth.session_key(token)
    redis_store.data[key] = json.dumps(
        {
            "user_id": user_id,
            "version": 0,
            "created_at": 900,
            "absolute_expires_at": 1_100,
        }
    )

    await auth.validate_session_token(token, touch=True)
    assert redis_store.expirations[key] == 100

    far_token = "normal-idle-window"
    far_key = auth.session_key(far_token)
    redis_store.data[far_key] = json.dumps(
        {
            "user_id": user_id,
            "version": 0,
            "created_at": 900,
            "absolute_expires_at": 100_000,
        }
    )
    await auth.validate_session_token(far_token, touch=True)
    assert redis_store.expirations[far_key] == 12 * 60 * 60


@pytest.mark.asyncio
async def test_store_failure_is_503_not_invalid_session(monkeypatch):
    class FailedRedis:
        async def get(self, key):
            raise RedisConnectionError("offline")

    monkeypatch.setattr(auth, "get_redis_client", lambda: FailedRedis())
    with pytest.raises(HTTPException) as exc:
        await auth.validate_session_token("valid-looking-token", touch=True)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_logout_delete_cannot_be_undone_by_late_touch(redis_store):
    token, _ = await auth.create_session(str(uuid4()))
    key = auth.session_key(token)

    await auth.delete_session(token)
    assert await redis_store.expire(key, 60) is False
    assert key not in redis_store.data


@pytest.mark.asyncio
async def test_logout_all_version_invalidates_every_existing_session(redis_store):
    user_id = str(uuid4())
    first, _ = await auth.create_session(user_id)
    second, _ = await auth.create_session(user_id)

    await auth.revoke_user_sessions(user_id)

    for token in (first, second):
        with pytest.raises(HTTPException) as exc:
            await auth.validate_session_token(token, touch=False)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_deleted_or_inactive_user_invalidates_session(redis_store):
    token, _ = await auth.create_session(str(uuid4()))

    class MissingUserSession:
        async def execute(self, statement):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    with pytest.raises(HTTPException) as exc:
        await auth.authenticate_session(token, MissingUserSession(), touch=True)
    assert exc.value.status_code == 401
    assert auth.session_key(token) not in redis_store.data


@pytest.mark.asyncio
async def test_user_database_failure_is_sanitized_503(redis_store):
    token, _ = await auth.create_session(str(uuid4()))

    class FailedUserSession:
        async def execute(self, statement):
            raise OperationalError("select", {}, RuntimeError("offline"))

    with pytest.raises(HTTPException) as exc:
        await auth.authenticate_session(token, FailedUserSession(), touch=True)
    assert exc.value.status_code == 503
    assert exc.value.detail == "Authentication service is temporarily unavailable."


@pytest.mark.asyncio
async def test_login_limit_repairs_missing_ttl_before_rate_limit(redis_store):
    login_id = "limited-user"
    key = auth.login_failure_key(login_id)
    redis_store.data[key] = str(auth.LOGIN_FAILURE_LIMIT)

    with pytest.raises(HTTPException) as exc:
        await auth.ensure_login_allowed(login_id)
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "300"}
    assert redis_store.expirations[key] == auth.LOGIN_FAILURE_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_login_failure_fixed_window_blocks_fifth_and_success_reset(redis_store):
    login_id = "fixed-window-user"
    key = auth.login_failure_key(login_id)

    for expected_count in range(1, auth.LOGIN_FAILURE_LIMIT):
        await auth.record_login_failure(login_id)
        assert int(redis_store.data[key]) == expected_count
        assert redis_store.expirations[key] == auth.LOGIN_FAILURE_WINDOW_SECONDS

    with pytest.raises(HTTPException) as exc:
        await auth.record_login_failure(login_id)
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "300"}

    await auth.clear_login_failures(login_id)
    assert key not in redis_store.data


def test_session_settings_require_signup_code_and_valid_lifetimes(monkeypatch):
    monkeypatch.delenv("SIGNUP_ACCESS_CODE", raising=False)
    with pytest.raises(ValidationError):
        config.Settings(_env_file=None)

    with pytest.raises(ValidationError):
        config.Settings(
            _env_file=None,
            SIGNUP_ACCESS_CODE="configured",
            SESSION_IDLE_TTL_HOURS=400,
            SESSION_ABSOLUTE_TTL_DAYS=1,
        )


def test_container_config_path_selects_app_env(monkeypatch):
    monkeypatch.setattr(config, "__file__", "/app/app/core/config.py")
    assert config._get_project_root().name == "app"
