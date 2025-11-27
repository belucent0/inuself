import asyncio
import logging
import sys

from ..db.session import AsyncSessionLocal
from ..services.llm_summary_service import LlmSummaryService
from .utils import safe_print
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from ..core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_worker_loop: asyncio.AbstractEventLoop | None = None


def process_llm_job(*, content_id: int) -> None:
    """RQ 워커가 호출하는 요약 작업 진입점."""
    safe_print(f"[LLM] ========================================")
    safe_print(f"[LLM] 요약 작업 시작: content_id={content_id}")
    logger.info("LLM job started for content_id=%s", content_id)
    
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
        safe_print(f"[LLM] 이벤트 루프 실행 중...")
        loop.run_until_complete(_process_job(content_id=content_id))
        safe_print(f"[LLM] OK 요약 작업 완료: content_id={content_id}")
        logger.info("LLM job completed for content_id=%s", content_id)
    except Exception as exc:
        safe_print(f"[LLM] ERROR 요약 작업 실패: content_id={content_id}, error={exc}")
        logger.exception("LLM job failed for content_id=%s", content_id)
        raise
    finally:
        if sys.platform == "win32":
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                if not loop.is_closed():
                    loop.close()


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
    safe_print(f"[LLM] DB 세션 생성 중...")
    
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
        safe_print(f"[LLM] LlmSummaryService 초기화 중...")
        service = LlmSummaryService(session)
        safe_print(f"[LLM] 요약 함수 호출 중...")
        await service.summarize(content_id)
        safe_print(f"[LLM] 요약 함수 완료")
    finally:
        safe_print(f"[LLM] DB 세션 종료 중...")
        await session.close()
        # Windows에서 생성한 엔진도 닫기
        if current_engine:
            await current_engine.dispose()

