"""Recoverable Celery dispatch for persisted Agent messages."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from ..core.logging import logger
from ..core.redis import get_redis_client
from ..db.models import AiMessage
from ..db.session import async_session_factory
from ..utils.task_queue_adapter import ACTIVE_JOB_TTL, get_task_queue


AGENT_JOB_METADATA_KEY = "_agent_job"
AGENT_DISPATCH_KEY_PREFIX = "agent_dispatch:"
AGENT_DISPATCH_CLAIM_SECONDS = 60
AGENT_RECONCILE_INTERVAL_SECONDS = 5


async def dispatch_agent_job(message_id: str, job: dict[str, str]) -> bool:
    """Publish once per short claim window; duplicates remain worker-idempotent."""
    redis = get_redis_client()
    claim_key = f"{AGENT_DISPATCH_KEY_PREFIX}{message_id}"
    claim_id = uuid4().hex
    claimed = await redis.set(
        claim_key,
        claim_id,
        nx=True,
        ex=AGENT_DISPATCH_CLAIM_SECONDS,
    )
    if not claimed:
        return False

    await asyncio.to_thread(
        get_task_queue().enqueue_agent_job,
        thread_id=job["thread_id"],
        user_id=job["user_id"],
        user_message_id=job["user_message_id"],
        assistant_message_id=message_id,
    )
    await redis.eval(
        """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """,
        1,
        claim_key,
        claim_id,
        ACTIVE_JOB_TTL,
    )
    return True


async def reconcile_agent_jobs_once() -> int:
    """Republish persisted queued messages whose dispatch claim expired."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(AiMessage)
            .where(
                AiMessage.role == "assistant",
                AiMessage.status == "queued",
                AiMessage.metadata_.has_key(AGENT_JOB_METADATA_KEY),  # type: ignore[attr-defined]
            )
            .order_by(AiMessage.created_at)
            .limit(100)
        )
        messages = list(result.scalars().all())

    dispatched = 0
    for message in messages:
        job: Any = (message.metadata_ or {}).get(AGENT_JOB_METADATA_KEY)
        if not isinstance(job, dict) or not all(
            isinstance(job.get(key), str)
            for key in ("thread_id", "user_id", "user_message_id")
        ):
            logger.error("[AgentDispatch] Invalid job metadata: message_id={}", message.id)
            continue
        try:
            dispatched += int(await dispatch_agent_job(str(message.id), job))
        except Exception as exc:
            logger.warning(
                "[AgentDispatch] Publish deferred: message_id={} error={}",
                message.id,
                exc,
            )
    return dispatched


async def run_agent_dispatch_reconciler() -> None:
    while True:
        try:
            await reconcile_agent_jobs_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[AgentDispatch] Reconcile failed: {}", exc)
        await asyncio.sleep(AGENT_RECONCILE_INTERVAL_SECONDS)
