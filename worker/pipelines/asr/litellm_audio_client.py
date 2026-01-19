"""LiteLLM Proxy를 통한 ASR/Diarization 요청 클라이언트.

Architecture V6.6 Hybrid:
- ASR/Diarization: Worker → Redis Stream → Provider Manager (Host) → GPU
  (LiteLLM 우회 - LiteLLM transcription이 custom provider 미지원)
- LLM: Worker → LiteLLM (HTTP) → Redis Stream → Provider Manager → GPU
  (LiteLLM 경유 - custom provider 지원)
"""
import os
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any

# infra/litellm/gpu_stream_client.py 임포트를 위한 경로 추가
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "infra" / "litellm"))

from gpu_stream_client import GPUStreamClient, get_gpu_stream_client

from worker.logging_config import logger
from worker.telemetry import get_trace_id


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
) -> tuple[dict[str, Any], float, float, ASRProvider]:
    """Redis Stream을 통한 ASR 요청.

    Architecture V6.6 Hybrid: LiteLLM 우회, Redis Stream 직접 사용
    - LiteLLM transcription은 custom provider 미지원
    - Worker → Redis Stream → Provider Manager (Host) → GPU Server

    Args:
        audio_file_path: 오디오 파일 경로
        accuracy_mode: "speed" (whisper-turbo) 또는 "accuracy" (whisper-large-v3)
        language: 언어 코드
        timeout: ASR 요청 타임아웃 (초)
        resource_timeout: (미사용, 호환성 유지)
        on_resource_acquired: ASR 요청 직전 호출되는 콜백 (상태 업데이트용)

    Returns:
        (전사 결과, 대기 시간, 전사 시간, 사용된 Provider)
    """
    start_time = time.time()

    # accuracy_mode에 따라 모델 선택
    if accuracy_mode == "accuracy":
        model = "whisper-large-v3"  # → insanely-fast (GPU Accuracy)
    else:
        model = "whisper-turbo"     # → whisper.cpp (GPU Speed)

    # OpenTelemetry trace 확인
    current_trace_id = get_trace_id()
    logger.info(f"[GPUStream] Requesting transcription: model={model}, accuracy_mode={accuracy_mode}, trace_id={current_trace_id}")

    # V7.2: ASR 요청 직전에 "started" 콜백 호출 (상태를 PROCESSING으로 업데이트)
    if on_resource_acquired:
        logger.info("[GPUStream] Calling on_resource_acquired callback (ASR started)")
        on_resource_acquired()

    # Redis Stream 클라이언트 사용
    gpu_client = get_gpu_stream_client()

    try:
        result = gpu_client.request_transcription(
            audio_file_path=audio_file_path,
            model=model,
            language=language,
            timeout=timeout,
        )
    except TimeoutError:
        logger.error(f"[GPUStream] Transcription timed out after {timeout}s")
        raise
    except Exception as e:
        logger.error(f"[GPUStream] Transcription failed: {e}")
        raise

    total_time = time.time() - start_time

    # 사용된 Provider 파싱
    used_model = result.get("model", model)
    if "insanely-fast" in used_model or "whisper-large-v3" in used_model:
        provider = ASRProvider.INSANELY_FAST
    elif "whisper-turbo" in used_model or "whisper-cpp" in used_model:
        provider = ASRProvider.WHISPER_CPP
    elif "flm" in used_model:
        provider = ASRProvider.FLM
    else:
        provider = ASRProvider.INSANELY_FAST if accuracy_mode == "accuracy" else ASRProvider.WHISPER_CPP

    logger.info(f"[GPUStream] Transcription completed in {total_time:.2f}s")
    logger.info(f"[GPUStream] Provider used: {provider.value}")
    logger.info(f"[GPUStream] Segments: {len(result.get('segments', []))}, Text length: {len(result.get('text', ''))}")

    # 호환성: (결과, 대기시간, 처리시간, Provider)
    return result, 0.0, total_time, provider


def call_litellm_diarization(
    audio_file_path: Path,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    return_embeddings: bool = False,
    timeout: float = 1800.0,
    resource_timeout: float = 120.0,
) -> tuple[DiarizationAnnotationWrapper, float, float, dict | None, Any, dict]:
    """Redis Stream을 통한 Diarization 요청.

    Architecture V6.6 Hybrid: LiteLLM 우회, Redis Stream 직접 사용
    - LiteLLM transcription은 custom provider 미지원
    - Worker → Redis Stream → Provider Manager (Host) → GPU Server (pyannote)

    Args:
        audio_file_path: 오디오 파일 경로
        min_speakers: 최소 화자 수
        max_speakers: 최대 화자 수
        return_embeddings: 임베딩 반환 여부 (미지원, 호환성 유지)
        timeout: Diarization 요청 타임아웃 (초)
        resource_timeout: (미사용, 호환성 유지)

    Returns:
        (diarization, load_time, inference_time, embeddings_dict, pipeline, params)
    """
    start_time = time.time()

    # OpenTelemetry trace 확인
    current_trace_id = get_trace_id()
    logger.info(f"[GPUStream] Requesting diarization: min_speakers={min_speakers}, max_speakers={max_speakers}, trace_id={current_trace_id}")

    # Redis Stream 클라이언트 사용
    gpu_client = get_gpu_stream_client()

    try:
        result = gpu_client.request_diarization(
            audio_file_path=audio_file_path,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            timeout=timeout,
        )
    except TimeoutError:
        logger.error(f"[GPUStream] Diarization timed out after {timeout}s")
        raise
    except Exception as e:
        logger.error(f"[GPUStream] Diarization request failed: {e}")
        raise

    total_time = time.time() - start_time

    # 결과 파싱
    segments = result.get("segments", [])
    num_speakers = result.get("num_speakers", 0)

    logger.info(f"[GPUStream] Diarization completed in {total_time:.2f}s")
    logger.info(f"[GPUStream] Speakers: {num_speakers}, Segments: {len(segments)}")

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
