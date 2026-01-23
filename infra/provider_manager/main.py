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
import os
import atexit
from pathlib import Path

# 패키지 경로 추가 (상대 임포트 지원)
_THIS_DIR = Path(__file__).parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import signal
import asyncio
import logging
import argparse
import time
import psutil
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI

from core.config import settings
from core.manager import ProviderManager
from core.telemetry import setup_telemetry
from api.routes import providers_router, groups_router, health_router, jobs_router
from api.routes.providers import set_service
from services.stream_processor import StreamProcessor
from services.provider_service import ProviderService
from services.idle_manager import IdleTimeoutManager

# ==========================================
# Singleton Lock (PID file based)
# ==========================================
PID_FILE = settings.log_dir / "provider-manager.pid"
_singleton_lock_fd = None


def _is_process_running(pid: int, expected_cmdline: str = "main.py") -> bool:
    """PID가 실제로 Provider Manager 프로세스인지 확인."""
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline())
        # main.py 또는 provider_manager가 cmdline에 포함되어 있는지 확인
        return expected_cmdline in cmdline or "provider_manager" in cmdline
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def acquire_singleton_lock() -> bool:
    """
    싱글톤 락 획득 (PID 파일 기반, Windows/Linux 호환).

    Returns:
        True if lock acquired, sys.exit(1) if another instance is running.
    """
    global _singleton_lock_fd

    settings.log_dir.mkdir(exist_ok=True)

    # 기존 PID 파일 확인
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if _is_process_running(old_pid):
                print(f"ERROR: Another Provider Manager instance is already running (PID: {old_pid})")
                print(f"       If this is incorrect, delete {PID_FILE} and retry.")
                sys.exit(1)
            else:
                # 오래된 PID 파일 - 프로세스가 비정상 종료된 경우
                print(f"WARNING: Stale PID file found (PID: {old_pid} not running). Removing...")
                PID_FILE.unlink()
        except (ValueError, FileNotFoundError):
            # 잘못된 PID 파일 형식
            PID_FILE.unlink(missing_ok=True)

    # 새 PID 파일 생성
    current_pid = os.getpid()
    PID_FILE.write_text(str(current_pid))

    # 종료 시 PID 파일 삭제
    def cleanup_pid_file():
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass

    atexit.register(cleanup_pid_file)

    print(f"Provider Manager started (PID: {current_pid})")
    return True


def release_singleton_lock():
    """싱글톤 락 해제."""
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


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
# OpenTelemetry 초기화
# ==========================================
setup_telemetry(service_name="provider-manager")

# ==========================================
# Global Instances
# ==========================================
provider_manager: ProviderManager = None
stream_processor: StreamProcessor = None
idle_manager: IdleTimeoutManager = None
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


def _wait_for_port_available(port: int, timeout: float = 30.0) -> bool:
    """포트가 사용 가능해질 때까지 대기.

    Args:
        port: 확인할 포트 번호
        timeout: 최대 대기 시간 (초)

    Returns:
        True if port is available, False if timeout
    """
    import socket
    import time

    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
                return True
        except OSError:
            logger.info(f"Port {port} is in use, waiting...")
            time.sleep(2)
    return False


async def run_combined(api_port: int = 9998):
    """API 서버 + Stream 처리 동시 실행."""
    import uvicorn
    from uvicorn import Config, Server
    import httpx
    import redis.asyncio as redis_async

    global provider_manager, stream_processor, idle_manager, _running_combined

    # 포트 사용 가능 여부 확인 (재시작 시 이전 프로세스 종료 대기)
    if not _wait_for_port_available(api_port):
        logger.error(f"Port {api_port} is still in use after timeout. Exiting.")
        sys.exit(1)

    # combined 모드 플래그 설정 (lifespan에서 중복 초기화 방지)
    _running_combined = True

    # ProviderManager 초기화
    provider_manager = ProviderManager()

    # 프로바이더 시작
    logger.info("Starting all provider processes...")
    await provider_manager.start_all_providers()

    # IdleTimeoutManager 초기화 (On-Demand 프로바이더 자동 언로드)
    idle_manager = IdleTimeoutManager(
        provider_manager,
        idle_timeout=settings.idle_timeout,
        check_interval=settings.idle_check_interval,
    )
    await idle_manager.start()
    logger.info(f"IdleTimeoutManager initialized (timeout={settings.idle_timeout}s)")

    # Stream Processor 초기화 (프로바이더 공유, idle_manager 연동)
    stream_processor = StreamProcessor(provider_manager, idle_manager)

    # Signal handler
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received...")
        if idle_manager:
            idle_manager.stop()
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

    # Redis가 준비될 때까지 대기 (BusyLoadingError 처리)
    max_retries = 30
    for attempt in range(max_retries):
        try:
            await stream_processor.redis.ping()
            logger.info("Redis connection ready")
            break
        except Exception as e:
            if "loading" in str(e).lower() or "LOADING" in str(e):
                logger.warning(f"Redis is loading dataset, waiting... (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(1)
            else:
                raise
    else:
        raise RuntimeError("Redis failed to become ready after maximum retries")

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

    # Stale job cleanup 주기 (5분마다)
    last_cleanup_time = time.time()
    CLEANUP_INTERVAL = 300  # 5분
    STALE_JOB_MAX_AGE = settings.default_timeout + 300  # timeout + 5분

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
                            # GPU 작업은 병렬 처리 (Background Task)
                            asyncio.create_task(stream_processor.process_message(message_id, message_data))
                        elif stream_name == settings.control_request_stream:
                            await stream_processor.process_control_message(message_id, message_data)

            # 주기적 stale job 정리 (5분마다)
            current_time = time.time()
            if current_time - last_cleanup_time >= CLEANUP_INTERVAL:
                last_cleanup_time = current_time
                try:
                    if stream_processor.job_tracker:
                        cleaned = await stream_processor.job_tracker.cleanup_stale_jobs(max_age=STALE_JOB_MAX_AGE)
                        if cleaned > 0:
                            logger.info(f"Cleaned up {cleaned} stale jobs (max_age={STALE_JOB_MAX_AGE}s)")
                            # ProviderManager의 active_jobs도 동기화
                            for name, state in provider_manager.provider_states.items():
                                if state.active_jobs > 0:
                                    # JobTracker에서 해당 프로바이더의 실제 활성 작업 수 확인
                                    active = await stream_processor.job_tracker.get_provider_jobs(name)
                                    if len(active) != state.active_jobs:
                                        logger.warning(f"Syncing {name} active_jobs: {state.active_jobs} -> {len(active)}")
                                        state.active_jobs = len(active)
                except Exception as e:
                    logger.warning(f"Stale job cleanup error: {e}")

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
    parser.add_argument("--no-singleton", action="store_true", help="싱글톤 체크 비활성화 (개발용)")

    args = parser.parse_args()

    # 싱글톤 락 획득 (다른 인스턴스가 실행 중이면 종료)
    if not args.no_singleton:
        acquire_singleton_lock()

    try:
        if args.api_only:
            run_api_server(host=args.host, port=args.port)
        elif args.stream_only:
            run_stream_only()
        else:
            # 기본: Stream + API 동시 실행
            if sys.platform == 'win32':
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            asyncio.run(run_combined(api_port=args.port))
    finally:
        # 종료 시 락 해제
        release_singleton_lock()


if __name__ == "__main__":
    main()
