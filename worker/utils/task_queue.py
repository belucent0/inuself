"""Task Queue 추상화 레이어 - Celery를 사용.

중복 enqueue 방지 로직 포함:
- enqueue 전에 is_celery_task_in_queue()로 이미 큐에 있는지 확인
- 이미 있으면 스킵하고 None 반환
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

from worker.config import get_settings

logger = logging.getLogger(__name__)


class TaskQueueAdapter(ABC):
    """Task Queue 추상 인터페이스."""
    
    @abstractmethod
    def enqueue_asr_job(
        self,
        file_id: int,
        storage_key: str,
        original_filename: str,
        model_size: str,
        processing_mode: str,
        num_asr_chunks: int,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        accuracy_mode: str = "speed",
    ) -> str | None:
        """ASR 작업을 큐에 등록하고 작업 ID를 반환. 이미 큐에 있으면 None."""
        pass
    
    @abstractmethod
    def enqueue_llm_job(self, file_id: int) -> str | None:
        """LLM 작업을 큐에 등록하고 작업 ID를 반환. 이미 큐에 있으면 None."""
        pass
    
    @abstractmethod
    def enqueue_ocr_job(
        self,
        file_id: int,
        storage_key: str,
        original_filename: str,
        ocr_mode: str = "basic",
    ) -> str | None:
        """OCR 작업을 큐에 등록하고 작업 ID를 반환. 이미 큐에 있으면 None."""
        pass
    
    @abstractmethod
    def get_job_status(self, job_id: str) -> str:
        """작업 상태 조회."""
        pass


# 태스크 이름 매핑 (짧은 이름 -> 전체 이름)
_TASK_NAME_MAPPING = {
    "process_asr_task": "worker.tasks.asr_task.process_asr_task",
    "process_llm_task": "worker.tasks.llm_task.process_llm_task",
    "process_ocr_task": "worker.tasks.ocr_task.process_ocr_task",
}


def _match_task_name(actual_name: str, expected_name: str) -> bool:
    """태스크 이름이 일치하는지 확인 (짧은 이름과 전체 이름 모두 지원)."""
    if actual_name == expected_name:
        return True
    # 짧은 이름으로 호출된 경우, 전체 이름과도 비교
    if expected_name in _TASK_NAME_MAPPING:
        return actual_name == _TASK_NAME_MAPPING[expected_name]
    return False


def _is_task_in_queue(file_id: int, task_name: str) -> bool:
    """
    해당 file_id의 작업이 큐에 이미 있는지 확인.
    
    Celery Inspector를 직접 사용하여 확인합니다 (백엔드 의존성 없음).
    """
    from celery import Celery
    
    settings = get_settings()
    
    try:
        celery_app = Celery(
            "task_queue_checker",
            broker=settings.redis_url,
            backend=settings.redis_url,
        )
        inspector = celery_app.control.inspect()
        
        # 활성 작업 확인 (현재 실행 중인 작업)
        active = inspector.active()
        if active:
            for worker_name, tasks in active.items():
                for task in tasks:
                    if _match_task_name(task.get("name", ""), task_name):
                        task_kwargs = task.get("kwargs", {})
                        task_args = task.get("args", [])
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        if task_file_id == file_id:
                            return True
        
        # 예약된 작업 확인 (큐에 대기 중인 작업)
        reserved = inspector.reserved()
        if reserved:
            for worker_name, tasks in reserved.items():
                for task in tasks:
                    if _match_task_name(task.get("name", ""), task_name):
                        task_kwargs = task.get("kwargs", {})
                        task_args = task.get("args", [])
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        if task_file_id == file_id:
                            return True
        
        # 스케줄된 작업 확인 (ETA로 예약된 작업)
        scheduled = inspector.scheduled()
        if scheduled:
            for worker_name, tasks in scheduled.items():
                for task in tasks:
                    request = task.get("request", {})
                    if _match_task_name(request.get("task", ""), task_name):
                        task_kwargs = request.get("kwargs", {})
                        task_args = request.get("args", [])
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        if task_file_id == file_id:
                            return True
        
        return False
    except Exception as e:
        logger.warning("[TaskQueue] Failed to check task in queue: %s", e)
        return False  # 에러 시 안전하게 False 반환 (중복 체크 실패해도 enqueue 허용)


class CeleryAdapter(TaskQueueAdapter):
    """Celery 구현."""
    
    def __init__(self):
        from celery import Celery
        settings = get_settings()
        # send_task 전용 Celery 앱 (태스크 정의 없이 이름으로 호출)
        self.celery = Celery(
            "task_client",
            broker=settings.redis_url,
            backend=settings.redis_url,
        )
    
    def enqueue_asr_job(
        self,
        file_id: int,
        storage_key: str,
        original_filename: str,
        model_size: str,
        processing_mode: str,
        num_asr_chunks: int,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        accuracy_mode: str = "speed",
    ) -> str | None:
        # 중복 enqueue 방지
        if _is_task_in_queue(file_id, "process_asr_task"):
            logger.info("[TaskQueue] ASR job already in queue, skipping: file_id=%s", file_id)
            return None
        
        # send_task로 새 worker의 태스크 이름 직접 호출
        result = self.celery.send_task(
            "worker.tasks.asr_task.process_asr_task",
            kwargs={
                "file_id": file_id,
                "storage_key": storage_key,
                "original_filename": original_filename,
                "model_size": model_size,
                "processing_mode": processing_mode,
                "num_asr_chunks": num_asr_chunks,
                "min_speakers": min_speakers,
                "max_speakers": max_speakers,
                "accuracy_mode": accuracy_mode,
            },
            queue="asr",
        )
        logger.info("[TaskQueue] ASR job enqueued: file_id=%s, job_id=%s", file_id, result.id)
        return result.id
    
    def enqueue_llm_job(self, file_id: int) -> str | None:
        # 중복 enqueue 방지
        if _is_task_in_queue(file_id, "process_llm_task"):
            logger.info("[TaskQueue] LLM job already in queue, skipping: file_id=%s", file_id)
            return None
        
        try:
            result = self.celery.send_task(
                "worker.tasks.llm_task.process_llm_task",
                kwargs={"file_id": file_id},
                queue="llm",
            )
            logger.info("[TaskQueue] LLM job enqueued: file_id=%s, job_id=%s", file_id, result.id)
            return result.id
        except Exception as exc:
            logger.error("[TaskQueue] Failed to enqueue LLM job: file_id=%s, error=%s", file_id, exc)
            raise
    
    def enqueue_ocr_job(
        self,
        file_id: int,
        storage_key: str,
        original_filename: str,
        ocr_mode: str = "basic",
    ) -> str | None:
        # 중복 enqueue 방지
        if _is_task_in_queue(file_id, "process_ocr_task"):
            logger.info("[TaskQueue] OCR job already in queue, skipping: file_id=%s", file_id)
            return None
        
        result = self.celery.send_task(
            "worker.tasks.ocr_task.process_ocr_task",
            kwargs={
                "file_id": file_id,
                "storage_key": storage_key,
                "original_filename": original_filename,
                "ocr_mode": ocr_mode,
            },
            queue="ocr",
        )
        logger.info("[TaskQueue] OCR job enqueued: file_id=%s, job_id=%s", file_id, result.id)
        return result.id
    
    def get_job_status(self, job_id: str) -> str:
        from celery.result import AsyncResult
        
        result = AsyncResult(job_id, app=self.celery)
        return result.status


def get_task_queue() -> TaskQueueAdapter:
    """Celery Task Queue 어댑터를 반환."""
    import logging
    logger = logging.getLogger(__name__)
    
    settings = get_settings()
    queue_type = settings.task_queue_type.lower()
    
    logger.info(f"[TaskQueue] 큐 타입 확인: task_queue_type={queue_type}")
    
    if queue_type == "celery":
        logger.info("[TaskQueue] Celery 어댑터 사용")
        return CeleryAdapter()
    else:
        raise ValueError(f"Unknown task queue type: {queue_type}. Only 'celery' is supported.")
