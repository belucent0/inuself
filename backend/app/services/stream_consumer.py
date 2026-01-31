"""Redis Stream Consumer.

워커가 발행한 결과 메시지를 수신하여 DB에 저장합니다.
"""

import asyncio
import json
import socket
from typing import Any

from redis.asyncio import Redis

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import download_json, delete_file, delete_files_by_prefix
from ..db.models import FileStatus
from ..db.session import AsyncSessionLocal
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from ..utils.task_queue_adapter import get_task_queue
from ..utils.event_publisher import publish_file_progress
from .transcription_postprocess import (
    segments_to_text_with_metadata,
    split_long_segments,
    merge_consecutive_speaker_segments,
    rebuild_speaker_stats,
    rebuild_transcription_text,
)
from .section_executor import SectionGraphExecutor, PhaseExecutionError

# Stream 이름 (worker/utils/result_publisher.py와 동일해야 함)
RESULT_STREAM = "stream:worker:results"
CONSUMER_GROUP = "backend"


class StreamConsumer:
    """Redis Stream Consumer.

    워커가 발행한 결과 메시지를 수신하여 DB에 저장합니다.
    """

    def __init__(self):
        self.settings = get_settings()
        self.redis: Redis | None = None
        self.consumer_name = f"backend-{socket.gethostname()}"
        self._running = False

    async def _get_redis(self) -> Redis:
        """Redis 클라이언트를 가져옵니다."""
        if self.redis is None:
            self.redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
        return self.redis

    async def _ensure_consumer_group(self) -> None:
        """Consumer Group이 존재하는지 확인하고 없으면 생성합니다."""
        redis = await self._get_redis()
        try:
            await redis.xgroup_create(
                RESULT_STREAM, CONSUMER_GROUP, id="0", mkstream=True
            )
            logger.info(
                f"Created consumer group: stream={RESULT_STREAM}, group={CONSUMER_GROUP}"
            )
        except Exception as e:
            error_str = str(e).upper()
            # 이미 존재하는 경우 무시
            if "BUSYGROUP" in error_str:
                logger.debug(f"Consumer group already exists: {CONSUMER_GROUP}")
            else:
                logger.error(f"Failed to create consumer group: {e}")
                raise

    async def start(self) -> None:
        """Stream Consumer를 시작합니다."""
        logger.info(f"Starting StreamConsumer: {self.consumer_name}")

        await self._ensure_consumer_group()
        self._running = True

        redis = await self._get_redis()

        # 시작 시 처리되지 않은(Pending) 메시지 확인 및 복구
        await self._claim_pending_messages()

        while self._running:
            try:
                # 새 메시지 읽기 (5초 블록)
                messages = await redis.xreadgroup(
                    groupname=CONSUMER_GROUP,
                    consumername=self.consumer_name,
                    streams={RESULT_STREAM: ">"},
                    count=10,
                    block=5000,
                )

                if not messages:
                    continue

                for stream_name, entries in messages:
                    for entry_id, data in entries:
                        try:
                            await self._handle_message(entry_id, data)
                            # 처리 완료 후 ACK
                            await redis.xack(RESULT_STREAM, CONSUMER_GROUP, entry_id)
                        except Exception as e:
                            logger.error(f"Failed to handle message {entry_id}: {e}")
                            # TODO: 실패한 메시지 처리 (재시도 또는 DLQ)

            except asyncio.CancelledError:
                logger.info("StreamConsumer cancelled")
                break
            except Exception as e:
                error_str = str(e).upper()
                # NOGROUP 에러 시 컨슈머 그룹 재생성 시도
                if "NOGROUP" in error_str:
                    logger.warning(f"Consumer group not found, recreating: {e}")
                    try:
                        await self._ensure_consumer_group()
                    except Exception as create_err:
                        logger.error(f"Failed to recreate consumer group: {create_err}")
                else:
                    logger.error(f"StreamConsumer error: {e}")
                await asyncio.sleep(1)

        logger.info("StreamConsumer stopped")

    async def _claim_pending_messages(self) -> None:
        """처리되지 않은(Pending) 메시지를 가져와서 처리합니다 (Recovery)."""
        redis = await self._get_redis()

        try:
            # 1. 자신의 Pending 메시지 확인 (재시작 시)
            # '0'은 history의 처음부터 끝까지 모든 pending 메시지를 의미
            pending_messages = await redis.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=self.consumer_name,
                streams={RESULT_STREAM: "0"},
                count=100,
            )

            if pending_messages:
                logger.info(
                    f"[Recovery] Found pending messages for {self.consumer_name}"
                )
                for stream_name, entries in pending_messages:
                    for entry_id, data in entries:
                        logger.info(
                            f"[Recovery] Reprocessing pending message: {entry_id}"
                        )
                        try:
                            await self._handle_message(entry_id, data)
                            await redis.xack(RESULT_STREAM, CONSUMER_GROUP, entry_id)
                            logger.info(
                                f"[Recovery] Successfully recovered message: {entry_id}"
                            )
                        except Exception as e:
                            logger.error(
                                f"[Recovery] Failed to recover message {entry_id}: {e}"
                            )
            else:
                logger.info(f"[Recovery] No pending messages for {self.consumer_name}")

        except Exception as e:
            logger.error(f"[Recovery] Error claiming pending messages: {e}")

    async def stop(self) -> None:
        """Stream Consumer를 중지합니다."""
        self._running = False
        if self.redis:
            await self.redis.close()
            self.redis = None

    async def _handle_message(self, entry_id: str, data: dict[str, Any]) -> None:
        """메시지를 처리합니다."""
        # data는 {"data": json_string} 형태
        message = json.loads(data.get("data", "{}"))

        msg_type = message.get("type")
        event = message.get("event")
        file_id = message.get("file_id")

        if not message:
            logger.warning(f"Received empty data in message: {entry_id}")
            return

        msg_type = message.get("type")
        event = message.get("event")
        file_id = message.get("file_id")

        logger.info(
            f"[StreamConsumer] Handling message {entry_id}: type={msg_type}, event={event}, file_id={file_id}"
        )
        logger.debug(f"[StreamConsumer] Message payload: {message}")

        if msg_type == "asr":
            await self._handle_asr_result(message)
        elif msg_type == "llm":
            await self._handle_llm_result(message)
        elif msg_type == "ocr":
            await self._handle_ocr_result(message)
        else:
            logger.warning(f"Unknown message type: {msg_type}")

    async def _handle_asr_result(self, message: dict[str, Any]) -> None:
        """ASR 결과를 처리합니다."""
        file_id = message.get("file_id")
        event = message.get("event")

        async with AsyncSessionLocal() as session:
            file_repo = FileRepository(session)
            transcription_repo = TranscriptionRepository(session)

            if event == "started":
                # 상태 업데이트: PROCESSING
                await file_repo.update_file_status(file_id, FileStatus.PROCESSING)
                await session.commit()
                logger.info(f"ASR started: file_id={file_id}")

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="processing",
                    step="asr",
                    progress=10.0,
                    message="음성 인식 처리 중...",
                )

            elif event == "completed":
                result_s3_key = message.get("result_s3_key")
                duration_seconds = message.get("duration_seconds", 0)
                num_speakers = message.get("num_speakers", 0)
                speaker_labels = message.get("speaker_labels", [])

                # S3에서 결과 다운로드
                result_data = download_json(result_s3_key)
                transcription_data = result_data.get("transcription", {})

                # =========================================================
                # [Phase 1] Worker 역할 축소: 후처리 로직을 Backend로 이관
                # =========================================================
                # Worker는 이제 Raw Segment만 반환하므로, 여기서 후처리(분할/병합)를 수행합니다.
                original_segments = transcription_data.get("segments", [])

                if original_segments:
                    logger.info(
                        f"Applying post-processing for file_id={file_id} (segments: {len(original_segments)})"
                    )

                    # 1. 긴 세그먼트 분할 (>30s)
                    split_segments = split_long_segments(
                        original_segments, max_duration=30.0
                    )

                    # 2. 연속된 화자 세그먼트 병합
                    processed_segments = merge_consecutive_speaker_segments(
                        split_segments, max_duration=30.0
                    )

                    # 3. 데이터 갱신
                    transcription_data["segments"] = processed_segments
                    transcription_data["text"] = rebuild_transcription_text(
                        processed_segments
                    )

                    # 4. 화자 통계 재계산
                    new_speaker_stats = rebuild_speaker_stats(processed_segments)

                    # 5. 메타데이터 업데이트
                    if "diarization_metadata" not in transcription_data:
                        transcription_data["diarization_metadata"] = {}

                    transcription_data["diarization_metadata"].update(
                        {
                            "num_speakers": len(new_speaker_stats),
                            "speaker_labels": sorted(new_speaker_stats.keys()),
                        }
                    )

                    # duration/speaker 정보도 갱신된 통계 기반으로 업데이트
                    num_speakers = len(new_speaker_stats)
                    speaker_labels = sorted(new_speaker_stats.keys())

                    logger.info(
                        f"Post-processing completed: {len(original_segments)} -> {len(processed_segments)} segments"
                    )

                # Transcription 저장 (있으면 업데이트, 없으면 생성)
                existing = await transcription_repo.get_by_file_id(file_id)
                if existing:
                    await transcription_repo.update_transcription(
                        file_id=file_id,
                        speakers=speaker_labels,
                        duration_seconds=duration_seconds,
                        transcription=transcription_data,
                    )
                else:
                    await transcription_repo.create_transcription(
                        file_id=file_id,
                        speakers=speaker_labels,
                        duration_seconds=duration_seconds,
                        transcription=transcription_data,
                    )

                # 상태 업데이트: SUMMARY_QUEUED
                await file_repo.update_file_status(file_id, FileStatus.SUMMARY_QUEUED)
                await file_repo.add_log(
                    file_id,
                    log={
                        "event": "asr_completed",
                        "duration_seconds": duration_seconds,
                        "num_speakers": num_speakers,
                    },
                    message=f"ASR completed ({duration_seconds:.1f}s, {num_speakers} speakers)",
                )
                await session.commit()

                logger.info(
                    f"ASR completed: file_id={file_id}, duration={duration_seconds}s"
                )

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="summary_queued",
                    step="asr_completed",
                    progress=50.0,
                    message="음성 인식 완료, 요약 대기 중...",
                    metadata={
                        "duration_seconds": duration_seconds,
                        "speakers": speaker_labels,
                    },
                )

                # LLM 요약 실행 - SectionGraphExecutor 사용 (직접 실행)
                segments = transcription_data.get("segments", [])
                if segments:
                    text_to_summarize = segments_to_text_with_metadata(segments)
                    logger.info(
                        f"Using segment format with speaker/time metadata: file_id={file_id}"
                    )
                else:
                    text_to_summarize = transcription_data.get("text", "")

                if text_to_summarize:
                    try:
                        # 상태 업데이트: SUMMARIZING
                        await file_repo.update_file_status(
                            file_id, FileStatus.SUMMARIZING
                        )
                        await session.commit()

                        # 클라이언트에 이벤트 발행
                        publish_file_progress(
                            file_id=file_id,
                            status="summarizing",
                            step="llm",
                            progress=60.0,
                            message="요약 생성 중...",
                        )

                        # SectionGraphExecutor로 직접 요약 실행
                        executor = SectionGraphExecutor()
                        title, summary_md = await executor.execute(text_to_summarize)

                        # 제목과 요약 저장
                        if title:
                            await file_repo.update_title(file_id, title)
                        if summary_md:
                            await file_repo.update_summary_markdown(file_id, summary_md)

                        # 상태 업데이트: COMPLETED
                        await file_repo.update_file_status(
                            file_id, FileStatus.COMPLETED
                        )
                        await file_repo.add_llm_log(
                            file_id,
                            log={
                                "event": "llm_completed",
                                "title_length": len(title),
                                "summary_length": len(summary_md),
                            },
                            message="LLM summarization completed (direct execution)",
                        )
                        await session.commit()

                        logger.info(
                            f"LLM completed (direct): file_id={file_id}, title={title[:50]}..."
                        )

                        # 클라이언트에 이벤트 발행
                        publish_file_progress(
                            file_id=file_id,
                            status="completed",
                            step="completed",
                            progress=100.0,
                            message="모든 처리가 완료되었습니다.",
                            metadata={"title": title} if title else None,
                        )

                    except PhaseExecutionError as exc:
                        logger.error(
                            f"LLM summarization failed (PhaseExecutionError): file_id={file_id}, error={exc}"
                        )
                        await file_repo.update_file_status(
                            file_id, FileStatus.SUMMARY_FAILED
                        )
                        await file_repo.add_llm_log(
                            file_id,
                            log={"event": "llm_failed", "error": str(exc)},
                            message=f"LLM failed: {exc}",
                        )
                        await session.commit()

                        # 클라이언트에 이벤트 발행
                        publish_file_progress(
                            file_id=file_id,
                            status="failed",
                            step="llm_failed",
                            progress=0.0,
                            message=f"요약 생성 실패: {exc}",
                        )

                    except Exception as exc:
                        logger.error(
                            f"LLM summarization failed (unexpected): file_id={file_id}, error={exc}"
                        )
                        await file_repo.update_file_status(
                            file_id, FileStatus.SUMMARY_FAILED
                        )
                        await file_repo.add_llm_log(
                            file_id,
                            log={"event": "llm_failed", "error": str(exc)},
                            message=f"LLM failed: {exc}",
                        )
                        await session.commit()

                        # 클라이언트에 이벤트 발행
                        publish_file_progress(
                            file_id=file_id,
                            status="failed",
                            step="llm_failed",
                            progress=0.0,
                            message=f"요약 생성 실패: {exc}",
                        )

                # ASR 활성 작업 해제
                task_queue = get_task_queue()
                task_queue.clear_active_job("asr", file_id)

            elif event == "failed":
                error = message.get("error", "Unknown error")

                await file_repo.update_file_status(file_id, FileStatus.ASR_FAILED)
                await file_repo.add_log(
                    file_id,
                    log={"event": "asr_failed", "error": error},
                    message=f"ASR failed: {error}",
                )
                await session.commit()

                logger.error(f"ASR failed: file_id={file_id}, error={error}")

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="failed",
                    step="asr_failed",
                    progress=0.0,
                    message=f"음성 인식 실패: {error}",
                )

                # ASR 활성 작업 해제
                task_queue = get_task_queue()
                task_queue.clear_active_job("asr", file_id)

    async def _handle_llm_result(self, message: dict[str, Any]) -> None:
        """LLM 결과를 처리합니다."""
        file_id = message.get("file_id")
        event = message.get("event")

        async with AsyncSessionLocal() as session:
            file_repo = FileRepository(session)

            if event == "started":
                await file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
                await session.commit()
                logger.info(f"LLM started: file_id={file_id}")

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="summarizing",
                    step="llm",
                    progress=60.0,
                    message="요약 생성 중...",
                )

            elif event == "completed":
                result_s3_key = message.get("result_s3_key")

                # S3에서 결과 다운로드
                result_data = download_json(result_s3_key)
                raw_response = result_data.get(
                    "raw_response", ""
                )  # [Phase 1] Worker는 Raw 응답만 반환
                skipped = result_data.get("skipped", False)

                if skipped:
                    logger.info(f"LLM skipped (empty text): file_id={file_id}")

                    # 상태 업데이트: COMPLETED (요약 생략)
                    await file_repo.update_file_status(file_id, FileStatus.COMPLETED)
                    await session.commit()
                    return

                # [Phase 1] Backend에서 응답 파싱
                from .llm_summary_service import parse_llm_response

                # Raw 응답 파싱
                try:
                    title, summary_md = parse_llm_response(raw_response)
                except Exception as exc:
                    logger.error(
                        f"Failed to parse LLM response: file_id={file_id}, error={exc}"
                    )
                    # 파싱 실패 시 실패 처리
                    await file_repo.update_file_status(
                        file_id, FileStatus.SUMMARY_FAILED
                    )
                    await session.commit()
                    return

                # 제목과 요약 저장
                if title:
                    await file_repo.update_title(file_id, title)
                if summary_md:
                    await file_repo.update_summary_markdown(file_id, summary_md)

                # 상태 업데이트: COMPLETED
                await file_repo.update_file_status(file_id, FileStatus.COMPLETED)
                await file_repo.add_llm_log(
                    file_id,
                    log={
                        "event": "llm_completed",
                        "title_length": len(title),
                        "summary_length": len(summary_md),
                    },
                    message="LLM summarization completed",
                )
                await session.commit()

                logger.info(
                    f"LLM completed (Prompt Injection): file_id={file_id}, title={title[:50]}..."
                )

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="completed",
                    step="completed",
                    progress=100.0,
                    message="모든 처리가 완료되었습니다.",
                    metadata={"title": title} if title else None,
                )

                # LLM 활성 작업 해제
                task_queue = get_task_queue()
                task_queue.clear_active_job("llm", file_id)

            elif event == "failed":
                error = message.get("error", "Unknown error")

                await file_repo.update_file_status(file_id, FileStatus.SUMMARY_FAILED)
                await file_repo.add_llm_log(
                    file_id,
                    log={"event": "llm_failed", "error": error},
                    message=f"LLM failed: {error}",
                )
                await session.commit()

                logger.error(f"LLM failed: file_id={file_id}, error={error}")

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="failed",
                    step="llm_failed",
                    progress=0.0,
                    message=f"요약 생성 실패: {error}",
                )

                # LLM 활성 작업 해제
                task_queue = get_task_queue()
                task_queue.clear_active_job("llm", file_id)

    async def _handle_ocr_result(self, message: dict[str, Any]) -> None:
        """OCR 결과를 처리합니다."""
        file_id = message.get("file_id")
        event = message.get("event")

        async with AsyncSessionLocal() as session:
            file_repo = FileRepository(session)
            document_repo = DocumentRepository(session)

            if event == "started":
                await file_repo.update_file_status(file_id, FileStatus.OCR_PROCESSING)
                await session.commit()
                logger.info(f"OCR started: file_id={file_id}")

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="processing",
                    step="ocr",
                    progress=10.0,
                    message="문서 인식 처리 중...",
                )

            elif event == "completed":
                result_s3_key = message.get("result_s3_key")
                page_count = message.get("page_count", 0)
                text_length = message.get("text_length", 0)

                # S3에서 결과 다운로드
                result_data = download_json(result_s3_key)
                ocr_text = result_data.get("ocr_text", "")
                ocr_metadata = result_data.get("ocr_metadata", {})

                # Document 저장/업데이트
                existing_doc = await document_repo.get_by_file_id(file_id)
                if existing_doc:
                    await document_repo.update_document(
                        file_id=file_id,
                        ocr_text=ocr_text,
                        page_count=page_count,
                        ocr_metadata=ocr_metadata,
                    )
                else:
                    await document_repo.create_document(
                        file_id=file_id,
                        ocr_text=ocr_text,
                        page_count=page_count,
                        ocr_metadata=ocr_metadata,
                    )

                # 상태 업데이트: SUMMARY_QUEUED
                await file_repo.update_file_status(file_id, FileStatus.SUMMARY_QUEUED)
                await file_repo.add_log(
                    file_id,
                    log={
                        "event": "ocr_completed",
                        "page_count": page_count,
                        "text_length": text_length,
                    },
                    message=f"OCR completed ({page_count} pages, {text_length} chars)",
                )
                await session.commit()

                logger.info(f"OCR completed: file_id={file_id}, pages={page_count}")

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="summary_queued",
                    step="ocr_completed",
                    progress=50.0,
                    message="문서 인식 완료, 요약 대기 중...",
                    metadata={"page_count": page_count},
                )

                # LLM 요약 큐잉
                if ocr_text:
                    task_queue = get_task_queue()
                    task_queue.enqueue_llm_job(
                        file_id=file_id, text_to_summarize=ocr_text
                    )
                    logger.info(f"LLM job enqueued: file_id={file_id}")

                # OCR 활성 작업 해제
                task_queue = get_task_queue()
                task_queue.clear_active_job("ocr", file_id)

            elif event == "failed":
                error = message.get("error", "Unknown error")

                await file_repo.update_file_status(file_id, FileStatus.OCR_FAILED)
                await file_repo.add_log(
                    file_id,
                    log={"event": "ocr_failed", "error": error},
                    message=f"OCR failed: {error}",
                )
                await session.commit()

                logger.error(f"OCR failed: file_id={file_id}, error={error}")

                # 클라이언트에 이벤트 발행
                publish_file_progress(
                    file_id=file_id,
                    status="failed",
                    step="ocr_failed",
                    progress=0.0,
                    message=f"문서 인식 실패: {error}",
                )

                # OCR 활성 작업 해제
                task_queue = get_task_queue()
                task_queue.clear_active_job("ocr", file_id)

                # 임시 이미지 삭제 (실패 시)
                try:
                    await self._delete_temp_ocr_images(file_id)
                except Exception as e:
                    logger.warning(
                        f"Failed to delete temp OCR images: file_id={file_id}, error={e}"
                    )


# 싱글톤 인스턴스
_consumer: StreamConsumer | None = None


def get_stream_consumer() -> StreamConsumer:
    """StreamConsumer 싱글톤 인스턴스를 반환합니다."""
    global _consumer
    if _consumer is None:
        _consumer = StreamConsumer()
    return _consumer
