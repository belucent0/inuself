"""OCR 처리 워커 - 문서 파일 OCR 처리."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import download_file
from ..db.models import FileStatus
from ..db.session import AsyncSessionLocal
from ..repositories.file_repository import FileRepository
from ..repositories.document_repository import DocumentRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from ..services.ocr_service import OcrService


settings = get_settings()


def process_ocr_job(
    *,
    file_id: int,
    storage_key: str,
    original_filename: str,
) -> None:
    """Celery 워커가 호출하는 OCR 작업 진입점."""
    logger.info("[OCR] ========================================")
    logger.info(f"[OCR] OCR job started: file_id={file_id}")
    logger.info(f"[OCR] File: {original_filename}")
    logger.info(f"[OCR] Storage key: {storage_key}")
    logger.info("[OCR] ========================================")
    
    # Windows에서는 매 작업마다 새로운 이벤트 루프를 생성
    if sys.platform == "win32":
        try:
            existing_loop = asyncio.get_event_loop()
            if existing_loop and not existing_loop.is_closed():
                try:
                    pending = asyncio.all_tasks(existing_loop)
                    if pending:
                        existing_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                try:
                    existing_loop.close()
                except Exception:
                    pass
        except RuntimeError:
            pass
        
        policy = asyncio.get_event_loop_policy()
        if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        if isinstance(loop, asyncio.ProactorEventLoop):
            try:
                async def _init_proactor():
                    pass
                loop.run_until_complete(_init_proactor())
            except Exception as exc:
                logger.warning("Failed to initialize Proactor, recreating loop: {}", exc)
                try:
                    loop.close()
                except Exception:
                    pass
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    async def _init_proactor_retry():
                        pass
                    loop.run_until_complete(_init_proactor_retry())
                except Exception:
                    pass
    else:
        loop = _ensure_worker_loop()
    
    try:
        loop.run_until_complete(
            _process_ocr_job(
                file_id=file_id,
                storage_key=storage_key,
                original_filename=original_filename,
            )
        )
        logger.info(f"[OCR] OK OCR job completed: file_id={file_id}")
    except Exception as e:
        logger.error(f"[OCR] ERROR OCR job failed: file_id={file_id}, error={e}")
        raise
    finally:
        if sys.platform == "win32":
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    try:
                        loop.run_until_complete(
                            asyncio.wait_for(
                                asyncio.gather(*pending, return_exceptions=True),
                                timeout=5.0
                            )
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Timeout waiting for pending OCR tasks to complete")
                    except Exception as e:
                        logger.error("Error waiting for pending OCR tasks: {}", e)
            except Exception as e:
                logger.error("Error during OCR event loop cleanup: {}", e)
            finally:
                try:
                    if not loop.is_closed():
                        loop.close()
                except Exception as e:
                    logger.error("Error closing OCR event loop: {}", e)
                
                try:
                    asyncio.set_event_loop(None)
                except Exception as e:
                    logger.error("Error unsetting OCR event loop: {}", e)


_worker_loop: asyncio.AbstractEventLoop | None = None


def _ensure_worker_loop() -> asyncio.AbstractEventLoop:
    """asyncpg 연결 재사용을 위해 단일 이벤트 루프를 생성/재사용."""
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        if sys.platform == "win32":
            policy = asyncio.get_event_loop_policy()
            if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    else:
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop


async def _process_ocr_job(
    *,
    file_id: int,
    storage_key: str,
    original_filename: str,
) -> None:
    """OCR 작업 처리 함수."""
    logger.info("[OCR] Processing OCR job file_id={} key={}", file_id, storage_key)
    logger.info(f"[OCR] [1/4] Starting OCR job: file_id={file_id}, file={original_filename}")
    
    # 파일 다운로드
    logger.info(f"[OCR] [2/4] Downloading file: {storage_key}")
    
    temp_root = settings.upload_dir
    temp_root.mkdir(parents=True, exist_ok=True)
    file_extension = Path(original_filename).suffix
    temp_path = temp_root / f"ocr_{file_id}_{uuid4().hex}{file_extension}"
    
    try:
        download_file(storage_key, destination=temp_path)
        logger.info(f"[OCR] [3/4] File download completed: {temp_path}")
        
        # 상태를 OCR_PROCESSING으로 변경
        logger.info("[OCR] [4/4] Updating status to OCR_PROCESSING and starting OCR...")
        
        current_engine = None
        if sys.platform == "win32":
            current_engine = create_async_engine(
                settings.postgres_dsn,
                echo=settings.debug,
                future=True,
                pool_pre_ping=True,
                pool_recycle=3600,
            )
            CurrentAsyncSessionLocal = async_sessionmaker(
                current_engine,
                expire_on_commit=False,
            )
            session = CurrentAsyncSessionLocal()
        else:
            session = AsyncSessionLocal()
        
        try:
            file_repo = FileRepository(session)
            file_obj = await file_repo.get_file(file_id)
            if not file_obj:
                logger.warning("File not found: file_id={}, skipping status update and log", file_id)
                return
            
            await file_repo.update_file_status(file_id, FileStatus.OCR_PROCESSING)
            await file_repo.add_log(
                file_id=file_id,
                log={"event": "ocr_started", "file": original_filename},
                message="OCR processing started",
            )
            await session.commit()
        finally:
            await session.close()
            if current_engine:
                try:
                    await asyncio.wait_for(current_engine.dispose(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("Timeout disposing database engine after status update")
                except Exception as e:
                    logger.error("Error disposing database engine: {}", e)
        
        # OCR 처리
        logger.info("[OCR] Starting OCR processing...")
        ocr_service = OcrService()
        ocr_result = ocr_service.process_document(temp_path)
        
        logger.info("[OCR] OCR processing completed!")
        logger.info(f"[OCR] - Extracted text length: {len(ocr_result['ocr_text'])} chars")
        logger.info(f"[OCR] - Page count: {ocr_result['page_count']}")
        
    except Exception as exc:
        logger.error(f"[OCR] ERROR Error occurred: {exc}")
        logger.error("[OCR] Updating status to OCR_FAILED...")
        logger.exception("OCR processing failed for file_id={}", file_id)
        
        error_engine = None
        if sys.platform == "win32":
            error_engine = create_async_engine(
                settings.postgres_dsn,
                echo=settings.debug,
                future=True,
            )
            ErrorAsyncSessionLocal = async_sessionmaker(
                error_engine,
                expire_on_commit=False,
            )
            session = ErrorAsyncSessionLocal()
        else:
            session = AsyncSessionLocal()
        try:
            file_repo = FileRepository(session)
            await file_repo.update_file_status(file_id, FileStatus.OCR_FAILED)
            await file_repo.add_log(
                file_id=file_id,
                log={"event": "ocr_error", "details": str(exc)},
                message="OCR processing failed",
            )
            await session.commit()
        finally:
            await session.close()
            if error_engine:
                try:
                    await asyncio.wait_for(error_engine.dispose(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("Timeout disposing error engine")
                except Exception as e:
                    logger.error("Error disposing error engine: {}", e)
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except TypeError:
            if temp_path.exists():
                temp_path.unlink()
    
    # OCR 결과 저장
    logger.info("[OCR] Saving OCR results to database...")
    result_engine = None
    if sys.platform == "win32":
        result_engine = create_async_engine(
            settings.postgres_dsn,
            echo=settings.debug,
            future=True,
        )
        ResultAsyncSessionLocal = async_sessionmaker(
            result_engine,
            expire_on_commit=False,
        )
        session = ResultAsyncSessionLocal()
    else:
        session = AsyncSessionLocal()
    try:
        file_repo = FileRepository(session)
        document_repo = DocumentRepository(session)
        
        await document_repo.update_document(
            file_id=file_id,
            ocr_text=ocr_result["ocr_text"],
            page_count=ocr_result["page_count"],
            ocr_metadata=ocr_result["ocr_metadata"],
        )
        await file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
        
        await file_repo.add_log(
            file_id,
            log={
                "event": "ocr_completed",
                "text_length": len(ocr_result["ocr_text"]),
                "page_count": ocr_result["page_count"],
            },
            message=f"OCR processing completed ({ocr_result['page_count']} pages)",
        )
        await session.commit()
    finally:
        await session.close()
        if result_engine:
            try:
                await asyncio.wait_for(result_engine.dispose(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout disposing result engine")
            except Exception as e:
                logger.error("Error disposing result engine: {}", e)
    
    logger.info("[OCR] OK Database save completed, starting LLM summarization")
    logger.info("OCR processing completed for file_id={}", file_id)
    
    # LLM 요약 처리 (async 함수 직접 호출)
    try:
        from .llm_processor import _process_job
        
        logger.info(f"[OCR] >> Starting LLM summarization for file_id={file_id}")
        await _process_job(content_id=file_id)  # 하위 호환성을 위해 content_id로 전달
        logger.info(f"[OCR] >> LLM summarization completed for file_id={file_id}")
        logger.info("LLM summarization completed for file_id={}", file_id)
    except Exception as exc:
        logger.error(f"[OCR] ERROR Failed to process LLM summarization: {exc}")
        logger.exception("Failed to process LLM summarization for file_id={}", file_id)
        raise

