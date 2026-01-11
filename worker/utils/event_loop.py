"""워커 이벤트 루프 유틸리티.

Windows/Linux 환경에서 asyncio 이벤트 루프를 안전하게 관리합니다.
"""
import asyncio
import sys

from worker.logging_config import logger


def setup_worker_event_loop() -> asyncio.AbstractEventLoop:
    """
    워커 프로세스를 위한 이벤트 루프를 설정하고 반환합니다.
    Windows 환경에서는 asyncpg와의 호환성을 위해 ProactorEventLoopPolicy를 사용하고,
    이전 루프의 잔여 작업을 정리합니다.
    """
    if sys.platform == "win32":
        # 기존 이벤트 루프 정리
        try:
            existing_loop = asyncio.get_event_loop()
            if existing_loop and not existing_loop.is_closed():
                # 남은 작업 완료 대기
                try:
                    pending = asyncio.all_tasks(existing_loop)
                    if pending:
                        existing_loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
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
                logger.warning(f"Failed to initialize Proactor, recreating loop: {exc}")
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
        # Linux/Mac에서는 단일 루프 재사용 시도
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
    return loop


def cleanup_worker_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """
    워커 이벤트 루프를 안전하게 정리합니다.
    Windows 환경에서만 루프를 닫고 정리합니다.
    """
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
                    logger.warning("Timeout waiting for pending tasks to complete during cleanup")
                except Exception as e:
                    logger.error(f"Error waiting for pending tasks during cleanup: {e}")
        except Exception as e:
            logger.error(f"Error during event loop cleanup: {e}")
        finally:
            try:
                if not loop.is_closed():
                    loop.close()
            except Exception as e:
                logger.error(f"Error closing event loop: {e}")
            
            try:
                asyncio.set_event_loop(None)
            except Exception as e:
                logger.error(f"Error unsetting event loop: {e}")
