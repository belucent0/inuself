"""Celery 태스크 정의 - ASR 및 LLM 처리."""
import asyncio
import sys

from ..core.logging import logger
from ..db.session import AsyncSessionLocal
from ..repositories.content_repository import ContentRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from .celery_app import celery_app
from .distributed_lock import acquire_lock  # LLM 워커용
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
    content_id: int,
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
    
    기존 RQ의 process_transcription_job을 재사용합니다.
    
    Args:
        **kwargs: 호환성을 위한 추가 인자 (무시됨)
    
    Note:
        ASR 락과 화자분리 락은 pipeline.py 내부에서 관리됩니다.
    """
    try:
        # Lazy import: API 서버에서는 torch가 없으므로 실행 시점에만 import
        from .processor import process_transcription_job
        
        logger.info(
            "[Celery ASR] Starting task: content_id={}, task_id={}, min_speakers={}, max_speakers={}",
            content_id,
            self.request.id,
            min_speakers,
            max_speakers,
        )
        
        # 기존 RQ 프로세서 재사용
        process_transcription_job(
            content_id=content_id,
            storage_key=storage_key,
            original_filename=original_filename,
            model_size=model_size,
            processing_mode=processing_mode,
            num_asr_chunks=num_asr_chunks,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        
        logger.info("[Celery ASR] Task completed: content_id={}", content_id)
        return {"status": "success", "content_id": content_id}
        
    except Exception as exc:
        error_str = str(exc)
        retry_count = self.request.retries
        
        # FileNotFoundError는 파일이 실제로 없을 가능성이 높으므로 재시도하지 않음
        # (재시도해도 같은 에러가 발생할 가능성이 높음)
        if isinstance(exc, FileNotFoundError) or "file not found" in error_str.lower():
            logger.error(
                "[Celery ASR] Task failed (no retry - file not found): content_id={}, error={}",
                content_id,
                error_str
            )
            return {"status": "failed", "content_id": content_id, "error": error_str}
        
        # 에러는 processor에서 이미 상세히 로깅되므로, 여기서는 간단히만 로깅
        # (Celery가 자동으로 traceback을 출력하므로 logger.exception은 사용하지 않음)
        if retry_count < self.max_retries:
            logger.warning(
                "[Celery ASR] Task failed (will retry {}/{}): content_id={}, error={}",
                retry_count + 1,
                self.max_retries,
                content_id,
                error_str
            )
        else:
            logger.error("[Celery ASR] Task failed (no more retries): content_id={}, error={}", content_id, error_str)
        # 재시도 로직
        raise self.retry(exc=exc)


def _get_content_duration_sync(content_id: int) -> float:
    """
    Content의 duration_seconds를 동기적으로 가져옵니다.
    락 TTL 계산을 위해 사용됩니다.
    """
    try:
        # Windows에서는 새로운 이벤트 루프 생성
        if sys.platform == "win32":
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        else:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        
        async def _get_duration():
            # Windows에서는 각 작업마다 새로운 엔진 생성
            current_engine = None
            if sys.platform == "win32":
                current_engine = create_async_engine(
                    settings.postgres_dsn,
                    echo=settings.debug,
                    future=True,
                )
                CurrentAsyncSessionLocal = async_sessionmaker(
                    current_engine,
                    expire_on_commit=False,
                )
                session = CurrentAsyncSessionLocal()
            else:
                session = AsyncSessionLocal()
            
            try:
                repo = ContentRepository(session)
                content = await repo.get_content(content_id)
                if content:
                    return content.duration_seconds or 0.0
                return 0.0
            finally:
                await session.close()
                if current_engine:
                    try:
                        await asyncio.wait_for(current_engine.dispose(), timeout=5.0)
                    except Exception:
                        pass
        
        duration = loop.run_until_complete(_get_duration())
        
        # Windows에서는 루프 정리
        if sys.platform == "win32":
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            asyncio.set_event_loop(None)
        
        return duration
    except Exception as e:
        logger.warning("Failed to get content duration for content_id={}, using default: {}", content_id, e)
        return 0.0


@celery_app.task(
    name="process_llm_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="llm",
)
def process_llm_task(self, content_id: int):
    """
    LLM 작업 처리 Celery 태스크.
    
    기존 RQ의 process_llm_job을 재사용합니다.
    """
    # 분산 락을 사용하여 LLM 워커가 한 번에 하나의 작업만 처리하도록 제한
    lock_key = "lock:llm:global"
    
    # 음성 파일 길이에 따라 락 TTL 동적 계산
    duration_seconds = _get_content_duration_sync(content_id)
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
            "[Celery LLM] Could not get content duration, using default lock TTL: {}s",
            lock_ttl
        )
    
    with acquire_lock(lock_key, timeout=lock_ttl, blocking_timeout=0.0) as acquired:
        if not acquired:
            logger.warning(
                "[Celery LLM] Task skipped (LLM worker is busy): content_id={}, task_id={}",
                content_id,
                self.request.id,
            )
            return {"status": "skipped", "content_id": content_id, "reason": "llm_worker_busy"}
        
        try:
            # Lazy import: API 서버에서는 torch가 없으므로 실행 시점에만 import
            from .llm_processor import process_llm_job
            
            logger.info(
                "[Celery LLM] Starting task: content_id={}, task_id={}",
                content_id,
                self.request.id,
            )
            
            # 기존 RQ 프로세서 재사용
            process_llm_job(content_id=content_id)
            
            logger.info("[Celery LLM] Task completed: content_id={}", content_id)
            return {"status": "success", "content_id": content_id}
            
        except Exception as exc:
            error_str = str(exc)
            # 재시도하지 않아야 하는 에러들:
            # 1. 컨텍스트 길이 초과
            # 2. LM Studio 모델 로드 실패 (400 Bad Request, GPU 메모리 부족 등)
            # 3. 모델 초기화 실패
            # 4. 존재하지 않는 콘텐츠 (삭제된 콘텐츠 등)
            no_retry_keywords = [
                "context", "token", "overflow",
                "400 bad request", "failed to load model", "gpu", "vram", "memory",
                "failed to initialize", "allocation failed", "outofdevicememory",
                "content", "not found"  # 존재하지 않는 콘텐츠는 재시도하지 않음
            ]
            
            should_retry = not any(keyword in error_str.lower() for keyword in no_retry_keywords)
            
            if not should_retry:
                logger.exception("[Celery LLM] Task failed (no retry): content_id={}, error={}", content_id, error_str)
                # 재시도하지 않고 즉시 실패 처리
                return {"status": "failed", "content_id": content_id, "error": error_str}
            
            logger.exception("[Celery LLM] Task failed: content_id={}", content_id)
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
    **kwargs
):
    """
    OCR 작업 처리 Celery 태스크.
    
    Args:
        **kwargs: 호환성을 위한 추가 인자 (무시됨)
    """
    try:
        # Lazy import: API 서버에서는 torch가 없으므로 실행 시점에만 import
        from .ocr_processor import process_ocr_job
        
        logger.info(
            "[Celery OCR] Starting task: file_id={}, task_id={}",
            file_id,
            self.request.id,
        )
        
        process_ocr_job(
            file_id=file_id,
            storage_key=storage_key,
            original_filename=original_filename,
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

