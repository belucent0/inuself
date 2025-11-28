import logging
from functools import lru_cache
from rq import Queue

from ..core.redis import get_redis_connection
from .llm_processor import process_llm_job
from .utils import safe_print

LLM_QUEUE_NAME = "llm_tasks"

logger = logging.getLogger(__name__)


@lru_cache
def _get_queue() -> Queue:
    return Queue(LLM_QUEUE_NAME, connection=get_redis_connection())


def is_llm_job_in_queue(*, content_id: int) -> bool:
    """
    해당 content_id의 LLM 작업이 큐에 이미 있는지 확인.
    
    Celery를 사용할 때는 Celery 작업 상태를 확인하고,
    RQ를 사용할 때는 RQ 큐를 확인합니다.
    """
    from ..core.config import get_settings
    
    settings = get_settings()
    queue_type = settings.task_queue_type.lower()
    
    if queue_type == "celery":
        # Celery를 사용할 때는 Celery 작업 상태 확인
        try:
            from celery.result import AsyncResult
            from .celery_app import celery_app
            from celery import current_app
            
            # Celery의 활성 작업 확인 (Inspector 사용)
            inspector = current_app.control.inspect()
            
            # 활성 작업 확인 (현재 실행 중인 작업)
            active = inspector.active()
            if active:
                for worker_name, tasks in active.items():
                    for task in tasks:
                        if task.get("name") == "process_llm_task":
                            task_args = task.get("args", [])
                            task_kwargs = task.get("kwargs", {})
                            # content_id 확인
                            if task_kwargs.get("content_id") == content_id or (task_args and task_args[0] == content_id):
                                return True
            
            # 예약된 작업 확인 (큐에 대기 중인 작업)
            scheduled = inspector.scheduled()
            if scheduled:
                for worker_name, tasks in scheduled.items():
                    for task in tasks:
                        if task.get("request", {}).get("task") == "process_llm_task":
                            task_kwargs = task.get("request", {}).get("kwargs", {})
                            if task_kwargs.get("content_id") == content_id:
                                return True
            
            # 예약된 작업 확인 (reserved)
            reserved = inspector.reserved()
            if reserved:
                for worker_name, tasks in reserved.items():
                    for task in tasks:
                        if task.get("name") == "process_llm_task":
                            task_kwargs = task.get("kwargs", {})
                            if task_kwargs.get("content_id") == content_id:
                                return True
        except Exception as exc:
            logger.warning("Failed to check Celery queue for content_id=%s: %s", content_id, exc)
            # Celery 확인 실패 시 안전하게 False 반환 (중복 등록 허용)
            return False
        
        return False
    else:
        # RQ를 사용할 때는 RQ 큐 확인
        queue = _get_queue()
        job_ids = queue.get_job_ids()
        
        # 시작된 작업도 확인 (처리 중인 작업)
        started_job_ids = queue.started_job_registry.get_job_ids()
        all_job_ids = set(job_ids) | set(started_job_ids)
        
        for job_id in all_job_ids:
            try:
                job = queue.fetch_job(job_id)
                if job is None:
                    continue
                
                job_kwargs = getattr(job, "kwargs", {})
                job_content_id = job_kwargs.get("content_id")
                
                if job_content_id == content_id:
                    return True
            except Exception:
                continue
        
        return False


def enqueue_llm_job(*, content_id: int) -> None:
    """LLM 요약 작업을 큐에 등록."""
    queue = _get_queue()
    job = queue.enqueue(process_llm_job, content_id=content_id)
    safe_print(f"[LLM Queue] 작업 등록됨: content_id={content_id}, job_id={job.id}, 큐 크기={len(queue)}")
    logger.info(
        "LLM job enqueued: content_id=%s, job_id=%s, queue_size=%s",
        content_id,
        job.id,
        len(queue),
    )


def cancel_llm_jobs_by_content_ids(content_ids: list[int]) -> int:
    """주어진 content_id와 매칭되는 LLM 작업을 큐에서 제거."""
    if not content_ids:
        return 0

    queue = _get_queue()
    cancelled = 0

    job_ids = queue.get_job_ids()
    safe_print(f"[LLM Queue] 큐의 {len(job_ids)}개 작업을 검사 중... (삭제 후보 {len(content_ids)}개)")

    for job_id in job_ids:
        try:
            job = queue.fetch_job(job_id)
            if job is None:
                continue

            job_kwargs = getattr(job, "kwargs", {})
            job_content_id = job_kwargs.get("content_id")

            if job_content_id and job_content_id in content_ids:
                try:
                    job.cancel()
                except Exception:
                    pass

                job.delete()
                cancelled += 1
                safe_print(f"[LLM Queue] 작업 취소/삭제: content_id={job_content_id}, job_id={job_id}")
                logger.info("Cancelled LLM job: content_id=%s, job_id=%s", job_content_id, job_id)
        except Exception as exc:
            logger.warning("Failed to cancel LLM job %s: %s", job_id, exc)
            continue

    safe_print(f"[LLM Queue] 총 {cancelled}개의 작업이 삭제되었습니다.")
    return cancelled

