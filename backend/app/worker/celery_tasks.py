"""Celery 태스크 정의 - ASR 및 LLM 처리."""
import asyncio
import sys

from ..core.logging import logger
from ..db.session import AsyncSessionLocal
from ..repositories.content_repository import ContentRepository
from ..repositories.file_repository import FileRepository
from .celery_app import celery_app
from .distributed_lock import acquire_task_locks
from ..core.config import get_settings

settings = get_settings()


@celery_app.task(
    name="process_asr_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="asr",
)
def process_asr_task(
    self,
    file_id: int,
    storage_key: str,
    original_filename: str,
    model_size: str,
    processing_mode: str,
    num_asr_chunks: int,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    **kwargs
):
    """
    ASR 작업 처리 Celery 태스크.
    
    Args:
        **kwargs: 호환성을 위한 추가 인자 (무시됨)
    """
    # 오디오 파일 길이에 따라 락 TTL 동적 계산
    duration_seconds = _get_file_duration_sync(file_id)
    if duration_seconds > 0:
        # duration_seconds의 0.5배를 락 TTL로 사용 (최소 300초, 최대 7200초)
        lock_ttl = max(300.0, min(duration_seconds * 0.5, 7200.0))
        logger.info(
            "[Celery ASR] Calculated lock TTL: duration={:.1f}s, lock_ttl={:.1f}s",
            duration_seconds,
            lock_ttl
        )
    else:
        # duration을 가져올 수 없으면 기본값 사용 (2시간)
        lock_ttl = 7200.0
        logger.warning(
            "[Celery ASR] Could not get file duration, using default lock TTL: {}s",
            lock_ttl
        )
    
    # 전역 락과 개별 락 획득 (OCR 방식 기준)
    with acquire_task_locks("asr", file_id, self.request.id, lock_ttl) as (global_acquired, individual_acquired):
        if not global_acquired:
            return {"status": "skipped", "file_id": file_id, "reason": "global_lock_failed"}
        if not individual_acquired:
            return {"status": "skipped", "file_id": file_id, "reason": "individual_lock_failed"}
        
        try:
            # Lazy import: API 서버에서는 torch가 없으므로 실행 시점에만 import
            from .processor import process_transcription_job
            
            logger.info(
                "[Celery ASR] Starting task: file_id={}, task_id={}, min_speakers={}, max_speakers={}",
                file_id,
                self.request.id,
                min_speakers,
                max_speakers,
            )
            
            process_transcription_job(
                file_id=file_id,
                storage_key=storage_key,
                original_filename=original_filename,
                model_size=model_size,
                processing_mode=processing_mode,
                num_asr_chunks=num_asr_chunks,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            
            logger.info("[Celery ASR] Task completed: file_id={}", file_id)
            return {"status": "success", "file_id": file_id}
            
        except Exception as exc:
                error_str = str(exc)
                retry_count = self.request.retries
                
                # FileNotFoundError는 파일이 실제로 없을 가능성이 높으므로 재시도하지 않음
                # (재시도해도 같은 에러가 발생할 가능성이 높음)
                if isinstance(exc, FileNotFoundError) or "file not found" in error_str.lower():
                    logger.error(
                        "[Celery ASR] Task failed (no retry - file not found): file_id={}, error={}",
                        file_id,
                        error_str
                    )
                    return {"status": "failed", "file_id": file_id, "error": error_str}
                
                # 에러는 processor에서 이미 상세히 로깅되므로, 여기서는 간단히만 로깅
                # (Celery가 자동으로 traceback을 출력하므로 logger.exception은 사용하지 않음)
                if retry_count < self.max_retries:
                    logger.warning(
                        "[Celery ASR] Task failed (will retry {}/{}): file_id={}, error={}",
                        retry_count + 1,
                        self.max_retries,
                        file_id,
                        error_str
                    )
                else:
                    logger.error("[Celery ASR] Task failed (no more retries): file_id={}, error={}", file_id, error_str)
                # 재시도 로직
                raise self.retry(exc=exc)


from ..core.system_utils import setup_worker_event_loop, cleanup_worker_event_loop, WorkerSessionContext

def _get_file_duration_sync(file_id: int) -> float:
    """
    File의 duration_seconds를 동기적으로 가져옵니다.
    락 TTL 계산을 위해 사용됩니다.
    """
    try:
        # 이벤트 루프 설정 (Windows/Linux 분기 처리는 system_utils 내부에서 수행)
        loop = setup_worker_event_loop()
        
        async def _get_duration():
            # WorkerSessionContext를 사용하여 세션 생성 (OS별 처리 포함)
            async with WorkerSessionContext() as session:
                repo = FileRepository(session)
                file_obj = await repo.get_file(file_id)
                if file_obj and file_obj.transcription:
                    return file_obj.transcription.duration_seconds or 0.0
                return 0.0
        
        duration = loop.run_until_complete(_get_duration())
        
        # 이벤트 루프 정리
        cleanup_worker_event_loop(loop)
        
        return duration
    except Exception as e:
        logger.warning("Failed to get file duration for file_id={}, using default: {}", file_id, e)
        return 0.0


@celery_app.task(
    name="process_llm_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="llm",
)
def process_llm_task(self, file_id: int):
    """
    LLM 작업 처리 Celery 태스크.
    
    """
    # 음성 파일 길이에 따라 락 TTL 동적 계산
    duration_seconds = _get_file_duration_sync(file_id)
    if duration_seconds > 0:
        # duration_seconds의 1/2를 락 TTL로 사용 (최소 5분, 최대 30분)
        lock_ttl = max(300.0, min(duration_seconds / 2, 1800.0))
        logger.info(
            "[Celery LLM] Calculated lock TTL: duration={:.1f}s, lock_ttl={:.1f}s",
            duration_seconds,
            lock_ttl
        )
    else:
        # duration을 가져올 수 없으면 기본값 사용 (30분)
        lock_ttl = 1800.0
        logger.warning(
            "[Celery LLM] Could not get file duration, using default lock TTL: {}s",
            lock_ttl
        )
    
    # 전역 락과 개별 락 획득 (OCR 방식 기준)
    with acquire_task_locks("llm", file_id, self.request.id, lock_ttl) as (global_acquired, individual_acquired):
        if not global_acquired:
            return {"status": "skipped", "file_id": file_id, "reason": "global_lock_failed"}
        if not individual_acquired:
            return {"status": "skipped", "file_id": file_id, "reason": "individual_lock_failed"}
        
        try:
            # Lazy import: API 서버에서는 torch가 없으므로 실행 시점에만 import
            from .llm_processor import process_llm_job
            
            logger.info(
                "[Celery LLM] Starting task: file_id={}, task_id={}",
                file_id,
                self.request.id,
            )
            
            process_llm_job(file_id=file_id)
            
            logger.info("[Celery LLM] Task completed: file_id={}", file_id)
            return {"status": "success", "file_id": file_id}
            
        except Exception as exc:
                error_str = str(exc)
                # 재시도하지 않아야 하는 에러들:
                # 1. 컨텍스트 길이 초과
                # 2. LM Studio 모델 로드 실패 (400 Bad Request, GPU 메모리 부족 등)
                # 3. 모델 초기화 실패
                # 4. 존재하지 않는 파일 (삭제된 파일 등)
                no_retry_keywords = [
                    "context", "token", "overflow",
                    "400 bad request", "failed to load model", "gpu", "vram", "memory",
                    "failed to initialize", "allocation failed", "outofdevicememory",
                    "file", "not found"  # 존재하지 않는 파일은 재시도하지 않음
                ]
                
                should_retry = not any(keyword in error_str.lower() for keyword in no_retry_keywords)
                
                if not should_retry:
                    logger.exception("[Celery LLM] Task failed (no retry): file_id={}, error={}", file_id, error_str)
                    # 재시도하지 않고 즉시 실패 처리
                    return {"status": "failed", "file_id": file_id, "error": error_str}
                
                logger.exception("[Celery LLM] Task failed: file_id={}", file_id)
                # 재시도 로직
                raise self.retry(exc=exc)


@celery_app.task(
    name="process_ocr_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="ocr",
)
def process_ocr_task(
    self,
    file_id: int,
    storage_key: str,
    original_filename: str,
    ocr_mode: str = "basic",
    **kwargs
):
    """
    OCR 작업 처리 Celery 태스크.
    
    Args:
        **kwargs: 호환성을 위한 추가 인자 (무시됨)
    """
    # OCR 작업은 문서 크기에 따라 다르지만, 기본적으로 5분 TTL 사용 (워커 상태 확인과 함께 사용)
    lock_ttl = 300.0  # 5분 (워커 상태 확인 실패 시 안전장치)
    
    # 전역 락과 개별 락 획득 (OCR 방식 기준 - 공통 함수 사용)
    with acquire_task_locks("ocr", file_id, self.request.id, lock_ttl) as (global_acquired, individual_acquired):
        if not global_acquired:
            return {"status": "skipped", "file_id": file_id, "reason": "global_lock_failed"}
        if not individual_acquired:
            return {"status": "skipped", "file_id": file_id, "reason": "individual_lock_failed"}
        
        try:
            # Lazy import: API 서버에서는 torch가 없으므로 실행 시점에만 import
            from .ocr_processor import process_ocr_job
            
            logger.info(
                "[Celery OCR] Starting task: file_id={}, task_id={}, ocr_mode={}",
                file_id,
                self.request.id,
                ocr_mode,
            )
            
            process_ocr_job(
                file_id=file_id,
                storage_key=storage_key,
                original_filename=original_filename,
                ocr_mode=ocr_mode,
            )
            
            logger.info("[Celery OCR] Task completed: file_id={}", file_id)
            return {"status": "success", "file_id": file_id}
            
        except Exception as exc:
                error_str = str(exc)
                retry_count = self.request.retries
                
                # FileNotFoundError는 파일이 실제로 없을 가능성이 높으므로 재시도하지 않음
                if isinstance(exc, FileNotFoundError) or "file not found" in error_str.lower():
                    logger.error(
                        "[Celery OCR] Task failed (no retry - file not found): file_id={}, error={}",
                        file_id,
                        error_str
                    )
                    return {"status": "failed", "file_id": file_id, "error": error_str}
                
                if retry_count < self.max_retries:
                    logger.warning(
                        "[Celery OCR] Task failed (will retry {}/{}): file_id={}, error={}",
                        retry_count + 1,
                        self.max_retries,
                        file_id,
                        error_str
                    )
                else:
                    logger.error("[Celery OCR] Task failed (no more retries): file_id={}, error={}", file_id, error_str)
                # 재시도 로직
                raise self.retry(exc=exc)

