
import redis
import os
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# 기본값은 로컬 호스트 (워커가 호스트에서 실행되므로)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

class WorkerSemaphore:
    """작업 실행 중임을 Redis에 표시하는 세마포어.
    
    사용 예:
        with WorkerSemaphore("npu"):
            # NPU 작업 수행
            ...
    
    이 세마포어가 활성화되어 있으면, LiteLLM Router는 해당 자원을 'busy'로 간주하고
    다른 자원(GPU)으로 트래픽을 우회합니다.
    """
    
    def __init__(self, resource_name: str, timeout: int = 600):
        """
        Args:
            resource_name: 자원 이름 ('npu', 'gpu' 등)
            timeout: 세마포어 만료 시간 (초). 프로세스 비정상 종료 시 자동 해제용.
        """
        # Redis 연결은 __enter__에서 하거나 여기서 해도 됨.
        # 매번 연결 생성 오버헤드를 줄이려면 전역 클라이언트를 쓰는 게 좋음.
        # 하지만 워커 프로세스는 Fork되거나 독립적이므로 인스턴스별 연결도 나쁘지 않음.
        self.resource_name = resource_name
        self.timeout = timeout
        self.redis_client = None
        self.key = f"worker:{resource_name}:active"

    def __enter__(self):
        """세마포어 획득."""
        try:
            self.redis_client = redis.from_url(REDIS_URL)
            self.redis_client.set(self.key, "1", ex=self.timeout)
            logger.info(f"Semaphore acquired: {self.key}")
        except Exception as e:
            logger.error(f"Error checking semaphore {self.key}: {e}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """세마포어 해제."""
        if self.redis_client:
            try:
                self.redis_client.delete(self.key)
                logger.info(f"Semaphore released: {self.key}")
                self.redis_client.close()
            except Exception as e:
                logger.error(f"Error releasing semaphore {self.key}: {e}")
