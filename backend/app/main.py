import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.config import get_settings
from .core.logging import logger
from .core.storage import check_storage_health
from .controllers import content_controller


class HealthCheckLogFilter(logging.Filter):
    """헬스체크 요청 로그를 필터링하는 필터."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        """헬스체크 경로(`/health`)는 로그에서 제외."""
        # uvicorn access log 형식: "127.0.0.1:xxxxx - "GET /health HTTP/1.1" 200 OK"
        message = record.getMessage()
        if "/health" in message and "200" in message:
            return False  # 로그 제외
        return True  # 다른 로그는 정상 출력


# RQ 워커는 제거되었습니다. Celery 워커는 PM2로 관리합니다.
        import traceback
        traceback.print_exc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행되는 lifespan 이벤트."""
    import asyncio
    import os
    
    # 시작 시: 워커 시작 제어
    # 개발 환경에서는 run_dev.sh가 워커를 관리하므로 기본적으로 비활성화
    # 프로덕션에서는 START_WORKER=true로 설정하여 FastAPI가 워커를 시작하도록 할 수 있음
    should_start_worker = os.getenv("START_WORKER", "false").lower() == "true"
    
    if should_start_worker:
        logger.info("[FastAPI] 워커를 백그라운드에서 시작합니다...")
        start_worker_background()
    else:
        logger.info("[FastAPI] 워커 자동 시작이 비활성화되었습니다.")
        logger.info("[FastAPI] 개발 환경: run_dev.sh가 워커를 관리합니다.")
        logger.info("[FastAPI] 프로덕션 환경: start.bat을 사용하면 PM2 워커도 함께 시작됩니다.")
    
    # 백그라운드 태스크: 주기적으로 SUMMARIZING 상태 콘텐츠 자동 재큐잉
    async def auto_requeue_llm_jobs():
        """주기적으로 SUMMARIZING 상태의 콘텐츠를 자동으로 재큐잉."""
        from .worker.requeue import requeue_summarizing_contents
        
        while True:
            try:
                await asyncio.sleep(60)  # 60초마다 체크
                requeued = await requeue_summarizing_contents()
                if requeued > 0:
                    logger.info("Auto-requeued %d LLM jobs", requeued)
                    logger.info(f"[Auto-Requeue] {requeued}개의 LLM 작업을 자동으로 재큐잉했습니다.")
            except Exception as exc:
                logger.exception("Failed to auto-requeue LLM jobs")
                logger.error(f"[Auto-Requeue] 자동 재큐잉 실패: {exc}")
    
    # 백그라운드 태스크 시작
    auto_requeue_task = asyncio.create_task(auto_requeue_llm_jobs())
    logger.info("[FastAPI] LLM 작업 자동 재큐잉 백그라운드 태스크 시작 (60초 간격)")
    
    yield
    
    # 종료 시: 백그라운드 태스크 취소
    auto_requeue_task.cancel()
    try:
        await auto_requeue_task
    except asyncio.CancelledError:
        pass
    logger.info("[FastAPI] LLM 작업 자동 재큐잉 백그라운드 태스크 종료")


def create_app() -> FastAPI:
    """FastAPI 애플리케이션 생성."""
    settings = get_settings()
    
    # 헬스체크 로그 필터 적용 (uvicorn access logger)
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    health_check_filter = HealthCheckLogFilter()
    uvicorn_access_logger.addFilter(health_check_filter)
    
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # CORS 미들웨어 추가
    # 환경 변수에서 허용할 origin 목록을 가져옴 (쉼표로 구분)
    cors_origins = [
        origin.strip() 
        for origin in settings.cors_origins.split(",") 
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],  # 모든 HTTP 메서드 허용
        allow_headers=["*"],  # 모든 헤더 허용
    )

    storage_ok, storage_message = check_storage_health()
    if storage_ok:
        logger.info("[Storage] %s", storage_message)
        logger.info(f"[Storage] ✓ {storage_message}")
    else:
        logger.warning("[Storage] %s", storage_message)
        logger.warning(f"[Storage] ✗ {storage_message}")

    app.include_router(content_controller.router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    async def healthcheck():
        return {"status": "ok"}

    @app.post(f"{settings.api_prefix}/queue/requeue-llm", tags=["system"])
    async def requeue_llm_jobs():
        """SUMMARIZING 또는 SUMMARY_FAILED 상태의 콘텐츠를 LLM 큐에 재등록합니다."""
        try:
            from .worker.requeue import requeue_summarizing_contents
            
            requeued = await requeue_summarizing_contents()
            return {
                "message": f"{requeued} LLM jobs requeued.",
                "requeued_count": requeued,
            }
        except Exception as e:
            logger.exception("Failed to requeue LLM jobs")
            return {"error": str(e)}

    return app


app = create_app()

