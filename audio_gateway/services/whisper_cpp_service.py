"""Whisper.cpp Service - whisper.cpp 기반 ASR 서비스 (turbo 모델).

Architecture V4: speed 모드 폴백용 whisper.cpp turbo.
Vulkan GPU 가속을 사용하여 빠른 전사 수행.
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import librosa
import soundfile as sf

from ..config import get_settings
from ..models.schemas import TranscriptionResponse, TranscriptionSegment

logger = logging.getLogger(__name__)

# whisper.cpp 경로 설정
WHISPER_CLI_PATH = os.getenv(
    "WHISPER_CLI_PATH",
    "C:/whisper-cpp/build/bin/Release/whisper-cli.exe"
)
WHISPER_MODELS_DIR = os.getenv(
    "WHISPER_MODELS_DIR",
    "C:/whisper-cpp/models"
)


def _parse_whispercpp_json(json_data: dict) -> dict:
    """whisper.cpp JSON 출력을 OpenAI Whisper 형식으로 변환."""
    result = {
        "text": "",
        "language": json_data.get("result", {}).get("language", "ko"),
        "segments": []
    }

    transcription = json_data.get("transcription", [])
    all_texts = []

    for i, seg in enumerate(transcription):
        from_ts = seg.get("timestamps", {}).get("from", "00:00:00,000")
        to_ts = seg.get("timestamps", {}).get("to", "00:00:00,000")

        def parse_timestamp(ts_str: str) -> float:
            """00:00:06,000 형식을 초 단위로 변환."""
            parts = ts_str.split(",")
            time_part = parts[0]
            h, m, s = map(int, time_part.split(":"))
            seconds = h * 3600 + m * 60 + s
            if len(parts) > 1:
                milliseconds = int(parts[1])
                seconds += milliseconds / 1000.0
            return seconds

        start = parse_timestamp(from_ts)
        end = parse_timestamp(to_ts)
        text = seg.get("text", "").strip()

        all_texts.append(text)

        result["segments"].append({
            "id": i,
            "start": start,
            "end": end,
            "text": text,
        })

    result["text"] = " ".join(all_texts)
    return result


class WhisperCppService:
    """whisper.cpp 기반 ASR 서비스 (turbo 모델 전용)."""

    def __init__(self):
        self._settings = get_settings()
        self._verify_installation()

    def _verify_installation(self):
        """whisper.cpp 설치 확인."""
        if not os.path.exists(WHISPER_CLI_PATH):
            logger.warning(f"[WhisperCppService] whisper-cli.exe not found: {WHISPER_CLI_PATH}")
        else:
            logger.info(f"[WhisperCppService] whisper-cli.exe found: {WHISPER_CLI_PATH}")

    def _get_model_path(self, model_size: str = "turbo") -> str:
        """모델 경로 반환."""
        model_mapping = {
            "tiny": "ggml-tiny.bin",
            "base": "ggml-base.bin",
            "small": "ggml-small.bin",
            "medium": "ggml-medium.bin",
            "large": "ggml-large-v3.bin",
            "large-v3": "ggml-large-v3.bin",
            "turbo": "ggml-large-v3-turbo.bin",
            "large-v3-turbo": "ggml-large-v3-turbo.bin",
        }

        filename = model_mapping.get(model_size, "ggml-large-v3-turbo.bin")
        model_path = os.path.join(WHISPER_MODELS_DIR, filename)

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found: {model_path}")

        return model_path

    async def transcribe(
        self,
        audio_path: Path,
        language: Optional[str] = None,
        model_size: str = "turbo",
    ) -> TranscriptionResponse:
        """
        오디오 파일 전사 (비동기).

        Args:
            audio_path: 오디오 파일 경로
            language: 언어 코드
            model_size: 모델 크기 (기본: turbo)

        Returns:
            TranscriptionResponse
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self._transcribe_sync,
            audio_path,
            language,
            model_size,
        )
        return result

    def _transcribe_sync(
        self,
        audio_path: Path,
        language: Optional[str],
        model_size: str,
    ) -> TranscriptionResponse:
        """동기 전사 실행."""
        logger.info(f"[WhisperCppService] Transcribing with whisper.cpp ({model_size}): {audio_path}")
        start_time = time.time()

        # 모델 경로
        model_path = self._get_model_path(model_size)
        logger.info(f"[WhisperCppService] Using model: {model_path}")

        # WAV 변환 (필요한 경우)
        audio_path_obj = Path(audio_path)
        temp_wav_path = None
        use_temp_wav = False

        if audio_path_obj.suffix.lower() not in ['.wav', '.wave']:
            logger.info("[WhisperCppService] Converting audio to WAV format...")
            temp_wav_fd, temp_wav_path = tempfile.mkstemp(suffix='.wav')
            os.close(temp_wav_fd)

            try:
                waveform, sample_rate = librosa.load(str(audio_path), sr=16000)
                sf.write(temp_wav_path, waveform, sample_rate)
                use_temp_wav = True
                actual_audio_path = temp_wav_path
            except Exception as e:
                logger.warning(f"[WhisperCppService] WAV conversion failed: {e}")
                actual_audio_path = str(audio_path)
                if temp_wav_path and os.path.exists(temp_wav_path):
                    os.unlink(temp_wav_path)
                    temp_wav_path = None
        else:
            actual_audio_path = str(audio_path)

        # 임시 JSON 출력 파일
        json_fd, json_output_base = tempfile.mkstemp(suffix='')
        os.close(json_fd)
        os.unlink(json_output_base)  # whisper.cpp가 .json 확장자를 추가함

        try:
            # whisper-cli.exe 명령 구성
            cmd = [
                WHISPER_CLI_PATH,
                "-m", model_path,
                "-l", language or "ko",
                "--output-json-full",
                "--output-file", json_output_base,
                actual_audio_path,
            ]

            # 환경 변수 (Vulkan GPU 사용)
            env = os.environ.copy()
            env.setdefault("GGML_VULKAN_DEVICE", "0")

            # Windows 프로세스 생성 플래그
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            # 실행
            logger.info(f"[WhisperCppService] Running whisper-cli.exe...")
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                env=env,
                creationflags=creation_flags,
                timeout=1800,  # 30분 타임아웃
            )

            if result.returncode != 0:
                raise RuntimeError(f"whisper-cli.exe failed: {result.stderr}")

            # JSON 파일 읽기
            json_file = json_output_base + '.json'
            if not os.path.exists(json_file):
                raise FileNotFoundError(f"JSON output not found: {json_file}")

            with open(json_file, 'rb') as f:
                content = f.read()
            if content.startswith(b'\xef\xbb\xbf'):
                content = content[3:]
            json_data = json.loads(content.decode('utf-8', errors='replace'))

            # 변환
            parsed = _parse_whispercpp_json(json_data)

            transcribe_time = time.time() - start_time
            logger.info(f"[WhisperCppService] Transcription completed in {transcribe_time:.2f}s")

            # 세그먼트 변환
            segments = [
                TranscriptionSegment(
                    id=seg["id"],
                    start=seg["start"],
                    end=seg["end"],
                    text=seg["text"],
                )
                for seg in parsed["segments"]
            ]

            # 오디오 길이
            duration = max(seg.end for seg in segments) if segments else None

            return TranscriptionResponse(
                text=parsed["text"],
                language=parsed.get("language", language or "ko"),
                duration=duration,
                segments=segments,
            )

        finally:
            # 임시 파일 정리
            json_file = json_output_base + '.json'
            if os.path.exists(json_file):
                try:
                    os.unlink(json_file)
                except:
                    pass
            if use_temp_wav and temp_wav_path and os.path.exists(temp_wav_path):
                try:
                    os.unlink(temp_wav_path)
                except:
                    pass


# 싱글톤 인스턴스
_whisper_cpp_service: Optional[WhisperCppService] = None


def get_whisper_cpp_service() -> WhisperCppService:
    """싱글톤 WhisperCppService 인스턴스."""
    global _whisper_cpp_service
    if _whisper_cpp_service is None:
        _whisper_cpp_service = WhisperCppService()
    return _whisper_cpp_service
