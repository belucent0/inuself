"""Task Queue 추상화 레이어 - Celery를 사용."""
from abc import ABC, abstractmethod
from typing import Any, Dict
import redis
from ..core.config import get_settings
from ..core.logging import logger
from ..core.telemetry import inject_trace_context, get_trace_id

# 활성 작업 추적 TTL (2시간 - 최대 작업 시간 + 여유분)
ACTIVE_JOB_TTL = 7200


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
        """ASR 작업을 큐에 등록하고 작업 ID를 반환.

        Returns:
            job_id: 작업 ID (성공 시)
            None: 중복으로 스킵됨
        """
        pass

    @abstractmethod
    def enqueue_llm_job(self, file_id: int, text_to_summarize: str) -> str | None:
        """LLM 작업을 큐에 등록하고 작업 ID를 반환.

        Args:
            file_id: 파일 ID
            text_to_summarize: 요약할 텍스트 (transcription 또는 OCR 결과)

        Returns:
            job_id: 작업 ID (성공 시)
            None: 중복으로 스킵됨
        """
        pass

    @abstractmethod
    def enqueue_ocr_job(
        self,
        file_id: int,
        file_s3_key: str | None = None,
        image_s3_keys: list[str] | None = None,
        ocr_mode: str = "document",
        ocr_accuracy_mode: str = "speed",
    ) -> str | None:
        """OCR 작업을 큐에 등록하고 작업 ID를 반환.

        Args:
            file_id: 파일 ID
            file_s3_key: 원본 파일 S3 경로 (새 방식, Worker 전처리)
            image_s3_keys: 이미지 S3 경로 목록 (기존 방식, Backend 전처리)
            ocr_mode: OCR 모드 ("document" 또는 "portray")
            ocr_accuracy_mode: OCR 정확도 모드 ("speed" 또는 "accuracy")

        Returns:
            job_id: 작업 ID (성공 시)
            None: 중복으로 스킵됨
        """
        pass

    @abstractmethod
    def clear_active_job(self, task_type: str, file_id: int) -> None:
        """활성 작업 해제 (작업 완료 시 호출)."""
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
        # 중복 방지용 Redis 클라이언트
        self.redis = redis.from_url(settings.redis_url, decode_responses=True)

    def _enqueue_with_dedup(
        self,
        task_type: str,
        file_id: int,
        task_name: str,
        kwargs: dict,
        queue: str,
        headers: dict | None = None,
    ) -> str | None:
        """중복 방지 미들웨어 - 모든 작업 큐잉에 공통 적용.

        Args:
            task_type: 작업 유형 (asr, ocr, llm)
            file_id: 파일 ID
            task_name: Celery 태스크 이름
            kwargs: 태스크에 전달할 인자
            queue: Celery 큐 이름
            headers: Celery 헤더 (trace context 등)

        Returns:
            job_id: 작업 ID (성공 시)
            None: 중복으로 스킵됨
        """
        key = f"active_job:{task_type}:{file_id}"

        # 중복 체크
        if self.redis.exists(key):
            existing_job_id = self.redis.get(key)
            logger.warning(
                "[TaskQueue] Duplicate job skipped: %s for file_id=%s (existing job_id=%s)",
                task_type, file_id, existing_job_id
            )
            return None

        # 작업 큐잉
        result = self.celery.send_task(
            task_name,
            kwargs=kwargs,
            queue=queue,
            headers=headers or {},
        )

        # 활성 작업 등록 (TTL 포함)
        self.redis.setex(key, ACTIVE_JOB_TTL, result.id)
        logger.info(
            "[TaskQueue] Job enqueued: %s for file_id=%s, job_id=%s",
            task_type, file_id, result.id
        )

        return result.id

    def clear_active_job(self, task_type: str, file_id: int) -> None:
        """활성 작업 해제 (작업 완료 시 Worker에서 호출).

        Args:
            task_type: 작업 유형 (asr, ocr, llm)
            file_id: 파일 ID
        """
        key = f"active_job:{task_type}:{file_id}"
        deleted = self.redis.delete(key)
        if deleted:
            logger.debug("[TaskQueue] Active job cleared: %s", key)
        else:
            logger.debug("[TaskQueue] Active job not found (already cleared or expired): %s", key)
    
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
        # Trace context 주입 (분산 추적)
        headers = {}
        inject_trace_context(headers)
        trace_id = get_trace_id()
        logger.debug(f"[TaskQueue] ASR headers: {headers}, trace_id={trace_id}")

        return self._enqueue_with_dedup(
            task_type="asr",
            file_id=file_id,
            task_name="worker.tasks.asr_task.process_asr_task",
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
    
    def enqueue_llm_job(self, file_id: int, text_to_summarize: str) -> str | None:
        try:
            # Trace context 주입 (분산 추적)
            headers = {}
            inject_trace_context(headers)
            trace_id = get_trace_id()
            logger.debug("[TaskQueue] LLM headers: trace_id=%s", trace_id)

            return self._enqueue_with_dedup(
                task_type="llm",
                file_id=file_id,
                task_name="worker.tasks.llm_task.process_llm_task",
                kwargs={
                    "file_id": file_id,
                    "text_to_summarize": text_to_summarize,
                },
                queue="llm_summary",
                headers=headers,
            )
        except Exception as exc:
            logger.error("[TaskQueue] Failed to enqueue LLM job: file_id=%s, error=%s", file_id, exc)
            raise
    
    def enqueue_ocr_job(
        self,
        file_id: int,
        file_s3_key: str | None = None,
        image_s3_keys: list[str] | None = None,
        ocr_mode: str = "document",
        ocr_accuracy_mode: str = "speed",
    ) -> str | None:
        # Trace context 주입 (분산 추적)
        headers = {}
        inject_trace_context(headers)
        trace_id = get_trace_id()
        logger.debug(f"[TaskQueue] OCR headers: trace_id={trace_id}")

        return self._enqueue_with_dedup(
            task_type="ocr",
            file_id=file_id,
            task_name="worker.tasks.ocr_task.process_ocr_task",
            kwargs={
                "file_id": file_id,
                "file_s3_key": file_s3_key,
                "image_s3_keys": image_s3_keys,
                "ocr_mode": ocr_mode,
                "ocr_accuracy_mode": ocr_accuracy_mode,
            },
            queue="ocr_tasks",
            headers=headers,
        )
    
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
