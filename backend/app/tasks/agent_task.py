"""Queued LangGraph execution and real-time event publishing."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import LockError, RedisError

from ..agent_celery import celery_app
from ..agents.graph import stream_ai_agent
from ..core.config import get_settings
from ..core.logging import logger
from ..db.session import async_session_factory
from ..repositories.thread_repository import ThreadRepository
from ..services.thread_service import get_thread_service
from ..utils.task_queue_adapter import AGENT_EVENT_CHANNEL_PREFIX, AGENT_THREAD_KEY_PREFIX


PARTIAL_SAVE_INTERVAL_SECONDS = 5.0
MESSAGE_LOCK_SECONDS = 90

_worker_loop: asyncio.AbstractEventLoop | None = None


class AgentMessageBusy(RuntimeError):
    """Raised when another delivery is already processing this message."""


def _run_async(coro):
    """Reuse one event loop per Celery child process."""
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
    return _worker_loop.run_until_complete(coro)


async def _publish(
    redis: Redis,
    message_id: str,
    event_type: str,
    data: Any,
    *,
    content_sequence: int | None = None,
) -> None:
    event = {
        "type": event_type,
        "data": data,
        "message_id": message_id,
    }
    if content_sequence is not None:
        event["content_sequence"] = content_sequence
    try:
        await redis.publish(
            f"{AGENT_EVENT_CHANNEL_PREFIX}{message_id}",
            json.dumps(event, ensure_ascii=False, default=str),
        )
    except RedisError as exc:
        # PostgreSQL is the source of truth; a lost live event is repaired by snapshot/final reads.
        logger.warning(
            "[AgentWorker] Pub/Sub failed: message_id={} error={}",
            message_id,
            exc,
        )


async def _load_run(
    *,
    thread_id: str,
    user_id: str,
    user_message_id: str,
    assistant_message_id: str,
) -> tuple[str, str, dict, dict] | None:
    async with async_session_factory() as session:
        repo = ThreadRepository(session)
        thread = await repo.get_thread_by_user(
            UUID(thread_id), UUID(user_id), include_messages=False
        )
        user_message = await repo.get_message(UUID(user_message_id))
        assistant_message = await repo.get_message(UUID(assistant_message_id))

        if not thread or not user_message or not assistant_message:
            raise ValueError("Agent run references a missing thread or message")
        if user_message.thread_id != thread.id or assistant_message.thread_id != thread.id:
            raise ValueError("Agent run message does not belong to the requested thread")
        if user_message.role != "user" or assistant_message.role != "assistant":
            raise ValueError("Agent run message roles are invalid")
        if assistant_message.status == "completed":
            return None

        base_metadata = dict(assistant_message.metadata_ or {})
        base_metadata.pop("_agent_job", None)
        base_metadata.pop("_content_sequence", None)
        context = dict(base_metadata.get("context") or {})
        context.update(
            {
                "thread_id": thread_id,
                "user_id": user_id,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
            }
        )
        return (
            user_message.content,
            str(base_metadata.get("mode") or "auto"),
            context,
            base_metadata,
        )


async def _save_status(
    message_id: str,
    status: str,
    *,
    content: str | None = None,
    metadata: dict | None = None,
) -> None:
    async with async_session_factory() as session:
        service = get_thread_service(session)
        message = await service.update_message_status(
            message_id,
            status=status,
            content=content,
            metadata=metadata,
        )
        if not message:
            raise ValueError(f"Assistant message not found: {message_id}")
        await session.commit()


async def _save_partial(message_id: str, content: str, content_sequence: int) -> None:
    async with async_session_factory() as session:
        service = get_thread_service(session)
        message = await service.update_message_partial_content(
            message_id,
            partial_content=content,
            status="generating",
        )
        if not message:
            raise ValueError(f"Assistant message not found: {message_id}")
        await service.update_message_metadata(
            message_id,
            _content_sequence=content_sequence,
        )
        await session.commit()


async def _save_completed(message_id: str, content: str, metadata: dict) -> None:
    async with async_session_factory() as session:
        service = get_thread_service(session)
        message = await service.update_message_status(
            message_id,
            status="completed",
            content=content,
            metadata=metadata,
        )
        if not message:
            raise ValueError(f"Assistant message not found: {message_id}")
        await service.update_message_partial_content(message_id, partial_content="")
        await session.commit()


async def _refresh_lock(lock, lost: asyncio.Event) -> None:
    while True:
        await asyncio.sleep(MESSAGE_LOCK_SECONDS / 3)
        try:
            if not await lock.extend(MESSAGE_LOCK_SECONDS, replace_ttl=True):
                lost.set()
                return
        except (LockError, RedisError):
            lost.set()
            return


async def _clear_thread_slot(redis: Redis, thread_id: str, message_id: str) -> None:
    script = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """
    with suppress(RedisError):
        await redis.eval(
            script,
            1,
            f"{AGENT_THREAD_KEY_PREFIX}{thread_id}",
            message_id,
        )


async def run_agent_message(
    *,
    thread_id: str,
    user_id: str,
    user_message_id: str,
    assistant_message_id: str,
) -> dict[str, Any]:
    """Execute one persisted assistant message independently of the SSE client."""
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    lock = redis.lock(
        f"lock:agent:message:{assistant_message_id}",
        timeout=MESSAGE_LOCK_SECONDS,
        blocking=False,
    )
    if not await lock.acquire(blocking=False):
        await redis.aclose()
        raise AgentMessageBusy(assistant_message_id)

    lock_lost = asyncio.Event()
    heartbeat = asyncio.create_task(_refresh_lock(lock, lock_lost))

    try:
        loaded = await _load_run(
            thread_id=thread_id,
            user_id=user_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        )
        if loaded is None:
            await _clear_thread_slot(redis, thread_id, assistant_message_id)
            return {"status": "already_completed", "message_id": assistant_message_id}

        query, mode, context, base_metadata = loaded
        await _save_status(assistant_message_id, "analyzing")
        await _publish(redis, assistant_message_id, "status", "analyzing")

        full_response = ""
        content_sequence = 0
        current_status = "analyzing"
        last_save = time.monotonic()
        result_metadata = {
            **base_metadata,
            "mode": mode,
            "sources": [],
            "citations": [],
            "intent": None,
            "search_queries": [],
            "search_results": [],
            "thinking_steps": [],
            "search_retry_count": 0,
            "search_quality_score": 0.0,
            "failed_queries": [],
            "retry_reason": "",
            "context": context,
        }

        async for event in stream_ai_agent(
            settings=settings,
            query=query,
            thread_id=thread_id,
            mode=mode,
            metadata=context,
            enable_retry=True,
            max_retries=3,
            user_id=user_id,
        ):
            if lock_lost.is_set():
                raise RuntimeError("Agent message lock was lost")

            event_type = str(event.get("type") or "")
            data = event.get("data")

            if event_type == "thinking":
                if current_status != "thinking":
                    current_status = "thinking"
                    await _save_status(assistant_message_id, current_status)
                    await _publish(redis, assistant_message_id, "status", current_status)
                if isinstance(data, dict):
                    result_metadata["thinking_steps"].append(data)
                    result_metadata["mode"] = data.get("mode", result_metadata["mode"])
                    result_metadata["search_retry_count"] = data.get(
                        "search_retry_count", result_metadata["search_retry_count"]
                    )
                await _publish(redis, assistant_message_id, "thinking_step", data)

            elif event_type == "query_analysis":
                result_metadata["intent"] = data
                await _publish(redis, assistant_message_id, event_type, data)

            elif event_type == "search_queries":
                if current_status != "searching":
                    current_status = "searching"
                    await _save_status(assistant_message_id, current_status)
                    await _publish(redis, assistant_message_id, "status", current_status)
                result_metadata["search_queries"] = data if isinstance(data, list) else []
                await _publish(redis, assistant_message_id, event_type, data)

            elif event_type in {"search_results", "sources", "citations"}:
                result_metadata[event_type] = data if isinstance(data, list) else []
                await _publish(redis, assistant_message_id, event_type, data)

            elif event_type == "search_retry":
                if isinstance(data, dict):
                    result_metadata["search_retry_count"] = data.get(
                        "retry_count", result_metadata["search_retry_count"]
                    )
                    result_metadata["search_quality_score"] = data.get(
                        "quality_score", result_metadata["search_quality_score"]
                    )
                    result_metadata["retry_reason"] = data.get(
                        "reason", result_metadata["retry_reason"]
                    )
                    if data.get("failed_query"):
                        result_metadata["failed_queries"].append(data["failed_query"])
                await _publish(redis, assistant_message_id, event_type, data)

            elif event_type == "token":
                if current_status != "generating":
                    current_status = "generating"
                    await _save_status(assistant_message_id, current_status)
                    await _publish(redis, assistant_message_id, "status", current_status)
                token = data if isinstance(data, str) else str(data or "")
                full_response += token
                content_sequence += 1
                await _publish(
                    redis,
                    assistant_message_id,
                    "token",
                    token,
                    content_sequence=content_sequence,
                )

                if time.monotonic() - last_save >= PARTIAL_SAVE_INTERVAL_SECONDS:
                    await _save_partial(
                        assistant_message_id,
                        full_response,
                        content_sequence,
                    )
                    await _publish(
                        redis,
                        assistant_message_id,
                        "content",
                        full_response,
                        content_sequence=content_sequence,
                    )
                    await _publish(redis, assistant_message_id, "partial_save", True)
                    last_save = time.monotonic()

            elif event_type == "content":
                full_response = data if isinstance(data, str) else str(data or "")
                await _publish(
                    redis,
                    assistant_message_id,
                    event_type,
                    full_response,
                    content_sequence=content_sequence,
                )

            elif event_type == "done":
                await _save_completed(
                    assistant_message_id,
                    full_response,
                    result_metadata,
                )
                done = {"content": full_response, "metadata": result_metadata}
                await _clear_thread_slot(redis, thread_id, assistant_message_id)
                await _publish(redis, assistant_message_id, "done", done)
                return {
                    "status": "completed",
                    "message_id": assistant_message_id,
                    "content_length": len(full_response),
                }

            elif event_type == "error":
                raise RuntimeError(str(data))

            else:
                await _publish(redis, assistant_message_id, event_type, data)

        raise RuntimeError("Agent stream ended without a done event")

    except Exception as exc:
        logger.exception(
            "[AgentWorker] Run failed: message_id={} error={}",
            assistant_message_id,
            exc,
        )
        with suppress(Exception):
            await _save_status(
                assistant_message_id,
                "failed",
                content="Agent execution failed. Please retry.",
            )
        await _clear_thread_slot(redis, thread_id, assistant_message_id)
        await _publish(
            redis,
            assistant_message_id,
            "error",
            "Agent execution failed. Please retry.",
        )
        raise
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        with suppress(LockError, RedisError):
            await lock.release()
        await redis.aclose()


@celery_app.task(
    bind=True,
    name="app.tasks.agent_task.process_agent_message",
    ignore_result=True,
)
def process_agent_message(
    self,
    *,
    thread_id: str,
    user_id: str,
    user_message_id: str,
    assistant_message_id: str,
):
    try:
        return _run_async(
            run_agent_message(
                thread_id=thread_id,
                user_id=user_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
        )
    except AgentMessageBusy as exc:
        raise self.retry(exc=exc, countdown=5, max_retries=120)
