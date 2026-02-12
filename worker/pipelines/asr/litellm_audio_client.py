"""LiteLLM chat completion 엔드포인트를 통한 ASR/Diarization 요청 클라이언트.

Architecture V7.5:
- ASR: Worker → LiteLLM HTTP (/v1/chat/completions, task_type=asr) → custom_handler.acompletion() → Redis Stream → Provider Manager → GPU
- Diarization: Worker → LiteLLM HTTP (/v1/chat/completions, task_type=diarization) → custom_handler.acompletion() → Redis Stream → Provider Manager → GPU
- LLM: Worker → LiteLLM HTTP → custom_handler.astreaming() → Redis Stream → Provider Manager → GPU
- OCR: Worker → LiteLLM HTTP → custom_handler.acompletion() → Redis Stream → Provider Manager → GPU

V7.5 변경사항:
- ASR+Diarization 묶음 잠금: pipeline.py에서 lock_id 획득 후 전달
- lock_id가 전달되면 LiteLLM에서 잠금 재획득 스킵 (이미 획득됨)
- GPU 리소스 독점: ASR+Diarization 실행 중 다른 GPU 작업 대기
"""
import base64
import json
import os
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import redis

from worker.logging_config import logger
from worker.telemetry import get_trace_id, inject_trace_context

# LiteLLM Proxy URL
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-1234")

# Redis URL (잠금용)
REDIS_URL = os.getenv("REDIS_URL", "redis://valkey:6379/0")

# Redis 클라이언트 (Worker측 잠금용)
try:
    _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:
    logger.warning(f"[LiteLLM Client] Redis client init failed: {e}")
    _redis_client = None

# Lua 스크립트: 원자적 잠금 해제 (본인 lock_id만 삭제)
RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def acquire_gpu_lock(timeout: int = 3600, max_wait: float = 3600.0) -> str | None:
    """GPU 잠금 획득 (Worker측).

    ASR+Diarization 묶음 작업 시작 전 호출.
    잠금 획득 때까지 대기합니다.

    Args:
        timeout: 잠금 TTL (초, 기본 1시간)
        max_wait: 최대 대기 시간 (초, 기본 1시간)

    Returns:
        성공 시 lock_id, 실패 시 None
    """
    if not _redis_client:
        logger.warning("[Worker Lock] Redis not available, proceeding without lock")
        return str(uuid.uuid4())  # Redis 없으면 더미 ID 반환

    key = "worker:gpu:active"
    lock_id = str(uuid.uuid4())
    wait_start = time.time()

    while time.time() - wait_start < max_wait:
        try:
            # SETNX: 키가 없을 때만 설정 (원자적)
            acquired = _redis_client.set(key, lock_id, nx=True, ex=timeout)
            if acquired:
                logger.info(f"[Worker Lock] GPU acquired: {key} (lock_id={lock_id[:8]}...)")
                return lock_id
            else:
                logger.debug(f"[Worker Lock] GPU busy, waiting...")
        except Exception as e:
            logger.error(f"[Worker Lock] Failed to acquire GPU: {e}")

        time.sleep(0.5)

    logger.warning(f"[Worker Lock] GPU lock timeout after {max_wait}s")
    return None


def release_gpu_lock(lock_id: str) -> bool:
    """GPU 잠금 해제 (Worker측).

    ASR+Diarization 묶음 작업 완료 후 호출.

    Args:
        lock_id: 획득 시 받은 lock_id

    Returns:
        해제 성공 여부
    """
    if not _redis_client or not lock_id:
        return True

    key = "worker:gpu:active"

    try:
        result = _redis_client.eval(RELEASE_LOCK_SCRIPT, 1, key, lock_id)
        released = result == 1
        if released:
            logger.info(f"[Worker Lock] GPU released: {key}")
        else:
            logger.warning(f"[Worker Lock] GPU release failed (not owner or expired): {key}")
        return released
    except Exception as e:
        logger.error(f"[Worker Lock] Failed to release GPU: {e}")
        return False


class ASRProvider(Enum):
    """ASR Provider 타입."""
    FLM = "flm"
    WHISPER_CPP = "whisper-cpp"
    INSANELY_FAST = "insanely-fast"

# Diarization 응답 Wrapper (기존 코드 호환성을 위해 유지)
try:
    from .audio_gateway_client import DiarizationAnnotationWrapper
except ImportError:
    # Fallback: 간단한 래퍼
    class DiarizationAnnotationWrapper:
        def __init__(self, segments):
            self.segments = segments

        def itertracks(self, yield_label=False):
            for seg in self.segments:
                if yield_label:
                    # (Segment, track, label) 형식으로 반환
                    class Segment:
                        def __init__(self, start, end):
                            self.start = start
                            self.end = end

                    yield Segment(seg["start"], seg["end"]), None, seg["speaker"]
                else:
                    yield seg


def call_litellm_transcription(
    audio_file_path: Path,
    accuracy_mode: str = "speed",
    language: str = "ko",
    timeout: float = 1800.0,
    resource_timeout: float = 120.0,
    on_resource_acquired: callable = None,
    lock_id: str | None = None,  # V7.5: Worker에서 획득한 GPU 잠금 ID
) -> tuple[dict[str, Any], float, float, ASRProvider]:
    """LiteLLM chat completion 엔드포인트를 통한 ASR 요청.

    Architecture V7.4: OCR과 동일한 방식
    - Worker → LiteLLM (/v1/chat/completions) → custom_handler.acompletion() → Redis Stream → Provider Manager → GPU
    - extra_body에 task_type=asr, audio_base64 전달

    Args:
        audio_file_path: 오디오 파일 경로
        accuracy_mode: "speed" (whisper-turbo) 또는 "accuracy" (whisper-large-v3)
        language: 언어 코드
        timeout: ASR 요청 타임아웃 (초)
        resource_timeout: (미사용, 호환성 유지)
        on_resource_acquired: ASR 요청 직전 호출되는 콜백 (상태 업데이트용)
        lock_id: Worker에서 획득한 GPU 잠금 ID (V7.5: LiteLLM에서 재획득 스킵)

    Returns:
        (전사 결과, 대기 시간, 전사 시간, 사용된 Provider)
    """
    start_time = time.time()

    # accuracy_mode에 따라 모델 선택
    if accuracy_mode == "accuracy":
        model = "asr-accuracy"  # custom_handler에서 whisper-large-v3로 라우팅
    else:
        model = "asr-speed"     # custom_handler에서 whisper-turbo로 라우팅

    # OpenTelemetry trace 확인
    current_trace_id = get_trace_id()
    logger.info(f"[LiteLLM ASR] Requesting transcription: model={model}, accuracy_mode={accuracy_mode}, trace_id={current_trace_id}")

    # Audio 파일을 base64로 인코딩
    with open(audio_file_path, "rb") as f:
        audio_content = f.read()
        audio_base64 = base64.b64encode(audio_content).decode('utf-8')

    # LiteLLM chat completion 요청
    url = f"{LITELLM_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }

    # OpenTelemetry trace context 주입
    try:
        inject_trace_context(headers)
    except Exception as e:
        logger.warning(f"[LiteLLM ASR] Failed to inject trace context: {e}")

    # Chat completion 형식으로 ASR 요청
    extra_body = {
        "task_type": "asr",
        "audio_base64": audio_base64,
        "language": language,
        "accuracy_mode": accuracy_mode,
    }

    # V7.5: Worker에서 획득한 lock_id 전달 (LiteLLM에서 재획득 스킵)
    if lock_id:
        extra_body["lock_id"] = lock_id
        logger.info(f"[LiteLLM ASR] Passing lock_id to LiteLLM: {lock_id[:8]}...")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": f"Transcribe this audio (language: {language})"
            }
        ],
        "extra_body": extra_body,
    }

    # LiteLLM 요청 직전에 콜백 호출하여 "started" 이벤트 발행
    if on_resource_acquired:
        on_resource_acquired()
        logger.info(f"[LiteLLM ASR] Resource acquired callback invoked")

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()

        # Chat completion 응답에서 transcription 결과 추출
        if "choices" in result and len(result["choices"]) > 0:
            content = result["choices"][0].get("message", {}).get("content", "")

            # custom_handler가 JSON 문자열로 반환
            try:
                transcription_result = json.loads(content)
            except json.JSONDecodeError:
                # Fallback
                transcription_result = {"text": content, "segments": [], "language": language}
        else:
            raise ValueError(f"Unexpected response format: {result}")

    except httpx.HTTPStatusError as e:
        logger.error(f"[LiteLLM ASR] HTTP error: {e.response.status_code} - {e.response.text}")
        raise RuntimeError(f"ASR HTTP error: {e.response.status_code}")
    except httpx.TimeoutException:
        logger.error(f"[LiteLLM ASR] Timeout after {timeout}s")
        raise TimeoutError(f"ASR timeout after {timeout}s")
    except Exception as e:
        logger.error(f"[LiteLLM ASR] Request failed: {e}")
        raise RuntimeError(f"ASR request failed: {e}")

    total_time = time.time() - start_time

    # 사용된 Provider 파싱
    used_model = transcription_result.get("model", "")
    if "insanely-fast" in used_model or "whisper-large-v3" in used_model:
        provider = ASRProvider.INSANELY_FAST
    elif "whisper-turbo" in used_model or "whisper-cpp" in used_model:
        provider = ASRProvider.WHISPER_CPP
    elif "flm" in used_model:
        provider = ASRProvider.FLM
    else:
        provider = ASRProvider.INSANELY_FAST if accuracy_mode == "accuracy" else ASRProvider.WHISPER_CPP

    logger.info(f"[LiteLLM ASR] Transcription completed in {total_time:.2f}s")
    logger.info(f"[LiteLLM ASR] Provider used: {provider.value}")
    logger.info(f"[LiteLLM ASR] Text length: {len(transcription_result.get('text', ''))}")

    # 호환성: (결과, 대기시간, 처리시간, Provider)
    return transcription_result, 0.0, total_time, provider


def call_litellm_diarization(
    audio_file_path: Path,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    return_embeddings: bool = False,
    timeout: float = 1800.0,
    resource_timeout: float = 120.0,
    lock_id: str | None = None,  # V7.5: Worker에서 획득한 GPU 잠금 ID
) -> tuple[DiarizationAnnotationWrapper, float, float, dict | None, Any, dict]:
    """LiteLLM chat completion 엔드포인트를 통한 Diarization 요청.

    Architecture V7.4: ASR과 동일한 방식
    - Worker → LiteLLM (/v1/chat/completions) → custom_handler.acompletion() → Redis Stream → Provider Manager → GPU

    Args:
        audio_file_path: 오디오 파일 경로
        min_speakers: 최소 화자 수
        max_speakers: 최대 화자 수
        return_embeddings: 임베딩 반환 여부 (미지원, 호환성 유지)
        timeout: Diarization 요청 타임아웃 (초)
        resource_timeout: (미사용, 호환성 유지)
        lock_id: Worker에서 획득한 GPU 잠금 ID (V7.5: LiteLLM에서 재획득 스킵)

    Returns:
        (diarization, load_time, inference_time, embeddings_dict, pipeline, params)
    """
    start_time = time.time()

    # OpenTelemetry trace 확인
    current_trace_id = get_trace_id()
    logger.info(f"[LiteLLM Diarization] Requesting: min_speakers={min_speakers}, max_speakers={max_speakers}, trace_id={current_trace_id}")

    # Audio 파일을 base64로 인코딩
    with open(audio_file_path, "rb") as f:
        audio_content = f.read()
        audio_base64 = base64.b64encode(audio_content).decode('utf-8')

    # LiteLLM chat completion 요청
    url = f"{LITELLM_BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
    }

    # OpenTelemetry trace context 주입
    try:
        inject_trace_context(headers)
    except Exception as e:
        logger.warning(f"[LiteLLM Diarization] Failed to inject trace context: {e}")

    # Chat completion 형식으로 Diarization 요청
    extra_body = {
        "task_type": "diarization",
        "audio_base64": audio_base64,
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
    }

    # V7.5: Worker에서 획득한 lock_id 전달 (LiteLLM에서 재획득 스킵)
    if lock_id:
        extra_body["lock_id"] = lock_id
        logger.info(f"[LiteLLM Diarization] Passing lock_id to LiteLLM: {lock_id[:8]}...")

    payload = {
        "model": "diarization",
        "messages": [
            {
                "role": "user",
                "content": f"Diarize this audio (min_speakers={min_speakers}, max_speakers={max_speakers})"
            }
        ],
        "extra_body": extra_body,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()

        # Chat completion 응답에서 diarization 결과 추출
        if "choices" in result and len(result["choices"]) > 0:
            content = result["choices"][0].get("message", {}).get("content", "")

            # custom_handler가 JSON 문자열로 반환
            try:
                diarization_result = json.loads(content)
            except json.JSONDecodeError:
                raise ValueError(f"Failed to parse diarization result: {content}")
        else:
            raise ValueError(f"Unexpected response format: {result}")

    except httpx.HTTPStatusError as e:
        logger.error(f"[LiteLLM Diarization] HTTP error: {e.response.status_code} - {e.response.text}")
        raise RuntimeError(f"Diarization HTTP error: {e.response.status_code}")
    except httpx.TimeoutException:
        logger.error(f"[LiteLLM Diarization] Timeout after {timeout}s")
        raise TimeoutError(f"Diarization timeout after {timeout}s")
    except Exception as e:
        logger.error(f"[LiteLLM Diarization] Request failed: {e}")
        raise RuntimeError(f"Diarization request failed: {e}")

    total_time = time.time() - start_time

    # 결과 파싱
    segments = diarization_result.get("segments", [])
    num_speakers = diarization_result.get("num_speakers", 0)

    logger.info(f"[LiteLLM Diarization] Completed in {total_time:.2f}s")
    logger.info(f"[LiteLLM Diarization] Speakers: {num_speakers}, Segments: {len(segments)}")

    # DiarizationAnnotationWrapper로 변환 (기존 API 호환)
    diarization = DiarizationAnnotationWrapper(segments)

    # 파라미터 정보 저장
    params = {
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
        "num_speakers_detected": num_speakers,
    }

    # 호환성: (diarization, 대기시간, 처리시간, embeddings, pipeline, params)
    return diarization, 0.0, total_time, None, None, params
