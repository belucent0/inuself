from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Sequence
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
from ..schemas.content import ContentDetail, ContentListItem, ContentListResponse, SttLogSchema, LlmLogSchema
from ..schemas.file import DocumentSchema, TranscriptionSchema
from ..worker.celery_queue import cancel_celery_tasks_by_content_ids


class FileService:
    """파일 관련 비즈니스 로직."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.transcription_repo = TranscriptionRepository(session)
        self.document_repo = DocumentRepository(session)
        self.settings = get_settings()

    async def list_files(self, page: int = 1, page_size: int = 10) -> ContentListResponse:
        """페이지네이션을 포함한 파일 목록 조회."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10
        
        offset = (page - 1) * page_size
        total = await self.file_repo.count_files()
        rows = await self.file_repo.list_files(limit=page_size, offset=offset)
        
        items = []
        for row in rows:
            # SQLAlchemy 객체를 딕셔너리로 변환
            item_data = {
                "id": row.id,
                "filename": row.filename,
                "object_key": row.object_key,
                "media_url": get_public_media_url(row.object_key),
                "content_type": row.content_type,
                "status": row.status,
                "summary_md": row.summary_md,
                "title": row.title,
                "created_at": row.created_at,
                "speakers": row.transcription.speakers if row.transcription else [],
                "duration_seconds": row.transcription.duration_seconds if row.transcription else 0.0,
                "file_type": row.content_type.value if row.content_type else None,
                "transcription_content": row.transcription.transcription if row.transcription else None,
                "document": DocumentSchema.model_validate(row.document).model_dump() if row.document else None,
            }
            item = ContentListItem.model_validate(item_data)
            items.append(item)
        
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        
        return ContentListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    async def get_file(self, file_id: int) -> ContentDetail:
        """파일 상세 조회."""
        file_obj = await self.file_repo.get_file(file_id)
        if not file_obj:
            raise ValueError("File not found")
        
        # SQLAlchemy 객체를 딕셔너리로 변환
        detail_data = {
            "id": file_obj.id,
            "filename": file_obj.filename,
            "object_key": file_obj.object_key,
            "media_url": get_public_media_url(file_obj.object_key),
            "content_type": file_obj.content_type,
            "status": file_obj.status,
            "summary_md": file_obj.summary_md,
            "title": file_obj.title,
            "created_at": file_obj.created_at,
            "updated_at": None,  # File 모델에는 없음
            "speakers": file_obj.transcription.speakers if file_obj.transcription else [],
            "duration_seconds": file_obj.transcription.duration_seconds if file_obj.transcription else 0.0,
            "file_type": file_obj.content_type.value if file_obj.content_type else None,
            # transcription 필드는 필수이므로, transcription이 없으면 빈 딕셔너리 사용
            "transcription": file_obj.transcription.transcription if file_obj.transcription else {},
            "transcription_content": file_obj.transcription.transcription if file_obj.transcription else None,
            "document": DocumentSchema.model_validate(file_obj.document).model_dump() if file_obj.document else None,
            "logs": [SttLogSchema.model_validate(log) for log in file_obj.logs],
            "llm_logs": [LlmLogSchema.model_validate(log) for log in file_obj.llm_logs],
        }
        detail = ContentDetail.model_validate(detail_data)
        return detail

    async def upload_and_enqueue(
        self,
        file: UploadFile,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        ocr_mode: str = "document",
    ) -> dict[str, int]:
        """파일 업로드 및 처리 큐에 등록."""
        logger.info("[Upload] 파일 업로드 시작: filename=%s", file.filename)
        print(f"[Upload] [1/4] 파일 업로드 시작: {file.filename}")
        
        object_key = self._build_object_key(file.filename)
        logger.info("[Upload] 스토리지 키 생성: object_key=%s", object_key)
        
        # 파일 내용 읽기 및 크기 확인
        file_content = await file.read()
        file_size = len(file_content)
        file_size_mb = file_size / (1024 * 1024)
        logger.info("[Upload] 파일 크기: %d bytes (%.2f MB)", file_size, file_size_mb)
        print(f"[Upload] [2/4] 파일 크기: {file_size:,} bytes ({file_size_mb:.2f} MB)")
        
        # 스토리지 업로드 (파일 내용을 직접 전달)
        logger.info("[Upload] 스토리지 업로드 시작: object_key=%s", object_key)
        print(f"[Upload] [3/4] 스토리지 업로드 중: {object_key}")
        await self._upload_file_content_to_storage(file_content, object_key)
        logger.info("[Upload] 스토리지 업로드 완료: object_key=%s", object_key)
        print(f"[Upload] OK 스토리지 업로드 완료: {object_key}")
        
        # 파일 닫기
        await file.close()
        
        
        # 파일 타입 결정
        content_type = self._determine_content_type(file.filename, file.content_type, ocr_mode=ocr_mode)
        
        # DB에 파일 생성
        logger.info("[Upload] DB에 파일 생성 시작: filename=%s", file.filename)
        file_obj = await self.file_repo.create_file(
            filename=file.filename or "unknown",
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
                transcription={},
                duration_seconds=0.0,
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
                    file_id=file_obj.id,
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
        
        elif content_type in (ContentType.DOCUMENT, ContentType.PORTRAY):
            # 문서 또는 이미지 묘사: Document 생성 + OCR 작업 큐잉
            await self.document_repo.create_document(
                file_id=file_obj.id,
                ocr_text="",
                page_count=0,
                ocr_metadata={},
            )
            await self.session.commit()
            
            # OCR 작업 큐잉
            logger.info("[Upload] OCR 작업 큐잉: file_id=%s, ocr_mode=%s", file_obj.id, ocr_mode)
            print(f"[Upload] [4/4] OCR 작업 큐잉 중: file_id={file_obj.id}, ocr_mode={ocr_mode}")
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
                    ocr_mode=ocr_mode,
                )
                job_id = await loop.run_in_executor(None, enqueue_func)
                logger.info("[Upload] OCR 작업 큐잉 성공: file_id=%s, job_id=%s, ocr_mode=%s", file_obj.id, job_id, ocr_mode)
                print(f"[Upload] OK OCR 큐 등록 완료: file_id={file_obj.id}, job_id={job_id}, ocr_mode={ocr_mode}")
            except Exception as exc:
                error_msg = f"OCR 작업 큐잉 실패: file_id={file_obj.id}, error={exc}"
                logger.exception("[Upload] %s", error_msg)
                print(f"[Upload] ERROR {error_msg}")
        
        logger.info("[Upload] 파일 업로드 전체 프로세스 완료: file_id=%s, filename=%s", file_obj.id, file.filename)
        print(f"[Upload] ========================================")
        print(f"[Upload] 파일 업로드 완료: file_id={file_obj.id}, filename={file.filename}")
        print(f"[Upload] ========================================")
        return {"file_id": file_obj.id}

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

    def _build_object_key(self, filename: str) -> str:
        """안전한 파일명으로 object_key 생성 (비ASCII 문자 제거)."""
        original_path = Path(filename)
        extension = original_path.suffix  # .mp4, .wav 등
        # UUID + 확장자로 안전한 파일명 생성 (비ASCII 문자 문제 해결)
        safe_filename = f"{uuid4().hex}{extension}"
        return f"{self.settings.s3_prefix}/{safe_filename}"

    async def _upload_file_content_to_storage(self, file_content: bytes, object_key: str) -> None:
        """파일 내용을 스토리지에 업로드."""
        # BytesIO로 변환하여 upload_fileobj에 전달
        from io import BytesIO
        file_obj = BytesIO(file_content)
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: upload_fileobj(file_obj, key=object_key))

    async def _cleanup_queue_and_storage(self, file_ids: list[int], object_keys: list[str]) -> None:
        """큐 작업 취소와 스토리지 파일 삭제를 일괄 처리."""
        loop = asyncio.get_running_loop()

        if file_ids:
            # Celery 큐 작업 취소
            celery_cancelled = await loop.run_in_executor(None, cancel_celery_tasks_by_content_ids, file_ids)
            if celery_cancelled:
                logger.info("Cancelled %s Celery tasks for deleted files", celery_cancelled)

        for object_key in object_keys:
            try:
                await loop.run_in_executor(None, delete_file, object_key)
            except Exception as exc:
                logger.warning("Failed to delete file from storage: %s, error: %s", object_key, exc)

    def _determine_content_type(self, filename: str | None, content_type: str | None, ocr_mode: str = "document") -> ContentType:
        """파일명과 content_type으로 파일 타입 결정."""
        if not filename:
            return ContentType.DOCUMENT  # 기본값
        
        filename_lower = filename.lower()
        
        # 오디오/비디오 확장자
        audio_extensions = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"}
        video_extensions = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv", ".wmv"}
        
        if any(filename_lower.endswith(ext) for ext in audio_extensions | video_extensions):
            return ContentType.AUDIO

        # Portray 모드인 경우 PORTRAY 타입 반환
        if ocr_mode == "portray":
            return ContentType.PORTRAY
        
        # 문서 확장자
        return ContentType.DOCUMENT

    async def enqueue_youtube_download(
        self,
        url: str,
        video_id: str,
        title: str,
    ) -> dict[str, int]:
        """
        YouTube 다운로드 작업을 큐에 등록.
        
        Args:
            url: YouTube URL
            video_id: YouTube video ID
            title: 영상 제목
            
        Returns:
            dict: {"file_id": int}
        """
        # 안전한 파일명 생성
        import re
        safe_title = re.sub(r'[^\w\s가-힣-]', '', title)[:50].strip()
        if not safe_title:
            safe_title = video_id
        filename = f"{safe_title}.mp4"
        object_key = self._build_object_key(filename)

        logger.info("[YouTube Upload] DB에 파일 생성 시작: title=%s", title)

        # DB에 File 생성 (QUEUED 상태)
        file_obj = await self.file_repo.create_file(
            filename=filename,
            object_key=object_key,
            content_type=ContentType.AUDIO,
            status=FileStatus.QUEUED,
        )

        # Transcription 레코드 생성
        await self.transcription_repo.create_transcription(
            file_id=file_obj.id,
            transcription={},
            duration_seconds=0.0,
        )
        await self.session.commit()

        logger.info("[YouTube Upload] DB에 파일 생성 완료: file_id=%s, title=%s", file_obj.id, title)

        # YouTube 다운로드 Celery 태스크 큐잉
        try:
            loop = asyncio.get_running_loop()
            from functools import partial
            from ..worker.task_queue_adapter import get_task_queue

            task_queue = get_task_queue()
            enqueue_func = partial(
                task_queue.enqueue_youtube_download_job,
                file_id=file_obj.id,
                youtube_url=url,
                storage_key=object_key,
                original_filename=filename,
            )
            job_id = await loop.run_in_executor(None, enqueue_func)

            logger.info(
                "[YouTube Upload] YouTube 다운로드 작업 큐잉 성공: file_id=%s, job_id=%s",
                file_obj.id,
                job_id
            )
        except Exception as exc:
            error_msg = f"YouTube 다운로드 작업 큐잉 실패: file_id={file_obj.id}, error={exc}"
            logger.exception("[YouTube Upload] %s", error_msg)
            raise

        return {"file_id": file_obj.id}
