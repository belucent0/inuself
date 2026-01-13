from __future__ import annotations

import logging
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Mapping

import httpx

from worker.config import WorkerSettings as Settings

logger = logging.getLogger(__name__)


class LlamaServerClientError(RuntimeError):
    """LLM API 서버 호출 실패 시 사용하는 예외 (llama.cpp 서버)."""


@contextmanager
def _llama_server_process(settings: Settings, ocr_provider: str | None = None, port: int | None = None):
    """
    llama-server를 subprocess로 시작하고 종료하는 컨텍스트 매니저.
    whisper-cli.exe처럼 요청마다 시작하고 종료함.
    
    Note: 락 없이 동작합니다. Celery concurrency=1에 의해 
    한 워커 프로세스 내에서 한 번에 하나의 태스크만 실행됩니다.
    
    Args:
        settings: WorkerSettings 객체
        ocr_provider: OCR provider (None이면 llm_provider 확인, "llamacpp_server"면 서버 시작)
        port: 사용할 포트 번호 (None이면 설정값 사용)
    """
    # OCR provider가 지정된 경우 OCR provider 확인, 아니면 LLM provider 확인
    # llamacpp_server provider가 아니면 서버를 시작하지 않음
    # flm provider는 외부 서버를 사용하므로 서버 시작 불필요
    if ocr_provider is not None:
        # OCR 요청인 경우
        if ocr_provider != "llamacpp_server":
            yield None
            return
    else:
        # LLM 요청인 경우
        if settings.llm_provider != "llamacpp_server":
            yield None
            return
    
    # LLM 서버 설정 확인
    if not settings.llm_server_path or not Path(settings.llm_server_path).exists():
        logger.error(
            "[LLM] LLM server path is not set or file does not exist: %s. Cannot start llama-server.",
            settings.llm_server_path or "(empty)"
        )
        yield None
        return
    
    model_path = settings.worker_model_path
    if not model_path:
        logger.error(
            "[LLM] LLM server model path is not set for worker_type=%s. "
            "Please set LLM_SERVER_MODEL (for llm worker) or OCR_SERVER_MODEL (for ocr worker). Cannot start llama-server.",
            settings.worker_type,
        )
        yield None
        return
    
    if not Path(model_path).exists():
        logger.error(
            "[LLM] LLM server model path does not exist for worker_type=%s: %s. Cannot start llama-server.",
            settings.worker_type,
            model_path,
        )
        yield None
        return
    
    mmproj_path = settings.worker_mmproj_path
    
    # 디버깅: 경로 확인
    logger.info(f"[LLM] llm_server_path: {settings.llm_server_path}")
    logger.info(f"[LLM] model_path: {model_path}")
    logger.info(f"[LLM] llm_server_path exists: {Path(settings.llm_server_path).exists() if settings.llm_server_path else False}")
    
    if not settings.llm_server_path:
        logger.error("[LLM] LLM_SERVER_PATH is not set. Cannot start llama-server.")
        yield None
        return
    
    if not Path(settings.llm_server_path).exists():
        logger.error(f"[LLM] LLM_SERVER_PATH does not exist: {settings.llm_server_path}")
        yield None
        return
    
    # LLM 서버 명령어 구성
    cmd = [
        settings.llm_server_path,
        '--model', model_path,
        '--n-gpu-layers', str(settings.llm_server_gpu_layers),
        '--host', '0.0.0.0',
        '--port', str(port) if port else str(settings.llm_server_port),
        '--ctx-size', str(settings.llm_context_length),  # LLM_CONTEXT_LENGTH 사용
        '--threads', str(settings.llm_server_threads),
        '--batch-size', str(settings.llm_server_batch_size),
        '--parallel', '1',
    ]
    
    # Vision 모델인 경우 mmproj 추가
    if mmproj_path and Path(mmproj_path).exists():
        cmd.extend(['--mmproj', mmproj_path, '--jinja'])
    
    logger.info(f"[LLM] Starting llama-server: {' '.join(cmd)}")
    
    # Windows에서 프로세스 그룹 생성 플래그 설정
    creation_flags = 0
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP: 프로세스 그룹 생성 (종료 시 자식 프로세스까지 종료 가능)
        # CREATE_NO_WINDOW: 콘솔 창 숨기기
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    
    # llama-server 로그 파일 경로
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "llama_server.log"
    
    # llama-server 시작 (stdout/stderr를 로그 파일로 저장)
    log_handle = open(log_file, 'w', encoding='utf-8', errors='replace')
    logger.info(f"[LLM] llama-server output will be logged to: {log_file}")
    
    proc = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,  # stderr도 stdout으로 리다이렉트
        text=True,
        encoding='utf-8',
        errors='replace',
        creationflags=creation_flags if sys.platform == "win32" else 0,
    )
    
    base_url = f"http://localhost:{port if port else settings.llm_server_port}"
    # llama.cpp 서버는 /v1/models 엔드포인트를 사용하여 헬스체크
    health_url = f"{base_url}/v1/models"
    
    try:
        # 서버가 준비될 때까지 대기 (최대 120초, 모델 로드 시간 고려)
        logger.info(f"[LLM] Waiting for llama-server to start on {base_url}...")
        max_wait_time = 120  # 모델 로드 시간을 고려하여 120초로 증가
        wait_interval = 2  # 2초마다 체크
        elapsed = 0
        
        while elapsed < max_wait_time:
            try:
                with httpx.Client(timeout=5.0) as client:
                    response = client.get(health_url)
                    if response.status_code == 200:
                        # 모델이 실제로 로드되었는지 확인
                        try:
                            models_data = response.json()
                            models = models_data.get("data", [])
                            if models:
                                logger.info(f"[LLM] llama-server is ready on {base_url} (models loaded: {len(models)})")
                                # 서버가 완전히 준비될 때까지 추가 대기 (Vision 모델 초기화 시간 고려)
                                logger.info(f"[LLM] Waiting additional 10 seconds for server to be fully ready...")
                                time.sleep(10)
                                break
                            else:
                                logger.info(f"[LLM] llama-server responded but no models loaded yet...")
                        except Exception:
                            # JSON 파싱 실패해도 서버가 응답했으므로 계속 진행
                            logger.info(f"[LLM] llama-server is responding on {base_url}")
                            # 서버가 완전히 준비될 때까지 추가 대기
                            logger.info(f"[LLM] Waiting additional 10 seconds for server to be fully ready...")
                            time.sleep(10)
                            break
            except (httpx.HTTPError, httpx.ConnectError):
                # 서버가 아직 준비되지 않음
                pass
            
            time.sleep(wait_interval)
            elapsed += wait_interval
            if elapsed % 10 == 0:
                logger.info(f"[LLM] Still waiting for llama-server... ({elapsed}s)")
        
        if elapsed >= max_wait_time:
            raise LlamaServerClientError(f"llama-server did not start within {max_wait_time} seconds.")
        
        yield proc
        
    finally:
        # llama-server 종료
        logger.info("[LLM] Stopping llama-server...")
        try:
            if sys.platform == "win32":
                # Windows에서는 taskkill을 사용하여 프로세스 트리 전체를 강제 종료
                # /F: Forcefully terminate
                # /T: Terminate child processes (process tree)
                # /PID: Process ID
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], 
                             stdout=subprocess.DEVNULL, 
                             stderr=subprocess.DEVNULL,
                             check=False)
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            
            # 로그 파일 핸들 닫기
            try:
                log_handle.close()
            except Exception:
                pass
            
            logger.info("[LLM] llama-server stopped")
        except Exception as e:
            logger.warning(f"[LLM] Error stopping llama-server: {e}")
            # 강제 종료 시도
            try:
                proc.kill()
            except Exception:
                pass


def _build_messages(raw_messages: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    """httpx가 직렬화할 수 있도록 메시지 시퀀스를 리스트로 변환."""
    converted: list[dict[str, str]] = []
    for message in raw_messages:
        role = message.get("role")
        content = message.get("content")
        if not role or not content:
            raise LlamaServerClientError("LLM API 메시지에 role/content가 필요합니다.")
        converted.append({"role": role, "content": content})
    if not converted:
        raise LlamaServerClientError("LLM API 요청 메시지가 비어 있습니다.")
    return converted


def request_chat_completion(
    *,
    settings: Settings,
    messages: Iterable[Mapping[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
) -> str:
    """
    LLM API 서버 Chat Completions API 호출 (llama.cpp 서버).
    
    llamacpp_server provider인 경우, 요청마다 llama-server를 시작하고 종료함
    (whisper-cli.exe와 동일한 방식으로 메모리 절약).
    
    Note: 락 없이 동작합니다. Celery concurrency=1에 의해 
    한 워커 프로세스 내에서 한 번에 하나의 태스크만 실행됩니다.
    """
    logger.info("[LLM] request_chat_completion called, provider=%s", settings.llm_provider)
    
    # llama-server 시작 및 API 호출 (락 없이)
    with _llama_server_process(settings):
        # llm_base_url/llm_model_name 우선, 없으면 하위 호환성 설정 사용
        base_url = settings.llm_api_base_url.rstrip("/")
        model_name = settings.llm_api_model_name
        url = f"{base_url}/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": _build_messages(messages),
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "max_tokens": max_tokens if max_tokens is not None else settings.llm_max_tokens,
            "stream": stream,
        }

        logger.info("LLM API server call: url=%s model=%s provider=%s", url, model_name, settings.llm_provider)

        # FLM provider는 Authorization 헤더 추가
        headers = {}
        if settings.llm_provider == "flm":
            headers["Authorization"] = "Bearer flm"

        # 모델이 로드될 때까지 재시도 (최대 180초)
        max_retry_time = 180
        retry_interval = 3
        elapsed = 0
        result = None
        
        while elapsed < max_retry_time:
            try:
                with httpx.Client(timeout=120.0, headers=headers) as client:
                    response = client.post(url, json=payload)
            
                # 503 "Loading model" 에러인 경우 재시도
                if response.status_code == 503:
                    try:
                        error_body = response.json()
                        error_message = error_body.get("error", {}).get("message", "")
                        if "Loading model" in error_message or "loading" in error_message.lower():
                            logger.info(f"[LLM] Model is still loading, waiting... ({elapsed}s)")
                            time.sleep(retry_interval)
                            elapsed += retry_interval
                            continue
                    except Exception:
                        pass
                
                # 400 에러인 경우 응답 본문을 로깅하여 정확한 원인 확인
                if response.status_code == 400:
                    try:
                        error_body = response.json()
                        logger.error(
                            "LLM API server 400 Bad Request response: %s",
                            error_body
                        )
                    except Exception:
                        error_text = response.text[:500] if response.text else "No response body"
                        logger.error(
                            "LLM API server 400 Bad Request response (JSON parse failed): %s",
                            error_text
                        )
                
                response.raise_for_status()
                result = response.json()
                break  # 성공하면 루프 종료
            
            except httpx.HTTPStatusError as exc:
                # 503이지만 "Loading model"이 아닌 경우는 재시도하지 않음
                if exc.response.status_code == 503:
                    try:
                        error_body = exc.response.json()
                        error_message = error_body.get("error", {}).get("message", "")
                        if "Loading model" in error_message or "loading" in error_message.lower():
                            logger.info(f"[LLM] Model is still loading, waiting... ({elapsed}s)")
                            time.sleep(retry_interval)
                            elapsed += retry_interval
                            continue
                    except Exception:
                        pass
                
                # 다른 에러는 즉시 처리
                try:
                    error_body = exc.response.json()
                    error_msg = f"LLM API server HTTP error ({exc.response.status_code}): {error_body}"
                except Exception:
                    error_text = exc.response.text[:500] if exc.response.text else "No response body"
                    error_msg = f"LLM API server HTTP error ({exc.response.status_code}): {error_text}"
                logger.error(error_msg)
                raise LlamaServerClientError(error_msg) from exc
            except (httpx.ReadError, httpx.ConnectError) as exc:
                # 연결 끊김 에러는 재시도 (llama-server가 응답 중 종료될 수 있음)
                error_str = str(exc)
                if elapsed < max_retry_time - retry_interval:
                    logger.warning(f"[LLM] Connection error (will retry): {error_str} ({elapsed}s)")
                    time.sleep(retry_interval)
                    elapsed += retry_interval
                    continue
                else:
                    error_msg = f"LLM API server connection error (retry timeout): {exc}"
                    logger.error(error_msg)
                    raise LlamaServerClientError(error_msg) from exc
            except httpx.HTTPError as exc:
                # 기타 HTTP 에러는 재시도하지 않음
                error_msg = f"LLM API server HTTP error: {exc}"
                logger.error(error_msg)
                raise LlamaServerClientError(error_msg) from exc
            except Exception as exc:
                # 기타 예외는 재시도하지 않음
                error_msg = f"LLM API server call unexpected error: {exc}"
                logger.error(error_msg)
                raise LlamaServerClientError(error_msg) from exc
        
        if elapsed >= max_retry_time:
            raise LlamaServerClientError(f"Model did not load within {max_retry_time} seconds.")
        
        if result is None:
            raise LlamaServerClientError("No result received from LLM API server.")

        choices = result.get("choices")
        if not choices:
            raise LlamaServerClientError("LLM API server response has no choices.")

        message = choices[0].get("message") or {}
        content = message.get("content", "").strip()
        finish_reason = choices[0].get("finish_reason", "")
        
        # content가 비어있지만 reasoning이 있으면 reasoning을 사용
        if not content:
            reasoning = message.get("reasoning", "").strip()
            if reasoning:
                logger.info("LLM API server response: content is empty but reasoning exists (length: %d chars)", len(reasoning))
                # reasoning이 있으면 모델이 작동 중이므로 reasoning의 일부를 반환
                return reasoning[:200] if len(reasoning) > 200 else reasoning
            # finish_reason이 "length"인 경우는 모델이 응답을 생성하려고 했지만 토큰 제한으로 실패한 것
            # 이 경우에도 헬스체크는 통과시킴 (워커는 시작하되 첫 요청이 느릴 수 있음)
            if finish_reason == "length":
                logger.warning("LLM API server response: content is empty and finish_reason is 'length'. Model failed to complete response due to token limit.")
                # 빈 문자열을 반환하지 않고 최소한의 응답을 반환
                return "Response generation in progress (token limit)"
            raise LlamaServerClientError("LLM API server response message.content is empty.")

        logger.info("LLM API server response received (length: %d chars, finish_reason: %s)", len(content), finish_reason)
        return content
