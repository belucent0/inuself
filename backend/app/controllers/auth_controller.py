import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import (
    SESSION_COOKIE_NAME,
    clear_login_failures,
    clear_session_cookie,
    create_session,
    delete_session,
    ensure_login_allowed,
    get_current_user,
    hash_password,
    record_login_failure,
    revoke_user_sessions,
    set_session_cookie,
    verify_password,
)
from ..core.config import get_settings
from ..db.models import User
from ..db.session import get_session
from ..schemas.auth import (
    LOGIN_ID_PATTERN,
    AuthLoginIdCheckResponse,
    AuthLoginRequest,
    AuthSessionResponse,
    AuthSignupRequest,
    AuthUserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_DUMMY_PASSWORD_HASH = f"pbkdf2_sha256$120000${'00' * 16}${'00' * 32}"


def _normalize_login_id(login_id: str) -> str:
    return login_id.strip().lower()


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _replace_session(
    request: Request, response: Response, user_id: str
) -> None:
    await delete_session(request.cookies.get(SESSION_COOKIE_NAME))
    token, record = await create_session(user_id)
    set_session_cookie(response, token, int(record["absolute_expires_at"]))


@router.get("/check-id", response_model=AuthLoginIdCheckResponse)
async def check_login_id(
    login_id: str = Query(..., min_length=4, max_length=20),
    session: AsyncSession = Depends(get_session, scope="function"),
):
    normalized_login_id = _normalize_login_id(login_id)
    if not LOGIN_ID_PATTERN.fullmatch(normalized_login_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid login ID format.",
        )

    result = await session.execute(
        select(User).where(User.email == normalized_login_id)
    )
    return AuthLoginIdCheckResponse(
        login_id=normalized_login_id,
        available=result.scalar_one_or_none() is None,
    )


@router.post("/signup", response_model=AuthSessionResponse)
async def signup(
    signup_request: AuthSignupRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session, scope="function"),
):
    settings = get_settings()
    if not hmac.compare_digest(signup_request.signup_code, settings.signup_access_code):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signup access code.",
        )

    login_id = _normalize_login_id(signup_request.login_id)
    result = await session.execute(select(User).where(User.email == login_id))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Login ID is already in use.",
        )

    user = User(
        email=login_id,
        password=hash_password(signup_request.password),
        name=signup_request.name.strip() if signup_request.name else None,
        is_active=True,
        is_super=False,
        last_login_at=datetime.now(timezone.utc),
    )
    session.add(user)
    await session.flush()
    await _replace_session(request, response, str(user.id))
    _no_store(response)
    return AuthSessionResponse(user=AuthUserResponse.model_validate(user))


@router.post("/login", response_model=AuthSessionResponse)
async def login(
    login_request: AuthLoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session, scope="function"),
):
    login_id = _normalize_login_id(login_request.login_id)
    await ensure_login_allowed(login_id)

    result = await session.execute(select(User).where(User.email == login_id))
    user = result.scalar_one_or_none()
    password_hash = user.password if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(login_request.password, password_hash)
    if user is None or not password_valid or not user.is_active:
        await record_login_failure(login_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid login ID or password.",
        )

    await clear_login_failures(login_id)
    user.last_login_at = datetime.now(timezone.utc)
    await session.flush()
    await _replace_session(request, response, str(user.id))
    _no_store(response)
    return AuthSessionResponse(user=AuthUserResponse.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    await delete_session(request.cookies.get(SESSION_COOKIE_NAME))
    clear_session_cookie(response)
    _no_store(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    response: Response,
    current_user: User = Depends(get_current_user),
) -> None:
    await revoke_user_sessions(str(current_user.id))
    clear_session_cookie(response)
    _no_store(response)


@router.get("/me", response_model=AuthUserResponse)
async def me(
    response: Response,
    current_user: User = Depends(get_current_user),
) -> AuthUserResponse:
    _no_store(response)
    return AuthUserResponse.model_validate(current_user)
