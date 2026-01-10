import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
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


def cleanup_temp_files() -> None:
    """
    서버 시작 시 임시 파일 정리.
    
    data/uploads/ 디렉토리에서 job_*, ocr_* 패턴의 임시 파일을 삭제합니다.
    storage/ 서브디렉토리는 영구 저장소이므로 제외합니다.
    """
    settings = get_settings()
    upload_dir = settings.upload_dir
    
    if not upload_dir.exists():
        logger.info("[Cleanup] Upload directory does not exist, skipping cleanup")
        return
    
    deleted_count = 0
    error_count = 0
    
    # job_* 패턴 파일 삭제 (ASR 임시 파일)
    for temp_file in upload_dir.glob("job_*"):
        if temp_file.is_file():
            try:
                temp_file.unlink()
                deleted_count += 1
                logger.debug(f"[Cleanup] Deleted temp file: {temp_file.name}")
            except Exception as e:
                error_count += 1
                logger.warning(f"[Cleanup] Failed to delete {temp_file.name}: {e}")
    
    # ocr_* 패턴 파일 삭제 (OCR 임시 파일)
    for temp_file in upload_dir.glob("ocr_*"):
        if temp_file.is_file():
            try:
                temp_file.unlink()
                deleted_count += 1
                logger.debug(f"[Cleanup] Deleted temp file: {temp_file.name}")
            except Exception as e:
                error_count += 1
                logger.warning(f"[Cleanup] Failed to delete {temp_file.name}: {e}")
    
    if deleted_count > 0:
        logger.info(f"[Cleanup] ✓ Cleaned up {deleted_count} temporary file(s)")
    else:
        logger.info("[Cleanup] No temporary files to clean up")
    
    if error_count > 0:
        logger.warning(f"[Cleanup] ✗ Failed to delete {error_count} file(s)")



@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행되는 lifespan 이벤트."""
    import asyncio
    import os
    
    # 임시 파일 정리 (서버 시작 시)
    logger.info("[Lifespan] Cleaning up temporary files...")
    cleanup_temp_files()
    
    # RedisListener 시작
    from .websocket.dependencies import get_redis_listener
    redis_listener = get_redis_listener()
    
    try:
        await redis_listener.start(pattern="events:*")
        logger.info("[Lifespan] RedisListener started")
    except Exception as exc:
        logger.exception("[Lifespan] Failed to start RedisListener: {}", exc)

    # StreamASRWorker 시작 (whisper-server.exe)
    from .worker.stream_asr import stream_asr_worker
    logger.info("[Lifespan] Starting StreamASRWorker...")
    stream_asr_worker.start()
    
    # 시작 시: 워커 시작 제어
    # 개발 환경에서는 run_dev.sh가 워커를 관리하므로 기본적으로 비활성화
    # 프로덕션에서는 START_WORKER=true로 설정하여 FastAPI가 워커를 시작하도록 할 수 있음
    should_start_worker = os.getenv("START_WORKER", "false").lower() == "true"
    
    if should_start_worker:
        logger.info("[FastAPI] 워커를 백그라운드에서 시작합니다...")
        start_worker_background()
    else:
        logger.info("[FastAPI] 워커 자동 시작이 비활성화되었습니다.")
    
    yield
    
    # 종료 시: RedisListener 중지
    try:
        await redis_listener.stop()
        logger.info("[Lifespan] RedisListener stopped")
    except Exception as exc:
        logger.exception("[Lifespan] Error stopping RedisListener: {}", exc)

    # StreamASRWorker 종료
    logger.info("[Lifespan] Stopping StreamASRWorker...")
    stream_asr_worker.stop()


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
    
    # 채팅 라우터 추가
    from .controllers import chat_controller
    app.include_router(chat_controller.router)
    logger.info("[FastAPI] Chat routes registered at /api/chat")
    
    # WebSocket 라우터 추가 (별도 경로, prefix 없음)
    from .controllers import websocket_controller
    app.include_router(websocket_controller.router)
    logger.info("[FastAPI] WebSocket routes registered at /ws")

    @app.get("/health", tags=["system"])
    async def healthcheck():
        return {"status": "ok"}
    
    # Prometheus 메트릭 수집 초기화
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
    logger.info("[FastAPI] Prometheus metrics endpoint registered at /metrics")

    return app


app = create_app()

