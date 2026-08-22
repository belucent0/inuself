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
from .controllers import auth_controller

# OpenTelemetry (optional - graceful fallback if not installed)
try:
    from .core.telemetry import setup_telemetry

    telemetry_available = True
except ImportError:
    telemetry_available = False

    def setup_telemetry(*args, **kwargs):
        pass


class HealthCheckLogFilter(logging.Filter):
    """헬스체크/메트릭 요청 로그를 필터링하는 필터.

    - 성공 응답(2xx)은 로그에서 제외
    - 에러 응답(4xx, 5xx)은 로깅하여 문제 감지
    """

    # 필터링할 경로 패턴
    EXCLUDED_PATHS = ("/health", "/metrics", "/ready", "/livez", "/readyz")
    # 성공 응답 코드 (제외 대상)
    SUCCESS_CODES = ("200", "204")

    def filter(self, record: logging.LogRecord) -> bool:
        """모니터링 경로의 성공 응답만 제외, 에러는 로깅."""
        # uvicorn access log 형식: "127.0.0.1:xxxxx - "GET /health HTTP/1.1" 200 OK"
        message = record.getMessage()

        # 모니터링 경로 체크
        is_monitoring_path = any(path in message for path in self.EXCLUDED_PATHS)
        if not is_monitoring_path:
            return True  # 일반 요청은 로깅

        # 성공 응답만 제외 (에러는 로깅)
        is_success = any(code in message for code in self.SUCCESS_CODES)
        return not is_success  # 성공이면 제외(False), 에러면 로깅(True)


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

    # Kiwi 형태소 분석기 워밍업 (첫 요청 지연 방지)
    try:
        from .agents.nodes.intent_parser import warmup_kiwi

        warmup_kiwi()
    except Exception as e:
        logger.warning(f"[Lifespan] Kiwi warmup failed: {e}")

    # RedisListener 시작
    from .websocket.dependencies import get_redis_listener

    redis_listener = get_redis_listener()

    try:
        await redis_listener.start(pattern="events:*")
        logger.info("[Lifespan] RedisListener started")
    except Exception as exc:
        logger.exception("[Lifespan] Failed to start RedisListener: {}", exc)

    # StreamConsumer 시작 (워커 결과 수신)
    from .services.stream_consumer import get_stream_consumer

    stream_consumer = get_stream_consumer()
    stream_consumer_task = asyncio.create_task(stream_consumer.start())
    logger.info("[Lifespan] StreamConsumer started")

    # StateWatchdog 스케줄러 시작 (5분마다 실행)
    # auto_reconcile=True - stuck 상태 파일을 자동으로 FAILED 처리
    from .services.watchdog_scheduler import WatchdogScheduler

    watchdog_scheduler = WatchdogScheduler(interval_minutes=5, auto_reconcile=True)
    watchdog_scheduler_task = asyncio.create_task(watchdog_scheduler.start())
    logger.info("[Lifespan] StateWatchdog scheduler started (interval: 5m)")

    from .services.agent_dispatcher import run_agent_dispatch_reconciler

    agent_dispatch_task = asyncio.create_task(run_agent_dispatch_reconciler())
    logger.info("[Lifespan] Agent dispatch reconciler started")

    yield

    agent_dispatch_task.cancel()
    try:
        await agent_dispatch_task
    except asyncio.CancelledError:
        pass
    logger.info("[Lifespan] Agent dispatch reconciler stopped")

    # 종료 시: WatchdogScheduler 중지
    try:
        watchdog_scheduler.stop()
        watchdog_scheduler_task.cancel()
        try:
            await watchdog_scheduler_task
        except asyncio.CancelledError:
            pass
        logger.info("[Lifespan] WatchdogScheduler stopped")
    except Exception as exc:
        logger.exception("[Lifespan] Error stopping WatchdogScheduler: {}", exc)

    # 종료 시: StreamConsumer 중지
    try:
        await stream_consumer.stop()
        stream_consumer_task.cancel()
        try:
            await stream_consumer_task
        except asyncio.CancelledError:
            pass
        logger.info("[Lifespan] StreamConsumer stopped")
    except Exception as exc:
        logger.exception("[Lifespan] Error stopping StreamConsumer: {}", exc)

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
        origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],  # 모든 HTTP 메서드 허용
        allow_headers=["*"],  # 모든 헤더 허용
    )

    # OpenTelemetry 분산 추적 초기화 (optional)
    if telemetry_available:
        setup_telemetry(app, service_name="asr-backend")
        logger.info("[FastAPI] OpenTelemetry tracing initialized")
    else:
        logger.warning("[FastAPI] OpenTelemetry not available - tracing disabled")

    storage_ok, storage_message = check_storage_health()
    if storage_ok:
        logger.info("[Storage] %s", storage_message)
        logger.info(f"[Storage] ✓ {storage_message}")
    else:
        logger.warning("[Storage] %s", storage_message)
        logger.warning(f"[Storage] ✗ {storage_message}")

    app.include_router(content_controller.router, prefix=settings.api_prefix)

    # 인증 라우터 추가
    app.include_router(auth_controller.router, prefix=settings.api_prefix)
    logger.info("[FastAPI] Auth routes registered at /api/auth")
    # 채팅 라우터 추가
    from .controllers import chat_controller

    app.include_router(chat_controller.router)
    logger.info("[FastAPI] Chat routes registered at /api/chat")

    # Deep Search 라우터 추가
    from .controllers import search_controller

    app.include_router(search_controller.router)
    logger.info("[FastAPI] Search routes registered at /api/search")

    # 관리자 라우터 추가
    from .controllers import admin_controller

    app.include_router(admin_controller.router, prefix=settings.api_prefix)
    logger.info("[FastAPI] Admin routes registered at /api/admin")

    # Langfuse 대시보드 라우터 추가
    from .controllers import langfuse_controller

    app.include_router(langfuse_controller.router, prefix=settings.api_prefix)
    logger.info("[FastAPI] Langfuse routes registered at /api/admin/langfuse")
    # WebSocket 라우터 추가 (별도 경로, prefix 없음)
    from .controllers import websocket_controller

    app.include_router(websocket_controller.router)
    logger.info("[FastAPI] WebSocket routes registered at /ws")

    # AI Chat 라우터 추가 (V8.0 LangGraph 기반)
    from .controllers import ai_chat_controller

    app.include_router(ai_chat_controller.router)
    logger.info("[FastAPI] AI Chat routes registered at /api/ai")

    # SSE 이벤트 라우터 추가
    from .controllers import events_controller

    app.include_router(events_controller.router)
    logger.info("[FastAPI] SSE events routes registered at /api/events")

    # 심리검사 라우터 추가 (WPI 등)
    from .controllers import scan_controller

    app.include_router(scan_controller.router, prefix=settings.api_prefix)
    logger.info("[FastAPI] Scan routes registered at /api/scan")

    # 미디어 프록시 라우터 추가 (인증 기반 스트리밍)
    from .controllers import media_controller

    app.include_router(media_controller.router)
    logger.info("[FastAPI] Media proxy routes registered at /api/media")

    @app.get("/health", tags=["system"])
    async def healthcheck():
        return {"status": "ok"}

    # /api/v1 prefix 호환 별칭
    @app.get("/api/v1/health", tags=["system"])
    async def healthcheck_v1():
        return {"status": "ok"}

    # Prometheus 메트릭 수집 초기화
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
    logger.info("[FastAPI] Prometheus metrics endpoint registered at /metrics")

    return app


app = create_app()
