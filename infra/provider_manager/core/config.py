"""Provider Manager Configuration.

모든 설정을 중앙에서 관리하는 Pydantic Settings 기반 설정 모듈.
"""
import os
from pathlib import Path
from typing import Optional
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Provider Manager 설정."""

    # Project paths
    project_root: Path = Path(__file__).parent.parent.parent.parent
    log_dir: Optional[Path] = None

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Stream names (GPU 작업용)
    request_stream: str = "stream:gpu:requests"
    response_stream: str = "stream:gpu:responses"
    consumer_group: str = "gpu-workers"

    # Control Stream names (Provider 관리용)
    control_request_stream: str = "stream:provider:requests"
    control_response_stream: str = "stream:provider:responses"
    control_consumer_group: str = "provider-managers"

    # GPU Server URLs
    diarization_url: str = "http://localhost:8003"
    whisper_cpp_url: str = "http://localhost:8001"
    insanely_fast_url: str = "http://localhost:8002"
    llama_server_url: str = "http://localhost:8080"
    llama_ocr_server_url: str = "http://localhost:8081"

    # FLM NPU Server URLs
    flm_asr_url: str = "http://localhost:11434"
    flm_llm_url: str = "http://localhost:11435"
    flm_llm_thinking_url: str = "http://localhost:11437"  # 추론 모델
    flm_ocr_url: str = "http://localhost:11436"

    # Server paths (from environment)
    llm_server_path: str = "llama-server"
    llm_model: str = ""
    llm_server_port: str = "8080"
    llm_context_length: str = "15000"
    llm_n_gpu_layers: str = "99"
    llm_n_threads: str = "8"

    ocr_server_model: str = ""
    ocr_server_mmproj: str = ""
    ocr_server_port: str = "8081"
    ocr_context_length: str = "10000"
    ocr_server_gpu_layers: str = "75"
    ocr_server_threads: str = "4"

    rocm_env_path: str = ""

    # Timeouts
    default_timeout: float = 7200.0  # 2시간 (3시간 음성 파일의 화자분리 대응)

    # Health Monitoring (Always-On Recovery)
    health_check_interval: int = 30  # 헬스체크 주기 (초)
    health_check_timeout: float = 10.0  # 헬스체크 타임아웃 (초)
    max_recovery_attempts: int = 3  # 최대 복구 시도 횟수
    recovery_cooldown: int = 300  # 복구 실패 후 대기 시간 (초, 5분)
    consecutive_failure_threshold: int = 3  # 연속 실패 임계값 (이 횟수 이상 연속 실패 시 recovery 시작)

    # Idle Timeout (On-Demand 프로바이더 자동 언로드)
    idle_timeout: float = 60.0  # idle timeout 시간 (초, 기본값 60초)
    idle_check_interval: float = 10.0  # idle 체크 주기 (초)

    # Concurrency Control (프로바이더 그룹별 동시 실행 제한)
    # GPU 프로바이더: diarization, whisper-cpp, insanely-fast, llama-server, llama-ocr-server
    # NPU 프로바이더: flm-asr, flm-llm, flm-ocr
    # 메모리 대역폭 제한으로 최대 2채널 병렬 처리 (ASR + Diarization 동시 실행용)
    gpu_max_concurrent: int = 2  # GPU 그룹 동시 작업 수 (같은 GPU 공유)
    npu_max_concurrent: int = 1  # NPU 그룹 동시 작업 수 (같은 NPU 공유)

    # Redis Keys for Status Sharing
    status_hash_key: str = "providers:status"  # 현재 상태 (Hash)
    events_stream_key: str = "stream:provider:events"  # 이벤트 로그 (Stream)
    jobs_hash_key: str = "providers:jobs"  # 작업 상태 (Hash)
    events_stream_maxlen: int = 10000  # 이벤트 스트림 최대 길이

    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 9998

    class Config:
        env_prefix = ""
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # log_dir 초기화
        if self.log_dir is None:
            self.log_dir = self.project_root / "logs"
        self.log_dir.mkdir(exist_ok=True)

        # 환경변수에서 추가 설정 로드
        self._load_env_file()

    def _load_env_file(self):
        """Load additional settings from .env file."""
        env_path = self.project_root / ".env"
        if env_path.exists():
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, _, value = line.partition('=')
                        key = key.strip().lower()
                        value = value.strip().strip('"').strip("'")

                        # 설정 매핑
                        attr_name = key.lower()
                        if hasattr(self, attr_name) and not getattr(self, attr_name):
                            setattr(self, attr_name, value)

    @property
    def rocm_pythonw(self) -> str:
        """ROCm Python executable path."""
        if self.rocm_env_path:
            return str(Path(self.rocm_env_path) / "Scripts" / "pythonw.exe")
        return str(self.project_root / "rocm_env" / "Scripts" / "pythonw.exe")

    @property
    def scripts_dir(self) -> Path:
        """Scripts directory path."""
        return self.project_root / "scripts"

    @property
    def consumer_name(self) -> str:
        """Unique consumer name for Redis Stream."""
        return f"worker-{os.getpid()}"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
