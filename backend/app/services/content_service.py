from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import (
    delete_file,
    get_public_media_url,
    delete_files_by_prefix,
)
from ..core.telemetry import preserve_otel_context
from ..db.models import ContentStatus
from ..repositories.content_repository import ContentRepository
from ..schemas.content import (
    ContentDetail,
    ContentListItem,
    ContentListResponse,
)
from ..utils.celery_queue import cancel_celery_tasks_by_content_ids
from .section_executor import (
    SectionGraphExecutor,
    PhaseExecutionError,
    extract_metadata,
    generate_core_summary,
)


class ContentService:
    """콘텐츠 관련 비즈니스 로직."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ContentRepository(session)
        self.settings = get_settings()

    async def list_contents(
        self, page: int = 1, page_size: int = 10
    ) -> ContentListResponse:
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

    async def get_content(self, file_id: UUID) -> ContentDetail:
        """File ID로 콘텐츠 상세 조회."""
        content = await self.repo.get_by_file_id(file_id)
        if not content:
            raise ValueError("Content not found")
        # lazy load logs
        await self.session.refresh(content)
        detail = ContentDetail.model_validate(content)
        # media_url 추가 (File에서 object_key 조회)
        object_key = content.file.object_key if content.file else None
        detail.media_url = get_public_media_url(object_key) if object_key else None
        return detail

    async def delete_queued_contents(self) -> int:
        """QUEUED 상태인 모든 콘텐츠 삭제 (DB + 스토리지 + 큐)."""
        count, content_ids, object_keys = await self.repo.delete_queued_contents()
        await self._cleanup_queue_and_storage(content_ids, object_keys)
        await self.session.commit()
        return count

    async def delete_contents_by_ids(
        self, content_ids: list[int]
    ) -> tuple[list[int], list[int]]:
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
        skipped_ids = [
            content_id for content_id in unique_ids if content_id not in deleted_set
        ]
        return deleted_ids, skipped_ids

    async def _cleanup_queue_and_storage(
        self, content_ids: list[int], object_keys: list[str]
    ) -> None:
        """큐 작업 취소와 스토리지 파일 삭제를 일괄 처리."""
        loop = asyncio.get_running_loop()

        if content_ids:
            # Celery 큐 작업 취소
            celery_cancelled = await loop.run_in_executor(
                None, cancel_celery_tasks_by_content_ids, content_ids
            )
            if celery_cancelled:
                logger.info(
                    "Cancelled %s Celery tasks for deleted contents", celery_cancelled
                )

        # object_key 기반 파일 삭제
        for object_key in object_keys:
            try:
                await loop.run_in_executor(None, delete_file, object_key)
            except Exception as exc:
                logger.warning(
                    "Failed to delete file from storage: %s, error: %s", object_key, exc
                )

        # 임시 파일 삭제 (temp/ocr/{content_id}/, temp/asr/{content_id}/ 등)
        for content_id in content_ids:
            try:
                # OCR 임시 이미지
                ocr_prefix = f"temp/ocr/{content_id}/"
                await loop.run_in_executor(None, delete_files_by_prefix, ocr_prefix)

                # ASR 임시 파일
                asr_prefix = f"temp/asr/{content_id}/"
                await loop.run_in_executor(None, delete_files_by_prefix, asr_prefix)
            except Exception as exc:
                logger.warning(
                    "Failed to delete temp files for content_id=%s: error=%s",
                    content_id,
                    exc,
                )

    async def retry_processing(
        self,
        content_id: int,
        retry_type: str,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
        ocr_mode: str = "document",
        ocr_accuracy_mode: str = "speed",
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
            ocr_accuracy_mode: OCR 정확도 모드 ("speed", "accuracy")
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
            # File 기반 처리 (상태는 Content에서 조회)
            file_content = file_obj.content
            if retry_type == "ocr":
                # OCR 재처리
                # 허용되는 content_type: DOCUMENT, PORTRAY
                if file_obj.content_type not in [
                    ContentType.DOCUMENT,
                    ContentType.PORTRAY,
                ]:
                    raise ValueError(
                        f"Cannot retry OCR for non-document/non-portray file (content_type: {file_obj.content_type.value})"
                    )

                if file_content and file_content.status not in [
                    FileStatus.OCR_FAILED,
                    FileStatus.OCR_PROCESSING,
                    FileStatus.QUEUED,
                    FileStatus.COMPLETED,
                ]:
                    # COMPLETED 상태에서도 재처리 가능하게 허용 (모드 변경 등)
                    pass

                # OCR 모드에 따라 ContentType 업데이트
                new_content_type = ContentType.DOCUMENT
                if ocr_mode == "portray":
                    new_content_type = ContentType.PORTRAY

                # ContentType이 변경되면 업데이트
                if file_obj.content_type != new_content_type:
                    file_obj.content_type = new_content_type
                    logger.info(
                        "Content type updated to %s for file_id=%s",
                        new_content_type,
                        content_id,
                    )

                # 상태를 QUEUED로 변경 (재처리이므로 validate=False)
                await file_repo.update_file_status(
                    content_id, FileStatus.QUEUED, triggered_by="manual_retry", validate=False
                )
                await file_repo.add_log(
                    file_id=content_id,
                    log={"event": "manual_retry", "type": "ocr", "ocr_mode": ocr_mode},
                    message=f"Manual OCR retry requested by user (mode: {ocr_mode})",
                )
                await self.session.commit()

                # OCR 작업 큐잉 (Worker에서 전처리)
                loop = asyncio.get_running_loop()
                from functools import partial
                from ..utils.task_queue_adapter import get_task_queue

                task_queue = get_task_queue()
                enqueue_func = partial(
                    task_queue.enqueue_ocr_job,
                    file_id=content_id,
                    file_s3_key=file_obj.object_key,  # 원본 파일 S3 경로
                    ocr_mode=ocr_mode,
                    ocr_accuracy_mode=ocr_accuracy_mode,
                )
                job_id = await loop.run_in_executor(
                    None, preserve_otel_context(enqueue_func)
                )

                logger.info(
                    "Manual OCR retry enqueued: file_id=%s, job_id=%s",
                    content_id,
                    job_id,
                )
                return {
                    "success": True,
                    "message": "OCR reprocessing started",
                    "job_id": job_id,
                }
            elif retry_type == "asr":
                # ASR 재처리
                if file_obj.content_type != ContentType.AUDIO:
                    raise ValueError(
                        f"Cannot retry ASR for non-audio file (content_type: {file_obj.content_type.value})"
                    )

                if not file_content or file_content.status not in [
                    FileStatus.ASR_FAILED,
                    FileStatus.PROCESSING,
                    FileStatus.QUEUED,
                ]:
                    raise ValueError(
                        f"Cannot retry ASR for file with status: {file_content.status.value if file_content else 'UNKNOWN'}"
                    )

                # 화자수 범위 검증
                if (
                    min_speakers is not None
                    and max_speakers is not None
                    and min_speakers > max_speakers
                ):
                    raise ValueError(
                        "min_speakers must be less than or equal to max_speakers"
                    )

                # 상태를 QUEUED로 변경 (재처리이므로 validate=False)
                await file_repo.update_file_status(
                    content_id, FileStatus.QUEUED, triggered_by="manual_retry", validate=False
                )
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
                job_id = await loop.run_in_executor(
                    None, preserve_otel_context(enqueue_func)
                )

                logger.info(
                    "Manual ASR retry enqueued: file_id=%s, job_id=%s, min_speakers=%s, max_speakers=%s, accuracy_mode=%s",
                    content_id,
                    job_id,
                    min_speakers,
                    max_speakers,
                    accuracy_mode,
                )
                return {
                    "success": True,
                    "message": "ASR reprocessing started",
                    "job_id": job_id,
                }

            elif retry_type == "summary":
                # LLM Summary 재처리
                if not file_content or file_content.status not in [
                    FileStatus.SUMMARY_FAILED,
                    FileStatus.SUMMARY_QUEUED,
                    FileStatus.SUMMARIZING,
                    FileStatus.COMPLETED,
                ]:
                    raise ValueError(
                        f"Cannot retry LLM summary for file with status: {file_content.status.value if file_content else 'UNKNOWN'}"
                    )

                # 텍스트 추출 (타입에 따라)
                from ..repositories.transcription_repository import (
                    TranscriptionRepository,
                )
                from ..repositories.document_repository import DocumentRepository

                transcription_repo = TranscriptionRepository(self.session)
                document_repo = DocumentRepository(self.session)

                text_to_summarize = ""
                if file_obj.content_type == ContentType.AUDIO:
                    transcription = await transcription_repo.get_by_file_id(content_id)
                    if transcription and transcription.transcription:
                        text_to_summarize = str(
                            transcription.transcription.get("text", "")
                        ).strip()
                elif file_obj.content_type in [
                    ContentType.DOCUMENT,
                    ContentType.PORTRAY,
                ]:
                    document = await document_repo.get_by_file_id(content_id)
                    if document:
                        text_to_summarize = (document.ocr_text or "").strip()

                if not text_to_summarize:
                    raise ValueError(
                        f"Cannot retry LLM summary: no text available for file_id={content_id} "
                        f"(content_type: {file_obj.content_type.value})"
                    )

                # 상태를 SUMMARY_QUEUED로 변경 (재처리이므로 validate=False)
                await file_repo.update_file_status(
                    content_id, FileStatus.SUMMARY_QUEUED, triggered_by="manual_retry", validate=False
                )
                await file_repo.add_llm_log(
                    file_id=content_id,
                    log={"event": "manual_retry", "type": "llm_summary"},
                    message="Manual LLM summary retry requested by user",
                )
                await self.session.commit()

                # LLM 작업 큐잉 - [Phase 1] 프롬프트 주입 패턴
                if text_to_summarize:
                    # SectionGraphExecutor (LangGraph 기반) 사용
                    executor = SectionGraphExecutor(self.settings)

                    try:
                        # 상태를 SUMMARIZING으로 변경
                        await file_repo.update_file_status(
                            content_id, FileStatus.SUMMARIZING, triggered_by="manual_retry"
                        )
                        await file_repo.add_llm_log(
                            file_id=content_id,
                            log={"event": "manual_retry", "type": "llm_summary"},
                            message="Manual LLM summary retry requested by user",
                        )
                        await self.session.commit()

                        # [NEW] LangGraph 기반 3단계 요약 실행
                        # Phase 1: 메타데이터 추출 (기존)
                        from .llm_summary_service import summarize_transcription_3phase

                        loop = asyncio.get_running_loop()
                        metadata = await loop.run_in_executor(
                            None,
                            lambda: extract_metadata(text_to_summarize, self.settings),
                        )

                        # Phase 2: 핵심 요약 (기존)
                        core_summary = await loop.run_in_executor(
                            None,
                            lambda: generate_core_summary(
                                text_to_summarize, metadata, self.settings
                            ),
                        )

                        # Phase 3~N: LangGraph 병렬 섹션 생성
                        sections, detailed_md, logs = await executor.generate_sections(
                            toc=metadata.get("toc", []),
                            transcript=text_to_summarize,
                            keywords=metadata.get("keywords", []),
                            title=metadata.get("title", "요약"),
                            max_retries=3,
                        )

                        # 결과 조합
                        summary_md = executor.generate_summary_md(
                            metadata, core_summary, sections
                        )
                        title = metadata.get("title", "요약")

                        # 결과 저장
                        await file_repo.update_title(content_id, title)
                        await file_repo.update_summary_markdown(content_id, summary_md)
                        await file_repo.update_file_status(
                            content_id, FileStatus.COMPLETED
                        )
                        await file_repo.add_llm_log(
                            file_id=content_id,
                            log={
                                "event": "summarizing_completed",
                                "langgraph": True,
                                "sections": len(sections),
                            },
                            message="LLM summarization completed (LangGraph)",
                        )
                        await self.session.commit()

                        logger.info(
                            "Manual LLM retry completed (LangGraph): file_id=%s, title='%s', sections=%d, summary_length=%d",
                            content_id,
                            title[:50],
                            len(sections),
                            len(summary_md),
                        )
                        return {
                            "success": True,
                            "message": "LLM summary reprocessing completed (LangGraph)",
                            "file_id": content_id,
                        }

                    except Exception as exc:
                        logger.exception(
                            "LLM summarization failed for file_id=%s", content_id
                        )
                        await file_repo.update_file_status(
                            content_id, FileStatus.SUMMARY_FAILED
                        )
                        await file_repo.add_llm_log(
                            file_id=content_id,
                            log={"event": "summarizing_failed", "error": str(exc)},
                            message=f"LLM summarization failed: {exc}",
                        )
                        await self.session.commit()
                        raise ValueError(f"LLM summarization failed: {exc}")

            else:
                raise ValueError(
                    f"Invalid retry_type: {retry_type}. Must be 'asr', 'summary', or 'ocr'"
                )

        # 하위 호환성: Content 기반 처리 (file_id -> Content UUID 조회)
        content = await self.repo.get_by_file_id(content_id)
        if not content:
            raise ValueError("Content not found")

        # Content.id (UUID)를 사용
        content_uuid = content.id

        if retry_type == "asr":
            # PROCESSING, QUEUED 상태에서 멈춘 경우도 재시도 가능
            if content.status not in [
                ContentStatus.ASR_FAILED,
                ContentStatus.PROCESSING,
                ContentStatus.QUEUED,
            ]:
                raise ValueError(
                    f"Cannot retry ASR for content with status: {content.status.value}"
                )

            # 화자수 범위 검증
            if (
                min_speakers is not None
                and max_speakers is not None
                and min_speakers > max_speakers
            ):
                raise ValueError(
                    "min_speakers must be less than or equal to max_speakers"
                )

            # 상태를 QUEUED로 변경 (UUID 사용)
            await self.repo.update_content_status(content_uuid, ContentStatus.QUEUED)
            log_data = {"event": "manual_retry", "type": "asr"}
            if min_speakers is not None:
                log_data["min_speakers"] = min_speakers
            if max_speakers is not None:
                log_data["max_speakers"] = max_speakers
            await self.repo.add_log(
                content_id=content_uuid,
                log=log_data,
                message="Manual ASR retry requested by user",
            )
            await self.session.commit()

            # 큐에 작업 등록
            loop = asyncio.get_running_loop()
            from functools import partial
            from ..utils.task_queue_adapter import get_task_queue

            task_queue = get_task_queue()
            # File에서 object_key, filename 조회
            storage_key = content.file.object_key if content.file else content.object_key
            original_filename = content.file.filename if content.file else content.filename
            enqueue_func = partial(
                task_queue.enqueue_asr_job,
                file_id=content_id,
                storage_key=storage_key,
                original_filename=original_filename,
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
                "Manual ASR retry enqueued: content_id=%s, job_id=%s, min_speakers=%s, max_speakers=%s, accuracy_mode=%s",
                content_id,
                job_id,
                min_speakers,
                max_speakers,
                accuracy_mode,
            )
            return {
                "success": True,
                "message": "ASR reprocessing started",
                "job_id": job_id,
            }

        elif retry_type == "summary":
            if content.status not in [
                ContentStatus.SUMMARY_FAILED,
                ContentStatus.SUMMARY_QUEUED,
                ContentStatus.SUMMARIZING,
            ]:
                raise ValueError(
                    f"Cannot retry LLM summary for content with status: {content.status.value}"
                )

            # 텍스트 추출 (Transcription/Document 관계에서 가져오기)
            text_to_summarize = ""
            if content.transcription_result and content.transcription_result.transcription:
                # ASR 전사 텍스트
                transcript_data = content.transcription_result.transcription
                text_to_summarize = str(transcript_data.get("text", "")).strip()
            elif content.document_result:
                # OCR 텍스트
                text_to_summarize = (content.document_result.ocr_text or "").strip()

            if not text_to_summarize:
                raise ValueError(
                    f"Cannot retry LLM summary: no text available for file_id={content_id}"
                )

            # 상태를 SUMMARY_QUEUED로 변경 (UUID 사용)
            await self.repo.update_content_status(
                content_uuid, ContentStatus.SUMMARY_QUEUED
            )
            await self.repo.add_llm_log(
                content_id=content_uuid,
                log={"event": "manual_retry", "type": "llm_summary"},
                message="Manual LLM summary retry requested by user",
            )
            await self.session.commit()

            # 큐에 작업 등록
            loop = asyncio.get_running_loop()
            from functools import partial
            from ..utils.task_queue_adapter import get_task_queue

            task_queue = get_task_queue()
            enqueue_func = partial(
                task_queue.enqueue_llm_job,
                file_id=content_id,
                text_to_summarize=text_to_summarize,
            )
            job_id = await loop.run_in_executor(
                None, preserve_otel_context(enqueue_func)
            )

            logger.info(
                "Manual LLM retry enqueued: content_id=%s, job_id=%s, text_length=%d",
                content_id,
                job_id,
                len(text_to_summarize),
            )
            return {
                "success": True,
                "message": "LLM summary reprocessing started",
                "job_id": job_id,
            }

        else:
            raise ValueError(
                f"Invalid retry_type: {retry_type}. Must be 'asr' or 'summary'"
            )

    async def recluster_speakers(
        self,
        file_id: UUID,
        num_speakers: int | None = None,
        similarity_threshold: float = 0.7,
    ) -> dict[str, Any]:
        """
        저장된 세그먼트 임베딩을 기반으로 화자를 재클러스터링합니다.

        Args:
            file_id: 파일 ID
            num_speakers: 목표 화자 수 (None이면 자동 결정)
            similarity_threshold: 코사인 유사도 임계값

        Returns:
            재클러스터링 결과 딕셔너리
        """
        # 콘텐츠 조회 및 검증 (file_id -> Content 조회)
        content = await self.repo.get_by_file_id(file_id)
        if not content:
            raise ValueError("Content not found")

        if content.status != ContentStatus.COMPLETED:
            raise ValueError(
                f"Content must be COMPLETED status. Current status: {content.status}"
            )

        # transcription에서 segment_embeddings 확인 (Transcription 관계에서 가져오기)
        if not content.transcription_result or not content.transcription_result.transcription:
            raise ValueError("Transcription not found")

        transcription = content.transcription_result.transcription
        diarization_metadata = transcription.get("diarization_metadata", {})
        segment_embeddings = diarization_metadata.get("segment_embeddings", [])

        if not segment_embeddings:
            raise ValueError(
                "segment_embeddings not found. Please ensure the content has been processed with diarization."
            )

        # Content.id (UUID) 사용
        content_uuid = content.id

        logger.info(
            "[Reclustering] Starting reclustering for file_id=%s, content_uuid=%s, num_speakers=%s, threshold=%s",
            file_id,
            content_uuid,
            num_speakers,
            similarity_threshold,
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
        new_speaker_labels = updated_transcription["diarization_metadata"][
            "speaker_labels"
        ]
        num_speakers_result = len(new_speaker_labels)

        # DB 업데이트 (TranscriptionRepository 사용, UUID 기반)
        from ..repositories.transcription_repository import TranscriptionRepository
        transcription_repo = TranscriptionRepository(self.session)
        await transcription_repo.update_transcription_by_content_id(
            content_id=content_uuid,
            speakers=new_speaker_labels,
            duration_seconds=content.transcription_result.duration_seconds,
            transcription=updated_transcription,
        )
        await self.session.commit()

        # 로그 기록 (UUID 사용)
        await self.repo.add_log(
            content_id=content_uuid,
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
            "[Reclustering] Completed for file_id=%s, content_uuid=%s, num_speakers=%s",
            file_id,
            content_uuid,
            num_speakers_result,
        )

        return {
            "message": f"Speaker reclustering completed. {num_speakers_result} speakers identified.",
            "num_speakers": num_speakers_result,
            "speaker_labels": new_speaker_labels,
            "updated_segments_count": len(segment_to_speaker_mapping),
        }
