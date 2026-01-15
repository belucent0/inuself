"""ASR (음성 인식) 처리 프로세서.

이 모듈은 음성 파일을 전사하고 화자 분리를 수행합니다.
결과는 S3에 저장하고 Redis Stream으로 완료를 알립니다.
백엔드 DB에 직접 접근하지 않습니다.
"""
import asyncio
from pathlib import Path
from uuid import uuid4

from worker.config import get_settings
from worker.logging_config import logger
from worker.utils.event_loop import setup_worker_event_loop, cleanup_worker_event_loop
from worker.utils.storage import download_file, upload_json
from worker.utils.postprocess import (
    split_long_segments,
    merge_consecutive_speaker_segments,
    rebuild_speaker_stats,
    rebuild_transcription_text,
)
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
    logger.info(f"[Worker] Model: {model_size}, Mode: {processing_mode}, Accuracy: {accuracy_mode}")
    
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
    logger.info(f"[Worker] [1/5] Starting job: file_id={file_id}, file={original_filename}")

    # Note: "started" 이벤트는 ASR 리소스 획득 후 pipeline에서 발행
    # (UI에 정확한 처리 상태 표시를 위해)

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
        from worker.pipelines.asr.pipeline import PipelineResult, run_asr_diarization_pipeline
        from functools import partial

        # ASR 리소스 획득 후 "started" 이벤트 발행 콜백
        def on_asr_resource_acquired():
            publish_asr_started(file_id)
            logger.info(f"[Worker] ASR resource acquired, published 'started' event for file_id={file_id}")

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
            on_asr_resource_acquired=on_asr_resource_acquired,
        )
        
        loop = asyncio.get_running_loop()
        result: PipelineResult = await loop.run_in_executor(None, pipeline_func)
        logger.info("[Worker] [5/5] ASR pipeline completed!")
        
        num_speakers = len(result.speaker_stats)
        logger.info(f"[Worker] - Number of speakers: {num_speakers}")
        logger.info(f"[Worker] - Duration: {result.duration_seconds:.2f} seconds")
        
    except Exception as exc:
        logger.error(f"[Worker] ERROR Error occurred: {exc}")
        logger.exception("Processing failed for file_id={}", file_id)
        
        # Redis Stream: 실패 알림
        publish_asr_failed(file_id, error=str(exc))
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except TypeError:
            if temp_path.exists():
                temp_path.unlink()
    
    # 후처리
    logger.info("[Worker] Post-processing: Splitting long segments and merging consecutive speaker segments...")
    
    original_segments = result.transcription.get("segments", [])
    
    # Step 1: 긴 세그먼트 분할 (30초 초과 세그먼트를 분할)
    split_segments = split_long_segments(original_segments, max_duration=30.0)
    if len(split_segments) != len(original_segments):
        logger.info(f"[Worker] Split long segments: {len(original_segments)} -> {len(split_segments)}")
    
    # Step 2: 같은 화자의 연속 세그먼트 병합 (30초 이하 범위에서)
    processed_segments = merge_consecutive_speaker_segments(split_segments, max_duration=30.0)
    
    if original_segments:
        result.transcription["segments"] = processed_segments
        result.segments = processed_segments
        result.transcription["text"] = rebuild_transcription_text(processed_segments)
        result.speaker_stats = rebuild_speaker_stats(processed_segments)
        if len(original_segments) != len(processed_segments):
            result.logs.append(
                {
                    "event": "post_processed",
                    "segments_before": len(original_segments),
                    "segments_after": len(processed_segments),
                }
            )
    
    # 화자 정보를 transcription에 추가
    num_speakers = len(result.speaker_stats)
    if not result.transcription.get("diarization_metadata"):
        result.transcription["diarization_metadata"] = {}
    result.transcription["diarization_metadata"].update({
        "num_speakers": num_speakers,
        "speaker_labels": sorted(result.speaker_stats.keys()),
    })
    
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
