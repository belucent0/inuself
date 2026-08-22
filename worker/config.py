"""워커 설정 모듈 - 환경변수 기반 독립 설정."""

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
    # AI Gateway 설정 (모든 LLM/OCR/ASR/임베딩 요청은 ai-gateway 경유)
    # ========================================
    ai_gateway_url: str = Field(
        "http://localhost:4000", validation_alias="AI_GATEWAY_URL"
    )
    ai_gateway_api_key: str = Field("", validation_alias="AI_GATEWAY_API_KEY")

    # ========================================
    # LLM 공통 설정
    # ========================================
    llm_provider: str = Field("ai-gateway", validation_alias="LLM_PROVIDER")
    llm_system_prompt: str = (
        "당신은 회의록을 요약하는 전문가입니다. 모든 응답은 반드시 한글로 작성하세요."
    )
    llm_context_length: int = 15000
    llm_temperature: float = 0.1
    llm_top_p: float = 0.9
    llm_max_tokens: int = 3072

    # ========================================
    # OCR 설정
    # ========================================
    # ocr_provider: vision.py에서 이미지 크기 분기·ai-gateway accuracy_mode 매핑 키.
    # "flm" → speed mode, "llamacpp_server" → accuracy mode (의미는 모호하지만 식별자로 활성).
    ocr_provider: str = Field("flm", validation_alias="OCR_PROVIDER")
    poppler_path: str = Field("", validation_alias="POPPLER_PATH")
    libreoffice_path: str = Field("", validation_alias="LIBREOFFICE_PATH")

    # ========================================
    # 임시 파일 경로
    # ========================================
    temp_dir: Path = Path("data/temp")


@lru_cache
def get_settings() -> WorkerSettings:
    """싱글톤 설정 인스턴스."""
    settings = WorkerSettings()
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    return settings
