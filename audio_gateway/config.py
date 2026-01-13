"""Audio Gateway 설정 모듈."""
import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings


class AudioGatewaySettings(BaseSettings):
    """Audio Gateway 환경설정."""

    # 서버 설정
    host: str = "0.0.0.0"
    port: int = 8001

    # Whisper 모델 설정 (정확도 모드: v3 사용)
    whisper_model: str = "openai/whisper-large-v3"
    whisper_device: str = "0"  # ROCm GPU 인덱스
    whisper_batch_size: int = 24
    whisper_compute_type: str = "float16"

    # 화자분리 설정 (pyannote)
    diarization_model: str = "pyannote/speaker-diarization-community-1"
    diarization_min_speakers: int | None = None
    diarization_max_speakers: int | None = None

    # 클러스터링 하이퍼파라미터
    clustering_threshold: float = 0.45

    # 임시 파일 경로
    temp_dir: Path = Path("C:/timblo/torch-test/data/temp/audio_gateway")

    # 로깅
    log_level: str = "INFO"

    # HuggingFace 토큰 (pyannote 모델 다운로드용)
    hf_token: str | None = None

    class Config:
        env_prefix = "AUDIO_GATEWAY_"
        extra = "ignore"


@lru_cache
def get_settings() -> AudioGatewaySettings:
    """설정 싱글톤 인스턴스 반환."""
    return AudioGatewaySettings()
