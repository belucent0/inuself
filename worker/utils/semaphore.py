
import redis
import os
import logging

logger = logging.getLogger(__name__)

# 기본값은 로컬 호스트 (워커가 호스트에서 실행되므로)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


class WorkerLock:
    """작업 실행 중임을 Redis에 표시하는 분산 뮤텍스.

    redis-py의 Lock을 래핑하여 동일한 컨텍스트 매니저 인터페이스를 제공한다.
    내부적으로 SETNX + Lua 원자적 해제 + TTL을 사용한다.

    사용 예:
        with WorkerLock("npu"):
            # NPU 작업 수행
            ...

    이 Lock이 활성화되어 있으면, LiteLLM Router는 해당 자원을 'busy'로 간주하고
    다른 자원(GPU)으로 트래픽을 우회합니다.
    """

    def __init__(self, resource_name: str, timeout: int = 600):
        """
        Args:
            resource_name: 자원 이름 ('npu', 'gpu' 등)
            timeout: 잠금 만료 시간 (초). 프로세스 비정상 종료 시 자동 해제용.
        """
        self.resource_name = resource_name
        self.timeout = timeout
        self.key = f"worker:{resource_name}:active"
        self._redis_client = None
        self._lock = None
        self._acquired = False

    def __enter__(self):
        """잠금 획득."""
        try:
            self._redis_client = redis.from_url(REDIS_URL)
            self._lock = self._redis_client.lock(
                self.key, timeout=self.timeout, blocking=False
            )
            self._acquired = self._lock.acquire()
            if self._acquired:
                logger.info(f"[Lock] acquired: {self.key}")
            else:
                logger.warning(f"[Lock] already held: {self.key}")
        except Exception as e:
            logger.error(f"[Lock] Error acquiring {self.key}: {e}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """잠금 해제 (획득한 경우에만)."""
        if self._acquired and self._lock:
            try:
                self._lock.release()
                logger.info(f"[Lock] released: {self.key}")
            except Exception as e:
                logger.error(f"[Lock] Error releasing {self.key}: {e}")
        if self._redis_client:
            try:
                self._redis_client.close()
            except Exception:
                pass


# 하위 호환성 별칭 (기존 코드에서 WorkerSemaphore를 참조하는 경우)
WorkerSemaphore = WorkerLock
