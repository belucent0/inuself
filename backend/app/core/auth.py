import json
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import User
from ..db.session import get_session
from .config import get_settings
from .redis import get_redis_client

ACCESS_KEY_PREFIX = "auth:access:"
REFRESH_KEY_PREFIX = "auth:refresh:"
REFRESH_FAMILY_KEY_PREFIX = "auth:refresh-family:"
REFRESH_FAMILY_REVOKED_PREFIX = "auth:refresh-family-revoked:"
USER_TOKEN_VERSION_PREFIX = "auth:user-token-version:"

http_bearer = HTTPBearer(auto_error=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seconds_until(expires_at: datetime) -> int:
    seconds = int((expires_at - _utcnow()).total_seconds())
    return max(seconds, 1)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 120_000
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${hashed.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    import hmac

    try:
        algorithm, iterations_raw, salt_hex, hash_hex = password_hash.split("$", 3)
    except ValueError:
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    try:
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
    except (TypeError, ValueError):
        return False

    computed_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )

    return hmac.compare_digest(computed_hash, expected_hash)


def _encode_token(payload: dict[str, Any]) -> str:
    settings = get_settings()
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def _decode_token(token: str, expected_type: str) -> dict[str, Any]:
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti", "typ"],
            },
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 토큰입니다.",
        ) from exc

    token_type = str(payload.get("typ", ""))
    if token_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰 타입이 올바르지 않습니다.",
        )

    return payload


async def _get_user_token_version(user_id: str) -> int:
    redis = get_redis_client()
    value = await redis.get(f"{USER_TOKEN_VERSION_PREFIX}{user_id}")
    if value is None:
        return 0
    # Decode bytes to string if needed
    if isinstance(value, bytes):
        value = value.decode('utf-8')
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def issue_token_pair(
    user_id: str, family_id: str | None = None
) -> dict[str, Any]:
    settings = get_settings()
    redis = get_redis_client()

    now = _utcnow()
    access_expires_at = now + timedelta(minutes=settings.jwt_access_token_ttl_minutes)
    refresh_expires_at = now + timedelta(days=settings.jwt_refresh_token_ttl_days)
    token_version = await _get_user_token_version(user_id)

    family = family_id or str(uuid.uuid4())
    access_jti = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())

    access_payload = {
        "sub": user_id,
        "jti": access_jti,
        "typ": "access",
        "sid": family,
        "ver": token_version,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": access_expires_at,
    }
    refresh_payload = {
        "sub": user_id,
        "jti": refresh_jti,
        "typ": "refresh",
        "sid": family,
        "ver": token_version,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": refresh_expires_at,
    }

    access_token = _encode_token(access_payload)
    refresh_token = _encode_token(refresh_payload)

    access_ttl = _seconds_until(access_expires_at)
    refresh_ttl = _seconds_until(refresh_expires_at)

    await redis.set(
        f"{ACCESS_KEY_PREFIX}{access_jti}",
        user_id,
        ex=access_ttl,
    )

    refresh_record = {
        "user_id": user_id,
        "family_id": family,
        "status": "active",
        "token_version": token_version,
        "expires_at": refresh_expires_at.isoformat(),
    }
    await redis.set(
        f"{REFRESH_KEY_PREFIX}{refresh_jti}",
        json.dumps(refresh_record),
        ex=refresh_ttl,
    )
    await redis.set(f"{REFRESH_FAMILY_KEY_PREFIX}{family}", refresh_jti, ex=refresh_ttl)

    return {
        "user_id": user_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "access_expires_in": access_ttl,
        "refresh_expires_in": refresh_ttl,
        "family_id": family,
    }


async def revoke_access_token_jti(access_jti: str) -> None:
    redis = get_redis_client()
    await redis.delete(f"{ACCESS_KEY_PREFIX}{access_jti}")


async def revoke_user_sessions(user_id: str) -> None:
    redis = get_redis_client()
    await redis.incr(f"{USER_TOKEN_VERSION_PREFIX}{user_id}")


async def rotate_refresh_token(refresh_token: str) -> dict[str, Any]:
    payload = _decode_token(refresh_token, expected_type="refresh")
    redis = get_redis_client()

    refresh_jti = str(payload["jti"])
    family_id = str(payload["sid"])
    user_id = str(payload["sub"])

    family_revoked_key = f"{REFRESH_FAMILY_REVOKED_PREFIX}{family_id}"
    if await redis.get(family_revoked_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션이 만료되었습니다. 다시 로그인 해주세요.",
        )

    record_key = f"{REFRESH_KEY_PREFIX}{refresh_jti}"
    refresh_record_raw = await redis.get(record_key)
    if refresh_record_raw is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 리프레시 토큰입니다.",
        )

    # Decode bytes to string if needed
    if isinstance(refresh_record_raw, bytes):
        refresh_record_raw = refresh_record_raw.decode('utf-8')

    try:
        refresh_record = json.loads(refresh_record_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="리프레시 토큰 상태가 손상되었습니다.",
        ) from exc

    family_current_key = f"{REFRESH_FAMILY_KEY_PREFIX}{family_id}"
    current_jti = await redis.get(family_current_key)
    # Decode bytes to string if needed
    if isinstance(current_jti, bytes):
        current_jti = current_jti.decode('utf-8')
    if current_jti != refresh_jti or refresh_record.get("status") != "active":
        refresh_ttl = await redis.ttl(record_key)
        if refresh_ttl > 0:
            await redis.set(family_revoked_key, "1", ex=refresh_ttl)
        await redis.delete(family_current_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="리프레시 토큰 재사용이 감지되었습니다. 다시 로그인 해주세요.",
        )

    token_version = await _get_user_token_version(user_id)
    if int(payload.get("ver", 0)) != token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션이 갱신되어 다시 로그인이 필요합니다.",
        )

    refresh_ttl = await redis.ttl(record_key)
    refresh_record["status"] = "rotated"
    await redis.set(record_key, json.dumps(refresh_record), ex=max(refresh_ttl, 1))

    return await issue_token_pair(user_id=user_id, family_id=family_id)


async def revoke_refresh_family(refresh_token: str) -> None:
    payload = _decode_token(refresh_token, expected_type="refresh")
    redis = get_redis_client()

    family_id = str(payload["sid"])
    refresh_jti = str(payload["jti"])
    family_current_key = f"{REFRESH_FAMILY_KEY_PREFIX}{family_id}"
    record_key = f"{REFRESH_KEY_PREFIX}{refresh_jti}"
    refresh_ttl = await redis.ttl(record_key)

    if refresh_ttl > 0:
        await redis.set(
            f"{REFRESH_FAMILY_REVOKED_PREFIX}{family_id}",
            "1",
            ex=refresh_ttl,
        )

    await redis.delete(family_current_key)
    await redis.delete(record_key)


async def validate_access_token(access_token: str) -> dict[str, Any]:
    payload = _decode_token(access_token, expected_type="access")

    redis = get_redis_client()
    access_jti = str(payload["jti"])
    user_id = str(payload["sub"])

    exists_user_id = await redis.get(f"{ACCESS_KEY_PREFIX}{access_jti}")
    # Decode bytes to string if needed
    if isinstance(exists_user_id, bytes):
        exists_user_id = exists_user_id.decode('utf-8')

    if exists_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="만료되었거나 폐기된 액세스 토큰입니다.",
        )

    token_version = await _get_user_token_version(user_id)
    if int(payload.get("ver", 0)) != token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="세션이 만료되었습니다. 다시 로그인 해주세요.",
        )

    return payload


async def get_current_access_payload(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
    access_token: str | None = Query(None),
) -> dict[str, Any]:
    token = credentials.credentials if credentials else access_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 필요합니다.",
        )
    return await validate_access_token(token)


async def get_current_user(
    payload: dict[str, Any] = Depends(get_current_access_payload),
    session: AsyncSession = Depends(get_session),
) -> User:
    user_id = str(payload["sub"])

    stmt = select(User).where(User.id == uuid.UUID(user_id), User.is_active.is_(True))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="활성 사용자 정보를 찾을 수 없습니다.",
        )

    return user


async def get_current_user_id(
    current_user: User = Depends(get_current_user),
) -> uuid.UUID:
    return current_user.id
