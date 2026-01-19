"""Task Queue 추상화 레이어 - Celery를 사용."""
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict
from ..core.config import get_settings
from ..core.telemetry import inject_trace_context, get_trace_id

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
    ) -> str:
        """ASR 작업을 큐에 등록하고 작업 ID를 반환."""
        pass
    
    @abstractmethod
    def enqueue_llm_job(self, file_id: int, text_to_summarize: str) -> str:
        """LLM 작업을 큐에 등록하고 작업 ID를 반환.
        
        Args:
            file_id: 파일 ID
            text_to_summarize: 요약할 텍스트 (transcription 또는 OCR 결과)
        """
        pass
    
    @abstractmethod
    def enqueue_ocr_job(
        self,
        file_id: int,
        image_s3_keys: list[str],
        ocr_mode: str = "document",
        ocr_accuracy_mode: str = "speed",
    ) -> str:
        """OCR 작업을 큐에 등록하고 작업 ID를 반환.
        
        Args:
            file_id: 파일 ID
            image_s3_keys: 이미지 S3 경로 목록 (백엔드에서 전처리된 이미지들)
            ocr_mode: OCR 모드 ("document" 또는 "portray")
            ocr_accuracy_mode: OCR 정확도 모드 ("speed" 또는 "accuracy")
        """
        pass
    
    @abstractmethod
    def get_job_status(self, job_id: str) -> str:
        """작업 상태 조회."""
        pass


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
    ) -> str:
        # Trace context 주입 (분산 추적)
        headers = {}
        inject_trace_context(headers)
        trace_id = get_trace_id()

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
            headers=headers,
        )
        logger.debug(f"[TaskQueue] ASR job enqueued with trace_id={trace_id}")
        return result.id
    
    def enqueue_llm_job(self, file_id: int, text_to_summarize: str) -> str:
        try:
            # Trace context 주입 (분산 추적)
            headers = {}
            inject_trace_context(headers)
            trace_id = get_trace_id()

            result = self.celery.send_task(
                "worker.tasks.llm_task.process_llm_task",
                kwargs={
                    "file_id": file_id,
                    "text_to_summarize": text_to_summarize,
                },
                queue="llm_summary",
                headers=headers,
            )
            logger.info("[TaskQueue] LLM job enqueued: file_id=%s, job_id=%s, trace_id=%s", file_id, result.id, trace_id)
            return result.id
        except Exception as exc:
            logger.error("[TaskQueue] Failed to enqueue LLM job: file_id=%s, error=%s", file_id, exc)
            raise
    
    def enqueue_ocr_job(
        self,
        file_id: int,
        image_s3_keys: list[str],
        ocr_mode: str = "document",
        ocr_accuracy_mode: str = "speed",
    ) -> str:
        # Trace context 주입 (분산 추적)
        headers = {}
        inject_trace_context(headers)
        trace_id = get_trace_id()

        result = self.celery.send_task(
            "worker.tasks.ocr_task.process_ocr_task",
            kwargs={
                "file_id": file_id,
                "image_s3_keys": image_s3_keys,
                "ocr_mode": ocr_mode,
                "ocr_accuracy_mode": ocr_accuracy_mode,
            },
            queue="ocr_tasks",
            headers=headers,
        )
        logger.debug(f"[TaskQueue] OCR job enqueued with trace_id={trace_id}")
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
