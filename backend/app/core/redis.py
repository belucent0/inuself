import redis

from .config import get_settings

settings = get_settings()
_pool = redis.ConnectionPool.from_url(settings.redis_url)


def get_redis_connection() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)


