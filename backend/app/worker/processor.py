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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from ..services.transcription_postprocess import (
    merge_consecutive_speaker_segments,
    rebuild_speaker_stats,
    rebuild_transcription_text,
)

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
    if min_speakers is not None or max_speakers is not None:
        logger.info(f"[Worker] Speaker range: min={min_speakers}, max={max_speakers}")
    logger.info("[Worker] ========================================")
    logger.info("Job started: file_id={}, file={}, key={}, min_speakers={}, max_speakers={}", 
               file_id, original_filename, storage_key, min_speakers, max_speakers)
    
    # Windows에서는 매 작업마다 새로운 이벤트 루프를 생성 (이벤트 루프 닫힘 문제 방지)
    # asyncpg는 Windows에서 ProactorEventLoop를 사용해야 함
    if sys.platform == "win32":
        # 기존 이벤트 루프 정리
        try:
            existing_loop = asyncio.get_event_loop()
            if existing_loop and not existing_loop.is_closed():
                # 남은 작업 완료 대기
                try:
                    pending = asyncio.all_tasks(existing_loop)
                    if pending:
                        existing_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                try:
                    existing_loop.close()
                except Exception:
                    pass
        except RuntimeError:
            pass  # 이벤트 루프가 없으면 무시
        
        # Windows에서 ProactorEventLoop 사용 (asyncpg 호환)
        # 정책을 먼저 설정한 후 새 루프 생성
        policy = asyncio.get_event_loop_policy()
        if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
        # 새 이벤트 루프 생성 및 설정
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # ProactorEventLoop의 Proactor를 명시적으로 초기화
        # ProactorEventLoop는 루프가 실행될 때 Proactor가 초기화됨
        # 따라서 빈 작업을 실행하여 Proactor를 초기화
        if isinstance(loop, asyncio.ProactorEventLoop):
            try:
                # Proactor를 초기화하기 위해 루프를 한 번 실행
                # 빈 코루틴을 실행하여 Proactor가 초기화되도록 함
                async def _init_proactor():
                    pass
                
                # 루프를 실행하여 Proactor 초기화
                loop.run_until_complete(_init_proactor())
            except Exception as exc:
                # 초기화 실패 시 루프를 다시 생성
                logger.warning("Failed to initialize Proactor, recreating loop: {}", exc)
                try:
                    loop.close()
                except Exception:
                    pass
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                # 다시 시도
                try:
                    async def _init_proactor_retry():
                        pass
                    loop.run_until_complete(_init_proactor_retry())
                except Exception:
                    logger.warning("Failed to initialize Proactor on retry")
                    pass
    else:
        # Linux/Mac에서는 전역 이벤트 루프를 재사용
        loop = _ensure_worker_loop()
    
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
        # Windows에서는 작업 완료 후 이벤트 루프 정리
        if sys.platform == "win32":
            try:
                # 남은 작업이 있으면 타임아웃과 함께 완료 대기
                pending = asyncio.all_tasks(loop)
                if pending:
                    try:
                        loop.run_until_complete(
                            asyncio.wait_for(
                                asyncio.gather(*pending, return_exceptions=True),
                                timeout=5.0
                            )
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Timeout waiting for pending tasks to complete")
                    except Exception as e:
                        logger.error("Error waiting for pending tasks: {}", e)
            except Exception as e:
                logger.error("Error during event loop cleanup: {}", e)
            finally:
                # 이벤트 루프 닫기
                try:
                    if not loop.is_closed():
                        loop.close()
                except Exception as e:
                    logger.error("Error closing event loop: {}", e)
                
                # 현재 이벤트 루프 제거 (중요! 다음 작업이 닫힌 루프를 사용하지 않도록)
                try:
                    asyncio.set_event_loop(None)
                except Exception as e:
                    logger.error("Error unsetting event loop: {}", e)


_worker_loop: asyncio.AbstractEventLoop | None = None


def _ensure_worker_loop() -> asyncio.AbstractEventLoop:
    """asyncpg 연결 재사용을 위해 단일 이벤트 루프를 생성/재사용."""
    global _worker_loop
    # 이벤트 루프가 없거나 닫혔으면 새로 생성
    if _worker_loop is None or _worker_loop.is_closed():
        if sys.platform == "win32":
            # Windows에서는 ProactorEventLoop 사용 (asyncpg 호환)
            policy = asyncio.get_event_loop_policy()
            if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    else:
        # 기존 루프가 있으면 현재 스레드에 설정
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop


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
    logger.info("Processing job file_id={} key={}", file_id, storage_key)
    is_file_based = False  # file 테이블 사용 여부
    logger.info(f"[Worker] [1/5] Starting job: file_id={file_id}, file={original_filename}")
    
    # 파일 다운로드 먼저 수행 (상태 변경 전)
    logger.info(f"[Worker] [2/5] Downloading file: {storage_key}")

    temp_root = settings.upload_dir
    temp_root.mkdir(parents=True, exist_ok=True)
    # 안전한 파일명 생성 (확장자만 유지, UUID 사용)
    file_extension = Path(original_filename).suffix  # .mp4, .wav 등
    temp_path = temp_root / f"job_{file_id}_{uuid4().hex}{file_extension}"

    try:
        download_file(storage_key, destination=temp_path)
        logger.info(f"[Worker] [3/5] File download completed: {temp_path}")
        
        # 파일 다운로드 완료 후, 실제 파이프라인 시작 전에 상태를 PROCESSING으로 변경
        # 이 시점이 실제 처리 작업이 시작되는 시점입니다.
        logger.info("[Worker] [4/5] Updating status to PROCESSING and starting ASR pipeline...")
        
        # Windows에서 각 작업마다 새로운 이벤트 루프를 사용하므로
        # 현재 이벤트 루프에서 새로운 DB 엔진과 세션을 생성해야 함
        # 전역 엔진은 다른 이벤트 루프의 연결을 사용할 수 있어 충돌 발생 가능
        current_engine = None
        if sys.platform == "win32":
            # 현재 이벤트 루프에서 새로운 엔진 생성
            current_engine = create_async_engine(
                settings.postgres_dsn,
                echo=settings.debug,
                future=True,
                pool_pre_ping=True,  # 연결 상태 체크
                pool_recycle=3600,   # 1시간마다 연결 재생성
            )
            CurrentAsyncSessionLocal = async_sessionmaker(
                current_engine,
                expire_on_commit=False,
            )
            session = CurrentAsyncSessionLocal()
        else:
            # Linux/Mac에서는 전역 엔진 사용
            session = AsyncSessionLocal()
        
        try:
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
        finally:
            await session.close()
            # Windows에서 생성한 엔진도 타임아웃과 함께 정리
            if current_engine:
                try:
                    await asyncio.wait_for(current_engine.dispose(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("Timeout disposing database engine after status update")
                except Exception as e:
                    logger.error("Error disposing database engine: {}", e)
        
        # 프로젝트 루트 경로 계산 (backend/app/worker -> backend -> project_root)
        project_root = backend_dir.parent
        
        # Lazy import: torchaudio DLL 로드 오류 방지
        from worker.pipeline import PipelineResult, run_asr_diarization_pipeline
        
        loop = asyncio.get_running_loop()
        result: PipelineResult = await loop.run_in_executor(
            None,
            lambda: run_asr_diarization_pipeline(
                temp_path,
                model_size=model_size,
                processing_mode=processing_mode,
                num_asr_chunks=num_asr_chunks,
                project_root=project_root,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                file_id=file_id,  # 락 회복을 위해 file_id 전달
            ),
        )
        logger.info("[Worker] [5/5] ASR pipeline completed!")
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
        
        # Windows에서 새로운 엔진 생성, Linux/Mac에서는 전역 엔진 사용
        error_engine = None
        if sys.platform == "win32":
            error_engine = create_async_engine(
                settings.postgres_dsn,
                echo=settings.debug,
                future=True,
            )
            ErrorAsyncSessionLocal = async_sessionmaker(
                error_engine,
                expire_on_commit=False,
            )
            session = ErrorAsyncSessionLocal()
        else:
            session = AsyncSessionLocal()
        try:
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
        finally:
            await session.close()
            if error_engine:
                try:
                    await asyncio.wait_for(error_engine.dispose(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("Timeout disposing error engine")
                except Exception as e:
                    logger.error("Error disposing error engine: {}", e)
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except TypeError:
            # Python 3.11 compatibility
            if temp_path.exists():
                temp_path.unlink()

    logger.info("[Worker] Post-processing: Merging consecutive speaker segments...")
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
    # Windows에서 새로운 엔진 생성, Linux/Mac에서는 전역 엔진 사용
    result_engine = None
    if sys.platform == "win32":
        result_engine = create_async_engine(
            settings.postgres_dsn,
            echo=settings.debug,
            future=True,
        )
        ResultAsyncSessionLocal = async_sessionmaker(
            result_engine,
            expire_on_commit=False,
        )
        session = ResultAsyncSessionLocal()
    else:
        session = AsyncSessionLocal()
    try:
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
        await file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
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
    finally:
        await session.close()
        if result_engine:
            try:
                await asyncio.wait_for(result_engine.dispose(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout disposing result engine")
            except Exception as e:
                logger.error("Error disposing result engine: {}", e)
    logger.info("[Worker] OK Database save completed, ready to enqueue summary job")
    logger.info("Processing completed for file_id={}", file_id)
    
    # LLM 요약 작업 큐잉 (Task Queue Adapter 사용)
    try:
        from .task_queue_adapter import get_task_queue
        
        task_queue = get_task_queue()
        job_id = task_queue.enqueue_llm_job(file_id=file_id)
        logger.info(f"[Worker] >> Summary job enqueued to LLM queue (file_id={file_id}, job_id={job_id})")
        logger.info("LLM job enqueued for file_id={}, job_id={}", file_id, job_id)
    except Exception as exc:
        logger.error(f"[Worker] ERROR Failed to enqueue LLM job: {exc}")
        logger.exception("Failed to enqueue LLM job for file_id={}", file_id)

