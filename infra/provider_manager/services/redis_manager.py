"""Redis Connection Manager — SRP, OCP, DIP 준수.

부팅 시 Redis가 준비되지 않아도 프로세스가 crash되지 않도록
연결 lifecycle을 background task로 완전 분리.
"""
import asyncio
import logging
from enum import Enum
from typing import Callable, List, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# 모듈 레벨 싱글톤 (health route 등 외부 접근용)
_redis_manager: Optional["RedisConnectionManager"] = None


def get_redis_manager() -> Optional["RedisConnectionManager"]:
    """모듈 레벨 getter."""
    return _redis_manager


class RedisState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class RedisConnectionManager:
    """Redis 연결 lifecycle 관리 (SRP).

    - 프로세스 crash 없이 백그라운드에서 연결 재시도
    - on_ready 콜백으로 의존 컴포넌트에게 연결 완료 통지 (OCP/DIP)
    - exponential backoff: 2s → 30s 상한
    - 연결 후 10초마다 ping 헬스체크, 실패 시 자동 재연결
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        self._state = RedisState.DISCONNECTED
        self._ready_event = asyncio.Event()
        self._callbacks: List[Callable] = []
        self._connect_task: Optional[asyncio.Task] = None

        # 모듈 레벨 등록
        global _redis_manager
        _redis_manager = self

    # ==========================================
    # Properties
    # ==========================================

    @property
    def redis(self) -> Optional[aioredis.Redis]:
        return self._redis

    @property
    def is_connected(self) -> bool:
        return self._state == RedisState.CONNECTED

    @property
    def state(self) -> RedisState:
        return self._state

    # ==========================================
    # Public API
    # ==========================================

    def on_ready(self, callback: Callable) -> None:
        """Redis 연결 성공 시 호출될 콜백 등록 (OCP — 확장에 열림)."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        """백그라운드 연결 루프 시작 (non-blocking)."""
        self._connect_task = asyncio.create_task(self._connect_loop())
        logger.info("RedisConnectionManager: background connect loop started")

    async def wait_until_ready(self, timeout: Optional[float] = None) -> bool:
        """Redis 연결까지 대기 (stream-only 모드 호환용).

        Returns:
            True if connected, False if timeout.
        """
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        """연결 루프 취소 + Redis graceful close."""
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass

        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

        self._state = RedisState.DISCONNECTED
        logger.info("RedisConnectionManager: closed")

    # ==========================================
    # Internal
    # ==========================================

    async def _connect_loop(self) -> None:
        """영구 연결 루프 — 절대 raise하지 않음 (프로세스 crash 불가)."""
        base_delay = 2.0
        max_delay = 30.0
        attempt = 0

        while True:
            self._state = RedisState.CONNECTING
            self._ready_event.clear()

            try:
                # pool 오염 방지: 새 클라이언트 생성
                if self._redis:
                    try:
                        await self._redis.aclose()
                    except Exception:
                        pass

                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
                await self._redis.ping()

                # 연결 성공
                self._state = RedisState.CONNECTED
                self._ready_event.set()
                attempt = 0
                logger.info("RedisConnectionManager: connected to Redis")

                # on_ready 콜백 실행 (OCP — 외부 컴포넌트 초기화)
                for callback in self._callbacks:
                    try:
                        result = callback(self._redis)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"RedisConnectionManager: on_ready callback error: {e}")

                # 헬스체크 루프 (10초마다 ping)
                while True:
                    await asyncio.sleep(10)
                    try:
                        await self._redis.ping()
                    except Exception as e:
                        logger.warning(f"RedisConnectionManager: ping failed, reconnecting: {e}")
                        self._state = RedisState.DISCONNECTED
                        break  # 외부 루프로 복귀 → 재연결

            except asyncio.CancelledError:
                return
            except Exception as e:
                delay = min(base_delay * (1.3 ** min(attempt, 20)), max_delay)
                logger.warning(
                    f"RedisConnectionManager: connection failed: {e} "
                    f"(attempt {attempt + 1}, retry in {delay:.1f}s)"
                )
                attempt += 1
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
