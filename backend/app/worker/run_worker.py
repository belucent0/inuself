import logging
import sys
from rq import Connection
from rq.worker import SimpleWorker
from rq.timeouts import BaseDeathPenalty

from ..core.redis import get_redis_connection
from .queue import QUEUE_NAME

logger = logging.getLogger(__name__)


class NoOpDeathPenalty(BaseDeathPenalty):
    """Windows 호환을 위한 빈 DeathPenalty (SIGALRM 사용 안 함)."""
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class WindowsCompatibleWorker(SimpleWorker):
    """Windows 호환 SimpleWorker (SIGALRM 사용 안 함)."""
    
    def __init__(self, *args, **kwargs):
        # job_timeout을 kwargs에서 제거 (BaseWorker가 받지 않음)
        kwargs.pop("job_timeout", None)
        super().__init__(*args, **kwargs)
        # Windows에서는 death_penalty_class를 NoOpDeathPenalty로 설정
        if sys.platform == "win32":
            self.death_penalty_class = NoOpDeathPenalty
            # job_timeout 속성도 None으로 설정 (타임아웃 비활성화)
            self.job_timeout = None


def main() -> None:
    """RQ 워커 메인 함수."""
    logger.info("Starting RQ worker for queue: %s", QUEUE_NAME)
    print(f"[Worker] ========================================")
    print(f"[Worker] RQ 워커 시작")
    print(f"[Worker] 큐 이름: {QUEUE_NAME}")
    
    # Windows 환경 감지
    is_windows = sys.platform == "win32"
    if is_windows:
        print("[Worker] Windows 환경: SimpleWorker 사용 (fork 없음, 타임아웃 비활성화)")
    print(f"[Worker] ========================================")
    
    try:
        redis = get_redis_connection()
        logger.info("Redis connection established")
        print("[Worker] ✓ Redis 연결 성공")
        
        with Connection(redis):
            # Windows에서는 WindowsCompatibleWorker 사용 (fork 없음, SIGALRM 사용 안 함)
            # Linux/Mac에서는 일반 SimpleWorker 사용
            if is_windows:
                worker = WindowsCompatibleWorker(
                    [QUEUE_NAME],
                    connection=redis,
                )
            else:
                worker = SimpleWorker(
                    [QUEUE_NAME],
                    connection=redis,
                )
            
            logger.info("Worker created, starting work...")
            print("[Worker] ✓ 워커 생성 완료")
            print("[Worker] 작업 대기 중... (큐에서 작업을 기다립니다)")
            print("[Worker] ========================================")
            worker.work()
    except Exception as e:
        logger.exception("Worker failed to start")
        print(f"[Worker] ✗ 워커 시작 실패: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()


