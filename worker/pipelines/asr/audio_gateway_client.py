"""Audio Gateway API 클라이언트.

Worker에서 Audio Gateway 및 LiteLLM을 통해 ASR/Diarization을 호출하는 클라이언트.
아키텍처 V4 원칙: Worker는 직접 추론하지 않고 API만 호출.
"""
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from worker.logging_config import logger


def get_audio_gateway_url() -> str:
    """Audio Gateway URL 반환."""
    return os.getenv("AUDIO_GATEWAY_URL", "http://localhost:8001")


def get_litellm_url() -> str:
    """LiteLLM Proxy URL 반환."""
    return os.getenv("LITELLM_BASE_URL", "http://localhost:4000")


def get_litellm_api_key() -> str:
    """LiteLLM API Key 반환."""
    return os.getenv("LITELLM_API_KEY", "sk-litellm-master")


def call_transcription_api(
    audio_file_path: Path,
    accuracy_mode: str = "speed",
    language: str = "ko",
    timeout: float = 1800.0,
) -> tuple[dict[str, Any], float, float]:
    """
    LiteLLM을 통한 전사 API 호출.

    LiteLLM이 OpenAI 호환 모드로 Audio Gateway에 라우팅:
    - "whisper-large-v3": insanely-fast-whisper-rocm (정확도 모드)
    - "whisper-turbo": whisper.cpp turbo (속도 모드)

    Args:
        audio_file_path: 오디오 파일 경로
        accuracy_mode: "speed" (whisper.cpp turbo) 또는 "accuracy" (insanely-fast-whisper)
        language: 언어 코드
        timeout: 타임아웃 (초)

    Returns:
        (전사 결과 dict, 모델 로드 시간, 전사 시간)
        전사 결과: {"text": str, "segments": list, "language": str}
    """
    start_time = time.time()

    # LiteLLM 프록시를 통해 호출 (OpenAI 호환 모드로 Audio Gateway에 라우팅)
    litellm_url = get_litellm_url()
    url = f"{litellm_url}/v1/audio/transcriptions"
    api_key = get_litellm_api_key()

    # 모델명 결정: accuracy 모드에서는 whisper-large-v3, speed 모드에서는 whisper-turbo
    model = "whisper-large-v3" if accuracy_mode == "accuracy" else "whisper-turbo"

    logger.info(f"[ASR Client] Calling LiteLLM transcription API")
    logger.info(f"[ASR Client] URL: {url}, model={model}, accuracy_mode={accuracy_mode}")

    with open(audio_file_path, "rb") as f:
        files = {"file": (audio_file_path.name, f, "audio/wav")}
        data = {
            "model": model,
            "language": language,
            "response_format": "verbose_json",
        }

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    url,
                    files=files,
                    data=data,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.error(f"[ASR Client] Request timed out after {timeout}s")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"[ASR Client] HTTP error: {e.response.status_code} - {e.response.text}")
            raise

    total_time = time.time() - start_time

    result = response.json()
    logger.info(f"[ASR Client] Transcription completed in {total_time:.2f}s")

    # LiteLLM은 OpenAI 호환 응답을 그대로 전달
    transcription_result = {
        "text": result.get("text", ""),
        "segments": result.get("segments", []),
        "language": result.get("language", language),
    }

    # 세그먼트 형식 변환 (필요한 경우)
    converted_segments = []
    for i, seg in enumerate(transcription_result.get("segments", [])):
        converted_segments.append({
            "id": seg.get("id", i),
            "start": seg.get("start", 0.0),
            "end": seg.get("end", 0.0),
            "text": seg.get("text", "").strip(),
        })
    transcription_result["segments"] = converted_segments

    # 모델 로드 시간은 서버 측에서 처리되므로 0으로 반환
    return transcription_result, 0.0, total_time


def call_diarization_api(
    audio_file_path: Path,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
    return_embeddings: bool = False,
    timeout: float = 1800.0,
) -> tuple[Any, float, float, dict | None, Any, dict]:
    """
    Audio Gateway 화자분리 API 직접 호출.

    화자분리는 항상 GPU에서 실행되므로 LiteLLM 라우팅 불필요.
    Audio Gateway를 직접 호출.

    Args:
        audio_file_path: 오디오 파일 경로
        min_speakers: 최소 화자 수
        max_speakers: 최대 화자 수
        return_embeddings: 임베딩 반환 여부
        timeout: 타임아웃 (초)

    Returns:
        run_diarization()과 동일한 형식:
        (diarization_result, load_time, process_time, embeddings_dict, pipeline, used_params)
    """
    start_time = time.time()

    gateway_url = get_audio_gateway_url()
    url = f"{gateway_url}/v1/audio/diarization"

    logger.info(f"[Diarization Client] Calling Audio Gateway diarization API")
    logger.info(f"[Diarization Client] URL: {url}, min_speakers={min_speakers}, max_speakers={max_speakers}")

    with open(audio_file_path, "rb") as f:
        files = {"file": (audio_file_path.name, f, "audio/wav")}
        data = {
            "return_embeddings": str(return_embeddings).lower(),
        }
        if min_speakers is not None:
            data["min_speakers"] = str(min_speakers)
        if max_speakers is not None:
            data["max_speakers"] = str(max_speakers)

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, files=files, data=data)
                response.raise_for_status()
        except httpx.TimeoutException:
            logger.error(f"[Diarization Client] Request timed out after {timeout}s")
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"[Diarization Client] HTTP error: {e.response.status_code} - {e.response.text}")
            raise

    total_time = time.time() - start_time

    result = response.json()
    logger.info(f"[Diarization Client] Diarization completed in {total_time:.2f}s")
    logger.info(f"[Diarization Client] Found {result.get('num_speakers', 0)} speakers")

    # API 응답을 pyannote Annotation과 호환되는 형식으로 변환
    # DiarizationAnnotationWrapper를 사용하여 기존 코드와 호환성 유지
    diarization_result = DiarizationAnnotationWrapper(result.get("segments", []))

    # 임베딩 딕셔너리
    embeddings_dict = result.get("embeddings")

    # 하이퍼파라미터 (API 응답에 포함된 경우)
    used_params = {
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
        "num_speakers": result.get("num_speakers", 0),
    }

    # pipeline은 API 호출에서는 None
    return diarization_result, 0.0, total_time, embeddings_dict, None, used_params


class DiarizationAnnotationWrapper:
    """
    Audio Gateway API 응답을 pyannote Annotation 인터페이스로 래핑.

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


def call_transcription_api_direct(
    audio_file_path: Path,
    language: str = "ko",
    timeout: float = 1800.0,
) -> tuple[dict[str, Any], float, float]:
    """
    Audio Gateway 전사 API 직접 호출 (LiteLLM 우회).

    LiteLLM 프록시를 거치지 않고 Audio Gateway를 직접 호출.
    테스트나 디버깅 용도로 사용.

    Args:
        audio_file_path: 오디오 파일 경로
        language: 언어 코드
        timeout: 타임아웃 (초)

    Returns:
        (전사 결과 dict, 모델 로드 시간, 전사 시간)
    """
    start_time = time.time()

    gateway_url = get_audio_gateway_url()
    url = f"{gateway_url}/v1/audio/transcriptions"

    logger.info(f"[ASR Client] Direct call to Audio Gateway: {url}")

    with open(audio_file_path, "rb") as f:
        files = {"file": (audio_file_path.name, f, "audio/wav")}
        data = {
            "model": "whisper-large-v3-turbo",
            "language": language,
            "response_format": "verbose_json",
        }

        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, files=files, data=data)
            response.raise_for_status()

    total_time = time.time() - start_time

    result = response.json()

    transcription_result = {
        "text": result.get("text", ""),
        "segments": result.get("segments", []),
        "language": result.get("language", language),
    }

    return transcription_result, 0.0, total_time
