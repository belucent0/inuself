import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from ..core.config import get_settings
from ..core.logging import logger
from ..core.storage import download_file
from ..db.models import FileStatus
from ..db.session import AsyncSessionLocal, engine
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..services.transcription_postprocess import (
    merge_consecutive_speaker_segments,
    rebuild_speaker_stats,
    rebuild_transcription_text,
)
from .event_publisher import ProgressReporter

from ..core.system_utils import setup_worker_event_loop, cleanup_worker_event_loop, WorkerSessionContext

# backend/worker 모듈을 import하기 위해 경로 추가
backend_dir = Path(__file__).parent.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# worker.pipeline은 lazy import (torchaudio DLL 로드 오류 방지)
# 함수 내부에서 import하여 사용

settings = get_settings()


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
) -> None:
    """RQ 워커가 호출하는 진입점."""
    logger.info("[Worker] ========================================")
    logger.info(f"[Worker] Job started: file_id={file_id}")
    logger.info(f"[Worker] File: {original_filename}")
    logger.info(f"[Worker] Storage key: {storage_key}")
    logger.info(f"[Worker] Model: {model_size}, Mode: {processing_mode}")
    logger.info("Job started: file_id={}, file={}, key={}, min_speakers={}, max_speakers={}", 
               file_id, original_filename, storage_key, min_speakers, max_speakers)
    
    # 이벤트 루프 설정 (Windows/Linux 분기 처리는 system_utils 내부에서 수행)
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
            )
        )
        logger.info(f"[Worker] OK Job completed: file_id={file_id}")
    except Exception as e:
        # 에러는 _process_job에서 이미 로깅되므로 여기서는 재로깅하지 않음
        raise
    finally:
        # 이벤트 루프 정리
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
) -> None:
    """
    ASR 작업 처리 함수.
    
    실제 처리 중인 작업만 PROCESSING 상태로 표시하기 위해,
    상태 변경은 파일 다운로드 완료 후 파이프라인이 실제로 시작될 때 수행합니다.
    
    file_id는 file 테이블의 id입니다.
    """
    reporter = ProgressReporter(file_id)
    
    logger.info("Processing job file_id={} key={}", file_id, storage_key)
    is_file_based = False  # file 테이블 사용 여부
    logger.info(f"[Worker] [1/5] Starting job: file_id={file_id}, file={original_filename}")
    
    # 이벤트: 시작
    reporter.processing("start", 0.0, "ASR 작업 시작")
    
    # 파일 다운로드 먼저 수행 (상태 변경 전)
    logger.info(f"[Worker] [2/5] Downloading file: {storage_key}")
    reporter.processing("download_start", 10.0, "파일 다운로드 중...")

    temp_root = settings.upload_dir
    temp_root.mkdir(parents=True, exist_ok=True)
    # 안전한 파일명 생성 (확장자만 유지, UUID 사용)
    file_extension = Path(original_filename).suffix  # .mp4, .wav 등
    temp_path = temp_root / f"job_{file_id}_{uuid4().hex}{file_extension}"

    try:
        download_file(storage_key, destination=temp_path)
        logger.info(f"[Worker] [3/5] File download completed: {temp_path}")
        
        # 이벤트 발행: 파일 다운로드 완료
        reporter.processing("download_complete", 20.0, "파일 다운로드 완료, ASR 파이프라인 시작 준비 중")
        
        # 파일 다운로드 완료 후, 실제 파이프라인 시작 전에 상태를 PROCESSING으로 변경
        # 이 시점이 실제 처리 작업이 시작되는 시점입니다.
        logger.info("[Worker] [4/5] Updating status to PROCESSING and starting ASR pipeline...")
        
        # WorkerSessionContext를 사용하여 세션 생성 (OS별 처리 포함)
        async with WorkerSessionContext() as session:
            file_repo = FileRepository(session)
            
            file_obj = await file_repo.get_file(file_id)
            if not file_obj:
                logger.error("File not found: file_id={}, cannot process job", file_id)
                raise ValueError(f"File not found: file_id={file_id}")
            
            logger.info("Found file_id={} in file table (using file-based processing)", file_id)
            
            # 실제 처리 작업이 시작되는 시점에 상태를 PROCESSING으로 변경
            await file_repo.update_file_status(file_id, FileStatus.PROCESSING)
            log_entry = await file_repo.add_log(
                file_id=file_id,
                log={"event": "started", "file": original_filename},
                message="ASR processing started",
            )
            
            if not log_entry:
                logger.warning("Failed to add log: file_id={} (file may have been deleted)", file_id)
            await session.commit()

        
        # 이벤트 발행: ASR 파이프라인 시작
        reporter.processing("asr_pipeline_start", 30.0, "ASR 파이프라인 실행 중")
        
        # 프로젝트 루트 경로 계산 (backend/app/worker -> backend -> project_root)
        project_root = backend_dir.parent
        
        # Lazy import: torchaudio DLL 로드 오류 방지
        from worker.pipeline import PipelineResult, run_asr_diarization_pipeline
        from functools import partial
        
        # functools.partial을 사용하여 명시적으로 인자를 캡처 (lambda 클로저 문제 방지)
        pipeline_func = partial(
            run_asr_diarization_pipeline,
            temp_path,
            model_size=model_size,
            processing_mode=processing_mode,
            num_asr_chunks=num_asr_chunks,
            project_root=project_root,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            file_id=file_id,
        )
        
        loop = asyncio.get_running_loop()
        result: PipelineResult = await loop.run_in_executor(None, pipeline_func)
        logger.info("[Worker] [5/5] ASR pipeline completed!")
        
        # 이벤트 발행: ASR 파이프라인 완료
        reporter.processing("asr_pipeline_complete", 70.0, "ASR 파이프라인 완료, 후처리 중")
        
        num_speakers = len(result.speaker_stats)
        logger.info(f"[Worker] - Number of speakers (from stats): {num_speakers}")
        # diarization_segments에서 고유 화자 수 계산
        unique_speakers = set(seg.get("speaker", "UNKNOWN") for seg in result.diarization_segments)
        logger.info(f"[Worker] - Number of speakers (from diarization): {len(unique_speakers)}")
        logger.info(f"[Worker] - Duration: {result.duration_seconds:.2f} seconds")
    except Exception as exc:
        # 에러 로깅 (한 번만)
        logger.error(f"[Worker] ERROR Error occurred: {exc}")
        logger.error("[Worker] Updating status to FAILED...")
        logger.exception("Processing failed for file_id={}", file_id)
        
        # 이벤트: 실패
        reporter.fail(f"ASR 처리 실패: {str(exc)}")
        
        # 이벤트: 실패
        reporter.fail(f"ASR 처리 실패: {str(exc)}")
        
        # WorkerSessionContext를 사용하여 세션 생성 (OS별 처리 포함)
        async with WorkerSessionContext() as session:
            file_repo = FileRepository(session)
            
            file_obj = await file_repo.get_file(file_id)
            if file_obj:
                await file_repo.update_file_status(file_id, FileStatus.ASR_FAILED)
                await file_repo.add_log(
                    file_id=file_id,
                    log={"event": "error", "details": str(exc)},
                    message="ASR pipeline failed",
                )
            else:
                logger.warning("Cannot update status: file not found: file_id={}", file_id)
            await session.commit()
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except TypeError:
            # Python 3.11 compatibility
            if temp_path.exists():
                temp_path.unlink()

    logger.info("[Worker] Post-processing: Merging consecutive speaker segments...")
    reporter.processing("post_processing", 80.0, "결과 데이터 후처리 중...")
    
    original_segments = result.transcription.get("segments", [])
    processed_segments = merge_consecutive_speaker_segments(original_segments, max_duration=30.0)

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

    logger.info("[Worker] Saving results to database...")
    reporter.processing("db_save", 90.0, "결과 저장 중...")
    
    
    # Windows에서 새로운 엔진 생성, Linux/Mac에서는 전역 엔진 사용
    # WorkerSessionContext를 사용하여 세션 생성 (OS별 처리 포함)
    async with WorkerSessionContext() as session:
        file_repo = FileRepository(session)
        transcription_repo = TranscriptionRepository(session)
        
        file_obj = await file_repo.get_file(file_id)
        if not file_obj:
            logger.error("File not found: file_id={}, cannot save results", file_id)
            raise ValueError(f"File not found: file_id={file_id}")
        
        # Transcription 테이블에 저장
        await transcription_repo.update_transcription(
            file_id=file_id,
            speakers=list(result.speaker_stats.keys()),
            duration_seconds=result.duration_seconds,
            transcription=result.transcription,
        )
        await file_repo.update_file_status(file_id, FileStatus.SUMMARY_QUEUED)
        await file_repo.add_log(
            file_id=file_id,
            log={
                "event": "completed",
                "speaker_stats": result.speaker_stats,
                "num_speakers": num_speakers,
                "speaker_labels": sorted(result.speaker_stats.keys()),
                "logs": result.logs,
            },
            message=f"ASR processing completed (speakers: {num_speakers})",
        )
        await session.commit()
    logger.info("[Worker] OK Database save completed, ready to enqueue summary job")
    logger.info("Processing completed for file_id={}", file_id)
    
    # LLM 요약 작업 큐잉 (Task Queue Adapter 사용)
    try:
        from .task_queue_adapter import get_task_queue
        
        task_queue = get_task_queue()
        job_id = task_queue.enqueue_llm_job(file_id=file_id)
        logger.info(f"[Worker] >> Summary job enqueued to LLM queue (file_id={file_id}, job_id={job_id})")
        logger.info("LLM job enqueued for file_id={}, job_id={}", file_id, job_id)
        
        # 이벤트 발행: 요약 작업 큐잉 완료 (summary_queued 메서드 사용)
        reporter.summary_queued(
            step="asr_complete",
            progress=100.0,
            message=f"ASR 처리 완료 (화자: {num_speakers}명), 요약 작업 큐에 등록됨"
        )
        
    except Exception as exc:
        logger.error(f"[Worker] ERROR Failed to enqueue LLM job: {exc}")
        logger.exception("Failed to enqueue LLM job for file_id={}", file_id)

