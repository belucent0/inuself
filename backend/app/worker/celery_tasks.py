"""Celery 태스크 정의 - ASR 및 LLM 처리."""
import logging
from .celery_app import celery_app
from .processor import process_transcription_job
from .llm_processor import process_llm_job

logger = logging.getLogger(__name__)


@celery_app.task(
    name="process_asr_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_asr_task(
    self,
    content_id: int,
    storage_key: str,
    original_filename: str,
    model_size: str,
    processing_mode: str,
    num_asr_chunks: int,
):
    """
    ASR 작업 처리 Celery 태스크.
    
    기존 RQ의 process_transcription_job을 재사용합니다.
    """
    try:
        logger.info(
            "[Celery ASR] Starting task: content_id=%s, task_id=%s",
            content_id,
            self.request.id,
        )
        
        # 기존 RQ 프로세서 재사용
        process_transcription_job(
            content_id=content_id,
            storage_key=storage_key,
            original_filename=original_filename,
            model_size=model_size,
            processing_mode=processing_mode,
            num_asr_chunks=num_asr_chunks,
        )
        
        logger.info("[Celery ASR] Task completed: content_id=%s", content_id)
        return {"status": "success", "content_id": content_id}
        
    except Exception as exc:
        logger.exception("[Celery ASR] Task failed: content_id=%s", content_id)
        # 재시도 로직
        raise self.retry(exc=exc)


@celery_app.task(
    name="process_llm_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_llm_task(self, content_id: int):
    """
    LLM 작업 처리 Celery 태스크.
    
    기존 RQ의 process_llm_job을 재사용합니다.
    """
    try:
        logger.info(
            "[Celery LLM] Starting task: content_id=%s, task_id=%s",
            content_id,
            self.request.id,
        )
        
        # 기존 RQ 프로세서 재사용
        process_llm_job(content_id=content_id)
        
        logger.info("[Celery LLM] Task completed: content_id=%s", content_id)
        return {"status": "success", "content_id": content_id}
        
    except Exception as exc:
        error_str = str(exc)
        # 재시도하지 않아야 하는 에러들:
        # 1. 컨텍스트 길이 초과
        # 2. LM Studio 모델 로드 실패 (400 Bad Request, GPU 메모리 부족 등)
        # 3. 모델 초기화 실패
        no_retry_keywords = [
            "context", "token", "overflow",
            "400 bad request", "failed to load model", "gpu", "vram", "memory",
            "failed to initialize", "allocation failed", "outofdevicememory"
        ]
        
        should_retry = not any(keyword in error_str.lower() for keyword in no_retry_keywords)
        
        if not should_retry:
            logger.exception("[Celery LLM] Task failed (no retry): content_id=%s, error=%s", content_id, error_str)
            # 재시도하지 않고 즉시 실패 처리
            return {"status": "failed", "content_id": content_id, "error": error_str}
        
        logger.exception("[Celery LLM] Task failed: content_id=%s", content_id)
        # 재시도 로직
        raise self.retry(exc=exc)

