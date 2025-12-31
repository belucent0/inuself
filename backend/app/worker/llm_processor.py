import asyncio
import sys

from ..core.config import get_settings
from ..core.logging import logger
from ..db.session import AsyncSessionLocal
from ..services.llm_summary_service import LlmSummaryService


from ..core.system_utils import setup_worker_event_loop, cleanup_worker_event_loop, WorkerSessionContext

settings = get_settings()

_worker_loop: asyncio.AbstractEventLoop | None = None


def process_llm_job(*, file_id: int) -> None:
    """RQ 워커가 호출하는 요약 작업 진입점."""
    logger.info("[LLM] ========================================")
    logger.info(f"[LLM] Summary job started: file_id={file_id}")
    logger.info("LLM job started for file_id={}", file_id)
    
    # 이벤트 루프 설정 (Windows/Linux 분기 처리는 system_utils 내부에서 수행)
    loop = setup_worker_event_loop()
    
    try:
        logger.info("[LLM] Running event loop...")
        loop.run_until_complete(_process_job(file_id=file_id))
        logger.info(f"[LLM] OK Summary job completed: file_id={file_id}")
        logger.info("LLM job completed for file_id={}", file_id)
    except Exception as exc:
        logger.error(f"[LLM] ERROR Summary job failed: file_id={file_id}, error={exc}")
        logger.exception("LLM job failed for file_id={}", file_id)
        raise
    finally:
        # 이벤트 루프 정리
        cleanup_worker_event_loop(loop)



def _ensure_worker_loop() -> asyncio.AbstractEventLoop:
    """asyncpg 연결 재사용을 위해 단일 이벤트 루프를 생성/재사용."""
    global _worker_loop
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


from .event_publisher import ProgressReporter

async def _process_job(*, file_id: int) -> None:
    reporter = ProgressReporter(file_id)

    # 이벤트 발행: LLM 요약 시작
    reporter.summarizing(
        step="llm_start",
        progress=10.0,
        message="LLM 요약 작업 시작"
    )

    logger.info("[LLM] Creating DB session...")
    
    logger.info("[LLM] Creating DB session...")
    
    # WorkerSessionContext를 사용하여 세션 생성 (OS별 처리 포함)
    async with WorkerSessionContext() as session:
        try:
            logger.info("[LLM] Initializing LlmSummaryService...")
            service = LlmSummaryService(session)
            logger.info("[LLM] Calling summarize function...")
            await service.summarize(file_id)
            logger.info("[LLM] Summarize function completed")
    
            # 이벤트 발행: LLM 요약 완료 (최종 완료)
            reporter.complete()
    
        except Exception as exc:
            # 이벤트 발행: 에러
            reporter.fail(f"LLM 요약 실패: {str(exc)}")
            raise exc


