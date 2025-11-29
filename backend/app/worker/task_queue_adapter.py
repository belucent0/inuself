"""Task Queue 추상화 레이어 - RQ와 Celery를 동일한 인터페이스로 사용."""
from abc import ABC, abstractmethod
from typing import Any, Dict
from ..core.config import get_settings


class TaskQueueAdapter(ABC):
    """Task Queue 추상 인터페이스."""
    
    @abstractmethod
    def enqueue_asr_job(
        self,
        content_id: int,
        storage_key: str,
        original_filename: str,
        model_size: str,
        processing_mode: str,
        num_asr_chunks: int,
    ) -> str:
        """ASR 작업을 큐에 등록하고 작업 ID를 반환."""
        pass
    
    @abstractmethod
    def enqueue_llm_job(self, content_id: int) -> str:
        """LLM 작업을 큐에 등록하고 작업 ID를 반환."""
        pass
    
    @abstractmethod
    def get_job_status(self, job_id: str) -> str:
        """작업 상태 조회."""
        pass


class RQAdapter(TaskQueueAdapter):
    """RQ 구현."""
    
    def enqueue_asr_job(
        self,
        content_id: int,
        storage_key: str,
        original_filename: str,
        model_size: str,
        processing_mode: str,
        num_asr_chunks: int,
    ) -> str:
        from .queue import enqueue_transcription_job
        
        # RQ는 직접 job 객체 반환하므로 변환 필요 없음
        enqueue_transcription_job(
            content_id=content_id,
            storage_key=storage_key,
            original_filename=original_filename,
            model_size=model_size,
            processing_mode=processing_mode,
            num_asr_chunks=num_asr_chunks,
        )
        return f"rq_{content_id}"  # RQ는 job_id를 별도 추적 안 함
    
    def enqueue_llm_job(self, content_id: int) -> str:
        from .llm_queue import enqueue_llm_job
        
        enqueue_llm_job(content_id=content_id)
        return f"rq_llm_{content_id}"
    
    def get_job_status(self, job_id: str) -> str:
        # RQ 구현 (나중에 필요시)
        return "unknown"


class CeleryAdapter(TaskQueueAdapter):
    """Celery 구현."""
    
    def enqueue_asr_job(
        self,
        content_id: int,
        storage_key: str,
        original_filename: str,
        model_size: str,
        processing_mode: str,
        num_asr_chunks: int,
    ) -> str:
        # Lazy import: celery_tasks가 processor를 import하므로 실행 시점에만 로드
        from .celery_tasks import process_asr_task
        
        result = process_asr_task.delay(
            content_id=content_id,
            storage_key=storage_key,
            original_filename=original_filename,
            model_size=model_size,
            processing_mode=processing_mode,
            num_asr_chunks=num_asr_chunks,
        )
        return result.id
    
    def enqueue_llm_job(self, content_id: int) -> str:
        # Lazy import: celery_tasks가 llm_processor를 import하므로 실행 시점에만 로드
        from .celery_tasks import process_llm_task
        
        result = process_llm_task.delay(content_id=content_id)
        return result.id
    
    def get_job_status(self, job_id: str) -> str:
        from celery.result import AsyncResult
        from .celery_app import celery_app
        
        result = AsyncResult(job_id, app=celery_app)
        return result.status


def get_task_queue() -> TaskQueueAdapter:
    """환경설정에 따라 적절한 Task Queue 어댑터를 반환."""
    import logging
    logger = logging.getLogger(__name__)
    
    settings = get_settings()
    queue_type = settings.task_queue_type.lower()
    
    logger.info(f"[TaskQueue] 큐 타입 확인: task_queue_type={queue_type}")
    
    if queue_type == "celery":
        logger.info("[TaskQueue] Celery 어댑터 사용")
        return CeleryAdapter()
    elif queue_type == "rq":
        logger.info("[TaskQueue] RQ 어댑터 사용")
        return RQAdapter()
    else:
        raise ValueError(f"Unknown task queue type: {queue_type}. Use 'rq' or 'celery'.")

