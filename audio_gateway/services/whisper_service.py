"""Whisper Service - insanely-fast-whisper-rocm 기반 ASR 서비스.

Architecture V4: AMD ROCm 환경에서 최적화된 Whisper ASR.
insanely-fast-whisper-rocm 라이브러리를 사용하여 GPU 가속 전사 수행.
"""
import logging
import asyncio
import time
from pathlib import Path
from typing import Optional

import torch

from ..config import get_settings
from ..models.schemas import TranscriptionResponse, TranscriptionSegment

logger = logging.getLogger(__name__)


class WhisperService:
    """insanely-fast-whisper-rocm 기반 ASR 서비스."""

    def __init__(self):
        self._backend = None
        self._settings = get_settings()
        self._device = None

    def _load_model(self):
        """모델 로드 (lazy loading)."""
        if self._backend is not None:
            return

        from insanely_fast_whisper_rocm.core.asr_backend import (
            HuggingFaceBackend,
            HuggingFaceBackendConfig,
        )

        logger.info(f"[WhisperService] Loading insanely-fast-whisper-rocm model: {self._settings.whisper_model}")
        start_time = time.time()

        # 디바이스 설정
        device_idx = int(self._settings.whisper_device)
        self._device = f"cuda:{device_idx}" if torch.cuda.is_available() else "cpu"
        dtype = "float16" if torch.cuda.is_available() else "float32"

        logger.info(f"[WhisperService] Device: {self._device}, dtype: {dtype}")

        # HuggingFace Backend 설정
        config = HuggingFaceBackendConfig(
            model_name=self._settings.whisper_model,
            device=self._device,
            dtype=dtype,
            batch_size=self._settings.whisper_batch_size,
            chunk_length=30,  # 30초 청크
            progress_group_size=10,
        )

        # Backend 생성
        self._backend = HuggingFaceBackend(config)

        load_time = time.time() - start_time
        logger.info(f"[WhisperService] Model loaded on {self._device} ({load_time:.2f}s)")

    async def transcribe(
        self,
        audio_path: Path,
        language: Optional[str] = None,
        response_format: str = "verbose_json",
    ) -> TranscriptionResponse:
        """
        오디오 파일 전사.

        Args:
            audio_path: 오디오 파일 경로
            language: 언어 코드 (None이면 자동 감지)
            response_format: 응답 형식

        Returns:
            TranscriptionResponse
        """
        # 동기 작업을 별도 스레드에서 실행
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self._transcribe_sync,
            audio_path,
            language,
        )
        return result

    def _transcribe_sync(
        self,
        audio_path: Path,
        language: Optional[str],
    ) -> TranscriptionResponse:
        """동기 전사 실행."""
        self._load_model()

        logger.info(f"[WhisperService] Transcribing: {audio_path}")
        start_time = time.time()

        # 전사 실행
        result = self._backend.process_audio(
            audio_file_path=str(audio_path),
            language=language,
            task="transcribe",
            return_timestamps_value="word",  # 단어별 타임스탬프
        )

        transcribe_time = time.time() - start_time
        logger.info(f"[WhisperService] Transcription completed in {transcribe_time:.2f}s")

        # 세그먼트 변환
        segments = []
        chunks = result.get("chunks", [])

        for i, chunk in enumerate(chunks):
            timestamp = chunk.get("timestamp", (0, 0))
            start = timestamp[0] if timestamp[0] is not None else 0.0
            end = timestamp[1] if timestamp[1] is not None else 0.0

            segments.append(TranscriptionSegment(
                id=i,
                start=start,
                end=end,
                text=chunk.get("text", "").strip(),
            ))

        # 오디오 길이 계산
        duration = None
        if segments:
            duration = max(seg.end for seg in segments)

        # 언어 감지 결과
        detected_language = language or result.get("language", "ko")

        return TranscriptionResponse(
            text=result.get("text", "").strip(),
            language=detected_language,
            duration=duration,
            segments=segments,
        )

    def unload_model(self):
        """모델 언로드 및 GPU 메모리 해제."""
        if self._backend is not None:
            # Backend cleanup - release() 또는 close() 호출
            try:
                if hasattr(self._backend, 'release'):
                    self._backend.release()
                elif hasattr(self._backend, 'close'):
                    self._backend.close()
            except Exception as e:
                logger.warning(f"[WhisperService] Backend cleanup warning: {e}")
            del self._backend
            self._backend = None

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("[WhisperService] Model unloaded, GPU memory released")


# 싱글톤 인스턴스
_whisper_service: Optional[WhisperService] = None


def get_whisper_service() -> WhisperService:
    """싱글톤 WhisperService 인스턴스."""
    global _whisper_service
    if _whisper_service is None:
        _whisper_service = WhisperService()
    return _whisper_service
