"""Audio Gateway API 클라이언트.

Worker에서 Audio Gateway (ASR/Diarization) 서버를 직접 호출하는 클라이언트.
아키텍처 V5 원칙: Worker는 직접 추론하지 않고 API 호출만 담당.

참고: LiteLLM의 /v1/audio/transcriptions 엔드포인트는 custom provider를 지원하지 않아
Audio Gateway를 직접 호출합니다. Provider Manager가 On-Demand 서버 관리를 담당.
"""
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
import librosa
import numpy as np
import redis
import soundfile as sf

from worker.logging_config import logger
from .vad_utils import get_speech_timestamps_energy, merge_speech_segments, extract_audio_chunk


class ASRProvider(Enum):
    """ASR Provider 타입."""
    FLM = "flm"                      # NPU, 세그먼트 없음 → VAD 청킹 필요
    WHISPER_CPP = "whisper-cpp"      # GPU, 세그먼트 있음
    INSANELY_FAST = "insanely-fast"  # GPU, 세그먼트 있음


# Audio Gateway 서버 URL (Host 머신에서 실행)
WHISPER_CPP_URL = os.getenv("WHISPER_CPP_URL", "http://host.docker.internal:8001")
INSANELY_FAST_URL = os.getenv("INSANELY_FAST_URL", "http://host.docker.internal:8002")
DIARIZATION_URL = os.getenv("DIARIZATION_URL", "http://host.docker.internal:8003")
FLM_AUDIO_URL = os.getenv("FLM_AUDIO_URL", "http://host.docker.internal:11434")

# Prometheus for dynamic routing
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://asr-prometheus:9090")

# 장치 ID (Prometheus 메트릭용)
GPU_DEVICE_ID = os.getenv("GPU_DEVICE_ID", "0x000142B6")
NPU_DEVICE_ID = os.getenv("NPU_DEVICE_ID", "0x000160E6")

# 사용률 임계값 (70% 이상이면 "바쁨")
BUSY_THRESHOLD = float(os.getenv("BUSY_THRESHOLD", "70"))

# Redis for Provider Manager communication
REDIS_URL = os.getenv("REDIS_URL", "redis://asr-redis:6379/0")

# Redis client (lazy init)
_redis_client = None


def _get_redis_client():
    """Redis 클라이언트 반환 (lazy initialization)."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning(f"[Audio Client] Redis connection failed: {e}")
    return _redis_client


def _send_provider_signal(provider: str, action: str = "start"):
    """Provider Manager에게 제어 신호 전송."""
    client = _get_redis_client()
    if client:
        try:
            message = {"action": action, "provider": provider}
            client.publish("provider.control", json.dumps(message))
            logger.debug(f"[Audio Client] Sent signal: {provider} -> {action}")
        except Exception as e:
            logger.warning(f"[Audio Client] Failed to send signal: {e}")


# GPU 세마포어 키
GPU_SEMAPHORE_KEY = "worker:gpu:active"
GPU_SEMAPHORE_TTL = int(os.getenv("GPU_SEMAPHORE_TTL", "300"))  # 5분 TTL (crash 대비)


def _set_gpu_semaphore(reason: str = "unknown"):
    """GPU 세마포어 설정 (GPU 사용 시작)."""
    client = _get_redis_client()
    if client:
        try:
            client.setex(GPU_SEMAPHORE_KEY, GPU_SEMAPHORE_TTL, reason)
            logger.info(f"[GPU Semaphore] SET: {reason} (TTL: {GPU_SEMAPHORE_TTL}s)")
        except Exception as e:
            logger.warning(f"[GPU Semaphore] Failed to set: {e}")


def _clear_gpu_semaphore():
    """GPU 세마포어 해제 (GPU 사용 완료)."""
    client = _get_redis_client()
    if client:
        try:
            client.delete(GPU_SEMAPHORE_KEY)
            logger.info(f"[GPU Semaphore] CLEARED")
        except Exception as e:
            logger.warning(f"[GPU Semaphore] Failed to clear: {e}")


# Touch interval for long-running operations (seconds)
TOUCH_INTERVAL = float(os.getenv("PROVIDER_TOUCH_INTERVAL", "30"))


@contextmanager
def _periodic_touch(provider: str, interval: float = TOUCH_INTERVAL):
    """
    장시간 처리 중 idle timeout 방지를 위한 주기적 touch 신호 전송.

    Context manager로 사용하여 API 호출 중 백그라운드에서 touch 신호를 전송합니다.

    Usage:
        with _periodic_touch("insanely-fast"):
            response = client.post(url, files=files, data=data)
    """
    stop_event = threading.Event()

    def touch_loop():
        while not stop_event.is_set():
            # interval마다 touch 전송
            if stop_event.wait(interval):
                break  # stop_event가 set되면 종료
            _send_provider_signal(provider, "touch")
            logger.debug(f"[Periodic Touch] Sent touch for {provider}")

    thread = threading.Thread(target=touch_loop, daemon=True)
    thread.start()

    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=1.0)


def _query_prometheus(device_id: str) -> float:
    """Prometheus에서 5초 평균 GPU/NPU 사용량 조회."""
    query = f'sum(avg_over_time(windows_gpu_engine_utilization_percentage{{exported_instance=~".*{device_id}.*engtype_Compute.*"}}[5s]))'

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                if not data["data"]["result"]:
                    return 0.0
                value = float(data["data"]["result"][0]["value"][1])
                logger.debug(f"[Audio Client] Prometheus {device_id}: {value:.1f}%")
                return value
            logger.debug(f"[Audio Client] Prometheus {device_id}: no data, returning 0")
            return 0.0
    except Exception as e:
        logger.warning(f"[Audio Client] Prometheus query failed for {device_id}: {e}")
        return 0.0


def _select_speed_provider() -> tuple[str, str, str, bool]:
    """
    Speed 모드에서 NPU(FLM) 또는 GPU(whisper.cpp) 중 하나를 동적 선택.

    Returns:
        (url, provider, provider_signal_name, use_openai_api)
    """
    # Redis 세마포어 체크 (NPU가 사용 중인 경우)
    client = _get_redis_client()
    if client:
        try:
            npu_active = client.exists("worker:npu:active")
            if npu_active:
                logger.info(f"[Audio Client] NPU Semaphore Active -> GPU (whisper.cpp)")
                return f"{WHISPER_CPP_URL}/inference", "whisper-cpp", "whisper-cpp", False
        except Exception as e:
            logger.warning(f"[Audio Client] Redis semaphore check failed: {e}")

    # Prometheus 메트릭 체크
    npu_usage = _query_prometheus(NPU_DEVICE_ID)
    gpu_usage = _query_prometheus(GPU_DEVICE_ID)

    logger.info(f"[Audio Client] Usage - GPU: {gpu_usage:.1f}%, NPU: {npu_usage:.1f}%")

    # NPU(FLM)가 덜 바쁘면 NPU 선택
    if npu_usage < BUSY_THRESHOLD:
        logger.info(f"[Audio Client] Selected: NPU (FLM) - usage {npu_usage:.1f}%")
        return f"{FLM_AUDIO_URL}/v1/audio/transcriptions", "flm", "flm", True
    # GPU(whisper.cpp)가 덜 바쁘면 GPU 선택
    elif gpu_usage < BUSY_THRESHOLD:
        logger.info(f"[Audio Client] Selected: GPU (whisper.cpp) - usage {gpu_usage:.1f}%")
        return f"{WHISPER_CPP_URL}/inference", "whisper-cpp", "whisper-cpp", False

    # 둘 다 바쁘면 NPU로 기본값 (FLM 사용)
    logger.warning(f"[Audio Client] All busy, defaulting to NPU (FLM)")
    return f"{FLM_AUDIO_URL}/v1/audio/transcriptions", "flm", "flm", True


def _call_flm_direct(
    audio_file_path: Path,
    language: str = "ko",
    timeout: float = 600.0,
    startup_timeout: float = 60.0,
) -> dict[str, Any]:
    """
    FLM에 전체 오디오를 직접 전송 (VAD 청킹 없음).

    FLM은 세그먼트를 반환하지 않으므로 전체 텍스트만 반환됩니다.
    세그먼트가 필요하면 전체 오디오 길이로 단일 세그먼트를 생성합니다.
    """
    logger.info(f"[FLM Direct] Sending full audio: {audio_file_path}")

    flm_url = f"{FLM_AUDIO_URL}/v1/audio/transcriptions"
    health_url = f"{FLM_AUDIO_URL}/v1/models"  # OpenAI 호환 API health check

    # Provider Manager에게 시작 신호 전송
    _send_provider_signal("flm", "start")

    # 서버가 준비될 때까지 대기
    logger.info(f"[FLM Direct] Waiting for server to be ready (max {startup_timeout}s)...")
    if not _wait_for_server_ready(health_url, max_wait=startup_timeout):
        logger.warning(f"[FLM Direct] Server not ready after {startup_timeout}s, proceeding anyway...")

    # 오디오 길이 확인
    waveform, sample_rate = librosa.load(str(audio_file_path), sr=16000)
    total_duration = len(waveform) / sample_rate
    logger.info(f"[FLM Direct] Audio duration: {total_duration:.2f}s")

    with httpx.Client(timeout=timeout, headers={"Authorization": "Bearer flm"}) as client:
        with open(audio_file_path, "rb") as f:
            audio_data = f.read()

        files = {"file": (audio_file_path.name, audio_data, "audio/wav")}
        data = {
            "model": "whisper-v3",
            "language": language,
        }

        response = client.post(flm_url, files=files, data=data)
        response.raise_for_status()

        result = response.json()
        text = result.get("text", "").strip()

    # 완료 신호 전송
    _send_provider_signal("flm", "touch")

    logger.info(f"[FLM Direct] Completed: {len(text)} chars")

    # 단일 세그먼트로 반환
    return {
        "text": text,
        "segments": [{
            "id": 0,
            "start": 0.0,
            "end": total_duration,
            "text": text,
        }] if text else [],
        "language": language,
    }


def _call_flm_with_vad_chunking(
    audio_file_path: Path,
    language: str = "ko",
    max_chunk_duration: float = 30.0,  # 이전 아키텍처와 동일 (30초)
    timeout: float = 300.0,
    startup_timeout: float = 60.0,
) -> dict[str, Any]:
    """
    FLM용 VAD 기반 청킹 전사.

    FLM은 세그먼트(타임스탬프)를 반환하지 않으므로,
    VAD로 음성 구간을 감지하고 각 청크별로 전사하여 타임스탬프를 생성합니다.

    Flow:
    1. 오디오 로드
    2. VAD로 음성 구간 감지
    3. 30초 이하 청크로 분할
    4. 각 청크 → FLM API 호출 → 텍스트 획득
    5. 세그먼트 생성 (청크 start/end 시간 사용)

    Args:
        audio_file_path: 오디오 파일 경로
        language: 언어 코드
        max_chunk_duration: 최대 청크 길이 (초)
        timeout: 각 청크 요청 타임아웃 (초)
        startup_timeout: 서버 시작 대기 최대 시간 (초)

    Returns:
        {"text": str, "segments": list, "language": str}
    """
    logger.info(f"[FLM VAD] Starting VAD-based chunked transcription: {audio_file_path}")

    flm_url = f"{FLM_AUDIO_URL}/v1/audio/transcriptions"
    health_url = f"{FLM_AUDIO_URL}/v1/models"  # OpenAI 호환 API health check

    # Provider Manager에게 시작 신호 전송
    _send_provider_signal("flm", "start")

    # 서버가 준비될 때까지 대기
    logger.info(f"[FLM VAD] Waiting for server to be ready (max {startup_timeout}s)...")
    if not _wait_for_server_ready(health_url, max_wait=startup_timeout):
        logger.warning(f"[FLM VAD] Server not ready after {startup_timeout}s, proceeding anyway...")

    # 오디오 로드
    waveform, sample_rate = librosa.load(str(audio_file_path), sr=16000)
    total_duration = len(waveform) / sample_rate
    logger.info(f"[FLM VAD] Audio duration: {total_duration:.2f}s")

    # VAD로 음성 구간 감지
    speech_segments = get_speech_timestamps_energy(waveform, sample_rate)

    # 구간 병합 및 분할 (30초 이하)
    chunks = merge_speech_segments(speech_segments, max_duration=max_chunk_duration)
    logger.info(f"[FLM VAD] Processing {len(chunks)} chunks")

    # 각 청크별 전사
    all_segments = []
    total_text = []

    # 이전 아키텍처와 동일한 인증 헤더 사용
    with httpx.Client(timeout=timeout, headers={"Authorization": "Bearer flm"}) as client:
        for i, chunk in enumerate(chunks):
            chunk_start = chunk["start"]
            chunk_end = chunk["end"]
            chunk_duration = chunk_end - chunk_start

            # 너무 짧은 청크는 스킵
            if chunk_duration < 0.5:
                continue

            # 오디오 청크 추출
            chunk_waveform = extract_audio_chunk(waveform, sample_rate, chunk_start, chunk_end)

            # 임시 파일로 저장
            chunk_path = Path(tempfile.gettempdir()) / f"flm_vad_chunk_{i}.wav"
            sf.write(str(chunk_path), chunk_waveform, sample_rate)

            try:
                # FLM API 호출 (이전 아키텍처와 동일한 방식)
                with open(chunk_path, "rb") as f:
                    audio_data = f.read()  # 파일을 메모리로 읽음

                files = {"file": ("audio.wav", audio_data, "audio/wav")}
                data = {
                    "model": "whisper-v3",  # 이전 아키텍처와 동일한 모델명
                    "language": language,
                }

                response = client.post(flm_url, files=files, data=data)

                if response.status_code == 200:
                    result = response.json()
                    text = result.get("text", "").strip()

                    if text:
                        all_segments.append({
                            "id": len(all_segments),
                            "start": chunk_start,
                            "end": chunk_end,
                            "text": text,
                        })
                        total_text.append(text)

                        # 모든 청크 결과 로깅 (디버깅용)
                        logger.info(
                            f"[FLM VAD] Chunk {i+1}/{len(chunks)}: "
                            f"{chunk_start:.2f}s-{chunk_end:.2f}s -> {len(text)} chars"
                        )
                else:
                    logger.warning(f"[FLM VAD] Chunk {i} failed: {response.status_code}")

            finally:
                # 임시 파일 삭제
                if chunk_path.exists():
                    chunk_path.unlink()

    # 완료 신호 전송
    _send_provider_signal("flm", "touch")

    logger.info(f"[FLM VAD] Completed: {len(all_segments)} segments from {len(chunks)} chunks")

    return {
        "text": " ".join(total_text),
        "segments": all_segments,
        "language": language,
    }


def _call_whisper_cpp(
    audio_file_path: Path,
    language: str = "ko",
    timeout: float = 1800.0,
    max_retries: int = 10,
    startup_timeout: float = 60.0,
) -> dict[str, Any]:
    """
    whisper.cpp API 호출 (세그먼트 포함 응답).

    Args:
        audio_file_path: 오디오 파일 경로
        language: 언어 코드
        timeout: 타임아웃 (초)
        max_retries: 서버 연결 실패 시 최대 재시도 횟수
        startup_timeout: 서버 시작 대기 최대 시간 (초)

    Returns:
        {"text": str, "segments": list, "language": str}
    """
    url = f"{WHISPER_CPP_URL}/inference"
    health_url = f"{WHISPER_CPP_URL}"  # whisper.cpp 루트는 health 역할

    logger.info(f"[whisper.cpp] Calling API: {url}")

    # GPU 세마포어 설정 (다른 GPU 작업과 충돌 방지)
    _set_gpu_semaphore("whisper-cpp")

    # Provider Manager에게 시작 신호 전송
    _send_provider_signal("whisper-cpp", "start")

    # 서버가 준비될 때까지 대기 (모델 로딩 시간 고려)
    logger.info(f"[whisper.cpp] Waiting for server to be ready (max {startup_timeout}s)...")
    if not _wait_for_server_ready(health_url, max_wait=startup_timeout):
        logger.warning(f"[whisper.cpp] Server not ready after {startup_timeout}s, proceeding with retries...")

    response = None
    last_error = None

    for attempt in range(max_retries):
        try:
            with open(audio_file_path, "rb") as f:
                files = {"file": (audio_file_path.name, f, "audio/wav")}
                data = {
                    "temperature": "0.0",
                    "temperature_inc": "0.2",
                    "response_format": "verbose_json",
                    "language": language,
                }

                with httpx.Client(timeout=timeout) as client:
                    # 장시간 처리 중 idle timeout 방지를 위한 주기적 touch
                    with _periodic_touch("whisper-cpp"):
                        response = client.post(url, files=files, data=data)
                    response.raise_for_status()

            # 성공 시 루프 종료
            logger.info(f"[whisper.cpp] Connected on attempt {attempt + 1}")
            break

        except httpx.TimeoutException:
            logger.error(f"[whisper.cpp] Request timed out after {timeout}s")
            _send_provider_signal("whisper-cpp", "touch")
            _clear_gpu_semaphore()
            raise

        except httpx.HTTPStatusError as e:
            logger.error(f"[whisper.cpp] HTTP error: {e.response.status_code} - {e.response.text}")
            _send_provider_signal("whisper-cpp", "touch")
            _clear_gpu_semaphore()
            raise

        except (httpx.ConnectError, httpx.RemoteProtocolError, OSError) as e:
            last_error = e
            wait_time = min(2 ** attempt, 30)  # Exponential backoff, max 30s

            if attempt < max_retries - 1:
                logger.warning(
                    f"[whisper.cpp] Connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                logger.info(f"[whisper.cpp] Retrying in {wait_time}s...")
                # 재시도 전 start 신호 다시 전송 (서버가 종료되었을 수 있음)
                _send_provider_signal("whisper-cpp", "start")
                time.sleep(wait_time)
            else:
                logger.error(
                    f"[whisper.cpp] Connection failed after {max_retries} attempts: {e}"
                )
                _send_provider_signal("whisper-cpp", "touch")
                _clear_gpu_semaphore()
                raise

    if response is None:
        _clear_gpu_semaphore()
        raise last_error or Exception("whisper.cpp request failed")

    _send_provider_signal("whisper-cpp", "touch")
    _clear_gpu_semaphore()

    result = response.json()
    logger.info(f"[whisper.cpp] Response segments: {len(result.get('segments', []))}")

    # 세그먼트 형식 변환
    segments = []
    for i, seg in enumerate(result.get("segments", [])):
        segments.append({
            "id": seg.get("id", i),
            "start": seg.get("start", 0.0),
            "end": seg.get("end", 0.0),
            "text": seg.get("text", "").strip(),
        })

    return {
        "text": result.get("text", ""),
        "segments": segments,
        "language": result.get("detected_language", language),
    }


def _call_insanely_fast(
    audio_file_path: Path,
    language: str = "ko",
    timeout: float = 1800.0,
    max_retries: int = 10,
    startup_timeout: float = 120.0,
) -> dict[str, Any]:
    """
    insanely-fast-whisper API 호출 (세그먼트 포함 응답).

    Args:
        audio_file_path: 오디오 파일 경로
        language: 언어 코드
        timeout: 타임아웃 (초)
        max_retries: 서버 연결 실패 시 최대 재시도 횟수
        startup_timeout: 서버 시작 대기 최대 시간 (초, 모델 로딩 시간 고려)

    Returns:
        {"text": str, "segments": list, "language": str}
    """
    url = f"{INSANELY_FAST_URL}/v1/audio/transcriptions"
    health_url = f"{INSANELY_FAST_URL}/health"

    logger.info(f"[insanely-fast] Calling API: {url}")

    # GPU 세마포어 설정 (다른 GPU 작업과 충돌 방지)
    _set_gpu_semaphore("insanely-fast")

    # Provider Manager에게 시작 신호 전송
    _send_provider_signal("insanely-fast", "start")

    # 서버가 준비될 때까지 대기 (Whisper 모델 로딩 시간 ~60초 고려)
    logger.info(f"[insanely-fast] Waiting for server to be ready (max {startup_timeout}s)...")
    if not _wait_for_server_ready(health_url, max_wait=startup_timeout):
        logger.warning(f"[insanely-fast] Server not ready after {startup_timeout}s, proceeding with retries...")

    response = None
    last_error = None

    for attempt in range(max_retries):
        try:
            with open(audio_file_path, "rb") as f:
                files = {"file": (audio_file_path.name, f, "audio/wav")}
                data = {
                    "model": "whisper-large-v3",
                    "language": language,
                    "response_format": "verbose_json",
                }

                with httpx.Client(timeout=timeout) as client:
                    # 장시간 처리 중 idle timeout 방지를 위한 주기적 touch
                    with _periodic_touch("insanely-fast"):
                        response = client.post(url, files=files, data=data)
                    response.raise_for_status()

            # 성공 시 루프 종료
            logger.info(f"[insanely-fast] Connected on attempt {attempt + 1}")
            break

        except httpx.TimeoutException:
            logger.error(f"[insanely-fast] Request timed out after {timeout}s")
            _send_provider_signal("insanely-fast", "touch")
            _clear_gpu_semaphore()
            raise

        except httpx.HTTPStatusError as e:
            logger.error(f"[insanely-fast] HTTP error: {e.response.status_code} - {e.response.text}")
            _send_provider_signal("insanely-fast", "touch")
            _clear_gpu_semaphore()
            raise

        except (httpx.ConnectError, httpx.RemoteProtocolError, OSError) as e:
            last_error = e
            wait_time = min(2 ** attempt, 30)  # Exponential backoff, max 30s

            if attempt < max_retries - 1:
                logger.warning(
                    f"[insanely-fast] Connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                logger.info(f"[insanely-fast] Retrying in {wait_time}s...")
                # 재시도 전 start 신호 다시 전송 (서버가 종료되었을 수 있음)
                _send_provider_signal("insanely-fast", "start")
                time.sleep(wait_time)
            else:
                logger.error(
                    f"[insanely-fast] Connection failed after {max_retries} attempts: {e}"
                )
                _send_provider_signal("insanely-fast", "touch")
                _clear_gpu_semaphore()
                raise

    if response is None:
        _clear_gpu_semaphore()
        raise last_error or Exception("insanely-fast request failed")

    _send_provider_signal("insanely-fast", "touch")
    _clear_gpu_semaphore()

    result = response.json()
    logger.info(f"[insanely-fast] Response segments: {len(result.get('segments', []))}")

    # 세그먼트 형식 변환
    segments = []
    for i, seg in enumerate(result.get("segments", [])):
        segments.append({
            "id": seg.get("id", i),
            "start": seg.get("start", 0.0),
            "end": seg.get("end", 0.0),
            "text": seg.get("text", "").strip(),
        })

    return {
        "text": result.get("text", ""),
        "segments": segments,
        "language": result.get("language", language),
    }


def call_transcription_api(
    audio_file_path: Path,
    accuracy_mode: str = "speed",
    language: str = "ko",
    timeout: float = 1800.0,
) -> tuple[dict[str, Any], float, float, ASRProvider]:
    """
    Audio Gateway를 직접 호출하여 전사를 수행합니다.

    Architecture V5: Worker는 API 호출만 담당.
    Provider Manager가 On-Demand 서버 관리를 담당합니다.

    각 Provider별 처리:
    - FLM (NPU): VAD 기반 청킹 → 청크별 전사 → 세그먼트 생성
    - whisper.cpp (GPU): 단일 호출 → 세그먼트 포함 응답
    - insanely-fast (GPU): 단일 호출 → 세그먼트 포함 응답

    Args:
        audio_file_path: 오디오 파일 경로
        accuracy_mode: "speed" (NPU/FLM 또는 GPU/whisper.cpp 동적 선택) 또는 "accuracy" (insanely-fast-whisper)
        language: 언어 코드
        timeout: 타임아웃 (초)

    Returns:
        (전사 결과 dict, 모델 로드 시간, 전사 시간, 사용된 Provider)
        전사 결과: {"text": str, "segments": list, "language": str}
    """
    start_time = time.time()

    # accuracy_mode에 따라 Provider 선택 및 처리
    if accuracy_mode == "accuracy":
        # insanely-fast-whisper (GPU, 고정밀)
        provider = ASRProvider.INSANELY_FAST
        logger.info(f"[ASR Client] Using {provider.value} (accuracy mode)")

        try:
            result = _call_insanely_fast(audio_file_path, language, timeout)
        except Exception as e:
            logger.error(f"[ASR Client] {provider.value} failed: {e}")
            raise

    else:
        # speed 모드: whisper.cpp (GPU) 사용
        # NOTE: FLM(NPU)은 타임스탬프를 제공하지 않아 화자분리와 병합 불가
        # FLM이 타임스탬프를 지원할 때까지 whisper.cpp만 사용
        # _, provider_name, _, _ = _select_speed_provider()

        # if provider_name == "flm":
        #     # FLM (NPU) - 전체 오디오 직접 전송 (VAD 비활성화)
        #     provider = ASRProvider.FLM
        #     logger.info(f"[ASR Client] Using {provider.value} direct mode (no VAD chunking)")
        #
        #     try:
        #         result = _call_flm_direct(audio_file_path, language)
        #     except Exception as e:
        #         logger.error(f"[ASR Client] {provider.value} failed: {e}")
        #         raise
        # else:

        # whisper.cpp (GPU) - 타임스탬프 제공으로 화자분리와 병합 가능
        provider = ASRProvider.WHISPER_CPP
        logger.info(f"[ASR Client] Using {provider.value} (speed mode - FLM disabled)")

        try:
            result = _call_whisper_cpp(audio_file_path, language, timeout)
        except Exception as e:
            logger.error(f"[ASR Client] {provider.value} failed: {e}")
            raise

    total_time = time.time() - start_time
    logger.info(f"[ASR Client] Transcription completed in {total_time:.2f}s using {provider.value}")
    logger.info(f"[ASR Client] Result: {len(result.get('segments', []))} segments, {len(result.get('text', ''))} chars")

    return result, 0.0, total_time, provider


def _wait_for_server_ready(
    health_url: str,
    max_wait: float = 60.0,
    poll_interval: float = 2.0,
) -> bool:
    """서버가 준비될 때까지 health check로 대기합니다."""
    start = time.time()
    while (time.time() - start) < max_wait:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(health_url)
                if resp.status_code == 200:
                    logger.info(f"[Health Check] Server ready after {time.time() - start:.1f}s")
                    return True
        except Exception:
            pass
        time.sleep(poll_interval)
    return False


def call_diarization_api(
    audio_file_path: Path,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
    return_embeddings: bool = False,
    timeout: float = 1800.0,
    max_retries: int = 5,
    startup_timeout: float = 60.0,
) -> tuple[Any, float, float, dict | None, Any, dict]:
    """
    Diarization Server를 직접 호출하여 화자 분리를 수행합니다.

    Architecture V5: Worker는 API 호출만 담당.
    Provider Manager가 On-Demand 서버 관리를 담당합니다.

    Args:
        max_retries: 서버 연결 실패 시 최대 재시도 횟수 (기본 5회)
        startup_timeout: 서버 시작 대기 최대 시간 (초, 기본 60초 - pyannote 로딩 고려)
    """
    start_time = time.time()

    # Diarization Server 직접 호출
    url = f"{DIARIZATION_URL}/v1/audio/diarization"
    health_url = f"{DIARIZATION_URL}/health"  # OpenAI 규격 health check
    provider = "diarization-server"

    logger.info(f"[Diarization Client] Calling Diarization Server directly")
    logger.info(f"[Diarization Client] URL: {url}")

    # GPU 세마포어 설정 (다른 GPU 작업과 충돌 방지)
    _set_gpu_semaphore("diarization")

    # Provider Manager에게 시작 신호 전송
    _send_provider_signal(provider, "start")

    # /health 엔드포인트로 모델 로드 완료까지 대기 (pyannote 모델 로딩 시간 ~35초)
    logger.info(f"[Diarization Client] Waiting for server to be ready (max {startup_timeout}s)...")
    if not _wait_for_server_ready(health_url, max_wait=startup_timeout):
        logger.warning(f"[Diarization Client] Server not ready after {startup_timeout}s, proceeding anyway...")

    data = {
        "return_embeddings": str(return_embeddings).lower(),
    }
    if min_speakers is not None:
        data["min_speakers"] = str(min_speakers)
    if max_speakers is not None:
        data["max_speakers"] = str(max_speakers)

    response = None
    last_error = None

    for attempt in range(max_retries):
        try:
            with open(audio_file_path, "rb") as f:
                files = {"file": (audio_file_path.name, f, "audio/wav")}

                with httpx.Client(timeout=timeout) as client:
                    # 장시간 처리 중 idle timeout 방지를 위한 주기적 touch
                    with _periodic_touch(provider):
                        response = client.post(url, files=files, data=data)
                    response.raise_for_status()

            # 성공 시 루프 종료
            logger.info(f"[Diarization Client] Connected on attempt {attempt + 1}")
            break

        except httpx.TimeoutException:
            logger.error(f"[Diarization Client] Request timed out after {timeout}s")
            _send_provider_signal(provider, "touch")
            _clear_gpu_semaphore()
            raise

        except httpx.HTTPStatusError as e:
            logger.error(f"[Diarization Client] HTTP error: {e.response.status_code} - {e.response.text}")
            _send_provider_signal(provider, "touch")
            _clear_gpu_semaphore()
            raise

        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_error = e
            wait_time = min(2 ** attempt, 30)  # Exponential backoff, max 30s

            if attempt < max_retries - 1:
                logger.warning(
                    f"[Diarization Client] Connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                logger.info(f"[Diarization Client] Retrying in {wait_time}s...")
                # 재시도 전 start 신호 다시 전송 (서버가 종료되었을 수 있음)
                _send_provider_signal(provider, "start")
                time.sleep(wait_time)
            else:
                logger.error(
                    f"[Diarization Client] Connection failed after {max_retries} attempts: {e}"
                )
                _send_provider_signal(provider, "touch")
                _clear_gpu_semaphore()
                raise

    if response is None:
        _clear_gpu_semaphore()
        raise last_error or Exception("Diarization request failed")

    # 완료 신호 전송 (idle timeout 리셋)
    _send_provider_signal(provider, "touch")
    _clear_gpu_semaphore()

    total_time = time.time() - start_time
    result = response.json()

    # 결과 처리
    # Diarization Server는 {"model": "pyannote", "segments": [...], "metrics": {...}} 반환
    segments = result.get("segments", [])
    metrics = result.get("metrics", {})
    load_time = metrics.get("load_time", 0.0)
    process_time = metrics.get("process_time", 0.0)

    logger.info(f"[Diarization Client] Completed in {total_time:.2f}s (Server Process: {process_time:.2f}s)")
    logger.info(f"[Diarization Client] Received {len(segments)} segments")

    # Wrapper 변환
    diarization_result = DiarizationAnnotationWrapper(segments)

    embeddings_dict = None
    used_params = {
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
    }

    return diarization_result, load_time, process_time, embeddings_dict, None, used_params


class DiarizationAnnotationWrapper:
    """
    LiteLLM/Diarization Server 응답을 pyannote Annotation 인터페이스로 래핑.

    기존 merge_segments_with_speakers() 함수와 호환성 유지를 위해
    itertracks() 및 labels() 메서드를 제공.
    """

    def __init__(self, segments: list[dict[str, Any]]):
        """
        Args:
            segments: [{"start": float, "end": float, "speaker": str, "duration": float}, ...]
        """
        self._segments = segments
        self._labels = set()
        for seg in segments:
            self._labels.add(seg.get("speaker", "UNKNOWN"))

    def itertracks(self, yield_label: bool = False):
        """
        pyannote Annotation.itertracks() 호환 메서드.

        Args:
            yield_label: True이면 (Turn, _, speaker) 형식으로 반환

        Yields:
            (Turn, _, speaker) 튜플
        """
        for seg in self._segments:
            turn = _Turn(seg["start"], seg["end"])
            if yield_label:
                yield turn, None, seg.get("speaker", "UNKNOWN")
            else:
                yield turn, None

    def labels(self) -> set[str]:
        """화자 라벨 집합 반환."""
        return self._labels


class _Turn:
    """pyannote Turn 호환 클래스."""

    def __init__(self, start: float, end: float):
        self.start = start
        self.end = end
