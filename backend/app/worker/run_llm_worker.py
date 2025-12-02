import asyncio
import sys
from rq import Connection
from rq.worker import SimpleWorker
from rq.timeouts import BaseDeathPenalty

from ..core.config import get_settings
from ..core.logging import logger, safe_print
from ..core.redis import get_redis_connection
from .llm_queue import LLM_QUEUE_NAME
from .requeue import requeue_summarizing_contents


def health_check_llm() -> bool:
    """LLM 모델 로드 및 간단한 쿼리 테스트."""
    try:
        import httpx
        from ..core.config import get_settings
        
        settings = get_settings()
        
        safe_print(f"[LLM Worker] Starting health check: LLM provider={settings.llm_provider}")
        
        if settings.llm_provider == "lmstudio":
            from .lmstudio_client import LMStudioClientError, request_chat_completion

            safe_print(f"[LLM Worker] Checking LM Studio connection: {settings.lmstudio_base_url}")
            safe_print(f"[LLM Worker] Model name: {settings.lmstudio_model_name}")

            test_messages = [
                {"role": "system", "content": settings.llm_system_prompt},
                {"role": "user", "content": "Please summarize this simple health check sentence."},
            ]

            try:
                # 헬스체크: max_tokens를 충분히 설정 (reasoning 모델의 경우 더 많은 토큰 필요)
                response = request_chat_completion(
                    settings=settings,
                    messages=test_messages,
                    temperature=0.1,
                    max_tokens=256,  # reasoning 모델을 위해 증가
                    stream=False,
                )
                # response가 "응답 생성 중 (토큰 제한)"인 경우는 모델이 작동 중이지만 토큰 제한으로 완료하지 못한 것
                if response == "응답 생성 중 (토큰 제한)":
                    safe_print("[LLM Worker] [WARN] LM Studio health check: Model could not complete response due to token limit.")
                    safe_print("[LLM Worker]   Model is working but please check max_tokens setting.")
                    safe_print("[LLM Worker]   Worker will start but first request may be slow.")
                    return True
                safe_print(f"[LLM Worker] [OK] LM Studio test response: '{response[:60]}...'")
                return True
            except LMStudioClientError as exc:
                error_msg = str(exc)
                # content가 비어있지만 reasoning이 있는 경우는 모델이 작동 중이므로 통과
                if "reasoning이 있음" in error_msg:
                    safe_print("[LLM Worker] [WARN] LM Studio health check: content is empty but reasoning exists.")
                    safe_print("[LLM Worker]   Model is using reasoning. Worker will start.")
                    return True
                safe_print(f"[LLM Worker] [ERROR] LM Studio health check failed: {exc}")
                safe_print("[LLM Worker]   Please check if LM Studio desktop app is running and proxy is not needed.")
                return False

        elif settings.llm_provider == "llama_cpp":
            # llama_cpp 직접 사용 (기존 방식)
            from pathlib import Path
            model_path = Path(settings.llm_model_path)
            
            safe_print(f"[LLM Worker] Checking model file: {model_path}")
            
            if not model_path.exists():
                safe_print(f"[LLM Worker] [ERROR] Health check failed: Model file does not exist: {model_path}")
                return False
            
            if not model_path.is_file():
                safe_print(f"[LLM Worker] [ERROR] Health check failed: Model path is not a file: {model_path}")
                return False
            
            file_size_mb = model_path.stat().st_size / (1024 * 1024)
            safe_print(f"[LLM Worker] [OK] Model file verified (size: {file_size_mb:.2f} MB)")
            
            safe_print("[LLM Worker] Loading llama.cpp model and running test query...")
            from .llm_summarizer import summarize_transcription
            
            test_text = "This is a test."
            result = summarize_transcription(test_text)
            
            if result and len(result) > 0:
                safe_print(f"[LLM Worker] [OK] Health check successful: Model responded normally (response length: {len(result)} chars)")
                return True
            else:
                safe_print("[LLM Worker] [ERROR] Health check failed: Model response is empty")
                return False
        else:
            safe_print(f"[LLM Worker] [ERROR] Unsupported LLM provider: {settings.llm_provider}")
            return False
            
    except SystemExit:
        safe_print("[LLM Worker] [ERROR] Health check failed: Process crash occurred")
        raise
    except Exception as exc:
        safe_print(f"[LLM Worker] [ERROR] Health check failed: {exc}")
        import traceback
        traceback.print_exc()
        return False


class NoOpDeathPenalty(BaseDeathPenalty):
    """Windows 호환을 위한 빈 DeathPenalty (SIGALRM 사용 안 함)."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class WindowsCompatibleWorker(SimpleWorker):
    """Windows 호환 SimpleWorker (SIGALRM 사용 안 함)."""

    def __init__(self, *args, **kwargs):
        kwargs.pop("job_timeout", None)
        super().__init__(*args, **kwargs)
        if sys.platform == "win32":
            self.death_penalty_class = NoOpDeathPenalty
            self.job_timeout = None


def main() -> None:
    """LLM 전용 RQ 워커 메인 함수."""
    logger.info("Starting LLM RQ worker for queue: {}", LLM_QUEUE_NAME)
    safe_print(f"[LLM Worker] ========================================")
    safe_print(f"[LLM Worker] Starting RQ worker")
    safe_print(f"[LLM Worker] Queue name: {LLM_QUEUE_NAME}")

    is_windows = sys.platform == "win32"
    if is_windows:
        safe_print("[LLM Worker] Windows environment: Using SimpleWorker (no fork, timeout disabled)")
    safe_print(f"[LLM Worker] ========================================")

    # DB에 남아있는 SUMMARIZING 상태 작업 재큐잉
    try:
        safe_print("[LLM Worker] Checking DB for requeue...")
        requeued = asyncio.run(requeue_summarizing_contents())
        if requeued:
            safe_print(f"[LLM Worker] [OK] Re-enqueued {requeued} LLM jobs to queue.")
        else:
            safe_print("[LLM Worker] [OK] No LLM jobs to requeue")
    except Exception as exc:
        safe_print(f"[LLM Worker] [WARN] DB requeue check failed: {exc}")
        logger.warning("Failed to requeue summarizing contents: {}", exc)

    # Stale 작업 정리
    try:
        from .cleanup import cleanup_stale_jobs
        safe_print(f"[LLM Worker] Cleaning up stale jobs...")
        stats = cleanup_stale_jobs("llm_tasks", requeue=True)
        if stats["started_jobs"] > 0:
            safe_print(f"[LLM Worker] [OK] {stats['requeued']} jobs requeued, {stats['failed']} jobs failed")
    except Exception as exc:
        safe_print(f"[LLM Worker] [WARN] Stale job cleanup failed: {exc}")
        logger.warning("Failed to cleanup stale jobs: {}", exc)
    
    # LLM 헬스체크
    safe_print("[LLM Worker] Running health check...")
    health_check_result = health_check_llm()
    if not health_check_result:
        safe_print("[LLM Worker] [ERROR] Cannot start worker due to health check failure.")
        safe_print("[LLM Worker]")
        safe_print("[LLM Worker] Possible solutions:")
        settings = get_settings()
        if settings.llm_provider == "lmstudio":
            safe_print("[LLM Worker] 1. Check if LM Studio app is running.")
            safe_print("[LLM Worker] 2. Check port/model settings in Settings > Local Server.")
            safe_print("[LLM Worker] 3. Verify that /v1/chat/completions responds with curl.")
        else:
            safe_print("[LLM Worker] 1. Check if model file is corrupted")
            safe_print("[LLM Worker] 2. Check if llama-cpp-python is built with Vulkan support")
            safe_print("[LLM Worker] 3. Check if model format is compatible with llama-cpp-python version")
        sys.exit(1)

    try:
        redis = get_redis_connection()
        logger.info("Redis connection established")
        safe_print("[LLM Worker] [OK] Redis connection successful")

        with Connection(redis):
            if is_windows:
                worker = WindowsCompatibleWorker(
                    [LLM_QUEUE_NAME],
                    connection=redis,
                )
            else:
                worker = SimpleWorker(
                    [LLM_QUEUE_NAME],
                    connection=redis,
                )

            logger.info("LLM worker created, starting work...")
            safe_print("[LLM Worker] [OK] Worker created")
            safe_print("[LLM Worker] Waiting for jobs... (waiting for jobs from LLM queue)")
            safe_print("[LLM Worker] ========================================")
            
            # 100개 작업 처리 후 자동 재시작 (리소스 누수 방지)
            worker.work(max_jobs=100)
            
            logger.info("LLM worker processed 100 jobs, will restart...")
            safe_print("[LLM Worker] 100 jobs processed, restarting worker...")
            
    except KeyboardInterrupt:
        logger.info("LLM worker shutdown requested")
        safe_print("[LLM Worker] Worker shutdown requested")
    except Exception as exc:
        logger.exception("LLM worker failed to start")
        safe_print(f"[LLM Worker] [ERROR] Worker start failed: {exc}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    # 워커를 주기적으로 재시작하여 리소스 누수 방지
    job_count = 0
    while True:
        try:
            main()
            job_count += 100
            logger.info("Total LLM jobs processed: {}, restarting...", job_count)
            safe_print(f"[LLM Worker] Total {job_count} jobs processed, restarting in 5 seconds...")
            
            import time
            time.sleep(5)  # 짧은 대기 후 재시작
            
        except KeyboardInterrupt:
            logger.info("LLM worker shutdown requested")
            safe_print("[LLM Worker] Worker shutdown")
            break
        except Exception as e:
            logger.exception("LLM worker crashed, restarting in 10 seconds...")
            safe_print(f"[LLM Worker] ERROR Worker crashed, restarting in 10 seconds: {e}")
            import time
            time.sleep(10)

