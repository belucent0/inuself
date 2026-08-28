"""Authenticated, user-isolated file progress SSE."""

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..core.auth import SESSION_COOKIE_NAME, authenticate_session
from ..core.logging import logger
from ..core.redis import get_redis_client
from ..db.models import Content
from ..db.session import async_session_factory

router = APIRouter(prefix="/api", tags=["events"])


async def _event_owned_by_user(
    event: object,
    user_id: UUID,
    ownership_cache: dict[UUID, bool],
) -> bool:
    if not isinstance(event, dict):
        return False
    try:
        file_id = UUID(str(event["file_id"]))
    except (KeyError, TypeError, ValueError):
        return False
    if file_id not in ownership_cache:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Content.user_id).where(Content.file_id == file_id)
            )
        owner_id = result.scalar_one_or_none()
        if owner_id is None:
            return False
        ownership_cache[file_id] = owner_id == user_id
    return ownership_cache[file_id]


async def file_progress_stream(
    request: Request,
    user_id: UUID,
) -> AsyncGenerator[str, None]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    pubsub = get_redis_client().pubsub()
    ownership_cache: dict[UUID, bool] = {}
    next_session_check = time.monotonic() + 30

    try:
        await pubsub.subscribe("events:file_progress:global")
        logger.info("[SSE] File progress subscriber connected user={}", user_id)
        yield f"data: {json.dumps({'type': 'connection', 'status': 'connected'})}\n\n"

        while not await request.is_disconnected():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message and message.get("type") == "message":
                raw = message.get("data")
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    event = json.loads(raw) if isinstance(raw, str) else raw
                    if await _event_owned_by_user(
                        event, user_id, ownership_cache
                    ):
                        yield f"data: {json.dumps(event)}\n\n"
                except (UnicodeDecodeError, json.JSONDecodeError):
                    logger.warning("[SSE] Ignored malformed file progress event")

            now = time.monotonic()
            if now >= next_session_check:
                try:
                    async with async_session_factory() as session:
                        await authenticate_session(token, session, touch=False)
                except HTTPException as exc:
                    logger.info(
                        "[SSE] Closing file progress stream after auth failure status={}",
                        exc.status_code,
                    )
                    break
                yield ": keep-alive\n\n"
                next_session_check = now + 30
    except asyncio.CancelledError:
        logger.info("[SSE] File progress stream cancelled user={}", user_id)
    except Exception as exc:
        logger.exception("[SSE] File progress stream failed user={}: {}", user_id, exc)
    finally:
        try:
            await pubsub.unsubscribe("events:file_progress:global")
            close = getattr(pubsub, "aclose", None) or pubsub.close
            await close()
        except Exception as exc:
            logger.warning("[SSE] Failed to close file progress subscriber: {}", exc)


@router.get("/events/file-progress/stream")
async def stream_file_progress(
    request: Request,
) -> StreamingResponse:
    async with async_session_factory() as session:
        current_user = await authenticate_session(
            request.cookies.get(SESSION_COOKIE_NAME), session, touch=True
        )
    return StreamingResponse(
        file_progress_stream(request, current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
