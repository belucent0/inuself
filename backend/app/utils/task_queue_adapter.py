"""Task Queue 추상화 레이어 - Celery를 사용."""

from abc import ABC, abstractmethod
from functools import lru_cache
import redis
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from ..core.config import get_settings
from ..core.logging import logger
from ..core.telemetry import inject_trace_context, get_trace_id, get_tracer

# 활성 작업 추적 TTL (2시간 - 최대 작업 시간 + 여유분)
ACTIVE_JOB_TTL = 7200
AGENT_EVENT_CHANNEL_PREFIX = "events:agent:"
AGENT_DISPATCH_KEY_PREFIX = "dispatched_agent_message:"
AGENT_MESSAGE_LOCK_PREFIX = "lock:agent:message:"
AGENT_MESSAGE_LOCK_SECONDS = 90
AGENT_FAILURE_CONTENT = "This response could not be completed. Please try again."


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
    def enqueue_llm_job(self, file_id: int, messages: list[dict]) -> str | None:
        """LLM 작업을 큐에 등록하고 작업 ID를 반환.

        [Phase 1] 프롬프트 주입 패턴: 완성된 messages 리스트를 받습니다.

        Args:
            file_id: 파일 ID
            messages: LLM 호출용 완성된 messages 리스트 (Backend에서 생성)

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
    def enqueue_agent_job(
        self,
        *,
        thread_id: str,
        user_id: str,
        user_message_id: str,
        assistant_message_id: str,
    ) -> str:
        """Queue one persisted assistant message for Agent Worker execution."""
        pass

    @abstractmethod
    def get_job_status(self, job_id: str) -> str:
        """작업 상태 조회."""
        pass

    @abstractmethod
    def clear_active_job(self, task_type: str, file_id: str | int) -> None:
        """활성 작업 해제 (작업 완료 시 호출)."""
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
        file_id: str | int,
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
                task_type,
                file_id,
                existing_job_id,
            )
            return None

        # Service Graph용 CLIENT span 생성 (Backend → Worker 연결 표시)
        tracer = get_tracer("celery-client")
        with tracer.start_as_current_span(
            f"celery.send.{task_type}",
            kind=SpanKind.CLIENT,
        ) as span:
            span.set_attribute("peer.service", "asr-worker")
            span.set_attribute("messaging.system", "celery")
            span.set_attribute("messaging.destination", queue)
            span.set_attribute("messaging.operation", "send")
            span.set_attribute("celery.task_name", task_name)
            span.set_attribute("file.id", str(file_id))

            # 작업 큐잉
            result = self.celery.send_task(
                task_name,
                kwargs=kwargs,
                queue=queue,
                headers=headers or {},
            )

            span.set_attribute("celery.task_id", result.id)

        # 활성 작업 등록 (TTL 포함)
        self.redis.setex(key, ACTIVE_JOB_TTL, result.id)
        logger.info(
            "[TaskQueue] Job enqueued: %s for file_id=%s, job_id=%s",
            task_type,
            file_id,
            result.id,
        )

        return result.id

    def clear_active_job(self, task_type: str, file_id: str | int) -> None:
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
            logger.debug(
                "[TaskQueue] Active job not found (already cleared or expired): %s", key
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

    def enqueue_llm_job(self, file_id: int, messages: list[dict]) -> str | None:
        """
        [Phase 1] LLM 작업을 큐에 등록 (프롬프트 주입 패턴).

        Args:
            file_id: 파일 ID
            messages: LLM 호출용 완성된 messages 리스트

        Returns:
            job_id: 작업 ID (성공 시)
            None: 중복으로 스킵됨
        """
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
                    "messages": messages,  # [Phase 1] 프롬프트 주입
                },
                queue="llm_summary",
                headers=headers,
            )
        except Exception as exc:
            logger.error(
                "[TaskQueue] Failed to enqueue LLM job: file_id=%s, error=%s",
                file_id,
                exc,
            )
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

    def enqueue_agent_job(
        self,
        *,
        thread_id: str,
        user_id: str,
        user_message_id: str,
        assistant_message_id: str,
    ) -> str:
        headers = {}
        inject_trace_context(headers)
        result = self.celery.send_task(
            "app.tasks.agent_task.process_agent_message",
            kwargs={
                "thread_id": thread_id,
                "user_id": user_id,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
            },
            queue="agent",
            task_id=assistant_message_id,
            headers=headers,
        )
        logger.info(
            "[TaskQueue] Agent job enqueued: thread_id={} message_id={}",
            thread_id,
            assistant_message_id,
        )
        return result.id

    def get_job_status(self, job_id: str) -> str:
        from celery.result import AsyncResult

        result = AsyncResult(job_id, app=self.celery)
        return result.status


@lru_cache(maxsize=1)
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
        raise ValueError(
            f"Unknown task queue type: {queue_type}. Only 'celery' is supported."
        )
