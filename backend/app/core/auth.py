import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User
from ..db.session import get_session
from .config import get_settings
from .logging import logger
from .redis import get_redis_client

SESSION_COOKIE_NAME = "timblo_session"
SESSION_KEY_PREFIX = "auth:session:"
USER_SESSION_VERSION_PREFIX = "auth:user-session-version:"
LOGIN_FAILURE_KEY_PREFIX = "auth:login-failure:"
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 5 * 60
_LOGIN_FAILURE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if count == 1 or ttl <= 0 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""

_STORE_ERRORS = (RedisError, OSError, TimeoutError)
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def origin_is_forbidden(method: str, origin: str | None, allowed: set[str]) -> bool:
    return bool(origin and method in _UNSAFE_METHODS and origin not in allowed)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 120_000
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${hashed.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_hex, hash_hex = password_hash.split("$", 3)
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
    except (TypeError, ValueError):
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    computed_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(computed_hash, expected_hash)


def session_key(token: str) -> str:
    return f"{SESSION_KEY_PREFIX}{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


def login_failure_key(login_id: str) -> str:
    digest = hashlib.sha256(login_id.encode("utf-8")).hexdigest()
    return f"{LOGIN_FAILURE_KEY_PREFIX}{digest}"


def _unauthorized(reason: str, token: str | None = None) -> HTTPException:
    digest = session_key(token)[-64:-56] if token else None
    logger.info("[Auth] session rejected reason={} digest={}", reason, digest)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required.",
    )


def _store_unavailable(operation: str, exc: BaseException) -> HTTPException:
    logger.error("[Auth] session store unavailable operation={}: {}", operation, exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Authentication service is temporarily unavailable.",
    )


def _decode_int(value: Any, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    return int(value)


async def get_user_session_version(user_id: str) -> int:
    try:
        value = await get_redis_client().get(
            f"{USER_SESSION_VERSION_PREFIX}{user_id}"
        )
        return _decode_int(value, default=0)
    except _STORE_ERRORS as exc:
        raise _store_unavailable("read-version", exc) from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise _store_unavailable("decode-version", exc) from exc


def _decode_session_record(raw: Any) -> dict[str, int | str]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError("session record is not an object")

    user_id = record.get("user_id")
    version = record.get("version")
    created_at = record.get("created_at")
    absolute_expires_at = record.get("absolute_expires_at")
    if not isinstance(user_id, str):
        raise ValueError("missing user_id")
    uuid.UUID(user_id)
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (version, created_at, absolute_expires_at)
    ):
        raise ValueError("invalid session timestamps or version")
    if created_at >= absolute_expires_at:
        raise ValueError("invalid session lifetime")
    return {
        "user_id": user_id,
        "version": version,
        "created_at": created_at,
        "absolute_expires_at": absolute_expires_at,
    }


async def _delete_key(key: str, *, required: bool) -> None:
    try:
        await get_redis_client().delete(key)
    except _STORE_ERRORS as exc:
        if required:
            raise _store_unavailable("delete-session", exc) from exc
        logger.warning("[Auth] failed to clean invalid session: {}", exc)


async def create_session(user_id: str) -> tuple[str, dict[str, int | str]]:
    settings = get_settings()
    now = int(time.time())
    absolute_expires_at = now + settings.session_absolute_ttl_days * 24 * 60 * 60
    record: dict[str, int | str] = {
        "user_id": user_id,
        "version": await get_user_session_version(user_id),
        "created_at": now,
        "absolute_expires_at": absolute_expires_at,
    }
    token = secrets.token_urlsafe(32)
    ttl = min(
        settings.session_idle_ttl_hours * 60 * 60,
        absolute_expires_at - now,
    )
    try:
        await get_redis_client().set(session_key(token), json.dumps(record), ex=ttl)
    except _STORE_ERRORS as exc:
        raise _store_unavailable("create-session", exc) from exc
    return token, record


async def delete_session(token: str | None) -> None:
    if token:
        await _delete_key(session_key(token), required=True)


async def validate_session_token(
    token: str | None, *, touch: bool
) -> dict[str, int | str]:
    if not token:
        raise _unauthorized("missing-cookie")

    key = session_key(token)
    redis = get_redis_client()
    try:
        raw = await redis.get(key)
    except _STORE_ERRORS as exc:
        raise _store_unavailable("read-session", exc) from exc
    if raw is None:
        raise _unauthorized("missing-or-idle-expired", token)

    try:
        record = _decode_session_record(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("[Auth] malformed session record digest={}: {}", key[-64:-56], exc)
        await _delete_key(key, required=False)
        raise _unauthorized("malformed", token) from exc

    now = int(time.time())
    absolute_expires_at = int(record["absolute_expires_at"])
    if absolute_expires_at <= now:
        await _delete_key(key, required=False)
        raise _unauthorized("absolute-expired", token)

    current_version = await get_user_session_version(str(record["user_id"]))
    if int(record["version"]) != current_version:
        await _delete_key(key, required=False)
        raise _unauthorized("revoked", token)

    if touch:
        settings = get_settings()
        ttl = min(
            settings.session_idle_ttl_hours * 60 * 60,
            absolute_expires_at - now,
        )
        try:
            touched = await redis.expire(key, ttl)
        except _STORE_ERRORS as exc:
            raise _store_unavailable("touch-session", exc) from exc
        if not touched:
            raise _unauthorized("expired-during-touch", token)

    return record


async def authenticate_session(
    token: str | None,
    session: AsyncSession,
    *,
    touch: bool,
) -> User:
    record = await validate_session_token(token, touch=False)
    stmt = (
        select(User)
        .where(User.id == uuid.UUID(str(record["user_id"])), User.is_active.is_(True))
        .execution_options(populate_existing=True)
    )
    try:
        result = await session.execute(stmt)
    except SQLAlchemyError as exc:
        raise _store_unavailable("read-user", exc) from exc
    user = result.scalar_one_or_none()
    if user is None:
        await _delete_key(session_key(token or ""), required=False)
        raise _unauthorized("inactive-or-deleted-user", token)
    if touch:
        await validate_session_token(token, touch=True)
    return user


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session, scope="function"),
) -> User:
    return await authenticate_session(
        request.cookies.get(SESSION_COOKIE_NAME), session, touch=True
    )


async def get_current_user_id(
    current_user: User = Depends(get_current_user),
) -> uuid.UUID:
    return current_user.id


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_super:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required.",
        )
    return current_user


async def revoke_user_sessions(user_id: str) -> None:
    try:
        await get_redis_client().incr(f"{USER_SESSION_VERSION_PREFIX}{user_id}")
    except _STORE_ERRORS as exc:
        raise _store_unavailable("revoke-user-sessions", exc) from exc


def _rate_limited(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many login attempts. Try again later.",
        headers={"Retry-After": str(max(retry_after, 1))},
    )


async def ensure_login_allowed(login_id: str) -> None:
    redis = get_redis_client()
    key = login_failure_key(login_id)
    try:
        count = _decode_int(await redis.get(key), default=0)
        if count >= LOGIN_FAILURE_LIMIT:
            ttl = await redis.ttl(key)
            if ttl <= 0:
                await redis.expire(key, LOGIN_FAILURE_WINDOW_SECONDS)
                ttl = LOGIN_FAILURE_WINDOW_SECONDS
            raise _rate_limited(ttl)
    except HTTPException:
        raise
    except _STORE_ERRORS as exc:
        raise _store_unavailable("check-login-limit", exc) from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise _store_unavailable("decode-login-limit", exc) from exc


async def record_login_failure(login_id: str) -> None:
    redis = get_redis_client()
    key = login_failure_key(login_id)
    try:
        count_raw, ttl_raw = await redis.eval(
            _LOGIN_FAILURE_SCRIPT,
            1,
            key,
            LOGIN_FAILURE_WINDOW_SECONDS,
        )
        count = _decode_int(count_raw)
        ttl = _decode_int(ttl_raw)
        if count >= LOGIN_FAILURE_LIMIT:
            raise _rate_limited(ttl)
    except HTTPException:
        raise
    except _STORE_ERRORS as exc:
        raise _store_unavailable("record-login-failure", exc) from exc
    except (TypeError, UnicodeDecodeError, ValueError) as exc:
        raise _store_unavailable("decode-login-failure", exc) from exc


async def clear_login_failures(login_id: str) -> None:
    try:
        await get_redis_client().delete(login_failure_key(login_id))
    except _STORE_ERRORS as exc:
        raise _store_unavailable("clear-login-failures", exc) from exc


def set_session_cookie(
    response: Response, token: str, absolute_expires_at: int
) -> None:
    settings = get_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_absolute_ttl_days * 24 * 60 * 60,
        expires=datetime.fromtimestamp(absolute_expires_at, timezone.utc),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
