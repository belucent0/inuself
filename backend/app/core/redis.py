import redis
import redis.asyncio as aioredis

from .config import get_settings

settings = get_settings()
_pool = redis.ConnectionPool.from_url(settings.redis_url)

# 비동기 Redis 연결 풀
_async_pool: aioredis.ConnectionPool | None = None


def get_redis_connection() -> redis.Redis:
    """동기 Redis 클라이언트 반환."""
    return redis.Redis(connection_pool=_pool)


def get_redis_client() -> aioredis.Redis:
    """비동기 Redis 클라이언트 반환 (AI Agent용)."""
    global _async_pool
    if _async_pool is None:
        _async_pool = aioredis.ConnectionPool.from_url(settings.redis_url)
    return aioredis.Redis(connection_pool=_async_pool)


