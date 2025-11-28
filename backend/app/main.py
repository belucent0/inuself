import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.config import get_settings
from .core.storage import check_storage_health
from .controllers import content_controller
from .worker.run_llm_worker import safe_print

logger = logging.getLogger(__name__)


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
        safe_print("[Worker] 워커를 subprocess로 시작합니다...")
        
        # subprocess로 워커 실행 (Windows에서 signal 문제 해결)
        # stdout/stderr를 None으로 설정하여 현재 터미널에 출력되도록 함
        process = subprocess.Popen(
            [python_executable, "-m", worker_module],
            cwd=str(backend_dir),
            stdout=None,  # 현재 터미널에 출력
            stderr=None,  # 현재 터미널에 출력
        )
        
        logger.info("Worker subprocess started with PID: %s", process.pid)
        safe_print(f"[Worker] 워커 프로세스 시작됨 (PID: {process.pid})")
        safe_print(f"[Worker] 워커는 별도 프로세스로 실행 중입니다.")
        
    except Exception as e:
        logger.exception("Failed to start worker subprocess")
        safe_print(f"[Worker] 워커 시작 실패: {e}")
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
        safe_print("[FastAPI] 워커를 백그라운드에서 시작합니다...")
        start_worker_background()
    else:
        safe_print("[FastAPI] 워커 자동 시작이 비활성화되었습니다.")
        safe_print("[FastAPI] 개발 환경: run_dev.sh가 워커를 관리합니다.")
        safe_print("[FastAPI] 프로덕션 환경: START_WORKER=true 설정 시 자동 시작됩니다.")
    
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
                    safe_print(f"[Auto-Requeue] {requeued}개의 LLM 작업을 자동으로 재큐잉했습니다.")
            except Exception as exc:
                logger.exception("Failed to auto-requeue LLM jobs")
                safe_print(f"[Auto-Requeue] 자동 재큐잉 실패: {exc}")
    
    # 백그라운드 태스크 시작
    auto_requeue_task = asyncio.create_task(auto_requeue_llm_jobs())
    safe_print("[FastAPI] LLM 작업 자동 재큐잉 백그라운드 태스크 시작 (60초 간격)")
    
    yield
    
    # 종료 시: 백그라운드 태스크 취소
    auto_requeue_task.cancel()
    try:
        await auto_requeue_task
    except asyncio.CancelledError:
        pass
    safe_print("[FastAPI] LLM 작업 자동 재큐잉 백그라운드 태스크 종료")


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

    storage_ok, storage_message = check_storage_health()
    if storage_ok:
        logger.info("[Storage] %s", storage_message)
        safe_print(f"[Storage] ✓ {storage_message}")
    else:
        logger.warning("[Storage] %s", storage_message)
        safe_print(f"[Storage] ✗ {storage_message}")

    app.include_router(content_controller.router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    async def healthcheck():
        return {"status": "ok"}

    @app.get(f"{settings.api_prefix}/queue/status", tags=["system"])
    async def queue_status():
        """큐 상태 확인."""
        try:
            from rq import Queue
            from rq.registry import FailedJobRegistry
            from .core.redis import get_redis_connection
            from .worker.queue import QUEUE_NAME
            
            redis = get_redis_connection()
            queue = Queue(QUEUE_NAME, connection=redis)
            failed_registry = FailedJobRegistry(queue=queue)
            
            # 실패한 작업의 에러 메시지 확인 (최근 5개)
            failed_jobs_info = []
            failed_job_ids = failed_registry.get_job_ids(0, 4)  # 최근 5개
            for job_id in failed_job_ids:
                try:
                    job = queue.fetch_job(job_id)
                    if job:
                        failed_jobs_info.append({
                            "job_id": job_id,
                            "error": str(job.exc_info) if job.exc_info else "Unknown error",
                            "ended_at": job.ended_at.isoformat() if job.ended_at else None,
                        })
                except Exception:
                    pass
            
            return {
                "queue_name": QUEUE_NAME,
                "queued_jobs": len(queue),
                "started_jobs": len(queue.started_job_registry),
                "finished_jobs": len(queue.finished_job_registry),
                "failed_jobs": len(queue.failed_job_registry),
                "recent_failed_jobs": failed_jobs_info,
            }
        except Exception as e:
            return {"error": str(e)}
    
    @app.post(f"{settings.api_prefix}/queue/cleanup-failed", tags=["system"])
    async def cleanup_failed_jobs():
        """실패한 작업들을 정리합니다."""
        try:
            from rq import Queue
            from rq.registry import FailedJobRegistry
            from .core.redis import get_redis_connection
            from .worker.queue import QUEUE_NAME
            
            redis = get_redis_connection()
            queue = Queue(QUEUE_NAME, connection=redis)
            failed_registry = FailedJobRegistry(queue=queue)
            
            failed_job_ids = failed_registry.get_job_ids()
            cleaned_count = 0
            
            for job_id in failed_job_ids:
                try:
                    job = queue.fetch_job(job_id)
                    if job:
                        job.delete()
                        cleaned_count += 1
                except Exception:
                    pass
            
            return {
                "message": f"{cleaned_count}개의 실패한 작업이 정리되었습니다.",
                "cleaned_count": cleaned_count,
            }
        except Exception as e:
            return {"error": str(e)}
    
    @app.post(f"{settings.api_prefix}/queue/requeue-llm", tags=["system"])
    async def requeue_llm_jobs():
        """SUMMARIZING 또는 SUMMARY_FAILED 상태의 콘텐츠를 LLM 큐에 재등록합니다."""
        try:
            from .worker.requeue import requeue_summarizing_contents
            
            requeued = await requeue_summarizing_contents()
            return {
                "message": f"{requeued}개의 LLM 작업을 큐에 재등록했습니다.",
                "requeued_count": requeued,
            }
        except Exception as e:
            logger.exception("Failed to requeue LLM jobs")
            return {"error": str(e)}
    
    @app.get(f"{settings.api_prefix}/queue/llm-status", tags=["system"])
    async def llm_queue_status():
        """LLM 큐 상태 확인."""
        try:
            from rq import Queue
            from rq.registry import FailedJobRegistry
            from .core.redis import get_redis_connection
            from .worker.llm_queue import LLM_QUEUE_NAME
            
            redis = get_redis_connection()
            queue = Queue(LLM_QUEUE_NAME, connection=redis)
            failed_registry = FailedJobRegistry(queue=queue)
            
            # 실패한 작업의 에러 메시지 확인 (최근 5개)
            failed_jobs_info = []
            failed_job_ids = failed_registry.get_job_ids(0, 4)  # 최근 5개
            for job_id in failed_job_ids:
                try:
                    job = queue.fetch_job(job_id)
                    if job:
                        failed_jobs_info.append({
                            "job_id": job_id,
                            "error": str(job.exc_info) if job.exc_info else "Unknown error",
                            "ended_at": job.ended_at.isoformat() if job.ended_at else None,
                        })
                except Exception:
                    pass
            
            return {
                "queue_name": LLM_QUEUE_NAME,
                "queued_jobs": len(queue),
                "started_jobs": len(queue.started_job_registry),
                "finished_jobs": len(queue.finished_job_registry),
                "failed_jobs": len(queue.failed_job_registry),
                "recent_failed_jobs": failed_jobs_info,
            }
        except Exception as e:
            return {"error": str(e)}

    return app


app = create_app()

