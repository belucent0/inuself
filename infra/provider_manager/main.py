"""Provider Manager - Main Entry Point.

Architecture V7.3: FastAPI + Redis Stream 통합 프로바이더 관리
- FastAPI HTTP API (프로바이더 조회/관리)
- Redis Stream 기반 GPU/NPU 작업 처리
- ProviderManager가 모든 프로바이더 프로세스 관리

Usage:
    # 기본 실행 (Redis Stream 처리 + API 서버)
    python main.py

    # API 서버만 실행
    python main.py --api-only --port 9998

    # Stream 처리만 실행
    python main.py --stream-only
"""

import sys
from pathlib import Path

# 패키지 경로 추가 (상대 임포트 지원)
_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import signal
import asyncio
import logging
import argparse
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

from core.config import settings
from core.manager import ProviderManager
from api.routes import providers_router, groups_router, health_router, jobs_router
from api.routes.providers import set_service
from services.stream_processor import StreamProcessor
from services.provider_service import ProviderService

# ==========================================
# Logging Setup
# ==========================================
settings.log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(
            settings.log_dir / "provider-manager.log",
            maxBytes=10*1024*1024,
            backupCount=5
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ProviderManager")

# ==========================================
# Global Instances
# ==========================================
provider_manager: ProviderManager = None
stream_processor: StreamProcessor = None
_running_combined: bool = False  # combined 모드 플래그


# ==========================================
# FastAPI App
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI Lifespan - 시작/종료 시 실행."""
    global provider_manager, stream_processor

    logger.info("Provider Manager starting...")

    # combined 모드에서는 run_combined에서 이미 초기화됨
    if not _running_combined:
        # ProviderManager 초기화 (API-only 모드)
        provider_manager = ProviderManager()

        # ProviderService 초기화 (API-only 모드용, JobTracker 없음)
        provider_service = ProviderService(provider_manager, None)
        set_service(provider_service)
        logger.info("API-only mode: ProviderService initialized without JobTracker")

    yield

    # 종료 시 정리
    logger.info("Provider Manager shutting down...")
    if stream_processor:
        stream_processor.shutdown()


def create_app() -> FastAPI:
    """FastAPI 앱 생성."""
    app = FastAPI(
        title="Provider Manager API",
        description="GPU/NPU 프로바이더 관리 API (Architecture V7.3)",
        version="7.3.0",
        lifespan=lifespan,
    )

    # 라우터 등록
    app.include_router(providers_router)
    app.include_router(groups_router)
    app.include_router(health_router)
    app.include_router(jobs_router)

    return app


app = create_app()


# ==========================================
# Stream Processing
# ==========================================
async def run_stream_processor():
    """Redis Stream 처리 실행."""
    global stream_processor, provider_manager

    stream_processor = StreamProcessor(provider_manager)

    def signal_handler(sig, frame):
        stream_processor.shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    await stream_processor.run()


# ==========================================
# Main Entry Points
# ==========================================
def run_api_server(host: str = "0.0.0.0", port: int = 9998):
    """API 서버만 실행."""
    import uvicorn
    logger.info(f"Starting API server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


def run_stream_only():
    """Stream 처리만 실행."""
    global provider_manager

    provider_manager = ProviderManager()

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(run_stream_processor())


async def run_combined(api_port: int = 9998):
    """API 서버 + Stream 처리 동시 실행."""
    import uvicorn
    from uvicorn import Config, Server
    import httpx
    import redis.asyncio as redis_async

    global provider_manager, stream_processor, _running_combined

    # combined 모드 플래그 설정 (lifespan에서 중복 초기화 방지)
    _running_combined = True

    # ProviderManager 초기화
    provider_manager = ProviderManager()

    # 프로바이더 시작
    logger.info("Starting all provider processes...")
    await provider_manager.start_all_providers()

    # Stream Processor 초기화 (프로바이더 공유)
    stream_processor = StreamProcessor(provider_manager)

    # Signal handler
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received...")
        stream_processor.shutdown()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Uvicorn 서버 설정
    config = Config(app=app, host="0.0.0.0", port=api_port, log_level="info")
    server = Server(config)

    # 병렬 실행
    api_task = asyncio.create_task(server.serve())

    # Redis 연결
    stream_processor.redis = redis_async.from_url(settings.redis_url, decode_responses=True)
    stream_processor.http_client = httpx.AsyncClient(timeout=settings.default_timeout)

    # JobTracker 초기화
    from services.job_tracker import JobTracker
    stream_processor.job_tracker = JobTracker(stream_processor.redis)
    logger.info("JobTracker initialized")

    # ProviderService 초기화 (HTTP API에서 사용)
    provider_service = ProviderService(provider_manager, stream_processor.job_tracker)
    set_service(provider_service)
    logger.info("ProviderService initialized")

    # GPU 작업 Consumer Group 생성
    try:
        await stream_processor.redis.xgroup_create(
            settings.request_stream,
            settings.consumer_group,
            id="0",
            mkstream=True
        )
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    # Control API Consumer Group 생성 (동일한 consumer_group 사용 - xreadgroup에서 함께 읽기 위해)
    try:
        await stream_processor.redis.xgroup_create(
            settings.control_request_stream,
            settings.consumer_group,  # gpu-workers (xreadgroup과 동일한 그룹)
            id="0",
            mkstream=True
        )
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise

    logger.info(f"Provider Manager started. API: http://0.0.0.0:{api_port}")
    logger.info(f"  GPU Stream: {settings.request_stream}")
    logger.info(f"  Control Stream: {settings.control_request_stream}")

    # Stream 처리 루프 (양쪽 스트림 동시 처리)
    while stream_processor.is_running:
        try:
            messages = await stream_processor.redis.xreadgroup(
                settings.consumer_group,
                settings.consumer_name,
                {
                    settings.request_stream: ">",
                    settings.control_request_stream: ">",
                },
                count=5,
                block=5000,
            )

            if messages:
                for stream_name, stream_messages in messages:
                    for message_id, message_data in stream_messages:
                        if stream_name == settings.request_stream:
                            await stream_processor.process_message(message_id, message_data)
                        elif stream_name == settings.control_request_stream:
                            await stream_processor.process_control_message(message_id, message_data)

        except asyncio.CancelledError:
            break
        except Exception as e:
            error_str = str(e)
            # NOGROUP 에러 시 consumer group 자동 재생성
            if "NOGROUP" in error_str:
                logger.warning(f"Consumer group missing, recreating...")
                try:
                    await stream_processor.redis.xgroup_create(
                        settings.request_stream,
                        settings.consumer_group,
                        id="0",
                        mkstream=True
                    )
                except Exception as create_err:
                    if "BUSYGROUP" not in str(create_err):
                        logger.error(f"Failed to recreate GPU stream group: {create_err}")
                try:
                    await stream_processor.redis.xgroup_create(
                        settings.control_request_stream,
                        settings.consumer_group,
                        id="0",
                        mkstream=True
                    )
                except Exception as create_err:
                    if "BUSYGROUP" not in str(create_err):
                        logger.error(f"Failed to recreate control stream group: {create_err}")
                logger.info("Consumer groups recreated")
            else:
                logger.error(f"Error in main loop: {e}")
            await asyncio.sleep(1)

    # 정리
    logger.info("Stopping all providers...")
    await provider_manager.stop_all_providers()

    if stream_processor.http_client:
        await stream_processor.http_client.aclose()
    if stream_processor.redis:
        await stream_processor.redis.aclose()

    api_task.cancel()
    logger.info("Provider Manager stopped.")


def main():
    """CLI 메인 함수."""
    parser = argparse.ArgumentParser(
        description="Provider Manager - GPU/NPU 프로바이더 관리",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # 기본 실행 (Stream + API)
  python main.py --api-only         # API 서버만 실행
  python main.py --stream-only      # Stream 처리만 실행
  python main.py --port 9998        # API 포트 지정
        """
    )

    parser.add_argument("--api-only", action="store_true", help="API 서버만 실행")
    parser.add_argument("--stream-only", action="store_true", help="Stream 처리만 실행")
    parser.add_argument("--host", default="0.0.0.0", help="API 서버 호스트 (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9998, help="API 서버 포트 (default: 9998)")

    args = parser.parse_args()

    if args.api_only:
        run_api_server(host=args.host, port=args.port)
    elif args.stream_only:
        run_stream_only()
    else:
        # 기본: Stream + API 동시 실행
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(run_combined(api_port=args.port))


if __name__ == "__main__":
    main()
