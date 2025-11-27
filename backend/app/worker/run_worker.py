import logging
import sys
import asyncio
from rq import Connection
from rq.worker import SimpleWorker
from rq.timeouts import BaseDeathPenalty

from ..core.config import get_settings
from ..core.redis import get_redis_connection
from .queue import QUEUE_NAME
from .requeue import requeue_processing_contents
from .utils import safe_print

logger = logging.getLogger(__name__)


class NoOpDeathPenalty(BaseDeathPenalty):
    """Windows 호환을 위한 빈 DeathPenalty (SIGALRM 사용 안 함)."""
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class WindowsCompatibleWorker(SimpleWorker):
    """Windows 호환 SimpleWorker (SIGALRM 사용 안 함)."""
    
    # 클래스 레벨에서 death_penalty_class 설정 (Windows에서만)
    if sys.platform == "win32":
        death_penalty_class = NoOpDeathPenalty
    
    def __init__(self, *args, **kwargs):
        # job_timeout을 kwargs에서 제거 (BaseWorker가 받지 않음)
        kwargs.pop("job_timeout", None)
        super().__init__(*args, **kwargs)
        # Windows에서는 job_timeout을 None으로 설정
        if sys.platform == "win32":
            self.job_timeout = None
            # 인스턴스 변수로도 설정 (이중 보장)
            self.death_penalty_class = NoOpDeathPenalty
    
    def perform_job(self, job, queue):
        """Windows에서 SIGALRM 문제를 피하기 위해 perform_job 오버라이드."""
        if sys.platform == "win32":
            # Windows에서는 death_penalty_class를 NoOpDeathPenalty로 강제 설정
            # 원본 death_penalty_class를 백업
            original_death_penalty = getattr(self, 'death_penalty_class', None)
            try:
                # NoOpDeathPenalty로 강제 설정
                self.death_penalty_class = NoOpDeathPenalty
                # job_timeout도 None으로 설정
                original_timeout = self.job_timeout
                self.job_timeout = None
                # 원본 perform_job 호출
                return super().perform_job(job, queue)
            finally:
                # 원래 값으로 복원 (필요시)
                if original_death_penalty is not None:
                    self.death_penalty_class = original_death_penalty
                self.job_timeout = original_timeout
        else:
            return super().perform_job(job, queue)


def main() -> None:
    """RQ 워커 메인 함수."""
    logger.info("Starting RQ worker for queue: %s", QUEUE_NAME)
    safe_print(f"[Worker] ========================================")
    safe_print(f"[Worker] RQ 워커 시작")
    safe_print(f"[Worker] 큐 이름: {QUEUE_NAME}")
    
    # Windows 환경 감지
    is_windows = sys.platform == "win32"
    if is_windows:
        safe_print("[Worker] Windows 환경: SimpleWorker 사용 (fork 없음, 타임아웃 비활성화)")
    safe_print(f"[Worker] ========================================")
    
    # DB에 남아있는 PROCESSING 상태 작업 재큐잉
    try:
        safe_print("[Worker] DB 재큐잉 검사 중...")
        requeued = asyncio.run(requeue_processing_contents())
        if requeued:
            safe_print(f"[Worker] OK {requeued}개의 작업을 다시 큐에 등록했습니다.")
        else:
            safe_print("[Worker] OK 재큐잉할 작업 없음")
    except Exception as exc:
        safe_print(f"[Worker] ⚠ DB 재큐잉 검사 실패: {exc}")
        logger.warning("Failed to requeue processing contents: %s", exc)

    # Stale 작업 정리
    try:
        from .cleanup import cleanup_stale_jobs
        safe_print(f"[Worker] Stale 작업 정리 중...")
        stats = cleanup_stale_jobs("asr_tasks", requeue=True)
        if stats["started_jobs"] > 0:
            safe_print(f"[Worker] OK {stats['requeued']}개 작업 재시도, {stats['failed']}개 실패 처리")
    except Exception as exc:
        safe_print(f"[Worker] ⚠ Stale 작업 정리 실패: {exc}")
        logger.warning("Failed to cleanup stale jobs: %s", exc)
    
    try:
        redis = get_redis_connection()
        logger.info("Redis connection established")
        safe_print("[Worker] OK Redis 연결 성공")
        
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
            safe_print("[Worker] OK 워커 생성 완료")
            safe_print("[Worker] 작업 대기 중... (큐에서 작업을 기다립니다)")
            safe_print("[Worker] ========================================")
            worker.work()
    except Exception as e:
        logger.exception("Worker failed to start")
        safe_print(f"[Worker] ERROR 워커 시작 실패: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()


