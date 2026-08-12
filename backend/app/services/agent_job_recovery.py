"""Recover queued agent messages lost between database commit and broker handoff."""

import asyncio
from datetime import datetime, timedelta, timezone

from ..core.logging import logger
from ..core.redis import get_redis_client
from ..db.session import async_session_factory
from ..repositories.thread_repository import ThreadRepository
from ..utils.task_queue_adapter import (
    ACTIVE_JOB_TTL,
    AGENT_DISPATCH_KEY_PREFIX,
    AGENT_THREAD_KEY_PREFIX,
    get_task_queue,
)


STALE_AGENT_JOB_SECONDS = 60
RECOVERY_LOCK_SECONDS = 60
RECOVERY_LOCK_PREFIX = "lock:recover:agent:"


async def recover_stale_agent_jobs() -> int:
    """Requeue stale messages that have no successful broker-dispatch marker."""
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

            slot_key = f"{AGENT_THREAD_KEY_PREFIX}{thread_id}"
            active_message_id = await redis.get(slot_key)
            if active_message_id not in {None, message_id}:
                continue
            if active_message_id is None:
                reserved = await redis.set(
                    slot_key, message_id, nx=True, ex=ACTIVE_JOB_TTL
                )
                if not reserved:
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
