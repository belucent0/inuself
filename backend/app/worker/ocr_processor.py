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
from ..services.ocr_service import OcrService


from ..core.system_utils import setup_worker_event_loop, cleanup_worker_event_loop, WorkerSessionContext

settings = get_settings()


def process_ocr_job(
    *,
    file_id: int,
    storage_key: str,
    original_filename: str,
    ocr_mode: str = "basic",
) -> None:
    """Celery 워커가 호출하는 OCR 작업 진입점."""
    logger.info("[OCR] ========================================")
    logger.info(f"[OCR] OCR job started: file_id={file_id}, ocr_mode={ocr_mode}")
    logger.info(f"[OCR] File: {original_filename}")
    logger.info(f"[OCR] Storage key: {storage_key}")
    logger.info("[OCR] ========================================")
    
    # 이벤트 루프 설정 (Windows/Linux 분기 처리는 system_utils 내부에서 수행)
    loop = setup_worker_event_loop()
    
    try:
        loop.run_until_complete(
            _process_ocr_job(
                file_id=file_id,
                storage_key=storage_key,
                original_filename=original_filename,
                ocr_mode=ocr_mode,
            )
        )
        logger.info(f"[OCR] OK OCR job completed: file_id={file_id}")
    except Exception as e:
        logger.error(f"[OCR] ERROR OCR job failed: file_id={file_id}, error={e}")
        raise
    finally:
        # 이벤트 루프 정리
        cleanup_worker_event_loop(loop)


_worker_loop: asyncio.AbstractEventLoop | None = None



from .event_publisher import ProgressReporter

async def _process_ocr_job(
    *,
    file_id: int,
    storage_key: str,
    original_filename: str,
    ocr_mode: str = "basic",
) -> None:
    """OCR 작업 처리 함수."""
    reporter = ProgressReporter(file_id)
    
    logger.info("[OCR] Processing OCR job file_id={} key={} mode={}", file_id, storage_key, ocr_mode)
    logger.info(f"[OCR] [1/4] Starting OCR job: file_id={file_id}, file={original_filename}, ocr_mode={ocr_mode}")
    
    # 이벤트: 시작
    reporter.processing("ocr_start", 0.0, "OCR 작업 시작")
    
    # 파일 다운로드
    logger.info(f"[OCR] [2/4] Downloading file: {storage_key}")
    reporter.processing("download_start", 10.0, "파일 다운로드 중...")
    
    temp_root = settings.upload_dir
    temp_root.mkdir(parents=True, exist_ok=True)
    file_extension = Path(original_filename).suffix
    temp_path = temp_root / f"ocr_{file_id}_{uuid4().hex}{file_extension}"
    
    try:
        download_file(storage_key, destination=temp_path)
        logger.info(f"[OCR] [3/4] File download completed: {temp_path}")
        reporter.processing("download_complete", 20.0, "파일 다운로드 완료")
        
        # 상태를 OCR_PROCESSING으로 변경
        logger.info("[OCR] [4/4] Updating status to OCR_PROCESSING and starting OCR...")
        reporter.processing("ocr_init", 25.0, "문서 분석 준비 중...")
        
        # WorkerSessionContext를 사용하여 세션 생성 (OS별 처리 포함)
        async with WorkerSessionContext() as session:
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

        
        # OCR 처리
        logger.info("[OCR] Starting OCR processing...")
        reporter.processing("ocr_running", 30.0, "문서 분석 및 텍스트 추출 중...")
        
        # txt 파일인 경우 OCR을 건너뛰고 파일 내용을 직접 읽기
        if temp_path.suffix.lower() == '.txt':
            logger.info("[OCR] Text file detected, skipping OCR and reading file directly...")
            reporter.processing("ocr_running", 40.0, "텍스트 파일 읽는 중...")
            try:
                # UTF-8로 먼저 시도
                used_encoding = "utf-8"
                try:
                    with open(temp_path, 'r', encoding='utf-8') as f:
                        ocr_text = f.read()
                except UnicodeDecodeError:
                    # UTF-8 실패 시 CP949 (한글 윈도우 기본 인코딩) 시도
                    logger.warning("[OCR] UTF-8 decoding failed, trying CP949...")
                    used_encoding = "cp949"
                    with open(temp_path, 'r', encoding='cp949') as f:
                        ocr_text = f.read()
                
                ocr_result = {
                    "ocr_text": ocr_text,
                    "page_count": 1,  # txt 파일은 페이지 개념이 없지만 1로 설정
                    "ocr_metadata": {
                        "file_path": str(temp_path),
                        "file_type": ".txt",
                        "page_count": 1,
                        "processing_method": "direct_read",
                        "encoding": used_encoding
                    }
                }
                logger.info("[OCR] Text file read completed!")
                logger.info(f"[OCR] - Extracted text length: {len(ocr_result['ocr_text'])} chars")
            except Exception as e:
                logger.error(f"[OCR] Failed to read text file: {e}")
                raise
        else:
            # 이미지나 PDF 파일인 경우 OCR 서비스 사용
            ocr_service = OcrService()
            ocr_result = ocr_service.process_document(temp_path, ocr_mode=ocr_mode)
            
            logger.info("[OCR] OCR processing completed!")
            logger.info(f"[OCR] - Extracted text length: {len(ocr_result['ocr_text'])} chars")
            logger.info(f"[OCR] - Page count: {ocr_result['page_count']}")
            logger.info(f"[OCR] - HTML content: {'Yes' if ocr_result.get('html_content') else 'No'}")
        
        reporter.processing("ocr_complete", 80.0, "문서 분석 완료, 결과 저장 중...")
        
    except Exception as exc:
        logger.error(f"[OCR] ERROR Error occurred: {exc}")
        logger.error("[OCR] Updating status to OCR_FAILED...")
        logger.exception("OCR processing failed for file_id={}", file_id)
        
        # 이벤트: 실패
        reporter.fail(f"OCR 처리 실패: {str(exc)}")
        
        # 이벤트: 실패
        reporter.fail(f"OCR 처리 실패: {str(exc)}")
        
        # WorkerSessionContext를 사용하여 세션 생성 (OS별 처리 포함)
        async with WorkerSessionContext() as session:
            file_repo = FileRepository(session)
            await file_repo.update_file_status(file_id, FileStatus.OCR_FAILED)
            await file_repo.add_log(
                file_id=file_id,
                log={"event": "ocr_error", "details": str(exc)},
                message="OCR processing failed",
            )
            await session.commit()
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except TypeError:
            if temp_path.exists():
                temp_path.unlink()
    
    # OCR 결과 저장
    logger.info("[OCR] Saving OCR results to database...")
    # WorkerSessionContext를 사용하여 세션 생성 (OS별 처리 포함)
    async with WorkerSessionContext() as session:
        file_repo = FileRepository(session)
        document_repo = DocumentRepository(session)
        
        # document가 없으면 생성, 있으면 업데이트
        existing_document = await document_repo.get_by_file_id(file_id)
        if existing_document:
            await document_repo.update_document(
                file_id=file_id,
                ocr_text=ocr_result["ocr_text"],
                page_count=ocr_result["page_count"],
                ocr_metadata=ocr_result["ocr_metadata"],
                html_content=ocr_result.get("html_content"),
            )
        else:
            # document가 없는 경우 생성 (txt 파일 등에서 발생할 수 있음)
            logger.warning("[OCR] Document not found, creating new document for file_id={}", file_id)
            await document_repo.create_document(
                file_id=file_id,
                ocr_text=ocr_result["ocr_text"],
                page_count=ocr_result["page_count"],
                ocr_metadata=ocr_result["ocr_metadata"],
                html_content=ocr_result.get("html_content"),
            )
        await file_repo.update_file_status(file_id, FileStatus.SUMMARY_QUEUED)
        
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
        
        # 이벤트: 요약 대기 (OCR 완료)
        # 요약 워커가 시작되기 전까지의 상태 보고
        reporter.summary_queued(
            step="ocr_complete", 
            progress=100.0, 
            message="문서 분석 완료 (요약 대기 중)"
        )
    
    logger.info("[OCR] OK Database save completed, ready to enqueue summary job")
    logger.info("OCR processing completed for file_id={}", file_id)
    
    # LLM 요약 작업 큐잉 (Task Queue Adapter 사용)
    try:
        from .task_queue_adapter import get_task_queue
        
        task_queue = get_task_queue()
        job_id = task_queue.enqueue_llm_job(file_id=file_id)
        logger.info(f"[OCR] >> Summary job enqueued to LLM queue (file_id={file_id}, job_id={job_id})")
        logger.info("LLM job enqueued for file_id={}, job_id={}", file_id, job_id)
    except Exception as exc:
        logger.error(f"[OCR] ERROR Failed to enqueue LLM job: {exc}")
        logger.exception("Failed to enqueue LLM job for file_id={}", file_id)

