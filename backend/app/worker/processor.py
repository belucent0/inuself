import asyncio
import logging
import sys
from pathlib import Path
from uuid import uuid4

from ..core.config import get_settings
from ..core.storage import download_file
from ..db.models import ContentStatus
from ..db.session import AsyncSessionLocal
from ..repositories.content_repository import ContentRepository

# backend/worker 모듈을 import하기 위해 경로 추가
backend_dir = Path(__file__).parent.parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from worker.pipeline import PipelineResult, run_asr_diarization_pipeline

logger = logging.getLogger(__name__)
settings = get_settings()


def process_transcription_job(
    *,
    content_id: int,
    storage_key: str,
    original_filename: str,
    model_size: str,
    processing_mode: str,
    num_asr_chunks: int,
) -> None:
    """RQ 워커가 호출하는 진입점."""
    print(f"[Worker] ========================================")
    print(f"[Worker] 작업 시작: content_id={content_id}")
    print(f"[Worker] 파일: {original_filename}")
    print(f"[Worker] 스토리지 키: {storage_key}")
    print(f"[Worker] 모델: {model_size}, 모드: {processing_mode}")
    print(f"[Worker] ========================================")
    logger.info("Job started: content_id=%s, file=%s, key=%s", content_id, original_filename, storage_key)
    
    # 전역 이벤트 루프를 재사용해 asyncpg가 동일 루프를 공유하도록 함
    loop = _ensure_worker_loop()
    try:
        loop.run_until_complete(
            _process_job(
                content_id=content_id,
                storage_key=storage_key,
                original_filename=original_filename,
                model_size=model_size,
                processing_mode=processing_mode,
                num_asr_chunks=num_asr_chunks,
            )
        )
        print(f"[Worker] ✓ 작업 완료: content_id={content_id}")
    except Exception as e:
        print(f"[Worker] ✗ 작업 실패: content_id={content_id}, error={e}")
        logger.exception("Job failed: content_id=%s", content_id)
        raise


_worker_loop: asyncio.AbstractEventLoop | None = None


def _ensure_worker_loop() -> asyncio.AbstractEventLoop:
    """asyncpg 연결 재사용을 위해 단일 이벤트 루프를 생성/재사용."""
    global _worker_loop
    if _worker_loop is None:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        _worker_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_worker_loop)
    return _worker_loop


async def _process_job(
    *,
    content_id: int,
    storage_key: str,
    original_filename: str,
    model_size: str,
    processing_mode: str,
    num_asr_chunks: int,
) -> None:
    print(f"[Worker] [1/5] 상태를 PROCESSING으로 변경 중...")
    logger.info("Processing job content_id=%s key=%s", content_id, storage_key)
    
    # 상태를 PROCESSING으로 변경 (세션을 명시적으로 닫기)
    session = AsyncSessionLocal()
    try:
        repo = ContentRepository(session)
        await repo.update_content_status(content_id, ContentStatus.PROCESSING)
        await repo.add_log(
            content_id=content_id,
            log={"event": "started", "file": original_filename},
            message="ASR processing started",
        )
        await session.commit()
    finally:
        await session.close()
    print(f"[Worker] [2/5] 파일 다운로드 중: {storage_key}")

    temp_root = settings.upload_dir
    temp_root.mkdir(parents=True, exist_ok=True)
    # 안전한 파일명 생성 (확장자만 유지, UUID 사용)
    file_extension = Path(original_filename).suffix  # .mp4, .wav 등
    temp_path = temp_root / f"job_{content_id}_{uuid4().hex}{file_extension}"

    try:
        download_file(storage_key, destination=temp_path)
        print(f"[Worker] [3/5] 파일 다운로드 완료: {temp_path}")
        print(f"[Worker] [4/5] ASR 파이프라인 실행 중... (이 작업은 시간이 걸릴 수 있습니다)")
        
        # 프로젝트 루트 경로 계산 (backend/app/worker -> backend -> project_root)
        project_root = backend_dir.parent
        
        loop = asyncio.get_running_loop()
        result: PipelineResult = await loop.run_in_executor(
            None,
            lambda: run_asr_diarization_pipeline(
                temp_path,
                model_size=model_size,
                processing_mode=processing_mode,
                num_asr_chunks=num_asr_chunks,
                project_root=project_root,
            ),
        )
        print(f"[Worker] [5/5] ASR 파이프라인 완료!")
        print(f"[Worker] - 화자 수: {len(result.speaker_stats)}")
        print(f"[Worker] - 재생 길이: {result.duration_seconds:.2f}초")
    except Exception as exc:
        print(f"[Worker] ✗ 에러 발생: {exc}")
        print(f"[Worker] 상태를 FAILED로 변경 중...")
        session = AsyncSessionLocal()
        try:
            repo = ContentRepository(session)
            await repo.update_content_status(content_id, ContentStatus.FAILED)
            await repo.add_log(
                content_id=content_id,
                log={"event": "error", "details": str(exc)},
                message="Pipeline failed",
            )
            await session.commit()
        finally:
            await session.close()
        logger.exception("Processing failed for content_id=%s", content_id)
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except TypeError:
            # Python 3.11 compatibility
            if temp_path.exists():
                temp_path.unlink()

    print(f"[Worker] 결과를 데이터베이스에 저장 중...")
    session = AsyncSessionLocal()
    try:
        repo = ContentRepository(session)
        await repo.update_content_result(
            content_id,
            speakers=list(result.speaker_stats.keys()),
            duration_seconds=result.duration_seconds,
            transcription=result.transcription,
        )
        await repo.update_content_status(content_id, ContentStatus.COMPLETED)
        await repo.add_log(
            content_id,
            log={
                "event": "completed",
                "speaker_stats": result.speaker_stats,
                "logs": result.logs,
            },
            message="ASR processing completed",
        )
        await session.commit()
    finally:
        await session.close()
    print(f"[Worker] ✓ 데이터베이스 저장 완료")
    logger.info("Processing completed for content_id=%s", content_id)

