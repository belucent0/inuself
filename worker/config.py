"""워커 설정 모듈 - 환경변수 기반 독립 설정."""

import os
from functools import lru_cache
from pathlib import Path
from pydantic import Field, field_validator
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
        extra="ignore",
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
    s3_access_key: str = Field("", validation_alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field("", validation_alias="S3_SECRET_KEY")
    s3_bucket: str = Field("asr-media", validation_alias="S3_BUCKET_NAME")
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
    # LiteLLM 프록시 설정 (V4 표준)
    # 모든 LLM 요청은 LiteLLM을 통해 라우팅됨
    # ========================================
    litellm_base_url: str = Field(
        "http://localhost:4000", validation_alias="LITELLM_BASE_URL"
    )
    litellm_api_key: str = Field("", validation_alias="LITELLM_API_KEY")
    litellm_model: str = Field("tier-simple", validation_alias="LITELLM_MODEL")
    litellm_model_summarize: str = Field(
        "tier-simple", validation_alias="LITELLM_MODEL_SUMMARIZE"
    )

    # WPI AI 리포트 생성 전용 타임아웃/재시도 설정
    wpi_report_llm_request_timeout_seconds: float = Field(
        45.0,
        validation_alias="WPI_REPORT_LLM_REQUEST_TIMEOUT_SECONDS",
    )
    wpi_report_llm_busy_max_seconds: int = Field(
        90,
        validation_alias="WPI_REPORT_LLM_BUSY_MAX_SECONDS",
    )
    wpi_report_llm_retry_interval_seconds: int = Field(
        3,
        validation_alias="WPI_REPORT_LLM_RETRY_INTERVAL_SECONDS",
    )
    wpi_report_graph_timeout_seconds: int = Field(
        420,
        validation_alias="WPI_REPORT_GRAPH_TIMEOUT_SECONDS",
    )
    wpi_report_single_prompt_timeout_seconds: int = Field(
        180,
        validation_alias="WPI_REPORT_SINGLE_PROMPT_TIMEOUT_SECONDS",
    )

    @field_validator("litellm_base_url")
    @classmethod
    def resolve_docker_host(cls, v: str) -> str:
        """Docker 서비스명(asr-litellm)을 로컬 환경(localhost)에 맞게 변환."""
        if "asr-litellm" in v:
            return v.replace("asr-litellm", "localhost")
        return v

    # ========================================
    # LLM 공통 설정 (LiteLLM 또는 직접 호출 시 사용)
    # ========================================
    llm_provider: str = Field("litellm", validation_alias="LLM_PROVIDER")
    llm_system_prompt: str = (
        "당신은 회의록을 요약하는 전문가입니다. 모든 응답은 반드시 한글로 작성하세요."
    )
    llm_context_length: int = 15000
    llm_temperature: float = 0.1
    llm_top_p: float = 0.9
    llm_max_tokens: int = 3072  # V6.6: JSON 응답 + 상세 요약을 위해 증가

    # ========================================
    # On-Demand LLM 서버 설정 (OCR 정밀모드용)
    # llama.cpp 서버를 필요시 띄우는 경우에만 사용
    # ========================================
    llm_server_path: str = Field("", validation_alias="LLM_SERVER_PATH")
    llm_server_model: str = Field("", validation_alias="LLM_SERVER_MODEL")
    llm_server_port: int = Field(8080, validation_alias="LLM_SERVER_PORT")
    llm_server_threads: int = Field(8, validation_alias="LLM_SERVER_THREADS")
    llm_server_gpu_layers: int = Field(99, validation_alias="LLM_SERVER_GPU_LAYERS")
    llm_server_batch_size: int = Field(512, validation_alias="LLM_SERVER_BATCH_SIZE")

    # ========================================
    # OCR 설정
    # ========================================
    ocr_server_port: int = Field(8082, validation_alias="OCR_SERVER_PORT")
    ocr_provider: str = Field(
        "flm", validation_alias="OCR_PROVIDER"
    )  # flm=NPU, llamacpp=GPU
    ocr_model_path: str = Field("", validation_alias="OCR_SERVER_MODEL")
    ocr_server_mmproj: str = Field("", validation_alias="OCR_SERVER_MMPROJ")
    poppler_path: str = Field("", validation_alias="POPPLER_PATH")
    libreoffice_path: str = Field("", validation_alias="LIBREOFFICE_PATH")

    # ========================================
    # 임시 파일 경로
    # ========================================
    temp_dir: Path = Path("data/temp")

    # ========================================
    # 헬퍼 프로퍼티
    # ========================================
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

    @property
    def ocr_api_base_url(self) -> str:
        """OCR API base URL."""
        if self.ocr_provider == "flm":
            return os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        else:
            return f"http://localhost:{self.ocr_server_port}"

    @property
    def ocr_api_model_name(self) -> str:
        """OCR API 모델 이름."""
        if self.ocr_provider == "flm":
            return os.getenv("FLM_OCR_MODEL", "qwen3vl-it:4b")
        else:
            return (
                self.ocr_model_path.split("/")[-1] if self.ocr_model_path else "default"
            )


@lru_cache
def get_settings() -> WorkerSettings:
    """싱글톤 설정 인스턴스."""
    settings = WorkerSettings()
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    return settings
