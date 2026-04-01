"""Redis 세마포어 기반 디바이스 잠금 관리.

GPU/NPU 동시 접근을 제어합니다.
키: worker:{device}:active (Worker와 동일한 키 사용)
"""

import asyncio
import logging
import threading
import time
import uuid
from typing import Optional

from config import LOCK_TTL_DEFAULT, LOCK_TTL_ASR
from core.redis import get_async_redis, get_sync_redis

logger = logging.getLogger(__name__)


async def acquire_device_lock(
    device: str,
    timeout: int = LOCK_TTL_DEFAULT,
    max_wait: float = 600.0,
    lock_id: Optional[str] = None,
) -> Optional[str]:
    """Redis Lock 획득.

    Args:
        device: "gpu" 또는 "npu"
        timeout: Lock TTL (초)
        max_wait: 획득 대기 최대 시간 (초)
        lock_id: 지정할 lock ID (None이면 자동 생성)

    Returns:
        lock_id (성공) 또는 None (실패)
    """
    r = await get_async_redis()
    lock_id = lock_id or str(uuid.uuid4())
    key = f"worker:{device}:active"
    start = time.time()

    while time.time() - start < max_wait:
        lock = r.lock(key, timeout=timeout, blocking=False)
        try:
            acquired = await lock.acquire(token=lock_id.encode())
            if acquired:
                logger.info(f"[DeviceLock] Acquired {device} lock: {lock_id[:8]}...")
                return lock_id
        except Exception as e:
            logger.warning(f"[DeviceLock] Lock acquire error: {e}")
        await asyncio.sleep(0.5)

    logger.warning(f"[DeviceLock] Failed to acquire {device} lock within {max_wait}s")
    return None


async def release_device_lock(device: str, lock_id: str) -> bool:
    """Redis Lock 해제 (토큰 매칭).

    Args:
        device: "gpu" 또는 "npu"
        lock_id: 해제할 lock ID

    Returns:
        해제 성공 여부
    """
    r = await get_async_redis()
    key = f"worker:{device}:active"

    try:
        lock = r.lock(key, thread_local=False)
        lock.local.token = lock_id.encode()
        await lock.release()
        logger.info(f"[DeviceLock] Released {device} lock: {lock_id[:8]}...")
        return True
    except Exception as e:
        logger.warning(f"[DeviceLock] Failed to release {device} lock: {e}")
        return False


async def is_device_busy(device: str) -> bool:
    """디바이스 사용 중 여부 (Redis 세마포어).

    Args:
        device: "gpu" 또는 "npu"

    Returns:
        True면 사용 중
    """
    r = await get_async_redis()
    key = f"worker:{device}:active"

    try:
        return bool(await r.exists(key))
    except Exception as e:
        logger.warning(f"[DeviceLock] Busy check error: {e}")
        return False


def start_lock_heartbeat(
    lock_id: str,
    device: str = "gpu",
    ttl: int = LOCK_TTL_ASR,
) -> threading.Event:
    """Lock TTL 갱신 heartbeat 스레드 시작.

    ASR 등 장시간 작업 시 Lock이 만료되지 않도록 TTL/2 간격으로 갱신합니다.

    Args:
        lock_id: 갱신할 lock ID
        device: "gpu" 또는 "npu"
        ttl: Lock TTL (초)

    Returns:
        stop_event — heartbeat를 중지하려면 이 이벤트를 set()
    """
    stop_event = threading.Event()
    key = f"worker:{device}:active"
    interval = ttl // 2

    def _heartbeat():
        r = get_sync_redis()
        while not stop_event.is_set():
            try:
                # Lock이 우리 것인지 확인 후 TTL 갱신
                current_token = r.get(key)
                if current_token == lock_id:
                    r.expire(key, ttl)
                    logger.debug(f"[Heartbeat] Renewed {device} lock TTL: {lock_id[:8]}...")
                else:
                    logger.warning(f"[Heartbeat] Lock token mismatch, stopping")
                    break
            except Exception as e:
                logger.warning(f"[Heartbeat] Error: {e}")
            stop_event.wait(interval)

    thread = threading.Thread(target=_heartbeat, daemon=True)
    thread.start()
    logger.info(f"[Heartbeat] Started for {device} lock: {lock_id[:8]}... (interval={interval}s)")
    return stop_event


def stop_lock_heartbeat(stop_event: Optional[threading.Event]):
    """Heartbeat 스레드 중지."""
    if stop_event:
        stop_event.set()
        logger.info("[Heartbeat] Stopped")
