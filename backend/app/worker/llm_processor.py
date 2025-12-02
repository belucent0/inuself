import asyncio
import sys

from ..core.config import get_settings
from ..core.logging import logger
from ..db.session import AsyncSessionLocal
from ..services.llm_summary_service import LlmSummaryService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

settings = get_settings()

_worker_loop: asyncio.AbstractEventLoop | None = None


def process_llm_job(*, content_id: int) -> None:
    """RQ 워커가 호출하는 요약 작업 진입점."""
    logger.info("[LLM] ========================================")
    logger.info(f"[LLM] Summary job started: content_id={content_id}")
    logger.info("LLM job started for content_id={}", content_id)
    
    # Windows에서는 매 작업마다 새로운 이벤트 루프를 생성 (ASR 워커와 동일)
    if sys.platform == "win32":
        # 기존 이벤트 루프 정리
        try:
            existing_loop = asyncio.get_event_loop()
            if existing_loop and not existing_loop.is_closed():
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
            pass
        
        # Windows에서 ProactorEventLoop 사용 (asyncpg 호환)
        policy = asyncio.get_event_loop_policy()
        if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Proactor 초기화
        if isinstance(loop, asyncio.ProactorEventLoop):
            try:
                async def _init_proactor():
                    pass
                loop.run_until_complete(_init_proactor())
            except Exception as exc:
                logger.warning("Failed to initialize Proactor, recreating loop: %s", exc)
                try:
                    loop.close()
                except Exception:
                    pass
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    async def _init_proactor_retry():
                        pass
                    loop.run_until_complete(_init_proactor_retry())
                except Exception:
                    pass
    else:
        loop = _ensure_worker_loop()
    
    try:
        logger.info("[LLM] Running event loop...")
        loop.run_until_complete(_process_job(content_id=content_id))
        logger.info(f"[LLM] OK Summary job completed: content_id={content_id}")
        logger.info("LLM job completed for content_id={}", content_id)
    except Exception as exc:
        logger.error(f"[LLM] ERROR Summary job failed: content_id={content_id}, error={exc}")
        logger.exception("LLM job failed for content_id={}", content_id)
        raise
    finally:
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
                        logger.warning("Timeout waiting for pending LLM tasks")
                    except Exception as e:
                        logger.error("Error waiting for pending LLM tasks: {}", e)
            except Exception as e:
                logger.error("Error during LLM event loop cleanup: {}", e)
            finally:
                # 이벤트 루프 닫기
                try:
                    if not loop.is_closed():
                        loop.close()
                except Exception as e:
                    logger.error("Error closing LLM event loop: {}", e)
                
                # 현재 이벤트 루프 제거 (중요!)
                try:
                    asyncio.set_event_loop(None)
                except Exception as e:
                    logger.error("Error unsetting LLM event loop: {}", e)


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


async def _process_job(*, content_id: int) -> None:
    logger.info("[LLM] Creating DB session...")
    
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
        logger.info("[LLM] Initializing LlmSummaryService...")
        service = LlmSummaryService(session)
        logger.info("[LLM] Calling summarize function...")
        await service.summarize(content_id)
        logger.info("[LLM] Summarize function completed")
    finally:
        logger.info("[LLM] Closing DB session...")
        await session.close()
        # Windows에서 생성한 엔진도 타임아웃과 함께 정리
        if current_engine:
            try:
                await asyncio.wait_for(current_engine.dispose(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout disposing LLM database engine")
            except Exception as e:
                logger.error("Error disposing LLM database engine: %s", e)

