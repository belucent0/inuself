"""LiteLLM Proxy를 통한 ASR/Diarization 요청 클라이언트.

Worker가 Audio Gateway를 직접 호출하는 대신 LiteLLM Proxy를 통해 요청합니다.
LiteLLM이 중앙집중 리소스 관리 (/resource/acquire, /resource/release)를 통해
GPU/NPU 리소스를 관리합니다.

Architecture V6.1: Worker → LiteLLM Resource Gate → Audio Gateway
- 중앙집중 리소스 관리 (SETNX 기반 Gate Semaphore)
- 자동 라우팅 (whisper.cpp/insanely-fast/FLM)
- 리소스 충돌 방지 및 순차 처리 보장
"""
import os
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from worker.logging_config import logger
from worker.utils.resource_client import (
    acquire_resource,
    release_resource,
    select_resource_type,
    ResourceAcquisitionError,
)


class ASRProvider(Enum):
    """ASR Provider 타입."""
    FLM = "flm"
    WHISPER_CPP = "whisper-cpp"
    INSANELY_FAST = "insanely-fast"


# LiteLLM Proxy 설정
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://asr-litellm:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-litellm-master")

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
) -> tuple[dict[str, Any], float, float, ASRProvider]:
    """LiteLLM Proxy를 통한 ASR 요청.

    중앙집중 리소스 관리를 사용하여 GPU/NPU 리소스를 안전하게 획득/해제합니다.

    Architecture V6.1 Flow:
    1. Worker → LiteLLM /resource/acquire (리소스 획득)
    2. Worker → LiteLLM /v1/audio/transcriptions (ASR 요청)
    3. Worker → LiteLLM /resource/release (리소스 해제)

    Args:
        audio_file_path: 오디오 파일 경로
        accuracy_mode: "speed" (whisper-turbo) 또는 "accuracy" (whisper-large-v3)
        language: 언어 코드
        timeout: ASR 요청 타임아웃 (초)
        resource_timeout: 리소스 획득 대기 시간 (초)
        on_resource_acquired: 리소스 획득 후 호출할 콜백 (UI 상태 업데이트용)

    Returns:
        (전사 결과, 모델 로드 시간, 전사 시간, 사용된 Provider)
    """
    start_time = time.time()
    task_id = f"asr-{uuid.uuid4().hex[:8]}"

    # 리소스 타입 결정 (speed=GPU, accuracy=GPU)
    # ASR은 항상 GPU 사용 (whisper.cpp 또는 insanely-fast)
    resource_type = "gpu"

    # accuracy_mode에 따라 모델 선택
    if accuracy_mode == "accuracy":
        model = "whisper-large-v3"  # → insanely-fast (GPU Accuracy)
    else:
        model = "whisper-turbo"     # → whisper.cpp (GPU Speed)

    logger.info(f"[LiteLLM Client] Requesting transcription: model={model}, accuracy_mode={accuracy_mode}")

    # ============================================================
    # Step 1: 리소스 획득 (중앙집중 Gate Semaphore)
    # ============================================================
    resource_info = None
    try:
        resource_info = acquire_resource(
            resource_type=resource_type,
            task_type="asr",
            task_id=task_id,
            accuracy_mode=accuracy_mode,
            timeout=resource_timeout,
        )
        resource_wait_time = resource_info.wait_time
        logger.info(f"[LiteLLM Client] Resource acquired: provider={resource_info.provider}, wait={resource_wait_time:.2f}s")
    except ResourceAcquisitionError as e:
        logger.error(f"[LiteLLM Client] Failed to acquire ASR resource: {e}")
        raise RuntimeError(f"ASR resource unavailable: {e}")

    # 리소스 획득 성공 → UI 상태 업데이트 콜백 호출
    if on_resource_acquired:
        try:
            on_resource_acquired()
        except Exception as cb_err:
            logger.warning(f"[LiteLLM Client] on_resource_acquired callback failed: {cb_err}")

    # ============================================================
    # Step 2: ASR API 호출
    # ============================================================
    try:
        url = f"{LITELLM_BASE_URL}/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {LITELLM_API_KEY}"}

        with open(audio_file_path, "rb") as f:
            files = {"file": (audio_file_path.name, f, "audio/wav")}
            data = {
                "model": model,
                "language": language,
            }

            try:
                with httpx.Client(timeout=timeout, headers=headers) as client:
                    logger.info(f"[LiteLLM Client] Sending request to {url}")
                    response = client.post(url, files=files, data=data)
                    response.raise_for_status()
                    result = response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[LiteLLM Client] HTTP Error: {e.response.status_code} - {e.response.text}")
                raise
            except httpx.TimeoutException:
                logger.error(f"[LiteLLM Client] Request timed out after {timeout}s")
                raise
            except Exception as e:
                logger.error(f"[LiteLLM Client] Request failed: {e}")
                raise

        total_time = time.time() - start_time

        # LiteLLM이 사용한 실제 Provider 파싱
        used_model = result.get("model", model)
        if "insanely-fast" in used_model or "whisper-large-v3" in used_model:
            provider = ASRProvider.INSANELY_FAST
        elif "whisper-turbo" in used_model or "whisper-cpp" in used_model:
            provider = ASRProvider.WHISPER_CPP
        elif "flm" in used_model:
            provider = ASRProvider.FLM
        else:
            provider = ASRProvider.INSANELY_FAST if accuracy_mode == "accuracy" else ASRProvider.WHISPER_CPP

        logger.info(f"[LiteLLM Client] Transcription completed in {total_time:.2f}s")
        logger.info(f"[LiteLLM Client] Provider used: {provider.value}")
        logger.info(f"[LiteLLM Client] Segments: {len(result.get('segments', []))}, Text length: {len(result.get('text', ''))}")

        return result, resource_wait_time, total_time - resource_wait_time, provider

    finally:
        # ============================================================
        # Step 3: 리소스 해제 (항상 실행)
        # ============================================================
        if resource_info:
            release_resource(resource_type, "asr", task_id)
            logger.info(f"[LiteLLM Client] Resource released: {resource_type}/asr")


def call_litellm_diarization(
    audio_file_path: Path,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    return_embeddings: bool = False,
    timeout: float = 1800.0,
    resource_timeout: float = 120.0,
) -> tuple[DiarizationAnnotationWrapper, float, float, dict | None, Any, dict]:
    """LiteLLM Proxy를 통한 Diarization 요청.

    중앙집중 리소스 관리를 사용하여 GPU 리소스를 안전하게 획득/해제합니다.
    Diarization은 pyannote를 사용하므로 항상 GPU 리소스가 필요합니다.

    Architecture V6.1 Flow:
    1. Worker → LiteLLM /resource/acquire (GPU/diarization 리소스 획득)
    2. Worker → LiteLLM /v1/audio/transcriptions (model="pyannote")
    3. Worker → LiteLLM /resource/release (리소스 해제)

    Args:
        audio_file_path: 오디오 파일 경로
        min_speakers: 최소 화자 수
        max_speakers: 최대 화자 수
        return_embeddings: 임베딩 반환 여부 (미지원, 호환성 유지)
        timeout: Diarization 요청 타임아웃 (초)
        resource_timeout: 리소스 획득 대기 시간 (초)

    Returns:
        (diarization, load_time, inference_time, embeddings_dict, pipeline, params)
    """
    start_time = time.time()
    task_id = f"diarization-{uuid.uuid4().hex[:8]}"

    # Diarization은 항상 GPU 사용 (pyannote)
    resource_type = "gpu"

    logger.info(f"[LiteLLM Client] Requesting diarization: min_speakers={min_speakers}, max_speakers={max_speakers}")

    # ============================================================
    # Step 1: 리소스 획득 (중앙집중 Gate Semaphore)
    # ============================================================
    resource_info = None
    try:
        resource_info = acquire_resource(
            resource_type=resource_type,
            task_type="diarization",
            task_id=task_id,
            accuracy_mode="speed",  # diarization은 accuracy_mode 무관
            timeout=resource_timeout,
        )
        resource_wait_time = resource_info.wait_time
        logger.info(f"[LiteLLM Client] Diarization resource acquired: provider={resource_info.provider}, wait={resource_wait_time:.2f}s")
    except ResourceAcquisitionError as e:
        logger.error(f"[LiteLLM Client] Failed to acquire diarization resource: {e}")
        raise RuntimeError(f"Diarization resource unavailable: {e}")

    # ============================================================
    # Step 2: Diarization API 호출
    # ============================================================
    try:
        url = f"{LITELLM_BASE_URL}/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {LITELLM_API_KEY}"}

        with open(audio_file_path, "rb") as f:
            files = {"file": (audio_file_path.name, f, "audio/wav")}
            data = {
                "model": "pyannote",  # LiteLLM이 이 모델명으로 라우팅
            }
            if min_speakers is not None:
                data["min_speakers"] = str(min_speakers)
            if max_speakers is not None:
                data["max_speakers"] = str(max_speakers)

            try:
                with httpx.Client(timeout=timeout, headers=headers) as client:
                    logger.info(f"[LiteLLM Client] Sending diarization request to {url}")
                    response = client.post(url, files=files, data=data)
                    response.raise_for_status()
                    result = response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"[LiteLLM Client] Diarization HTTP Error: {e.response.status_code} - {e.response.text}")
                raise
            except httpx.TimeoutException:
                logger.error(f"[LiteLLM Client] Diarization timed out after {timeout}s")
                raise
            except Exception as e:
                logger.error(f"[LiteLLM Client] Diarization request failed: {e}")
                raise

        total_time = time.time() - start_time

        # 결과 파싱
        segments = result.get("segments", [])
        num_speakers = result.get("num_speakers", 0)

        logger.info(f"[LiteLLM Client] Diarization completed in {total_time:.2f}s")
        logger.info(f"[LiteLLM Client] Speakers: {num_speakers}, Segments: {len(segments)}")

        # DiarizationAnnotationWrapper로 변환 (기존 API 호환)
        diarization = DiarizationAnnotationWrapper(segments)

        # 파라미터 정보 저장
        params = {
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
            "num_speakers_detected": num_speakers,
        }

        return diarization, resource_wait_time, total_time - resource_wait_time, None, None, params

    finally:
        # ============================================================
        # Step 3: 리소스 해제 (항상 실행)
        # ============================================================
        if resource_info:
            release_resource(resource_type, "diarization", task_id)
            logger.info(f"[LiteLLM Client] Diarization resource released: {resource_type}/diarization")
