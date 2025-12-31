"""WebSocket 관련 FastAPI 의존성."""
from typing import Annotated

from fastapi import Depends

from .connection_manager import ConnectionManager
from .redis_listener import RedisListener

# 전역 싱글톤 인스턴스
_connection_manager: ConnectionManager | None = None
_redis_listener: RedisListener | None = None


def get_connection_manager() -> ConnectionManager:
    """ConnectionManager 싱글톤을 반환합니다.

    Returns:
        ConnectionManager 인스턴스
    """
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = ConnectionManager()
    return _connection_manager


def get_redis_listener() -> RedisListener:
    """RedisListener 싱글톤을 반환합니다.

    Returns:
        RedisListener 인스턴스
    """
    global _redis_listener
    if _redis_listener is None:
        manager = get_connection_manager()
        _redis_listener = RedisListener(manager)
    return _redis_listener


# 타입 힌트용 Annotated
ConnectionManagerDep = Annotated[ConnectionManager, Depends(get_connection_manager)]
RedisListenerDep = Annotated[RedisListener, Depends(get_redis_listener)]
