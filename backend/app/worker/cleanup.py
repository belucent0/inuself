"""워커 시작 시 stale 작업 정리 유틸리티."""
import logging
from typing import Literal

from rq import Queue
from rq.job import JobStatus
from rq.registry import StartedJobRegistry, FailedJobRegistry

from ..core.redis import get_redis_connection
from .utils import safe_print

logger = logging.getLogger(__name__)

QueueName = Literal["asr_tasks", "llm_tasks"]


def cleanup_stale_jobs(queue_name: QueueName, *, requeue: bool = True) -> dict[str, int]:
    """
    워커 시작 시 stale 상태의 작업들을 정리합니다.
    
    Args:
        queue_name: 정리할 큐 이름
        requeue: True면 started 작업을 다시 큐에 넣고, False면 failed로 이동
        
    Returns:
        정리 통계 딕셔너리
    """
    redis = get_redis_connection()
    queue = Queue(queue_name, connection=redis)
    started_registry = StartedJobRegistry(queue=queue)
    failed_registry = FailedJobRegistry(queue=queue)
    
    stats = {
        "started_jobs": 0,
        "requeued": 0,
        "failed": 0,
        "cleaned": 0,
    }
    
    # Started 레지스트리의 작업들 처리
    started_job_ids = started_registry.get_job_ids()
    stats["started_jobs"] = len(started_job_ids)
    
    logger.info(
        "Cleaning up stale jobs for queue=%s: found %d started jobs",
        queue_name,
        len(started_job_ids),
    )
    
    for job_id in started_job_ids:
        try:
            job = queue.fetch_job(job_id)
            if not job:
                # Job이 없으면 레지스트리에서만 제거
                started_registry.remove(job_id)
                stats["cleaned"] += 1
                continue
            
            if requeue:
                # 작업을 다시 큐에 넣기
                job.set_status(JobStatus.QUEUED)
                started_registry.remove(job_id)
                queue.push_job_id(job_id)
                stats["requeued"] += 1
                logger.info("Requeued stale job: %s", job_id)
            else:
                # 실패로 처리
                job.set_status(JobStatus.FAILED)
                job.exc_info = "Worker crashed or was terminated"
                started_registry.remove(job_id)
                failed_registry.add(job, -1)  # -1 = no expiry
                stats["failed"] += 1
                logger.info("Marked stale job as failed: %s", job_id)
                
        except Exception as exc:
            logger.warning("Failed to process stale job %s: %s", job_id, exc)
            continue
    
    logger.info(
        "Cleanup complete for queue=%s: requeued=%d, failed=%d, cleaned=%d",
        queue_name,
        stats["requeued"],
        stats["failed"],
        stats["cleaned"],
    )
    
    return stats


def cleanup_all_queues(*, requeue: bool = True) -> None:
    """모든 큐의 stale 작업을 정리합니다."""
    safe_print("[Cleanup] ========================================")
    safe_print("[Cleanup] Stale 작업 정리 시작...")
    safe_print(f"[Cleanup] 정책: {'재시도' if requeue else '실패 처리'}")
    safe_print("[Cleanup] ========================================")
    
    for queue_name in ["asr_tasks", "llm_tasks"]:
        try:
            stats = cleanup_stale_jobs(queue_name, requeue=requeue)
            if stats["started_jobs"] > 0:
                safe_print(f"[Cleanup] {queue_name}: {stats['started_jobs']}개 발견")
                safe_print(f"[Cleanup]   - 재큐잉: {stats['requeued']}개")
                safe_print(f"[Cleanup]   - 실패 처리: {stats['failed']}개")
                safe_print(f"[Cleanup]   - 정리: {stats['cleaned']}개")
            else:
                safe_print(f"[Cleanup] {queue_name}: 정리할 작업 없음")
        except Exception as exc:
            safe_print(f"[Cleanup] ERROR {queue_name} 정리 실패: {exc}")
            logger.exception("Failed to cleanup queue: %s", queue_name)
    
    safe_print("[Cleanup] ========================================")


def get_queue_stats(queue_name: QueueName) -> dict[str, int]:
    """큐의 현재 상태를 반환합니다."""
    redis = get_redis_connection()
    queue = Queue(queue_name, connection=redis)
    started_registry = StartedJobRegistry(queue=queue)
    failed_registry = FailedJobRegistry(queue=queue)
    
    return {
        "queued": len(queue),
        "started": len(started_registry),
        "failed": len(failed_registry),
    }


