from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Sequence
from uuid import uuid4, UUID

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

# File.id는 이제 UUID

from ..core.config import get_settings
from ..core.logging import logger
from ..core.telemetry import preserve_otel_context
from ..core.storage import (
    delete_file,
    upload_fileobj,
    get_secure_media_url,
    wait_for_file,
    wait_for_files,
    download_file,
    delete_files_by_prefix,
)
from ..db import models
from ..db.models import FileStatus, ContentType
from ..repositories.file_repository import FileRepository
from ..repositories.content_repository import ContentRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from ..utils.event_publisher import publish_file_progress
from ..utils.progress_tracker import ProgressTracker, Phase
from ..schemas.content import (
    ContentDetail,
    ContentListItem,
    ContentListResponse,
    SttLogSchema,
    LlmLogSchema,
)
from ..schemas.file import DocumentSchema, TranscriptionSchema
from ..utils.celery_queue import cancel_celery_tasks_by_content_ids


class FileService:
    """파일 관련 비즈니스 로직."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.content_repo = ContentRepository(session)
        self.transcription_repo = TranscriptionRepository(session)
        self.document_repo = DocumentRepository(session)
        self.settings = get_settings()

    async def list_files(
        self, user_id: UUID, page: int = 1, page_size: int = 10
    ) -> ContentListResponse:
        """페이지네이션을 포함한 파일 목록 조회 (사용자별 필터링)."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10

        offset = (page - 1) * page_size
        total = await self.file_repo.count_files(user_id=user_id)
        rows = await self.file_repo.list_files(user_id=user_id, limit=page_size, offset=offset)

        items = []
        for row in rows:
            # Content와 관계 데이터 조회
            content = row.content
            transcription = content.transcription_result if content else None
            document = content.document_result if content else None

            # Content가 없는 File은 건너뛰기 (데이터 무결성 문제)
            if not content:
                logger.warning(f"File {row.id} has no associated Content, skipping")
                continue

            # SQLAlchemy 객체를 딕셔너리로 변환
            item_data = {
                "id": row.id,  # File.id (UUID v7) - 이벤트의 file_id와 일치
                "filename": row.filename,
                "object_key": row.object_key,
                "media_url": get_secure_media_url(row.id),
                "content_type": row.content_type,
                "status": content.status or FileStatus.QUEUED,  # None이면 기본값
                "summary_md": content.summary_md,
                "title": content.title,
                "created_at": row.created_at,
                "updated_at": content.updated_at,
                "speakers": transcription.speakers if transcription else [],
                "duration_seconds": transcription.duration_seconds
                if transcription
                else 0.0,
                "file_type": row.content_type.value if row.content_type else None,
                "transcription_content": transcription.transcription
                if transcription
                else None,
                "document": DocumentSchema.model_validate(document).model_dump()
                if document
                else None,
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

    async def get_file(self, file_id: UUID, user_id: UUID | None = None) -> ContentDetail:
        """파일 상세 조회 (UUID). user_id가 제공되면 소유자 검증."""
        file_obj = await self.file_repo.get_file(file_id)
        if not file_obj:
            raise ValueError("File not found")

        # Content와 관계 데이터 조회
        content = file_obj.content
        if not content:
            raise ValueError("Content not found for file")

        # user_id 검증
        if user_id and content.user_id != user_id:
            raise ValueError("You don't have permission to access this content")

        transcription = content.transcription_result
        document = content.document_result
        logs = content.logs or []
        llm_logs = content.llm_logs or []

        # SQLAlchemy 객체를 딕셔너리로 변환
        detail_data = {
            "id": file_obj.id,  # File.id (UUID v7) - 이벤트의 file_id와 일치
            "filename": file_obj.filename,
            "object_key": file_obj.object_key,
            "media_url": get_secure_media_url(file_obj.id),
            "content_type": file_obj.content_type,
            "status": content.status or FileStatus.QUEUED,  # None이면 기본값
            "summary_md": content.summary_md,
            "title": content.title,
            "created_at": file_obj.created_at,
            "updated_at": content.updated_at,
            "speakers": transcription.speakers if transcription else [],
            "duration_seconds": transcription.duration_seconds
            if transcription
            else 0.0,
            "file_type": file_obj.content_type.value if file_obj.content_type else None,
            # transcription 필드는 필수이므로, transcription이 없으면 빈 딕셔너리 사용
            "transcription": transcription.transcription if transcription else {},
            "transcription_content": transcription.transcription
            if transcription
            else None,
            "document": DocumentSchema.model_validate(document).model_dump()
            if document
            else None,
            "logs": [SttLogSchema.model_validate(log) for log in logs],
            "llm_logs": [LlmLogSchema.model_validate(log) for log in llm_logs],
        }
        detail = ContentDetail.model_validate(detail_data)
        return detail

    async def get_file_by_content_id(self, content_id: UUID) -> ContentDetail:
        """Content ID (UUID v7)로 파일 상세 조회."""
        content = await self.content_repo.get_content(content_id)
        if not content:
            raise ValueError("Content not found")

        # Content에서 File 정보 추출
        file_obj = content.file
        if not file_obj:
            raise ValueError("File not found for content")

        # SQLAlchemy 객체를 딕셔너리로 변환
        detail_data = {
            "id": file_obj.id,  # File.id (UUID v7) - 이벤트의 file_id와 일치
            "filename": file_obj.filename,
            "object_key": file_obj.object_key,
            "media_url": get_secure_media_url(file_obj.id),
            "content_type": file_obj.content_type,
            "status": content.status,  # Content의 status 사용
            "summary_md": content.summary_md,
            "title": content.title,
            "created_at": content.created_at,
            "updated_at": content.updated_at,
            "speakers": content.transcription_result.speakers
            if content.transcription_result
            else [],
            "duration_seconds": content.transcription_result.duration_seconds
            if content.transcription_result
            else 0.0,
            "file_type": file_obj.content_type.value if file_obj.content_type else None,
            "transcription": content.transcription_result.transcription
            if content.transcription_result
            else {},
            "transcription_content": content.transcription_result.transcription
            if content.transcription_result
            else None,
            "document": DocumentSchema.model_validate(
                content.document_result
            ).model_dump()
            if content.document_result
            else None,
            "logs": [SttLogSchema.model_validate(log) for log in content.logs],
            "llm_logs": [LlmLogSchema.model_validate(log) for log in content.llm_logs],
        }
        detail = ContentDetail.model_validate(detail_data)
        return detail

    async def upload_and_enqueue(
        self,
        file: UploadFile,
        user_id: UUID,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        ocr_mode: str = "document",
        ocr_accuracy_mode: str = "speed",
        accuracy_mode: str = "speed",
    ) -> dict[str, int]:
        """파일 업로드 및 처리 큐에 등록."""
        logger.info(
            "[Upload] 파일 업로드 시작: filename=%s, accuracy_mode=%s",
            file.filename,
            accuracy_mode,
        )
        print(
            f"[Upload] [1/4] 파일 업로드 시작: {file.filename}, accuracy_mode={accuracy_mode}"
        )

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
        await self._upload_file_content_to_storage(
            file_content,
            object_key,
            content_type=file.content_type or "application/octet-stream",
        )
        logger.info("[Upload] 스토리지 업로드 완료: object_key=%s", object_key)
        print(f"[Upload] OK 스토리지 업로드 완료: {object_key}")

        # 파일 가용성 확인 (S3 eventual consistency 대응)
        # 워커가 파일을 다운로드하기 전에 파일이 실제로 가용 상태인지 확인
        loop = asyncio.get_running_loop()
        file_ready = await loop.run_in_executor(
            None, lambda: wait_for_file(object_key, max_attempts=10, interval=0.5)
        )
        if not file_ready:
            settings = get_settings()
            error_msg = (
                f"파일 가용성 확인 실패: object_key={object_key}, "
                f"endpoint={settings.s3_endpoint}, bucket={settings.s3_bucket}"
            )
            logger.error("[Upload] %s", error_msg)
            raise RuntimeError(error_msg)
        logger.info("[Upload] 파일 가용성 확인 완료: object_key=%s", object_key)

        # 파일 닫기
        await file.close()

        # 파일 타입 결정
        content_type = self._determine_content_type(
            file.filename, file.content_type, ocr_mode=ocr_mode
        )

        # DB에 파일 생성
        logger.info("[Upload] DB에 파일 생성 시작: filename=%s", file.filename)
        file_obj = await self.file_repo.create_file(
            filename=file.filename or "unknown",
            object_key=object_key,
            content_type=content_type,
            user_id=user_id,
            status=FileStatus.QUEUED,
        )
        await self.session.commit()
        logger.info(
            "[Upload] DB에 파일 생성 완료: file_id=%s, filename=%s",
            file_obj.id,
            file.filename,
        )
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
                from ..utils.task_queue_adapter import get_task_queue

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
                    accuracy_mode=accuracy_mode,
                )
                job_id = await loop.run_in_executor(
                    None, preserve_otel_context(enqueue_func)
                )
                logger.info(
                    "[Upload] ASR 작업 큐잉 성공: file_id=%s, job_id=%s",
                    file_obj.id,
                    job_id,
                )
                print(
                    f"[Upload] OK ASR 큐 등록 완료: file_id={file_obj.id}, job_id={job_id}"
                )
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

            # OCR 작업 큐잉 (Worker에서 전처리)
            logger.info(
                "[Upload] OCR 작업 큐잉 시작: file_id=%s, ocr_mode=%s, ocr_accuracy_mode=%s",
                file_obj.id,
                ocr_mode,
                ocr_accuracy_mode,
            )
            print(
                f"[Upload] [4/4] OCR 큐잉 중: file_id={file_obj.id}, ocr_mode={ocr_mode}, ocr_accuracy_mode={ocr_accuracy_mode}"
            )

            try:
                loop = asyncio.get_running_loop()
                from functools import partial
                from ..utils.task_queue_adapter import get_task_queue

                task_queue = get_task_queue()
                enqueue_func = partial(
                    task_queue.enqueue_ocr_job,
                    file_id=file_obj.id,
                    file_s3_key=object_key,  # 원본 파일 S3 경로
                    ocr_mode=ocr_mode,
                    ocr_accuracy_mode=ocr_accuracy_mode,
                )
                job_id = await loop.run_in_executor(
                    None, preserve_otel_context(enqueue_func)
                )
                logger.info(
                    "[Upload] OCR 작업 큐잉 성공: file_id=%s, job_id=%s",
                    file_obj.id,
                    job_id,
                )
                print(
                    f"[Upload] OK OCR 큐 등록 완료: file_id={file_obj.id}, job_id={job_id}"
                )
            except Exception as exc:
                error_msg = f"OCR 큐잉 실패: file_id={file_obj.id}, error={exc}"
                logger.exception("[Upload] %s", error_msg)
                print(f"[Upload] ERROR {error_msg}")

        logger.info(
            "[Upload] 파일 업로드 전체 프로세스 완료: file_id=%s, filename=%s",
            file_obj.id,
            file.filename,
        )
        print(f"[Upload] ========================================")
        print(
            f"[Upload] 파일 업로드 완료: file_id={file_obj.id}, filename={file.filename}"
        )
        print(f"[Upload] ========================================")

        # File.id (UUID v7)를 반환 - 이벤트의 file_id와 일치하도록
        return {
            "content_id": file_obj.id,  # File.id (UUID v7) - 이벤트의 file_id와 일치
            "content_type": content_type.value,
        }

    async def delete_files_by_ids(
        self, file_ids: list[UUID], user_id: UUID | None = None
    ) -> tuple[list[UUID], list[UUID]]:
        """
        주어진 ID(UUID)의 파일을 상태와 무관하게 삭제하고,
        (deleted_ids, skipped_ids) 튜플을 반환한다.
        user_id가 제공되면 해당 사용자의 파일만 삭제 가능.
        """
        unique_ids = list(dict.fromkeys(file_ids))
        if not unique_ids:
            return [], []

        deleted_ids, object_keys = await self.file_repo.delete_files_by_ids(
            unique_ids, user_id=user_id
        )
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

    async def _upload_file_content_to_storage(
        self,
        file_content: bytes,
        object_key: str,
        content_type: str = "application/octet-stream",
    ) -> None:
        """파일 내용을 스토리지에 업로드."""
        # BytesIO로 변환하여 upload_fileobj에 전달
        from io import BytesIO

        file_obj = BytesIO(file_content)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: upload_fileobj(file_obj, key=object_key, content_type=content_type),
        )

    async def _cleanup_queue_and_storage(
        self, file_ids: list[UUID], object_keys: list[str]
    ) -> None:
        """큐 작업 취소와 스토리지 파일 삭제를 일괄 처리."""
        loop = asyncio.get_running_loop()

        if file_ids:
            # Celery 큐 작업 취소 (OTEL context 전파)
            celery_cancelled = await loop.run_in_executor(
                None, preserve_otel_context(lambda: cancel_celery_tasks_by_content_ids(file_ids))
            )
            if celery_cancelled:
                logger.info(
                    "Cancelled %s Celery tasks for deleted files", celery_cancelled
                )

        # object_key 기반 파일 삭제 (OTEL context 전파)
        for object_key in object_keys:
            try:
                await loop.run_in_executor(
                    None, preserve_otel_context(lambda key=object_key: delete_file(key))
                )
            except Exception as exc:
                logger.warning(
                    "Failed to delete file from storage: %s, error: %s", object_key, exc
                )

        # 임시 파일 삭제 (temp/ocr/{file_id}/, temp/asr/{file_id}/ 등)
        for file_id in file_ids:
            try:
                # OCR 임시 이미지 (OTEL context 전파)
                ocr_prefix = f"temp/ocr/{file_id}/"
                await loop.run_in_executor(
                    None, preserve_otel_context(lambda p=ocr_prefix: delete_files_by_prefix(p))
                )

                # ASR 임시 파일 (OTEL context 전파)
                asr_prefix = f"temp/asr/{file_id}/"
                await loop.run_in_executor(
                    None, preserve_otel_context(lambda p=asr_prefix: delete_files_by_prefix(p))
                )
            except Exception as exc:
                logger.warning(
                    "Failed to delete temp files for file_id=%s: error=%s", file_id, exc
                )

    def _determine_content_type(
        self, filename: str | None, content_type: str | None, ocr_mode: str = "document"
    ) -> ContentType:
        """파일명과 content_type으로 파일 타입 결정."""
        if not filename:
            return ContentType.DOCUMENT  # 기본값

        filename_lower = filename.lower()

        # 오디오/비디오 확장자
        audio_extensions = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"}
        video_extensions = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv", ".wmv"}

        if any(
            filename_lower.endswith(ext) for ext in audio_extensions | video_extensions
        ):
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
        YouTube 영상을 다운로드하고 ASR 작업을 큐에 등록.

        백엔드에서 직접 다운로드 후 ASR만 워커로 전달합니다.

        Args:
            url: YouTube URL
            video_id: YouTube video ID
            title: 영상 제목

        Returns:
            dict: {"file_id": int}
        """
        import re
        from .youtube_service import YouTubeService

        youtube_service = YouTubeService()

        # 안전한 파일명 생성
        safe_title = re.sub(r"[^\w\s가-힣-]", "", title)[:50].strip()
        if not safe_title:
            safe_title = video_id
        filename = f"{safe_title}.mp4"
        object_key = self._build_object_key(filename)

        # 임시 다운로드 디렉토리
        temp_dir = self.settings.upload_dir / "youtube_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        downloaded_file = None
        file_obj = None

        try:
            # 1. YouTube 다운로드 (백엔드에서 직접)
            logger.info("[YouTube] 다운로드 시작: url=%s, title=%s", url, title)
            video_info = await asyncio.get_running_loop().run_in_executor(
                None, youtube_service.download_video, url, temp_dir
            )
            downloaded_file = video_info.temp_path

            if not downloaded_file or not downloaded_file.exists():
                raise RuntimeError("YouTube 다운로드 실패: 파일을 찾을 수 없습니다")

            logger.info(
                "[YouTube] 다운로드 완료: %s (%d초)",
                video_info.title,
                video_info.duration,
            )

            # 2. S3에 업로드
            logger.info("[YouTube] S3 업로드 시작: key=%s", object_key)
            with open(downloaded_file, "rb") as f:
                upload_fileobj(f, key=object_key, content_type="video/mp4")

            # 파일 가용성 확인 (S3 eventual consistency 대응)
            settings = get_settings()
            file_ready = wait_for_file(object_key, max_attempts=10, interval=0.5)
            if not file_ready:
                error_msg = (
                    f"S3 파일 가용성 확인 실패: object_key={object_key}, "
                    f"endpoint={settings.s3_endpoint}, bucket={settings.s3_bucket}"
                )
                logger.error("[YouTube] %s", error_msg)
                raise RuntimeError(error_msg)
            logger.info("[YouTube] 파일 가용성 확인 완료: object_key=%s", object_key)

            logger.info("[YouTube] S3 업로드 완료: key=%s", object_key)

            # 3. DB에 File 생성
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
                duration_seconds=float(video_info.duration),
            )

            # 다운로드 완료 로그
            await self.file_repo.add_log(
                file_id=file_obj.id,
                log={
                    "event": "youtube_download_complete",
                    "video_id": video_info.video_id,
                    "title": video_info.title,
                    "duration": video_info.duration,
                    "url": url,
                },
                message=f"YouTube 다운로드 완료: {video_info.title}",
            )
            await self.session.commit()

            logger.info("[YouTube] DB 레코드 생성 완료: file_id=%s", file_obj.id)

            # 4. ASR 작업 큐잉 (일반 오디오와 동일)
            loop = asyncio.get_running_loop()
            from functools import partial
            from ..utils.task_queue_adapter import get_task_queue

            task_queue = get_task_queue()
            enqueue_func = partial(
                task_queue.enqueue_asr_job,
                file_id=file_obj.id,
                storage_key=object_key,
                original_filename=filename,
                model_size=self.settings.whisper_model_default,
                processing_mode="case4",
                num_asr_chunks=self.settings.max_workers,
                min_speakers=None,
                max_speakers=None,
                accuracy_mode="speed",
            )
            job_id = await loop.run_in_executor(
                None, preserve_otel_context(enqueue_func)
            )

            logger.info(
                "[YouTube] ASR 작업 큐잉 완료: file_id=%s, job_id=%s",
                file_obj.id,
                job_id,
            )

            return {"file_id": file_obj.id}

        except Exception as exc:
            logger.exception("[YouTube] 처리 실패: url=%s, error=%s", url, exc)

            # 실패 시 DB 레코드가 있으면 상태 업데이트
            if file_obj:
                await self.file_repo.update_file_status(
                    file_obj.id, FileStatus.ASR_FAILED
                )
                await self.file_repo.add_log(
                    file_id=file_obj.id,
                    log={"event": "youtube_error", "error": str(exc)},
                    message=f"YouTube 처리 실패: {exc}",
                )
                await self.session.commit()

            raise

    async def prepare_youtube_placeholder(
        self, title: str, video_id: str, source_url: str | None = None, user_id: UUID | None = None
    ) -> models.File:
        """
        YouTube 업로드를 위한 플레이스홀더 파일 레코드 생성.
        """
        import re

        if not user_id:
            raise ValueError("user_id is required for YouTube upload")

        # 안전한 파일명
        safe_title = re.sub(r"[^\w\s가-힣-]", "", title)[:50].strip()
        if not safe_title:
            safe_title = video_id
        filename = f"{safe_title}.mp4"
        object_key = self._build_object_key(filename)

        # DB에 File 생성 (PULLING 상태)
        file_obj = await self.file_repo.create_file(
            filename=filename,
            object_key=object_key,
            content_type=ContentType.AUDIO,
            user_id=user_id,
            status=FileStatus.PULLING,  # 다운로드 상태로 시작
            source_url=source_url,
        )
        await self.session.commit()
        return file_obj

    async def perform_youtube_download(
        self,
        file_id: UUID,
        url: str,
        video_id: str,
        title: str,
        trace_id: str | None = None,
    ) -> None:
        """
        [Background Task] YouTube 다운로드 수행 및 ASR 큐잉.
        """
        from .youtube_service import YouTubeService

        youtube_service = YouTubeService()

        # DB에서 파일 정보 조회
        file_obj = await self.file_repo.get_file(file_id)
        if not file_obj:
            logger.error("파일을 찾을 수 없습니다: file_id=%s", file_id)
            return

        object_key = file_obj.object_key
        filename = file_obj.filename

        # 임시 디렉토리
        temp_dir = self.settings.upload_dir / "youtube_temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        downloaded_file = None

        try:
            # 1. YouTube 다운로드 (with Progress)
            logger.info("[YouTube] 백그라운드 다운로드 시작: file_id=%s", file_id)

            # 진행률 추적기: 2-stream 다운로드를 연속 구간으로 매핑
            #   메타데이터: 0-5% | 영상: 5-55% | 음성: 55-95% | S3 업로드: 95-100%
            def emit_progress(progress: float, step: str, message: str):
                publish_file_progress(
                    file_id=file_id,
                    status="PULLING",
                    step=step,
                    progress=progress,
                    message=message,
                    metadata={"filename": filename},
                    trace_id=trace_id,
                )

            tracker = ProgressTracker(
                phases=[
                    Phase("youtube_download_video", 5, 55),
                    Phase("youtube_download_audio", 55, 95),
                ],
                on_progress=emit_progress,
            )

            tracker.set(3.0, "YouTube 영상 정보 분석 중...", step="youtube_metadata")

            def progress_hook(d):
                if d["status"] == "finished":
                    tracker.advance_phase()
                    return
                if d["status"] == "downloading":
                    try:
                        raw = float(d.get("_percent_str", "0%").replace("%", ""))
                    except Exception:
                        raw = 0.0
                    tracker.update(raw, f"YouTube 다운로드 중... {tracker.progress:.0f}%")

            from functools import partial

            download_func = partial(
                youtube_service.download_video,
                url,
                temp_dir,
                progress_callback=progress_hook,
            )

            video_info = await asyncio.get_running_loop().run_in_executor(
                None, download_func
            )
            downloaded_file = video_info.temp_path

            if not downloaded_file or not downloaded_file.exists():
                raise RuntimeError("YouTube 다운로드 파일 없음")

            # 2. S3 업로드
            logger.info("[YouTube] S3 업로드 시작: %s", object_key)
            tracker.set(95.0, "클라우드 저장소로 업로드 중...", step="uploading")

            with open(downloaded_file, "rb") as f:
                upload_fileobj(f, key=object_key, content_type="video/mp4")

            if not wait_for_file(object_key, max_attempts=10, interval=0.5):
                raise RuntimeError("S3 업로드 확인 실패")

            # 3. DB 업데이트 (duration, status=QUEUED)
            await self.file_repo.update_file_status(file_id, FileStatus.QUEUED)

            # Transcription 생성 (Duration 업데이트)
            # 기존에 없으면 생성
            if not file_obj.content.transcription_result:
                await self.transcription_repo.create_transcription(
                    file_id=file_id,
                    transcription={},
                    duration_seconds=float(video_info.duration),
                )

            await self.file_repo.add_log(
                file_id=file_id,
                log={
                    "event": "youtube_download_complete",
                    "duration": video_info.duration,
                },
                message="YouTube 다운로드 및 업로드 완료",
            )
            await self.session.commit()

            # 4. ASR 큐잉
            loop = asyncio.get_running_loop()
            from ..utils.task_queue_adapter import get_task_queue

            task_queue = get_task_queue()

            enqueue_func = partial(
                task_queue.enqueue_asr_job,
                file_id=file_id,
                storage_key=object_key,
                original_filename=filename,
                model_size=self.settings.whisper_model_default,
                processing_mode="case4",
                num_asr_chunks=self.settings.max_workers,
                accuracy_mode="speed",
            )

            job_id = await loop.run_in_executor(
                None, preserve_otel_context(enqueue_func)
            )

            logger.info("[YouTube] 완료 및 ASR 큐잉: job_id=%s", job_id)
            publish_file_progress(
                file_id=file_id,
                status="QUEUED",
                step="asr_queued",
                progress=0.0,
                message="음성 인식 대기 중...",
                trace_id=trace_id,
            )

        except Exception as exc:
            logger.exception("[YouTube] 백그라운드 처리 실패: %s", exc)
            await self.file_repo.update_file_status(
                file_id, FileStatus.DOWNLOAD_FAILED
            )
            await self.file_repo.add_log(
                file_id=file_id,
                log={"event": "youtube_failed", "error": str(exc)},
                message=f"YouTube 처리 실패: {exc}",
            )
            await self.session.commit()

            publish_file_progress(
                file_id=file_id,
                status="FAILED",
                step="youtube_failed",
                progress=0.0,
                message=f"YouTube 처리 실패: {str(exc)}",
                trace_id=trace_id,
            )
        finally:
            if downloaded_file and downloaded_file.exists():
                try:
                    downloaded_file.unlink()
                except:
                    pass
