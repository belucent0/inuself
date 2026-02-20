"""ASR (음성 인식) 처리 프로세서.

이 모듈은 음성 파일을 전사하고 화자 분리를 수행합니다.
결과는 S3에 저장하고 Redis Stream으로 완료를 알립니다.
백엔드 DB에 직접 접근하지 않습니다.
"""

import asyncio
from pathlib import Path
from uuid import uuid4

# OpenTelemetry context 전파 (run_in_executor용)
from opentelemetry import context as otel_context

from worker.config import get_settings
from worker.logging_config import logger
from worker.utils.event_loop import setup_worker_event_loop, cleanup_worker_event_loop
from worker.utils.storage import download_file, upload_json

# from worker.utils.postprocess import (
#     split_long_segments,
#     merge_consecutive_speaker_segments,
#     rebuild_speaker_stats,
#     rebuild_transcription_text,
# )
from worker.utils.result_publisher import (
    publish_asr_started,
    publish_asr_completed,
    publish_asr_failed,
)

settings = get_settings()

# 프로젝트 루트 경로 (파이프라인에서 사용)
_worker_dir = Path(__file__).parent.parent
_project_root = _worker_dir.parent


def process_transcription_job(
    *,
    file_id: int,
    storage_key: str,
    original_filename: str,
    model_size: str,
    processing_mode: str,
    num_asr_chunks: int,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    accuracy_mode: str = "speed",
) -> None:
    """Celery 워커가 호출하는 ASR 작업 진입점."""
    logger.info("[Worker] ========================================")
    logger.info(f"[Worker] Job started: file_id={file_id}")
    logger.info(f"[Worker] File: {original_filename}")
    logger.info(f"[Worker] Storage key: {storage_key}")
    logger.info(
        f"[Worker] Model: {model_size}, Mode: {processing_mode}, Accuracy: {accuracy_mode}"
    )

    # 이벤트 루프 설정
    loop = setup_worker_event_loop()

    try:
        loop.run_until_complete(
            _process_job(
                file_id=file_id,
                storage_key=storage_key,
                original_filename=original_filename,
                model_size=model_size,
                processing_mode=processing_mode,
                num_asr_chunks=num_asr_chunks,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                accuracy_mode=accuracy_mode,
            )
        )
        logger.info(f"[Worker] OK Job completed: file_id={file_id}")
    except Exception as e:
        logger.error(f"[Worker] Job failed: file_id={file_id}, error={e}")
        # 프로세서는 상태 변경 권한 없음 - Task 레벨에서 처리하도록 예외 전파
        raise
    finally:
        cleanup_worker_event_loop(loop)


async def _process_job(
    *,
    file_id: int,
    storage_key: str,
    original_filename: str,
    model_size: str,
    processing_mode: str,
    num_asr_chunks: int,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    accuracy_mode: str = "speed",
) -> None:
    """ASR 작업 처리 함수."""
    logger.info(
        f"[Worker] [1/5] Starting job: file_id={file_id}, file={original_filename}"
    )

    # Redis Stream: 처리 시작 이벤트 발행
    publish_asr_started(file_id)
    logger.info(f"[Worker] Published processing_started event: file_id={file_id}")

    # 파일 다운로드
    logger.info(f"[Worker] [2/5] Downloading file: {storage_key}")

    temp_root = settings.temp_dir
    temp_root.mkdir(parents=True, exist_ok=True)
    file_extension = Path(original_filename).suffix
    temp_path = temp_root / f"job_{file_id}_{uuid4().hex}{file_extension}"

    try:
        download_file(storage_key, destination=temp_path)
        logger.info(f"[Worker] [3/5] File download completed: {temp_path}")

        # ASR 파이프라인 실행
        logger.info("[Worker] [4/5] Starting ASR pipeline...")

        # Lazy import: torchaudio DLL 로드 오류 방지
        from worker.pipelines.asr.pipeline import (
            PipelineResult,
            run_asr_diarization_pipeline,
        )
        from functools import partial

        pipeline_func = partial(
            run_asr_diarization_pipeline,
            temp_path,
            model_size=model_size,
            processing_mode=processing_mode,
            num_asr_chunks=num_asr_chunks,
            project_root=_project_root,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            file_id=file_id,
            accuracy_mode=accuracy_mode,
        )

        # OpenTelemetry context를 executor 스레드로 전파
        current_otel_ctx = otel_context.get_current()

        def pipeline_with_otel_context():
            """OpenTelemetry context를 복원하여 파이프라인 실행."""
            token = otel_context.attach(current_otel_ctx)
            try:
                return pipeline_func()
            finally:
                otel_context.detach(token)

        loop = asyncio.get_running_loop()
        result: PipelineResult = await loop.run_in_executor(
            None, pipeline_with_otel_context
        )
        logger.info("[Worker] [5/5] ASR pipeline completed!")

        num_speakers = len(result.speaker_stats)
        logger.info(f"[Worker] - Number of speakers: {num_speakers}")
        logger.info(f"[Worker] - Duration: {result.duration_seconds:.2f} seconds")

    except Exception as exc:
        logger.error(f"[Worker] ERROR Error occurred: {exc}")
        logger.exception("Processing failed for file_id={}", file_id)

        # Redis Stream: 실패 알림 제거 (Task 레벨로 이관)
        # publish_asr_failed(file_id, error=str(exc))
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except TypeError:
            if temp_path.exists():
                temp_path.unlink()

    # [Phase 1] Worker 역할 축소: 후처리 로직 제거 (Backend로 이관됨)
    # 이제 Worker는 Raw Segment만 반환합니다.

    # 화자 정보를 transcription에 추가 (기본 정보만)
    num_speakers = len(result.speaker_stats)
    if not result.transcription.get("diarization_metadata"):
        result.transcription["diarization_metadata"] = {}
    result.transcription["diarization_metadata"].update(
        {
            "num_speakers": num_speakers,
            "speaker_labels": sorted(result.speaker_stats.keys()),
        }
    )

    # 결과를 S3에 JSON으로 저장
    logger.info("[Worker] Saving results to S3...")

    result_data = {
        "file_id": file_id,
        "transcription": result.transcription,
        "speaker_stats": result.speaker_stats,
        "duration_seconds": result.duration_seconds,
        "logs": result.logs,
    }

    result_s3_key = f"results/asr/{file_id}/{uuid4().hex}.json"
    upload_json(result_data, key=result_s3_key)

    logger.info(f"[Worker] Results saved to S3: {result_s3_key}")

    # Redis Stream: 완료 알림
    publish_asr_completed(
        file_id,
        result_s3_key=result_s3_key,
        duration_seconds=result.duration_seconds,
        num_speakers=num_speakers,
        speaker_labels=sorted(result.speaker_stats.keys()),
    )

    logger.info("[Worker] OK ASR processing completed, result published to stream")
    logger.info("Processing completed for file_id={}", file_id)
