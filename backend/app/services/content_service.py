from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import delete_file, upload_fileobj, get_public_media_url
from ..db.models import ContentStatus
from ..repositories.content_repository import ContentRepository
from ..schemas.content import ContentDetail, ContentListItem, ContentListResponse, UploadResponse
from ..utils.celery_queue import cancel_celery_tasks_by_content_ids


class ContentService:
    """콘텐츠 관련 비즈니스 로직."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ContentRepository(session)
        self.settings = get_settings()

    async def list_contents(self, page: int = 1, page_size: int = 10) -> ContentListResponse:
        """페이지네이션을 포함한 콘텐츠 목록 조회."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 10
        
        offset = (page - 1) * page_size
        total = await self.repo.count_contents()
        rows = await self.repo.list_contents(limit=page_size, offset=offset)
        
        items = []
        for row in rows:
            item = ContentListItem.model_validate(row)
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

    async def get_content(self, content_id: int) -> ContentDetail:
        content = await self.repo.get_content(content_id)
        if not content:
            raise ValueError("Content not found")
        # lazy load logs
        await self.session.refresh(content)
        detail = ContentDetail.model_validate(content)
        # media_url 추가
        detail.media_url = get_public_media_url(content.object_key)
        return detail

    async def upload_and_enqueue(
        self, 
        file: UploadFile,
        min_speakers: int | None = None,
        max_speakers: int | None = None
    ) -> UploadResponse:
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
        
        # DB에 콘텐츠 생성
        logger.info("[Upload] DB에 콘텐츠 생성 시작: filename=%s", file.filename)
        content = await self.repo.create_content(
            filename=file.filename,
            object_key=object_key,
            speakers=[],
            transcription={},
            duration_seconds=0.0,
            status=ContentStatus.QUEUED,
        )
        await self.session.commit()
        logger.info("[Upload] DB에 콘텐츠 생성 완료: content_id=%s, filename=%s", content.id, file.filename)
        print(f"[Upload] OK DB 저장 완료: content_id={content.id}")

        # 비동기 컨텍스트에서 동기 큐 작업을 executor에서 실행
        logger.info("[Upload] 큐에 작업 등록 시도: content_id=%s, filename=%s", content.id, file.filename)
        print(f"[Upload] [4/4] 큐에 작업 등록 중: content_id={content.id}")
        try:
            loop = asyncio.get_running_loop()
            # Task Queue Adapter 사용 (Celery)
            from functools import partial
            from ..utils.task_queue_adapter import get_task_queue
            
            task_queue = get_task_queue()
            queue_type_name = type(task_queue).__name__
            logger.info(f"[Upload] 사용 중인 큐 어댑터: {queue_type_name}")
            print(f"[Upload] 큐 어댑터: {queue_type_name}")
            
            enqueue_func = partial(
                task_queue.enqueue_asr_job,
                content_id=content.id,
                storage_key=object_key,
                original_filename=file.filename,
                model_size=self.settings.whisper_model_default,
                processing_mode="case4",
                num_asr_chunks=self.settings.max_workers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            job_id = await loop.run_in_executor(None, enqueue_func)
            logger.info("[Upload] 큐에 작업 등록 성공: content_id=%s, job_id=%s", content.id, job_id)
            print(f"[Upload] OK 큐 등록 완료: content_id={content.id}, job_id={job_id}")
        except Exception as exc:
            error_msg = f"큐에 작업 등록 실패: content_id={content.id}, error={exc}"
            logger.exception("[Upload] %s", error_msg)
            print(f"[Upload] ERROR {error_msg}")
            # 큐 등록 실패해도 파일은 업로드되었으므로 사용자에게는 성공으로 반환
            # 하지만 로그에는 기록
            # 필요시 상태를 QUEUED에서 ERROR로 변경할 수도 있음

        logger.info("[Upload] 파일 업로드 전체 프로세스 완료: content_id=%s, filename=%s", content.id, file.filename)
        print(f"[Upload] ========================================")
        print(f"[Upload] 파일 업로드 완료: content_id={content.id}, filename={file.filename}")
        print(f"[Upload] ========================================")
        return UploadResponse(content_id=content.id, queued=True)

    async def _upload_file_content_to_storage(self, file_content: bytes, object_key: str) -> None:
        """파일 내용을 스토리지에 업로드."""
        # BytesIO로 변환하여 upload_fileobj에 전달
        from io import BytesIO
        file_obj = BytesIO(file_content)
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: upload_fileobj(file_obj, key=object_key))
    
    async def _upload_to_storage(self, file: UploadFile, object_key: str) -> None:
        """파일을 스토리지에 업로드 (레거시 호환용)."""
        # 파일 내용을 메모리에 읽어서 저장 (파일 포인터 문제 방지)
        file_content = await file.read()
        await file.close()
        await self._upload_file_content_to_storage(file_content, object_key)

    async def delete_queued_contents(self) -> int:
        """QUEUED 상태인 모든 콘텐츠 삭제 (DB + 스토리지 + 큐)."""
        count, content_ids, object_keys = await self.repo.delete_queued_contents()
        await self._cleanup_queue_and_storage(content_ids, object_keys)
        await self.session.commit()
        return count

    async def delete_contents_by_ids(self, content_ids: list[int]) -> tuple[list[int], list[int]]:
        """
        주어진 ID의 콘텐츠를 상태와 무관하게 삭제하고,
        (deleted_ids, skipped_ids) 튜플을 반환한다.
        """
        unique_ids = list(dict.fromkeys(content_ids))
        if not unique_ids:
            return [], []

        deleted_ids, object_keys = await self.repo.delete_contents_by_ids(unique_ids)
        await self._cleanup_queue_and_storage(deleted_ids, object_keys)
        await self.session.commit()

        deleted_set = set(deleted_ids)
        skipped_ids = [content_id for content_id in unique_ids if content_id not in deleted_set]
        return deleted_ids, skipped_ids

    def _build_object_key(self, filename: str) -> str:
        """안전한 파일명으로 object_key 생성 (비ASCII 문자 제거)."""
        original_path = Path(filename)
        extension = original_path.suffix  # .mp4, .wav 등
        # UUID + 확장자로 안전한 파일명 생성 (비ASCII 문자 문제 해결)
        safe_filename = f"{uuid4().hex}{extension}"
        return f"{self.settings.s3_prefix}/{safe_filename}"

    async def _cleanup_queue_and_storage(self, content_ids: list[int], object_keys: list[str]) -> None:
        """큐 작업 취소와 스토리지 파일 삭제를 일괄 처리."""
        loop = asyncio.get_running_loop()

        if content_ids:
            # Celery 큐 작업 취소
            celery_cancelled = await loop.run_in_executor(None, cancel_celery_tasks_by_content_ids, content_ids)
            if celery_cancelled:
                logger.info("Cancelled %s Celery tasks for deleted contents", celery_cancelled)

        for object_key in object_keys:
            try:
                await loop.run_in_executor(None, delete_file, object_key)
            except Exception as exc:
                logger.warning("Failed to delete file from storage: %s, error: %s", object_key, exc)

    async def retry_processing(
        self, 
        content_id: int, 
        retry_type: str,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        ocr_mode: str = "document",
        accuracy_mode: str = "speed",
    ) -> dict:
        """
        실패한 콘텐츠를 재처리합니다.
        
        Args:
            content_id: 콘텐츠 ID (또는 File ID)
            retry_type: "asr", "summary", 또는 "ocr"
            min_speakers: 최소 화자 수 (선택사항, ASR 재처리 시에만 사용)
            max_speakers: 최대 화자 수 (선택사항, ASR 재처리 시에만 사용)
            ocr_mode: OCR 처리 모드 ("document", "portray")
            accuracy_mode: 전사 모드 ("speed", "accuracy")
        
        Returns:
            {"success": True, "message": "...", "job_id": "..."}
        """
        from ..repositories.file_repository import FileRepository
        from ..db.models import FileStatus, ContentType
        
        retry_type = retry_type.lower()
        
        # 먼저 File로 시도 (새로운 구조)
        file_repo = FileRepository(self.session)
        file_obj = await file_repo.get_file(content_id)
        
        if file_obj:
            # File 기반 처리
            if retry_type == "ocr":
                # OCR 재처리
                # 허용되는 content_type: DOCUMENT, PORTRAY
                if file_obj.content_type not in [ContentType.DOCUMENT, ContentType.PORTRAY]:
                    raise ValueError(f"Cannot retry OCR for non-document/non-portray file (content_type: {file_obj.content_type.value})")
                
                if file_obj.status not in [FileStatus.OCR_FAILED, FileStatus.OCR_PROCESSING, FileStatus.QUEUED, FileStatus.COMPLETED]:
                     # COMPLETED 상태에서도 재처리 가능하게 허용 (모드 변경 등)
                    pass
                
                # OCR 모드에 따라 ContentType 업데이트
                new_content_type = ContentType.DOCUMENT
                if ocr_mode == "portray":
                    new_content_type = ContentType.PORTRAY
                
                # ContentType이 변경되면 업데이트
                if file_obj.content_type != new_content_type:
                    file_obj.content_type = new_content_type
                    logger.info("Content type updated to %s for file_id=%s", new_content_type, content_id)

                # 상태를 QUEUED로 변경
                await file_repo.update_file_status(content_id, FileStatus.QUEUED)
                await file_repo.add_log(
                    file_id=content_id,
                    log={"event": "manual_retry", "type": "ocr", "ocr_mode": ocr_mode},
                    message=f"Manual OCR retry requested by user (mode: {ocr_mode})",
                )
                await self.session.commit()
                
                # OCR 작업 큐잉
                loop = asyncio.get_running_loop()
                from functools import partial
                from ..utils.task_queue_adapter import get_task_queue
                
                task_queue = get_task_queue()
                enqueue_func = partial(
                    task_queue.enqueue_ocr_job,
                    file_id=content_id,
                    storage_key=file_obj.object_key,
                    original_filename=file_obj.filename,
                    ocr_mode=ocr_mode,
                )
                job_id = await loop.run_in_executor(None, enqueue_func)
                
                logger.info("Manual OCR retry enqueued: file_id=%s, job_id=%s", content_id, job_id)
                return {"success": True, "message": "OCR reprocessing started", "job_id": job_id}
            elif retry_type == "asr":
                # ASR 재처리
                if file_obj.content_type != ContentType.AUDIO:
                    raise ValueError(f"Cannot retry ASR for non-audio file (content_type: {file_obj.content_type.value})")
                
                if file_obj.status not in [FileStatus.ASR_FAILED, FileStatus.PROCESSING, FileStatus.QUEUED]:
                    raise ValueError(f"Cannot retry ASR for file with status: {file_obj.status.value}")
                
                # 화자수 범위 검증
                if min_speakers is not None and max_speakers is not None and min_speakers > max_speakers:
                    raise ValueError("min_speakers must be less than or equal to max_speakers")
                
                # 상태를 QUEUED로 변경
                await file_repo.update_file_status(content_id, FileStatus.QUEUED)
                log_data = {"event": "manual_retry", "type": "asr"}
                if min_speakers is not None:
                    log_data["min_speakers"] = min_speakers
                if max_speakers is not None:
                    log_data["max_speakers"] = max_speakers
                await file_repo.add_log(
                    file_id=content_id,
                    log=log_data,
                    message="Manual ASR retry requested by user",
                )
                await self.session.commit()
                
                # ASR 작업 큐잉
                loop = asyncio.get_running_loop()
                from functools import partial
                from ..utils.task_queue_adapter import get_task_queue
                
                task_queue = get_task_queue()
                enqueue_func = partial(
                    task_queue.enqueue_asr_job,
                    file_id=content_id,
                    storage_key=file_obj.object_key,
                    original_filename=file_obj.filename,
                    model_size=self.settings.whisper_model_default,
                    processing_mode="case4",
                    num_asr_chunks=self.settings.max_workers,
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                    accuracy_mode=accuracy_mode,
                )
                job_id = await loop.run_in_executor(None, enqueue_func)
                
                logger.info("Manual ASR retry enqueued: file_id=%s, job_id=%s, min_speakers=%s, max_speakers=%s, accuracy_mode=%s", 
                           content_id, job_id, min_speakers, max_speakers, accuracy_mode)
                return {"success": True, "message": "ASR reprocessing started", "job_id": job_id}
            
            elif retry_type == "summary":
                # LLM Summary 재처리
                if file_obj.status not in [FileStatus.SUMMARY_FAILED, FileStatus.SUMMARY_QUEUED, FileStatus.SUMMARIZING]:
                    raise ValueError(f"Cannot retry LLM summary for file with status: {file_obj.status.value}")
                
                # 상태를 SUMMARY_QUEUED로 변경 (큐에 등록)
                await file_repo.update_file_status(content_id, FileStatus.SUMMARY_QUEUED)
                await file_repo.add_llm_log(
                    file_id=content_id,
                    log={"event": "manual_retry", "type": "llm_summary"},
                    message="Manual LLM summary retry requested by user",
                )
                await self.session.commit()
                
                # LLM 작업 큐잉
                loop = asyncio.get_running_loop()
                from functools import partial
                from ..utils.task_queue_adapter import get_task_queue
                
                task_queue = get_task_queue()
                enqueue_func = partial(task_queue.enqueue_llm_job, file_id=content_id)
                job_id = await loop.run_in_executor(None, enqueue_func)
                
                logger.info("Manual LLM retry enqueued: file_id=%s, job_id=%s", content_id, job_id)
                return {"success": True, "message": "LLM summary reprocessing started", "job_id": job_id}
            
            else:
                raise ValueError(f"Invalid retry_type: {retry_type}. Must be 'asr', 'summary', or 'ocr'")
        
        # 하위 호환성: Content 기반 처리
        content = await self.repo.get_content(content_id)
        if not content:
            raise ValueError("Content not found")
        
        if retry_type == "asr":
            # PROCESSING, QUEUED 상태에서 멈춘 경우도 재시도 가능
            if content.status not in [ContentStatus.ASR_FAILED, ContentStatus.PROCESSING, ContentStatus.QUEUED]:
                raise ValueError(f"Cannot retry ASR for content with status: {content.status.value}")
            
            # 화자수 범위 검증
            if min_speakers is not None and max_speakers is not None and min_speakers > max_speakers:
                raise ValueError("min_speakers must be less than or equal to max_speakers")
            
            # 상태를 QUEUED로 변경
            await self.repo.update_content_status(content_id, ContentStatus.QUEUED)
            log_data = {"event": "manual_retry", "type": "asr"}
            if min_speakers is not None:
                log_data["min_speakers"] = min_speakers
            if max_speakers is not None:
                log_data["max_speakers"] = max_speakers
            await self.repo.add_log(
                content_id=content_id,
                log=log_data,
                message="Manual ASR retry requested by user",
            )
            await self.session.commit()
            
            # 큐에 작업 등록
            loop = asyncio.get_running_loop()
            from functools import partial
            from ..utils.task_queue_adapter import get_task_queue
            
            task_queue = get_task_queue()
            enqueue_func = partial(
                task_queue.enqueue_asr_job,
                file_id=content_id,
                storage_key=content.object_key,
                original_filename=content.filename,
                model_size=self.settings.whisper_model_default,
                processing_mode="case4",
                num_asr_chunks=self.settings.max_workers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                accuracy_mode=accuracy_mode,
            )
            job_id = await loop.run_in_executor(None, enqueue_func)
            
            logger.info("Manual ASR retry enqueued: content_id=%s, job_id=%s, min_speakers=%s, max_speakers=%s, accuracy_mode=%s", 
                       content_id, job_id, min_speakers, max_speakers, accuracy_mode)
            return {"success": True, "message": "ASR reprocessing started", "job_id": job_id}
        
        elif retry_type == "summary":
            if content.status not in [ContentStatus.SUMMARY_FAILED, ContentStatus.SUMMARY_QUEUED, ContentStatus.SUMMARIZING]:
                raise ValueError(f"Cannot retry LLM summary for content with status: {content.status.value}")
            
            # 상태를 SUMMARY_QUEUED로 변경 (큐에 등록)
            await self.repo.update_content_status(content_id, ContentStatus.SUMMARY_QUEUED)
            await self.repo.add_llm_log(
                content_id=content_id,
                log={"event": "manual_retry", "type": "llm_summary"},
                message="Manual LLM summary retry requested by user",
            )
            await self.session.commit()
            
            # 큐에 작업 등록
            loop = asyncio.get_running_loop()
            from functools import partial
            from ..utils.task_queue_adapter import get_task_queue
            
            task_queue = get_task_queue()
            enqueue_func = partial(task_queue.enqueue_llm_job, file_id=content_id)
            enqueue_func = partial(task_queue.enqueue_llm_job, file_id=content_id)
            job_id = await loop.run_in_executor(None, enqueue_func)
            
            logger.info("Manual LLM retry enqueued: content_id=%s, job_id=%s", content_id, job_id)
            return {"success": True, "message": "LLM summary reprocessing started", "job_id": job_id}
        
        else:
            raise ValueError(f"Invalid retry_type: {retry_type}. Must be 'asr' or 'summary'")
    
    async def recluster_speakers(
        self,
        content_id: int,
        num_speakers: int | None = None,
        similarity_threshold: float = 0.7,
    ) -> dict[str, Any]:
        """
        저장된 세그먼트 임베딩을 기반으로 화자를 재클러스터링합니다.
        
        Args:
            content_id: 콘텐츠 ID
            num_speakers: 목표 화자 수 (None이면 자동 결정)
            similarity_threshold: 코사인 유사도 임계값
        
        Returns:
            재클러스터링 결과 딕셔너리
        """
        # 콘텐츠 조회 및 검증
        content = await self.repo.get_content(content_id)
        if not content:
            raise ValueError("Content not found")
        
        if content.status != ContentStatus.COMPLETED:
            raise ValueError(f"Content must be COMPLETED status. Current status: {content.status}")
        
        # transcription에서 segment_embeddings 확인
        transcription = content.transcription
        if not transcription:
            raise ValueError("Transcription not found")
        
        diarization_metadata = transcription.get("diarization_metadata", {})
        segment_embeddings = diarization_metadata.get("segment_embeddings", [])
        
        if not segment_embeddings:
            raise ValueError("segment_embeddings not found. Please ensure the content has been processed with diarization.")
        
        logger.info(
            "[Reclustering] Starting reclustering for content_id=%s, num_speakers=%s, threshold=%s",
            content_id, num_speakers, similarity_threshold
        )
        
        # 재클러스터링 수행 (torch 의존성 없는 별도 모듈 사용)
        import sys
        from asr_pipeline.reclustering import (
            recluster_speakers_from_embeddings,
            update_transcription_with_new_speakers,
        )
        
        segment_to_speaker_mapping = recluster_speakers_from_embeddings(
            segment_embeddings=segment_embeddings,
            target_num_speakers=num_speakers,
            similarity_threshold=similarity_threshold,
        )
        
        # transcription 업데이트
        updated_transcription = update_transcription_with_new_speakers(
            transcription=transcription,
            segment_to_speaker_mapping=segment_to_speaker_mapping,
            segment_embeddings=segment_embeddings,
        )
        
        # 새로운 화자 라벨 추출
        new_speaker_labels = updated_transcription["diarization_metadata"]["speaker_labels"]
        num_speakers_result = len(new_speaker_labels)
        
        # DB 업데이트
        await self.repo.update_content_result(
            content_id=content_id,
            speakers=new_speaker_labels,
            duration_seconds=content.duration_seconds,
            transcription=updated_transcription,
        )
        await self.session.commit()
        
        # 로그 기록
        await self.repo.add_log(
            content_id=content_id,
            log={
                "event": "reclustering_completed",
                "num_speakers": num_speakers_result,
                "speaker_labels": new_speaker_labels,
                "updated_segments_count": len(segment_to_speaker_mapping),
                "similarity_threshold": similarity_threshold,
            },
            message=f"Speaker reclustering completed: {num_speakers_result} speakers",
        )
        await self.session.commit()
        
        logger.info(
            "[Reclustering] Completed for content_id=%s, num_speakers=%s",
            content_id, num_speakers_result
        )
        
        return {
            "message": f"Speaker reclustering completed. {num_speakers_result} speakers identified.",
            "num_speakers": num_speakers_result,
            "speaker_labels": new_speaker_labels,
            "updated_segments_count": len(segment_to_speaker_mapping),
        }

