"""Centralized Redis connection pool."""

import redis
import redis.asyncio as redis_async

from config import REDIS_URL

_async_redis: redis_async.Redis | None = None
_sync_redis: redis.Redis | None = None


async def get_async_redis() -> redis_async.Redis:
    global _async_redis
    if _async_redis is None:
        _async_redis = redis_async.from_url(REDIS_URL, decode_responses=True)
    return _async_redis


def get_sync_redis() -> redis.Redis:
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _sync_redis


async def close_async_redis():
    global _async_redis
    if _async_redis:
        await _async_redis.aclose()
        _async_redis = None
