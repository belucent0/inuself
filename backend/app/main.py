import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.config import get_settings
from .controllers import content_controller


def start_worker_background() -> None:
    """백그라운드에서 RQ 워커 시작 (subprocess로 실행)."""
    import logging
    
    logger = logging.getLogger(__name__)
    
    # backend 디렉토리 경로
    backend_dir = Path(__file__).parent.parent
    
    # Poetry 환경에서 Python 실행 파일 경로 찾기
    python_executable = sys.executable
    
    # 워커 모듈 실행
    worker_module = "app.worker.run_worker"
    
    try:
        logger.info("Starting worker as subprocess")
        print("[Worker] 워커를 subprocess로 시작합니다...")
        
        # subprocess로 워커 실행 (Windows에서 signal 문제 해결)
        # stdout/stderr를 None으로 설정하여 현재 터미널에 출력되도록 함
        process = subprocess.Popen(
            [python_executable, "-m", worker_module],
            cwd=str(backend_dir),
            stdout=None,  # 현재 터미널에 출력
            stderr=None,  # 현재 터미널에 출력
        )
        
        logger.info("Worker subprocess started with PID: %s", process.pid)
        print(f"[Worker] 워커 프로세스 시작됨 (PID: {process.pid})")
        print(f"[Worker] 워커는 별도 프로세스로 실행 중입니다.")
        
    except Exception as e:
        logger.exception("Failed to start worker subprocess")
        print(f"[Worker] 워커 시작 실패: {e}")
        import traceback
        traceback.print_exc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행되는 lifespan 이벤트."""
    # 시작 시: 워커 시작 (개발 환경에서는 항상 시작)
    # 프로덕션에서는 환경변수로 제어 가능
    import os
    should_start_worker = os.getenv("START_WORKER", "true").lower() == "true"
    
    if should_start_worker:
        print("[FastAPI] 워커를 백그라운드에서 시작합니다...")
        start_worker_background()
    else:
        print("[FastAPI] 워커 자동 시작이 비활성화되었습니다.")
        print("[FastAPI] 워커 실행: poetry run python -m app.worker.run_worker")
    yield
    # 종료 시: 정리 작업 (필요시)


def create_app() -> FastAPI:
    """FastAPI 애플리케이션 생성."""
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # CORS 미들웨어 추가
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],  # 개발 환경
        allow_credentials=True,
        allow_methods=["*"],  # 모든 HTTP 메서드 허용
        allow_headers=["*"],  # 모든 헤더 허용
    )

    app.include_router(content_controller.router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    async def healthcheck():
        return {"status": "ok"}

    @app.get(f"{settings.api_prefix}/queue/status", tags=["system"])
    async def queue_status():
        """큐 상태 확인."""
        try:
            from rq import Queue
            from .core.redis import get_redis_connection
            from .worker.queue import QUEUE_NAME
            
            redis = get_redis_connection()
            queue = Queue(QUEUE_NAME, connection=redis)
            
            return {
                "queue_name": QUEUE_NAME,
                "queued_jobs": len(queue),
                "started_jobs": len(queue.started_job_registry),
                "finished_jobs": len(queue.finished_job_registry),
                "failed_jobs": len(queue.failed_job_registry),
            }
        except Exception as e:
            return {"error": str(e)}

    return app


app = create_app()

