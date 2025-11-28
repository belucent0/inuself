import asyncio
import logging
import sys
from rq import Connection
from rq.worker import SimpleWorker
from rq.timeouts import BaseDeathPenalty

from ..core.config import get_settings
from ..core.redis import get_redis_connection
from .llm_queue import LLM_QUEUE_NAME
from .requeue import requeue_summarizing_contents
from .utils import safe_print

logger = logging.getLogger(__name__)


def health_check_llm() -> bool:
    """LLM 모델 로드 및 간단한 쿼리 테스트."""
    try:
        import httpx
        from ..core.config import get_settings
        
        settings = get_settings()
        
        safe_print(f"[LLM Worker] 헬스체크 시작: LLM provider={settings.llm_provider}")
        
        if settings.llm_provider == "ollama":
            # Ollama API 헬스체크
            safe_print(f"[LLM Worker] Ollama 연결 확인: {settings.ollama_base_url}")
            safe_print(f"[LLM Worker] 모델 이름: {settings.ollama_model_name}")
            
            try:
                # Ollama API 태그 확인 (모델 목록)
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(f"{settings.ollama_base_url}/api/tags")
                    response.raise_for_status()
                    models = response.json().get("models", [])
                    model_names = [m.get("name", "") for m in models]
                    
                    # 모델 이름 매칭: 설정된 이름 또는 :latest 태그가 붙은 버전 확인
                    configured_name = settings.ollama_model_name
                    safe_print(f"[LLM Worker] [INFO] 설정된 모델 이름: '{configured_name}'")
                    safe_print(f"[LLM Worker] [INFO] 사용 가능한 모델: {', '.join(model_names)}")
                    
                    # 정확히 일치하는 모델이 있는지 확인
                    model_found = configured_name in model_names
                    
                    # 정확히 일치하지 않으면, :latest 태그가 붙은 버전 확인
                    if not model_found and f"{configured_name}:latest" in model_names:
                        model_found = True
                    
                    # 여전히 없으면, 콜론이 포함된 모델 이름인 경우 처리
                    if not model_found:
                        for name in model_names:
                            # 설정된 이름이 콜론을 포함하는 경우 (예: "gpt-oss:20b")
                            if ":" in configured_name:
                                # 정확히 일치하는지 확인
                                if name == configured_name:
                                    model_found = True
                                    break
                            else:
                                # 설정된 이름에 콜론이 없으면, 콜론으로 시작하는 태그 버전 확인 (예: "gpt-oss-20b" -> "gpt-oss-20b:latest")
                                if name.startswith(f"{configured_name}:"):
                                    model_found = True
                                    break
                    
                    if not model_found:
                        safe_print(f"[LLM Worker] [WARN] 모델 '{configured_name}'이 Ollama에 없습니다.")
                        safe_print(f"[LLM Worker]   사용 가능한 모델: {', '.join(model_names[:5])}")
                        safe_print(f"[LLM Worker]   모델을 먼저 import하세요: ollama create {configured_name} -f Modelfile")
                        return False
                    
                    # 실제 사용할 모델 이름 찾기
                    actual_model_name = configured_name
                    
                    # 정확히 일치하는 모델이 있으면 사용
                    if configured_name in model_names:
                        actual_model_name = configured_name
                    # :latest 태그가 있으면 사용
                    elif f"{configured_name}:latest" in model_names:
                        actual_model_name = f"{configured_name}:latest"
                        safe_print(f"[LLM Worker] [INFO] 모델 이름 자동 매칭: '{configured_name}' -> '{actual_model_name}'")
                    # 콜론이 포함된 모델 이름인 경우 (예: "gpt-oss:20b")
                    elif ":" in configured_name:
                        # 정확히 일치하는 모델 찾기
                        for name in model_names:
                            if name == configured_name:
                                actual_model_name = name
                                break
                    # 콜론으로 시작하는 태그 버전이 있으면 사용
                    elif any(name.startswith(f"{configured_name}:") for name in model_names):
                        # 첫 번째로 매칭되는 태그 버전 사용
                        for name in model_names:
                            if name.startswith(f"{configured_name}:"):
                                actual_model_name = name
                                safe_print(f"[LLM Worker] [INFO] 모델 이름 자동 매칭: '{configured_name}' -> '{actual_model_name}'")
                                break
                    
                    # 실제 모델 이름으로 설정 업데이트 (런타임에만 적용)
                    if actual_model_name != configured_name:
                        settings.ollama_model_name = actual_model_name
                        safe_print(f"[LLM Worker] [INFO] 모델 이름 업데이트: '{configured_name}' -> '{actual_model_name}'")
                    
                    safe_print(f"[LLM Worker] [OK] Ollama 연결 성공, 모델 확인 완료: {actual_model_name}")
                    
                    # 간단한 테스트 쿼리 (타임아웃 60초)
                    safe_print("[LLM Worker] 테스트 쿼리 실행 중: 'hello' (최대 60초, 첫 로딩 시 시간이 걸릴 수 있음)")
                    try:
                        import httpx
                        
                        # 간단한 'hello' 테스트
                        test_payload = {
                            "model": actual_model_name,
                            "prompt": "hello",
                            "stream": False,
                            "keep_alive": "0",  # 테스트 후 즉시 언로드
                            "options": {
                                "num_predict": 10,  # 매우 짧은 응답만 요청
                                "num_gpu": -1,  # GPU 사용 강제
                            },
                        }
                        
                        safe_print(f"[LLM Worker]   테스트 요청 전송 중... (타임아웃: 60초)")
                        with httpx.Client(timeout=60.0) as test_client:  # 60초 타임아웃 (첫 로딩 시 시간이 걸릴 수 있음)
                            test_response = test_client.post(
                                f"{settings.ollama_base_url}/api/generate",
                                json=test_payload
                            )
                            test_response.raise_for_status()
                            test_result = test_response.json()
                            test_response_text = test_result.get("response", "").strip()
                            
                            # 응답 확인
                            if test_response_text:
                                safe_print(f"[LLM Worker] [OK] 테스트 쿼리 성공: 응답='{test_response_text[:50]}...'")
                                
                                # GPU 사용 여부 확인을 위한 로그
                                load_duration = test_result.get("load_duration", 0)
                                total_duration = test_result.get("total_duration", 0)
                                if load_duration > 0:
                                    safe_print(f"[LLM Worker]   모델 로드 시간: {load_duration / 1e9:.2f}초")
                                if total_duration > 0:
                                    safe_print(f"[LLM Worker]   총 처리 시간: {total_duration / 1e9:.2f}초")
                                
                                safe_print("[LLM Worker] [OK] 헬스체크 성공: 모델이 정상 응답함, 워커 시작")
                                return True
                            else:
                                safe_print("[LLM Worker] [WARN] 테스트 응답이 비어있지만, 워커는 시작합니다")
                                return True  # 빈 응답이어도 워커 시작 (모델 로딩 중일 수 있음)
                    except httpx.TimeoutException:
                        safe_print("[LLM Worker] [WARN] 테스트 쿼리 타임아웃 (60초)")
                        safe_print("[LLM Worker]   모델이 첫 로딩 중일 수 있습니다. 워커는 시작하지만 첫 요청이 느릴 수 있습니다.")
                        safe_print("[LLM Worker]   GPU 메모리 부족으로 시스템 RAM에 로드 중일 수 있습니다.")
                        return True  # 타임아웃이어도 워커 시작
                    except Exception as test_exc:
                        safe_print(f"[LLM Worker] [WARN] 테스트 쿼리 실패: {test_exc}")
                        safe_print("[LLM Worker]   모델이 로딩 중일 수 있습니다. 워커는 시작하지만 첫 요청이 느릴 수 있습니다.")
                        return True  # 테스트 실패해도 워커는 시작 (모델이 로딩 중일 수 있음)
            except httpx.HTTPError as e:
                safe_print(f"[LLM Worker] [ERROR] Ollama 연결 실패: {e}")
                safe_print(f"[LLM Worker]   Ollama가 실행 중인지 확인하세요:")
                safe_print(f"[LLM Worker]   - Windows 서비스 확인: powershell.exe -Command 'Get-Service Ollama'")
                safe_print(f"[LLM Worker]   - 또는 수동 시작: ollama serve")
                return False
        
        elif settings.llm_provider == "lmstudio":
            from .lmstudio_client import LMStudioClientError, request_chat_completion

            safe_print(f"[LLM Worker] LM Studio 연결 확인: {settings.lmstudio_base_url}")
            safe_print(f"[LLM Worker] 모델 이름: {settings.lmstudio_model_name}")

            test_messages = [
                {"role": "system", "content": settings.lmstudio_system_prompt},
                {"role": "user", "content": "간단한 헬스체크 문장을 요약해 주세요."},
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
                    safe_print("[LLM Worker] [WARN] LM Studio 헬스체크: 모델이 토큰 제한으로 응답을 완료하지 못했습니다.")
                    safe_print("[LLM Worker]   모델은 작동 중이지만 max_tokens 설정을 확인하세요.")
                    safe_print("[LLM Worker]   워커는 시작하지만 첫 요청이 느릴 수 있습니다.")
                    return True
                safe_print(f"[LLM Worker] [OK] LM Studio 테스트 응답: '{response[:60]}...'")
                return True
            except LMStudioClientError as exc:
                error_msg = str(exc)
                # content가 비어있지만 reasoning이 있는 경우는 모델이 작동 중이므로 통과
                if "reasoning이 있음" in error_msg:
                    safe_print("[LLM Worker] [WARN] LM Studio 헬스체크: content가 비어있지만 reasoning이 있습니다.")
                    safe_print("[LLM Worker]   모델이 reasoning을 사용 중입니다. 워커는 시작합니다.")
                    return True
                safe_print(f"[LLM Worker] [ERROR] LM Studio 헬스체크 실패: {exc}")
                safe_print("[LLM Worker]   LM Studio 데스크톱 앱이 실행 중인지, 프록시가 필요 없는지 확인하세요.")
                return False

        elif settings.llm_provider == "llama_cpp":
            # llama_cpp 직접 사용 (기존 방식)
            from pathlib import Path
            model_path = Path(settings.llm_model_path)
            
            safe_print(f"[LLM Worker] 모델 파일 확인 중: {model_path}")
            
            if not model_path.exists():
                safe_print(f"[LLM Worker] [ERROR] 헬스체크 실패: 모델 파일이 존재하지 않습니다: {model_path}")
                return False
            
            if not model_path.is_file():
                safe_print(f"[LLM Worker] [ERROR] 헬스체크 실패: 모델 경로가 파일이 아닙니다: {model_path}")
                return False
            
            file_size_mb = model_path.stat().st_size / (1024 * 1024)
            safe_print(f"[LLM Worker] [OK] 모델 파일 확인 완료 (크기: {file_size_mb:.2f} MB)")
            
            safe_print("[LLM Worker] llama.cpp 모델 로드 및 테스트 쿼리 실행 중...")
            from .llm_summarizer import summarize_transcription
            
            test_text = "This is a test."
            result = summarize_transcription(test_text)
            
            if result and len(result) > 0:
                safe_print(f"[LLM Worker] [OK] 헬스체크 성공: 모델이 정상 응답함 (응답 길이: {len(result)} chars)")
                return True
            else:
                safe_print("[LLM Worker] [ERROR] 헬스체크 실패: 모델 응답이 비어있음")
                return False
        else:
            safe_print(f"[LLM Worker] [ERROR] 지원하지 않는 LLM provider: {settings.llm_provider}")
            return False
            
    except SystemExit:
        safe_print("[LLM Worker] [ERROR] 헬스체크 실패: 프로세스 크래시 발생")
        raise
    except Exception as exc:
        safe_print(f"[LLM Worker] [ERROR] 헬스체크 실패: {exc}")
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
    logger.info("Starting LLM RQ worker for queue: %s", LLM_QUEUE_NAME)
    safe_print(f"[LLM Worker] ========================================")
    safe_print(f"[LLM Worker] RQ 워커 시작")
    safe_print(f"[LLM Worker] 큐 이름: {LLM_QUEUE_NAME}")

    is_windows = sys.platform == "win32"
    if is_windows:
        safe_print("[LLM Worker] Windows 환경: SimpleWorker 사용 (fork 없음, 타임아웃 비활성화)")
    safe_print(f"[LLM Worker] ========================================")

    # DB에 남아있는 SUMMARIZING 상태 작업 재큐잉
    try:
        safe_print("[LLM Worker] DB 재큐잉 검사 중...")
        requeued = asyncio.run(requeue_summarizing_contents())
        if requeued:
            safe_print(f"[LLM Worker] [OK] {requeued}개의 LLM 작업을 다시 큐에 등록했습니다.")
        else:
            safe_print("[LLM Worker] [OK] 재큐잉할 LLM 작업 없음")
    except Exception as exc:
        safe_print(f"[LLM Worker] [WARN] DB 재큐잉 검사 실패: {exc}")
        logger.warning("Failed to requeue summarizing contents: %s", exc)

    # Stale 작업 정리
    try:
        from .cleanup import cleanup_stale_jobs
        safe_print(f"[LLM Worker] Stale 작업 정리 중...")
        stats = cleanup_stale_jobs("llm_tasks", requeue=True)
        if stats["started_jobs"] > 0:
            safe_print(f"[LLM Worker] [OK] {stats['requeued']}개 작업 재시도, {stats['failed']}개 실패 처리")
    except Exception as exc:
        safe_print(f"[LLM Worker] [WARN] Stale 작업 정리 실패: {exc}")
        logger.warning("Failed to cleanup stale jobs: %s", exc)
    
    # LLM 헬스체크
    safe_print("[LLM Worker] 헬스체크를 실행합니다...")
    health_check_result = health_check_llm()
    if not health_check_result:
        safe_print("[LLM Worker] [ERROR] 헬스체크 실패로 인해 워커를 시작할 수 없습니다.")
        safe_print("[LLM Worker]")
        safe_print("[LLM Worker] 가능한 해결 방법:")
        settings = get_settings()
        if settings.llm_provider == "ollama":
            safe_print(f"[LLM Worker] 1. Ollama가 실행 중인지 확인:")
            safe_print(f"[LLM Worker]    - Windows 서비스: powershell.exe -Command 'Get-Service Ollama'")
            safe_print(f"[LLM Worker]    - 또는 수동 시작: ollama serve")
            safe_print(f"[LLM Worker] 2. 모델이 등록되어 있는지 확인: ollama list")
            safe_print(f"[LLM Worker] 3. API 테스트: curl http://localhost:11434/api/tags")
        elif settings.llm_provider == "lmstudio":
            safe_print("[LLM Worker] 1. LM Studio 앱이 실행 중인지 확인하세요.")
            safe_print("[LLM Worker] 2. Settings > Local Server에서 포트/모델 설정을 점검하세요.")
            safe_print("[LLM Worker] 3. curl 로 /v1/chat/completions 호출 시 응답이 오는지 확인하세요.")
        else:
            safe_print("[LLM Worker] 1. 모델 파일이 손상되었는지 확인하세요")
            safe_print("[LLM Worker] 2. llama-cpp-python이 Vulkan을 지원하는 빌드인지 확인하세요")
            safe_print("[LLM Worker] 3. 모델 형식이 llama-cpp-python 버전과 호환되는지 확인하세요")
        sys.exit(1)

    try:
        redis = get_redis_connection()
        logger.info("Redis connection established")
        safe_print("[LLM Worker] [OK] Redis 연결 성공")

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
            safe_print("[LLM Worker] [OK] 워커 생성 완료")
            safe_print("[LLM Worker] 작업 대기 중... (LLM 큐에서 작업을 기다립니다)")
            safe_print("[LLM Worker] ========================================")
            
            # 100개 작업 처리 후 자동 재시작 (리소스 누수 방지)
            worker.work(max_jobs=100)
            
            logger.info("LLM worker processed 100 jobs, will restart...")
            safe_print("[LLM Worker] 100개 작업 처리 완료, 워커 재시작 중...")
            
    except KeyboardInterrupt:
        logger.info("LLM worker shutdown requested")
        safe_print("[LLM Worker] 워커 종료 요청됨")
    except Exception as exc:
        logger.exception("LLM worker failed to start")
        safe_print(f"[LLM Worker] [ERROR] 워커 시작 실패: {exc}")
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
            logger.info("Total LLM jobs processed: %d, restarting...", job_count)
            safe_print(f"[LLM Worker] 총 {job_count}개 작업 처리 완료, 5초 후 재시작...")
            
            import time
            time.sleep(5)  # 짧은 대기 후 재시작
            
        except KeyboardInterrupt:
            logger.info("LLM worker shutdown requested")
            safe_print("[LLM Worker] 워커 종료")
            break
        except Exception as e:
            logger.exception("LLM worker crashed, restarting in 10 seconds...")
            safe_print(f"[LLM Worker] ERROR 워커 크래시, 10초 후 재시작: {e}")
            import time
            time.sleep(10)

