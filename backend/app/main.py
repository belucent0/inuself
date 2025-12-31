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



@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행되는 lifespan 이벤트."""
    import asyncio
    import os
    
    # RedisListener 시작
    from .websocket.dependencies import get_redis_listener
    redis_listener = get_redis_listener()
    
    try:
        await redis_listener.start(pattern="events:*")
        logger.info("[Lifespan] RedisListener started")
    except Exception as exc:
        logger.exception("[Lifespan] Failed to start RedisListener: {}", exc)
    
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
    
    yield
    
    # 종료 시: RedisListener 중지
    try:
        await redis_listener.stop()
        logger.info("[Lifespan] RedisListener stopped")
    except Exception as exc:
        logger.exception("[Lifespan] Error stopping RedisListener: {}", exc)


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
    
    # WebSocket 라우터 추가 (별도 경로, prefix 없음)
    from .controllers import websocket_controller
    app.include_router(websocket_controller.router)
    logger.info("[FastAPI] WebSocket routes registered at /ws")

    @app.get("/health", tags=["system"])
    async def healthcheck():
        return {"status": "ok"}

    return app


app = create_app()

