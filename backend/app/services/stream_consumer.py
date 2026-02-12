"""Redis Stream Consumer.

워커가 발행한 결과 메시지를 수신하여 DB에 저장합니다.

분산 추적: Worker에서 주입한 traceparent를 복원하여 trace context 연결.
"""

import asyncio
import json
import socket
from contextlib import contextmanager
from typing import Any

from redis.asyncio import Redis
from opentelemetry import context as otel_context, trace

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import download_json, delete_file, delete_files_by_prefix
from ..core.telemetry import extract_trace_context, get_tracer
from ..db.models import FileStatus
from ..db.session import AsyncSessionLocal
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from ..utils.task_queue_adapter import get_task_queue
from ..utils.progress_tracker import PipelineProgress
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

        if not message:
            logger.warning(f"Received empty data in message: {entry_id}")
            return

        msg_type = message.get("type")
        event = message.get("event")
        file_id = message.get("file_id")

        # 분산 추적: Worker에서 주입한 traceparent 복원
        with self._restore_trace_context(message):
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

    @contextmanager
    def _restore_trace_context(self, message: dict[str, Any]):
        """Worker에서 주입한 traceparent를 복원하여 Span Link로 연결.

        Redis Stream 메시지에 traceparent가 있으면 Span Link를 사용하여
        Worker의 trace와 Backend 처리를 연결합니다.

        Span Link 패턴:
        - 독립적인 새 trace 생성 (새 trace_id)
        - 원본 trace와 Link로 연결
        - Tempo UI에서 "Related Traces"로 추적 가능
        """
        from opentelemetry.trace import Link, SpanContext, TraceFlags

        traceparent = message.get("traceparent")
        tracer = get_tracer("stream-consumer")

        if not traceparent:
            # traceparent 없으면 일반 span 생성
            with tracer.start_as_current_span(
                "stream_consumer.handle_message",
                kind=trace.SpanKind.CONSUMER,
            ) as span:
                span.set_attribute("message.type", message.get("type", "unknown"))
                span.set_attribute("file.id", str(message.get("file_id", "")))
                span.set_attribute("messaging.system", "redis-stream")
                span.set_attribute("messaging.destination", "asr-results")
                yield
            return

        # traceparent에서 원본 trace context 추출
        carrier = {"traceparent": traceparent}
        parent_ctx = extract_trace_context(carrier)
        parent_span_ctx = trace.get_current_span(parent_ctx).get_span_context()

        # Span Link로 연결된 새 독립 trace 생성
        links = [Link(parent_span_ctx)] if parent_span_ctx.is_valid else []

        with tracer.start_as_current_span(
            "stream_consumer.process_result",
            kind=trace.SpanKind.CONSUMER,
            links=links,  # 원본 trace와 링크!
        ) as span:
            # 메타데이터 추가
            span.set_attribute("message.type", message.get("type", "unknown"))
            span.set_attribute("file.id", str(message.get("file_id", "")))
            span.set_attribute("messaging.system", "redis-stream")
            span.set_attribute("messaging.destination", "asr-results")
            span.set_attribute("messaging.operation", "process")

            # 원본 trace ID를 attribute로 저장 (검색 용이)
            if parent_span_ctx.is_valid:
                span.set_attribute("link.trace_id", format(parent_span_ctx.trace_id, '032x'))
                span.set_attribute("link.span_id", format(parent_span_ctx.span_id, '016x'))

            yield

    async def _handle_asr_result(self, message: dict[str, Any]) -> None:
        """ASR 결과를 처리합니다."""
        file_id = message.get("file_id")
        event = message.get("event")
        progress = PipelineProgress(file_id)

        if event == "started":
            async with AsyncSessionLocal() as session:
                file_repo = FileRepository(session)
                await file_repo.update_file_status(file_id, FileStatus.PROCESSING)
                await session.commit()
            logger.info(f"ASR started: file_id={file_id}")
            progress.asr_started()

        elif event == "completed":
            result_s3_key = message.get("result_s3_key")
            duration_seconds = message.get("duration_seconds", 0)
            num_speakers = message.get("num_speakers", 0)
            speaker_labels = message.get("speaker_labels", [])

            # S3에서 결과 다운로드 (트랜잭션 밖에서 실행)
            result_data = download_json(result_s3_key)
            transcription_data = result_data.get("transcription", {})

            # Worker Raw Segment 후처리 (트랜잭션 밖에서 실행)
            original_segments = transcription_data.get("segments", [])

            if original_segments:
                logger.info(
                    f"Applying post-processing for file_id={file_id} (segments: {len(original_segments)})"
                )

                split_segments = split_long_segments(
                    original_segments, max_duration=30.0
                )
                processed_segments = merge_consecutive_speaker_segments(
                    split_segments, max_duration=30.0
                )

                transcription_data["segments"] = processed_segments
                transcription_data["text"] = rebuild_transcription_text(
                    processed_segments
                )

                new_speaker_stats = rebuild_speaker_stats(processed_segments)

                if "diarization_metadata" not in transcription_data:
                    transcription_data["diarization_metadata"] = {}
                transcription_data["diarization_metadata"].update(
                    {
                        "num_speakers": len(new_speaker_stats),
                        "speaker_labels": sorted(new_speaker_stats.keys()),
                    }
                )

                num_speakers = len(new_speaker_stats)
                speaker_labels = sorted(new_speaker_stats.keys())

                logger.info(
                    f"Post-processing completed: {len(original_segments)} -> {len(processed_segments)} segments"
                )

            # DB 작업만 트랜잭션 안에서 실행
            async with AsyncSessionLocal() as session:
                file_repo = FileRepository(session)
                transcription_repo = TranscriptionRepository(session)

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

                # 상태 전환: QUEUED → PROCESSING → SUMMARY_QUEUED
                # Worker가 "started" 이벤트를 보내지 않으므로 여기서 PROCESSING 거쳐야 함
                current_status = await file_repo.get_content_status(file_id)
                if current_status == FileStatus.QUEUED:
                    logger.info(f"[StreamConsumer] Transitioning {file_id}: QUEUED → PROCESSING → SUMMARY_QUEUED")
                    await file_repo.update_file_status(file_id, FileStatus.PROCESSING)
                    await session.flush()

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
                progress.asr_completed(
                    duration_seconds=duration_seconds, speakers=speaker_labels,
                )

                # LLM 요약 실행
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
                        await file_repo.update_file_status(
                            file_id, FileStatus.SUMMARIZING
                        )
                        await session.commit()
                        progress.llm_started()

                        executor = SectionGraphExecutor(
                            on_progress=progress.llm_progress,
                        )
                        title, summary_md = await executor.execute(text_to_summarize)

                        if title:
                            await file_repo.update_title(file_id, title)
                        if summary_md:
                            await file_repo.update_summary_markdown(file_id, summary_md)

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
                        progress.llm_completed(
                            **({"title": title} if title else {}),
                        )

                    except (PhaseExecutionError, Exception) as exc:
                        logger.error(
                            f"LLM summarization failed: file_id={file_id}, error={exc}"
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
                        progress.llm_failed(str(exc))

                else:
                    logger.info(
                        f"No text to summarize, completing without summary: file_id={file_id}"
                    )

                    file = await file_repo.get_file(file_id)
                    if file:
                        base_name = file.filename.rsplit(".", 1)[0] if "." in file.filename else file.filename
                        empty_title = f"{base_name} - 내용 없음"
                        await file_repo.update_title(file_id, empty_title)

                    await file_repo.update_file_status(
                        file_id, FileStatus.COMPLETED, triggered_by="stream_consumer"
                    )
                    await file_repo.add_llm_log(
                        file_id,
                        log={"event": "llm_skipped", "reason": "empty_text"},
                        message="LLM skipped: No text detected in audio",
                    )
                    await session.commit()
                    progress.completed_empty("처리 완료 (음성/텍스트가 감지되지 않았습니다)")

            # ASR 활성 작업 해제
            task_queue = get_task_queue()
            task_queue.clear_active_job("asr", file_id)

        elif event == "failed":
            error = message.get("error", "Unknown error")

            async with AsyncSessionLocal() as session:
                file_repo = FileRepository(session)

                await file_repo.update_file_status(file_id, FileStatus.ASR_FAILED)
                await file_repo.add_log(
                    file_id,
                    log={"event": "asr_failed", "error": error},
                    message=f"ASR failed: {error}",
                )
                await session.commit()

            logger.error(f"ASR failed: file_id={file_id}, error={error}")
            progress.asr_failed(error)

            task_queue = get_task_queue()
            task_queue.clear_active_job("asr", file_id)

    async def _handle_llm_result(self, message: dict[str, Any]) -> None:
        """LLM 결과를 처리합니다."""
        file_id = message.get("file_id")
        event = message.get("event")
        progress = PipelineProgress(file_id)

        async with AsyncSessionLocal() as session:
            file_repo = FileRepository(session)

            if event == "started":
                await file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
                await session.commit()
                logger.info(f"LLM started: file_id={file_id}")
                progress.llm_started()

            elif event == "completed":
                result_s3_key = message.get("result_s3_key")

                result_data = download_json(result_s3_key)
                raw_response = result_data.get("raw_response", "")
                skipped = result_data.get("skipped", False)

                if skipped:
                    logger.info(f"LLM skipped (empty text): file_id={file_id}")
                    await file_repo.update_file_status(file_id, FileStatus.COMPLETED)
                    await session.commit()
                    progress.completed_empty("처리 완료 (요약 생략)")
                    return

                from .llm_summary_service import parse_llm_response

                try:
                    title, summary_md = parse_llm_response(raw_response)
                except Exception as exc:
                    logger.error(
                        f"Failed to parse LLM response: file_id={file_id}, error={exc}"
                    )
                    await file_repo.update_file_status(
                        file_id, FileStatus.SUMMARY_FAILED
                    )
                    await session.commit()
                    progress.llm_failed(str(exc))
                    return

                if title:
                    await file_repo.update_title(file_id, title)
                if summary_md:
                    await file_repo.update_summary_markdown(file_id, summary_md)

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
                    f"LLM completed: file_id={file_id}, title={title[:50]}..."
                )
                progress.llm_completed(
                    **({"title": title} if title else {}),
                )

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
                progress.llm_failed(error)

                task_queue = get_task_queue()
                task_queue.clear_active_job("llm", file_id)

    async def _handle_ocr_result(self, message: dict[str, Any]) -> None:
        """OCR 결과를 처리합니다."""
        file_id = message.get("file_id")
        event = message.get("event")
        progress = PipelineProgress(file_id)

        if event == "started":
            async with AsyncSessionLocal() as session:
                file_repo = FileRepository(session)
                await file_repo.update_file_status(file_id, FileStatus.OCR_PROCESSING)
                await session.commit()
            logger.info(f"OCR started: file_id={file_id}")
            progress.ocr_started()

        elif event == "completed":
            result_s3_key = message.get("result_s3_key")
            page_count = message.get("page_count", 0)
            text_length = message.get("text_length", 0)

            # S3에서 결과 다운로드 (트랜잭션 밖에서 실행)
            result_data = download_json(result_s3_key)
            ocr_text = result_data.get("ocr_text", "")
            ocr_metadata = result_data.get("ocr_metadata", {})

            # DB 작업만 트랜잭션 안에서 실행
            async with AsyncSessionLocal() as session:
                file_repo = FileRepository(session)
                document_repo = DocumentRepository(session)

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

                # 상태 전환: QUEUED → OCR_PROCESSING → SUMMARY_QUEUED
                # Worker가 "started" 이벤트를 보내지 않으므로 여기서 OCR_PROCESSING 거쳐야 함
                current_status = await file_repo.get_content_status(file_id)
                if current_status == FileStatus.QUEUED:
                    logger.info(f"[StreamConsumer] Transitioning {file_id}: QUEUED → OCR_PROCESSING → SUMMARY_QUEUED")
                    await file_repo.update_file_status(file_id, FileStatus.OCR_PROCESSING)
                    await session.flush()

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
                progress.ocr_completed(page_count=page_count)

                if ocr_text:
                    try:
                        await file_repo.update_file_status(
                            file_id, FileStatus.SUMMARIZING
                        )
                        await session.commit()
                        progress.llm_started()

                        executor = SectionGraphExecutor(
                            on_progress=progress.llm_progress,
                        )
                        title, summary_md = await executor.execute(ocr_text)

                        if title:
                            await file_repo.update_title(file_id, title)
                        if summary_md:
                            await file_repo.update_summary_markdown(file_id, summary_md)

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
                            message="LLM summarization completed (direct execution, OCR)",
                        )
                        await session.commit()

                        logger.info(
                            f"LLM completed (direct/OCR): file_id={file_id}, title={title[:50]}..."
                        )
                        progress.llm_completed(
                            **({"title": title} if title else {}),
                        )

                    except (PhaseExecutionError, Exception) as exc:
                        logger.error(
                            f"LLM summarization failed (OCR): file_id={file_id}, error={exc}"
                        )
                        await file_repo.update_file_status(
                            file_id, FileStatus.SUMMARY_FAILED
                        )
                        await file_repo.add_llm_log(
                            file_id,
                            log={"event": "llm_failed", "error": str(exc)},
                            message=f"LLM failed (OCR): {exc}",
                        )
                        await session.commit()
                        progress.llm_failed(str(exc))

                else:
                    logger.info(
                        f"No text from OCR, completing without summary: file_id={file_id}"
                    )

                    file = await file_repo.get_file(file_id)
                    if file:
                        base_name = file.filename.rsplit(".", 1)[0] if "." in file.filename else file.filename
                        empty_title = f"{base_name} - 내용 없음"
                        await file_repo.update_title(file_id, empty_title)

                    await file_repo.update_file_status(
                        file_id, FileStatus.COMPLETED, triggered_by="stream_consumer"
                    )
                    await file_repo.add_llm_log(
                        file_id,
                        log={"event": "llm_skipped", "reason": "empty_ocr_text"},
                        message="LLM skipped: No text detected in document",
                    )
                    await session.commit()
                    progress.completed_empty("처리 완료 (문서에서 텍스트가 감지되지 않았습니다)")

            task_queue = get_task_queue()
            task_queue.clear_active_job("ocr", file_id)

        elif event == "failed":
            error = message.get("error", "Unknown error")

            async with AsyncSessionLocal() as session:
                file_repo = FileRepository(session)

                await file_repo.update_file_status(file_id, FileStatus.OCR_FAILED)
                await file_repo.add_log(
                    file_id,
                    log={"event": "ocr_failed", "error": error},
                    message=f"OCR failed: {error}",
                )
                await session.commit()

            logger.error(f"OCR failed: file_id={file_id}, error={error}")
            progress.ocr_failed(error)

            task_queue = get_task_queue()
            task_queue.clear_active_job("ocr", file_id)

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
