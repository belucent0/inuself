"""Task Queue 추상화 레이어 - Celery를 사용."""
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict
from ..core.config import get_settings

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
    ) -> str:
        """ASR 작업을 큐에 등록하고 작업 ID를 반환."""
        pass
    
    @abstractmethod
    def enqueue_llm_job(self, file_id: int) -> str:
        """LLM 작업을 큐에 등록하고 작업 ID를 반환."""
        pass
    
    @abstractmethod
    def enqueue_ocr_job(
        self,
        file_id: int,
        storage_key: str,
        original_filename: str,
        ocr_mode: str = "basic",
    ) -> str:
        """OCR 작업을 큐에 등록하고 작업 ID를 반환."""
        pass
    
    @abstractmethod
    def get_job_status(self, job_id: str) -> str:
        """작업 상태 조회."""
        pass


class CeleryAdapter(TaskQueueAdapter):
    """Celery 구현."""
    
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
    ) -> str:
        # Lazy import: celery_tasks가 processor를 import하므로 실행 시점에만 로드
        from .celery_tasks import process_asr_task
        
        # 큐를 명시적으로 지정하여 작업 전송
        result = process_asr_task.apply_async(
            args=(),
            kwargs={
                "file_id": file_id,
                "storage_key": storage_key,
                "original_filename": original_filename,
                "model_size": model_size,
                "processing_mode": processing_mode,
                "num_asr_chunks": num_asr_chunks,
                "min_speakers": min_speakers,
                "max_speakers": max_speakers,
            },
            queue="asr",  # 큐를 명시적으로 지정
        )
        return result.id
    
    def enqueue_llm_job(self, file_id: int) -> str:
        # Lazy import: celery_tasks가 llm_processor를 import하므로 실행 시점에만 로드
        from .celery_tasks import process_llm_task
        try:
            result = process_llm_task.apply_async(
                args=(),
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
    ) -> str:
        # Lazy import: celery_tasks가 ocr_processor를 import하므로 실행 시점에만 로드
        from .celery_tasks import process_ocr_task
        
        # 큐를 명시적으로 지정하여 작업 전송
        result = process_ocr_task.apply_async(
            args=(),
            kwargs={
                "file_id": file_id,
                "storage_key": storage_key,
                "original_filename": original_filename,
                "ocr_mode": ocr_mode,
            },
            queue="ocr",  # 큐를 명시적으로 지정
        )
        return result.id
    
    def get_job_status(self, job_id: str) -> str:
        from celery.result import AsyncResult
        from .celery_app import celery_app
        
        result = AsyncResult(job_id, app=celery_app)
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

