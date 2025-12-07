"""파일 서비스 - 파일 타입 감지 및 업로드/큐잉 처리."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import delete_file, upload_fileobj, get_public_media_url
from ..db.models import FileStatus, ContentType
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from ..worker.celery_queue import cancel_celery_tasks_by_content_ids


class FileService:
    """파일 관련 비즈니스 로직."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.transcription_repo = TranscriptionRepository(session)
        self.document_repo = DocumentRepository(session)
        self.settings = get_settings()

    async def list_files(self, page: int = 1, page_size: int = 10):
        """페이지네이션을 포함한 파일 목록 조회."""
        from ..schemas.content import ContentListResponse, ContentListItem
        
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10
        
        offset = (page - 1) * page_size
        total = await self.file_repo.count_files()
        rows = await self.file_repo.list_files(limit=page_size, offset=offset)
        
        items = []
        for row in rows:
            # File 모델을 ContentListItem으로 변환
            item_dict = {
                "id": row.id,
                "filename": row.filename,
                "object_key": row.object_key,
                "file_type": row.content_type.value,  # "AUDIO" 또는 "DOCUMENT"
                "content_type": row.content_type,  # ContentType enum
                "status": row.status,
                "summary_md": row.summary_md,
                "title": row.title,
                "created_at": row.created_at,
                "updated_at": getattr(row, 'updated_at', None),  # updated_at이 없을 수 있음
                "speakers": [],  # 기본값
                "duration_seconds": 0.0,  # 기본값
            }
            
            # 오디오 파일인 경우 transcription 정보 추가
            if row.content_type == ContentType.AUDIO and row.transcription:
                item_dict["duration_seconds"] = row.transcription.duration_seconds
                item_dict["speakers"] = row.transcription.speakers
                # transcription_content 추가 (dict로 변환)
                item_dict["transcription_content"] = {
                    "id": row.transcription.id,
                    "file_id": row.transcription.file_id,
                    "speakers": row.transcription.speakers,
                    "duration_seconds": row.transcription.duration_seconds,
                    "transcription": row.transcription.transcription,
                }
            # 문서 파일인 경우 document 정보 추가
            elif row.content_type == ContentType.DOCUMENT and row.document:
                item_dict["document_content"] = {
                    "id": row.document.id,
                    "file_id": row.document.file_id,
                    "ocr_text": row.document.ocr_text,
                    "page_count": row.document.page_count,
                    "ocr_metadata": row.document.ocr_metadata,
                }
            
            item = ContentListItem.model_validate(item_dict)
            # media_url 추가
            item.media_url = get_public_media_url(row.object_key)
            items.append(item)
        
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        
        return ContentListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    async def get_file(self, file_id: int):
        """파일 상세 조회."""
        from ..schemas.content import ContentDetail
        
        file_obj = await self.file_repo.get_file(file_id)
        if not file_obj:
            raise ValueError("File not found")
        
        # 관계 로드
        await self.session.refresh(file_obj, ["transcription", "document", "logs", "llm_logs"])
        
        # ContentDetail로 변환
        detail_dict = {
            "id": file_obj.id,
            "filename": file_obj.filename,
            "object_key": file_obj.object_key,
            "file_type": file_obj.content_type.value,
            "content_type": file_obj.content_type,
            "status": file_obj.status,
            "summary_md": file_obj.summary_md,
            "title": file_obj.title,
            "created_at": file_obj.created_at,
            "updated_at": getattr(file_obj, 'updated_at', None),
            "speakers": [],
            "duration_seconds": 0.0,
            "transcription": {},
        }
        
        # 오디오 파일인 경우 transcription 정보 추가
        if file_obj.content_type == ContentType.AUDIO and file_obj.transcription:
            detail_dict["duration_seconds"] = file_obj.transcription.duration_seconds
            detail_dict["speakers"] = file_obj.transcription.speakers
            detail_dict["transcription"] = file_obj.transcription.transcription
            detail_dict["transcription_content"] = {
                "id": file_obj.transcription.id,
                "file_id": file_obj.transcription.file_id,
                "speakers": file_obj.transcription.speakers,
                "duration_seconds": file_obj.transcription.duration_seconds,
                "transcription": file_obj.transcription.transcription,
            }
        # 문서 파일인 경우 document 정보 추가
        if file_obj.content_type == ContentType.DOCUMENT and file_obj.document:
            detail_dict["document_content"] = {
                "id": file_obj.document.id,
                "file_id": file_obj.document.file_id,
                "ocr_text": file_obj.document.ocr_text,
                "page_count": file_obj.document.page_count,
                "ocr_metadata": file_obj.document.ocr_metadata,
            }
        elif file_obj.content_type == ContentType.DOCUMENT:
            detail_dict["document_content"] = {
                "id": file_obj.document.id,
                "file_id": file_obj.document.file_id,
                "ocr_text": file_obj.document.ocr_text,
                "page_count": file_obj.document.page_count,
                "ocr_metadata": file_obj.document.ocr_metadata,
            }
        
        # 로그 추가
        detail_dict["logs"] = [
            {
                "id": log.id,
                "file_id": log.file_id,
                "message": log.message,
                "log": log.log,
                "created_at": log.created_at,
            }
            for log in file_obj.logs
        ]
        detail_dict["llm_logs"] = [
            {
                "id": log.id,
                "file_id": log.file_id,
                "message": log.message,
                "log": log.log,
                "created_at": log.created_at,
            }
            for log in file_obj.llm_logs
        ]
        
        detail = ContentDetail.model_validate(detail_dict)
        # media_url 추가
        detail.media_url = get_public_media_url(file_obj.object_key)
        return detail

    def _detect_content_type(self, filename: str) -> ContentType:
        """파일명으로 콘텐츠 타입 감지."""
        file_path = Path(filename)
        extension = file_path.suffix.lower()
        
        # 오디오 파일 확장자
        audio_extensions = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma", ".mp4", ".avi", ".mkv", ".mov", ".webm"}
        # 문서 파일 확장자
        document_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".webp", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
        
        if extension in audio_extensions:
            return ContentType.AUDIO
        elif extension in document_extensions:
            return ContentType.DOCUMENT
        else:
            # 기본값은 문서로 처리 (확장 가능성 고려)
            logger.warning(f"Unknown file extension: {extension}, treating as DOCUMENT")
            return ContentType.DOCUMENT

    def _build_object_key(self, filename: str) -> str:
        """안전한 파일명으로 object_key 생성 (비ASCII 문자 제거)."""
        original_path = Path(filename)
        extension = original_path.suffix  # .mp4, .wav, .pdf 등
        # UUID + 확장자로 안전한 파일명 생성 (비ASCII 문자 문제 해결)
        safe_filename = f"{uuid4().hex}{extension}"
        return f"{self.settings.s3_prefix}/{safe_filename}"

    async def _upload_file_content_to_storage(self, file_content: bytes, object_key: str) -> None:
        """파일 내용을 스토리지에 업로드."""
        from io import BytesIO
        file_obj = BytesIO(file_content)
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: upload_fileobj(file_obj, key=object_key))

    async def upload_and_enqueue(
        self,
        file: UploadFile,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> dict[str, int]:
        """
        파일 업로드 및 큐잉.
        
        Returns:
            {"file_id": int}
        """
        logger.info("[Upload] 파일 업로드 시작: filename=%s", file.filename)
        print(f"[Upload] [1/4] 파일 업로드 시작: {file.filename}")
        
        # 파일 타입 감지
        content_type = self._detect_content_type(file.filename or "")
        logger.info("[Upload] 파일 타입 감지: content_type=%s", content_type.value)
        
        object_key = self._build_object_key(file.filename or "")
        logger.info("[Upload] 스토리지 키 생성: object_key=%s", object_key)
        
        # 파일 내용 읽기 및 크기 확인
        file_content = await file.read()
        file_size = len(file_content)
        file_size_mb = file_size / (1024 * 1024)
        logger.info("[Upload] 파일 크기: %d bytes (%.2f MB)", file_size, file_size_mb)
        print(f"[Upload] [2/4] 파일 크기: {file_size:,} bytes ({file_size_mb:.2f} MB)")
        
        # 스토리지 업로드
        logger.info("[Upload] 스토리지 업로드 시작: object_key=%s", object_key)
        print(f"[Upload] [3/4] 스토리지 업로드 중: {object_key}")
        await self._upload_file_content_to_storage(file_content, object_key)
        logger.info("[Upload] 스토리지 업로드 완료: object_key=%s", object_key)
        print(f"[Upload] OK 스토리지 업로드 완료: {object_key}")
        
        # 파일 닫기
        await file.close()
        
        # DB에 파일 생성
        logger.info("[Upload] DB에 파일 생성 시작: filename=%s", file.filename)
        file_obj = await self.file_repo.create_file(
            filename=file.filename or "",
            object_key=object_key,
            content_type=content_type,
            status=FileStatus.QUEUED,
        )
        await self.session.commit()
        logger.info("[Upload] DB에 파일 생성 완료: file_id=%s, filename=%s", file_obj.id, file.filename)
        print(f"[Upload] OK DB 저장 완료: file_id={file_obj.id}")
        
        # 타입별 처리
        if content_type == ContentType.AUDIO:
            # 오디오: Transcription 생성 + ASR 작업 큐잉
            await self.transcription_repo.create_transcription(
                file_id=file_obj.id,
                speakers=[],
                duration_seconds=0.0,
                transcription={},
            )
            await self.session.commit()
            
            # ASR 작업 큐잉
            logger.info("[Upload] ASR 작업 큐잉: file_id=%s", file_obj.id)
            print(f"[Upload] [4/4] ASR 작업 큐잉 중: file_id={file_obj.id}")
            try:
                loop = asyncio.get_running_loop()
                from functools import partial
                from ..worker.task_queue_adapter import get_task_queue
                
                task_queue = get_task_queue()
                enqueue_func = partial(
                    task_queue.enqueue_asr_job,
                    content_id=file_obj.id,  # 하위 호환성을 위해 content_id로 전달
                    storage_key=object_key,
                    original_filename=file.filename or "",
                    model_size=self.settings.whisper_model_default,
                    processing_mode="case4",
                    num_asr_chunks=self.settings.max_workers,
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                )
                job_id = await loop.run_in_executor(None, enqueue_func)
                logger.info("[Upload] ASR 작업 큐잉 성공: file_id=%s, job_id=%s", file_obj.id, job_id)
                print(f"[Upload] OK ASR 큐 등록 완료: file_id={file_obj.id}, job_id={job_id}")
            except Exception as exc:
                error_msg = f"ASR 작업 큐잉 실패: file_id={file_obj.id}, error={exc}"
                logger.exception("[Upload] %s", error_msg)
                print(f"[Upload] ERROR {error_msg}")
        
        elif content_type == ContentType.DOCUMENT:
            # 문서: Document 생성 + OCR 작업 큐잉
            await self.document_repo.create_document(
                file_id=file_obj.id,
                ocr_text="",
                page_count=0,
                ocr_metadata={},
            )
            await self.session.commit()
            
            # OCR 작업 큐잉
            logger.info("[Upload] OCR 작업 큐잉: file_id=%s", file_obj.id)
            print(f"[Upload] [4/4] OCR 작업 큐잉 중: file_id={file_obj.id}")
            try:
                loop = asyncio.get_running_loop()
                from functools import partial
                from ..worker.task_queue_adapter import get_task_queue
                
                task_queue = get_task_queue()
                enqueue_func = partial(
                    task_queue.enqueue_ocr_job,
                    file_id=file_obj.id,
                    storage_key=object_key,
                    original_filename=file.filename or "",
                )
                job_id = await loop.run_in_executor(None, enqueue_func)
                logger.info("[Upload] OCR 작업 큐잉 성공: file_id=%s, job_id=%s", file_obj.id, job_id)
                print(f"[Upload] OK OCR 큐 등록 완료: file_id={file_obj.id}, job_id={job_id}")
            except Exception as exc:
                error_msg = f"OCR 작업 큐잉 실패: file_id={file_obj.id}, error={exc}"
                logger.exception("[Upload] %s", error_msg)
                print(f"[Upload] ERROR {error_msg}")
        
        logger.info("[Upload] 파일 업로드 전체 프로세스 완료: file_id=%s, filename=%s", file_obj.id, file.filename)
        print(f"[Upload] ========================================")
        print(f"[Upload] 파일 업로드 완료: file_id={file_obj.id}, filename={file.filename}")
        print(f"[Upload] ========================================")
        
        return {"file_id": file_obj.id}

    async def _cleanup_queue_and_storage(self, file_ids: list[int], object_keys: list[str]) -> None:
        """큐 작업 취소와 스토리지 파일 삭제를 일괄 처리."""
        loop = asyncio.get_running_loop()

        if file_ids:
            # Celery 큐 작업 취소 (하위 호환성을 위해 content_id로 전달)
            celery_cancelled = await loop.run_in_executor(
                None, cancel_celery_tasks_by_content_ids, file_ids
            )
            if celery_cancelled:
                logger.info("Cancelled %s Celery tasks for deleted files", celery_cancelled)

        for object_key in object_keys:
            try:
                await loop.run_in_executor(None, delete_file, object_key)
            except Exception as exc:
                logger.warning("Failed to delete file from storage: %s, error: %s", object_key, exc)

    async def delete_queued_files(self) -> int:
        """QUEUED 상태인 모든 파일 삭제 (DB + 스토리지 + 큐)."""
        count, file_ids, object_keys = await self.file_repo.delete_queued_files()
        await self._cleanup_queue_and_storage(file_ids, object_keys)
        await self.session.commit()
        return count

    async def delete_files_by_ids(self, file_ids: list[int]) -> tuple[list[int], list[int]]:
        """
        주어진 ID의 파일을 상태와 무관하게 삭제하고,
        (deleted_ids, skipped_ids) 튜플을 반환한다.
        """
        unique_ids = list(dict.fromkeys(file_ids))
        if not unique_ids:
            return [], []

        deleted_ids, object_keys = await self.file_repo.delete_files_by_ids(unique_ids)
        await self._cleanup_queue_and_storage(deleted_ids, object_keys)
        await self.session.commit()

        deleted_set = set(deleted_ids)
        skipped_ids = [file_id for file_id in unique_ids if file_id not in deleted_set]
        return deleted_ids, skipped_ids

