"""Recover queued agent messages lost between database commit and broker handoff."""

import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import suppress
from uuid import UUID

from redis.exceptions import LockError, RedisError

from ..core.logging import logger
from ..core.redis import get_redis_client
from ..db.session import async_session_factory
from ..repositories.thread_repository import ThreadRepository
from ..utils.task_queue_adapter import (
    ACTIVE_JOB_TTL,
    AGENT_DISPATCH_KEY_PREFIX,
    AGENT_FAILURE_CONTENT,
    AGENT_MESSAGE_LOCK_PREFIX,
    AGENT_MESSAGE_LOCK_SECONDS,
    get_task_queue,
)


STALE_AGENT_JOB_SECONDS = 60
RECOVERY_LOCK_SECONDS = 60
RECOVERY_LOCK_PREFIX = "lock:recover:agent:"


async def finalize_stale_active_agent_messages() -> int:
    """Fail abandoned active rows only while holding the worker's message lock."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ACTIVE_JOB_TTL)
    async with async_session_factory() as session:
        messages = await ThreadRepository(
            session
        ).get_stale_active_assistant_messages(cutoff)

    redis = get_redis_client()
    finalized = 0
    for message in messages:
        message_id = str(message.id)
        lock = None
        acquired = False
        try:
            lock = redis.lock(
                f"{AGENT_MESSAGE_LOCK_PREFIX}{message_id}",
                timeout=AGENT_MESSAGE_LOCK_SECONDS,
                blocking=False,
            )
            acquired = await lock.acquire(blocking=False)
            if not acquired:
                continue
            async with async_session_factory() as session:
                updated = await ThreadRepository(
                    session
                ).fail_stale_active_assistant_message(
                    UUID(message_id), cutoff, AGENT_FAILURE_CONTENT
                )
                await session.commit()
            finalized += int(updated)
        except RedisError:
            logger.exception(
                "[AgentRecovery] Stale finalizer lock unavailable: message_id={}",
                message_id,
            )
        except Exception:
            logger.exception(
                "[AgentRecovery] Stale finalizer failed: message_id={}", message_id
            )
        finally:
            if lock is not None and acquired:
                with suppress(LockError, RedisError):
                    await lock.release()
    return finalized


async def recover_stale_agent_jobs() -> int:
    """Requeue stale messages that have no successful broker-dispatch marker."""
    await finalize_stale_active_agent_messages()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_AGENT_JOB_SECONDS)
    async with async_session_factory() as session:
        messages = await ThreadRepository(session).get_stale_queued_assistant_messages(
            cutoff
        )

    redis = get_redis_client()
    recovered = 0
    for message in messages:
        message_id = str(message.id)
        thread_id = str(message.thread_id)
        metadata = dict(message.metadata_ or {})
        user_id = metadata.get("agent_user_id")
        user_message_id = metadata.get("agent_user_message_id")
        if not user_id or not user_message_id:
            logger.error(
                "[AgentRecovery] Missing handoff metadata: message_id={}", message_id
            )
            continue

        if await redis.exists(f"{AGENT_DISPATCH_KEY_PREFIX}{message_id}"):
            continue
        lock_key = f"{RECOVERY_LOCK_PREFIX}{message_id}"
        if not await redis.set(lock_key, "1", nx=True, ex=RECOVERY_LOCK_SECONDS):
            continue

        try:
            if await redis.exists(f"{AGENT_DISPATCH_KEY_PREFIX}{message_id}"):
                continue

            def enqueue() -> None:
                get_task_queue().enqueue_agent_job(
                    thread_id=thread_id,
                    user_id=str(user_id),
                    user_message_id=str(user_message_id),
                    assistant_message_id=message_id,
                )

            await asyncio.to_thread(enqueue)
            recovered += 1
            logger.warning(
                "[AgentRecovery] Requeued stale job: message_id={}", message_id
            )
        except Exception as exc:
            logger.exception(
                "[AgentRecovery] Requeue failed: message_id={} error={}",
                message_id,
                exc,
            )
        finally:
            await redis.delete(lock_key)

    return recovered
