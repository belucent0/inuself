"""워커 설정 모듈 - 환경변수 기반 독립 설정."""
import os
from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_project_root() -> Path:
    """프로젝트 루트 디렉토리를 반환한다 (worker의 부모 디렉토리)."""
    current_file = Path(__file__)
    return current_file.parent.parent


class WorkerSettings(BaseSettings):
    """워커 환경설정 모델."""

    model_config = SettingsConfigDict(
        env_file=str(_get_project_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # ========================================
    # Redis 설정
    # ========================================
    redis_url: str = "redis://localhost:6379/0"

    # ========================================
    # S3/MinIO 설정
    # ========================================
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "torchdev"
    s3_secret_key: str = "torchdev-secret"
    s3_bucket: str = "asr-media"
    s3_prefix: str = "uploads"

    # ========================================
    # 데이터베이스 설정 (결과 저장용)
    # ========================================
    database_url: str = Field(
        "postgresql+asyncpg://user:pass@localhost:5432/asr",
        validation_alias="DATABASE_URL",
    )

    # ========================================
    # 워커 설정
    # ========================================
    worker_type: str = Field("asr", validation_alias="WORKER_TYPE")
    max_workers: int = 2
    task_queue_type: str = Field("celery", validation_alias="TASK_QUEUE_TYPE")

    # ========================================
    # ASR 설정
    # ========================================
    whisper_model_default: str = "large-v3"
    asr_chunk_duration_minutes: int = 10
    asr_chunk_overlap_seconds: int = 0
    asr_chunk_threshold_minutes: int = 25

    # ========================================
    # LLM 설정
    # ========================================
    llm_provider: str = Field("llamacpp_server", validation_alias="LLM_PROVIDER")
    llm_base_url: str = "http://localhost:8080"
    llm_model_name: str = ""
    llm_system_prompt: str = "당신은 회의록을 요약하는 전문가입니다. 모든 응답은 반드시 한글로 작성하세요."
    llm_context_length: int = 15000
    llm_temperature: float = 0.4
    llm_top_p: float = 0.9
    llm_max_tokens: int = 1024
    llm_n_threads: int = 8

    # LLM 서버 관리 설정
    llm_server_path: str = Field("", validation_alias="LLM_SERVER_PATH")
    llm_server_model: str = Field("", validation_alias="LLM_SERVER_MODEL")
    llm_server_port: int = Field(8080, validation_alias="LLM_SERVER_PORT")
    llm_server_threads: int = Field(8, validation_alias="LLM_SERVER_THREADS")
    llm_server_gpu_layers: int = Field(99, validation_alias="LLM_SERVER_GPU_LAYERS")
    llm_server_batch_size: int = Field(512, validation_alias="LLM_SERVER_BATCH_SIZE")

    # ========================================
    # LiteLLM 프록시 설정
    # ========================================
    litellm_base_url: str = Field(
        "http://localhost:4000", 
        validation_alias="LITELLM_BASE_URL"
    )
    litellm_api_key: str = Field(
        "sk-litellm-master", 
        validation_alias="LITELLM_API_KEY"
    )
    litellm_model: str = Field(
        "qwen3-4b", 
        validation_alias="LITELLM_MODEL"
    )

    @property
    def llm_api_base_url(self) -> str:
        """LLM API base URL (LLM 요약용)."""
        import os
        if self.llm_provider == "flm":
            # FLM은 FLM_BASE_URL 환경 변수 사용
            return os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        else:
            # llama-server 포트 사용
            return f"http://localhost:{self.llm_server_port}"

    @property
    def llm_api_model_name(self) -> str:
        """LLM API 모델 이름 (LLM 요약용)."""
        import os
        if self.llm_provider == "flm":
            # FLM은 FLM_LLM_MODEL 환경 변수 사용
            return os.getenv("FLM_LLM_MODEL", "qwen3-it:4b")
        else:
            return self.llm_model_name or "default"
    
    @property
    def ocr_api_base_url(self) -> str:
        """OCR API base URL (OCR 전용)."""
        import os
        if self.ocr_provider == "flm":
            # FLM은 FLM_BASE_URL 환경 변수 사용
            return os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        else:
            # llama-server 포트 사용 (OCR은 항상 llama.cpp 서버 사용 권장)
            return f"http://localhost:{self.llm_server_port}"
    
    @property
    def ocr_api_model_name(self) -> str:
        """OCR API 모델 이름 (OCR 전용)."""
        import os
        if self.ocr_provider == "flm":
            # FLM OCR 모델 (환경 변수에서 가져오거나 기본값 사용)
            # Qwen3-VL 4B 모델: qwen3vl-it:4b (NPU 지원)
            return os.getenv("FLM_OCR_MODEL", "qwen3vl-it:4b")
        else:
            # llama.cpp 서버 모델 사용
            return self.llm_model_name or "default"

    # ========================================
    # OCR 설정
    # ========================================
    ocr_provider: str = Field("flm", validation_alias="OCR_PROVIDER")  # OCR 전용 provider (기본값: flm, NPU 지원)
    ocr_model_path: str = Field("", validation_alias="OCR_SERVER_MODEL")
    ocr_server_mmproj: str = Field("", validation_alias="OCR_SERVER_MMPROJ")
    poppler_path: str = Field("", validation_alias="POPPLER_PATH")
    libreoffice_path: str = Field("", validation_alias="LIBREOFFICE_PATH")

    # ========================================
    # 임시 파일 경로
    # ========================================
    temp_dir: Path = Path("data/temp")

    @property
    def is_ocr_worker(self) -> bool:
        """워커 타입이 OCR인지 여부."""
        return self.worker_type.lower() == "ocr"

    @property
    def worker_model_path(self) -> str:
        """워커 타입에 맞는 llama.cpp 모델 경로 반환."""
        if self.is_ocr_worker and self.ocr_model_path:
            return self.ocr_model_path
        return self.llm_server_model

    @property
    def worker_mmproj_path(self) -> str:
        """Vision 모델용 mmproj 경로 반환."""
        if self.is_ocr_worker:
            return self.ocr_server_mmproj
        return ""


@lru_cache
def get_settings() -> WorkerSettings:
    """싱글톤 설정 인스턴스."""
    settings = WorkerSettings()
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    return settings
