import hmac
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import (
    get_current_access_payload,
    get_current_user,
    hash_password,
    issue_token_pair,
    revoke_access_token_jti,
    revoke_refresh_family,
    revoke_user_sessions,
    rotate_refresh_token,
    verify_password,
)
from ..db.models import User
from ..db.session import get_session
from ..schemas.auth import (
    LOGIN_ID_PATTERN,
    AuthLoginRequest,
    AuthLoginIdCheckResponse,
    AuthLogoutRequest,
    AuthRefreshRequest,
    AuthSignupRequest,
    AuthTokenResponse,
    AuthUserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_SIGNUP_ACCESS_CODE = "zoo"


def _normalize_login_id(login_id: str) -> str:
    return login_id.strip().lower()


@router.get("/check-id", response_model=AuthLoginIdCheckResponse)
async def check_login_id(
    login_id: str = Query(..., min_length=4, max_length=20),
    session: AsyncSession = Depends(get_session),
):
    normalized_login_id = _normalize_login_id(login_id)
    if not LOGIN_ID_PATTERN.fullmatch(normalized_login_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="올바른 아이디 형식이 아닙니다.",
        )

    existing_user_result = await session.execute(
        select(User).where(User.email == normalized_login_id)
    )
    existing_user = existing_user_result.scalar_one_or_none()

    return AuthLoginIdCheckResponse(
        login_id=normalized_login_id,
        available=existing_user is None,
    )


def _to_token_response(tokens: dict[str, Any], user: User) -> AuthTokenResponse:
    return AuthTokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        access_expires_in=tokens["access_expires_in"],
        refresh_expires_in=tokens["refresh_expires_in"],
        user=AuthUserResponse.model_validate(user),
    )


@router.post("/signup", response_model=AuthTokenResponse)
async def signup(
    request: AuthSignupRequest,
    session: AsyncSession = Depends(get_session),
):
    if not hmac.compare_digest(request.signup_code, _SIGNUP_ACCESS_CODE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="가입 인증코드가 유효하지 않습니다.",
        )

    login_id = _normalize_login_id(request.login_id)

    existing_user_result = await session.execute(
        select(User).where(User.email == login_id)
    )
    existing_user = existing_user_result.scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 아이디입니다.",
        )

    user = User(
        email=login_id,
        password=hash_password(request.password),
        name=request.name.strip() if request.name else None,
        is_active=True,
    )
    user.last_login_at = datetime.now(timezone.utc)

    session.add(user)
    await session.flush()

    tokens = await issue_token_pair(user_id=str(user.id))
    return _to_token_response(tokens=tokens, user=user)


@router.post("/login", response_model=AuthTokenResponse)
async def login(
    request: AuthLoginRequest,
    session: AsyncSession = Depends(get_session),
):
    login_id = _normalize_login_id(request.login_id)

    user_result = await session.execute(select(User).where(User.email == login_id))
    user = user_result.scalar_one_or_none()

    if user is None or not verify_password(request.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화된 계정입니다.",
        )

    user.last_login_at = datetime.now(timezone.utc)
    await session.flush()

    tokens = await issue_token_pair(user_id=str(user.id))
    return _to_token_response(tokens=tokens, user=user)


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh_token(
    request: AuthRefreshRequest,
    session: AsyncSession = Depends(get_session),
):
    tokens = await rotate_refresh_token(request.refresh_token)
    user_result = await session.execute(
        select(User).where(User.id == UUID(str(tokens["user_id"])))
    )
    user = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자 정보를 찾을 수 없습니다.",
        )

    return _to_token_response(tokens=tokens, user=user)


@router.post("/logout")
async def logout(
    request: AuthLogoutRequest | None = None,
    payload: dict[str, Any] = Depends(get_current_access_payload),
):
    access_jti = str(payload["jti"])
    await revoke_access_token_jti(access_jti)

    if request and request.refresh_token:
        await revoke_refresh_family(request.refresh_token)

    return {"message": "로그아웃되었습니다."}


@router.post("/logout-all")
async def logout_all(
    current_user: User = Depends(get_current_user),
):
    await revoke_user_sessions(str(current_user.id))
    return {"message": "모든 디바이스에서 로그아웃되었습니다."}


@router.get("/me", response_model=AuthUserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return AuthUserResponse.model_validate(current_user)
