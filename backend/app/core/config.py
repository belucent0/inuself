import secrets
from functools import lru_cache
from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_project_root() -> Path:
    """프로젝트 루트 디렉토리를 반환한다 (backend의 부모 디렉토리)."""
    # backend/app/core/config.py -> backend -> 프로젝트 루트
    current_file = Path(__file__)
    backend_dir = current_file.parent.parent.parent
    return backend_dir.parent


class Settings(BaseSettings):
    """환경설정 모델."""

    model_config = SettingsConfigDict(
        # .env 파일 경로를 프로젝트 루트 기준으로 명시적으로 지정
        env_file=str(_get_project_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Torch ASR Backend"
    api_prefix: str = "/api"
    debug: bool = False

    # Task Queue 설정
    task_queue_type: str = "celery"  #  "celery"

    # DATABASE_URL 환경변수와 매핑 (유지보수성을 위해 외부 이름은 DATABASE_URL 사용)
    database_url: str = Field(
        "postgresql+asyncpg://user:pass@localhost:5432/asr",
        validation_alias="DATABASE_URL",
    )

    @property
    def postgres_dsn(self) -> str:
        """하위 호환성을 위한 postgres_dsn 프로퍼티."""
        return self.database_url

    redis_url: str = "redis://localhost:6379/0"

    upload_dir: Path = Path("data/uploads")
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = Field("", validation_alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field("", validation_alias="S3_SECRET_KEY")
    s3_bucket: str = Field("asr-media", validation_alias="S3_BUCKET_NAME")
    s3_prefix: str = "uploads"

    # 외부 접근용 미디어 URL (nginx 프록시 경로)
    # 환경변수로 설정 가능: MEDIA_BASE_URL=/media
    media_base_url: str = ""  # 비어있으면 s3_endpoint 사용

    whisper_model_default: str = "large-v3"
    max_workers: int = 2

    # ASR 청킹 설정 (긴 오디오 파일 처리용)
    asr_chunk_duration_minutes: int = 10  # 청크 크기 (분)
    asr_chunk_overlap_seconds: int = 0  # 오버랩 크기 (초) - 테스트용 0으로 설정
    asr_chunk_threshold_minutes: int = (
        25  # 이 길이 이상인 파일만 청킹 적용 (30분 = 1800초)
    )

    # LLM provider (v1.2.0: ai-gateway 단일 라우터)
    llm_provider: str = "ai-gateway"

    # AI Gateway 설정
    ai_gateway_url: str = Field(
        "http://ai-gateway:4000",  # Docker 내부 통신
        validation_alias="AI_GATEWAY_URL",
    )
    ai_gateway_api_key: str = Field("", validation_alias="AI_GATEWAY_API_KEY")
    ai_gateway_model: str = Field("tier-simple", validation_alias="AI_GATEWAY_MODEL")
    ai_gateway_model_summarize: str = Field(
        "tier-recap", validation_alias="AI_GATEWAY_MODEL_SUMMARIZE"
    )
    ai_gateway_allowed_models: str = Field("", validation_alias="AI_GATEWAY_ALLOWED_MODELS")

    # AI Agent 검색 품질 튜닝 파라미터
    agent_search_web_limit: int = Field(
        15,
        validation_alias="AGENT_SEARCH_WEB_LIMIT",
    )
    searxng_url: str = Field(
        "http://searxng:8080",
        validation_alias="SEARXNG_URL",
    )
    agent_hybrid_web_limit: int = Field(
        10,
        validation_alias="AGENT_HYBRID_WEB_LIMIT",
    )
    agent_content_fetch_top_k: int = Field(
        8,
        validation_alias="AGENT_CONTENT_FETCH_TOP_K",
    )

    # 공통 LLM 설정 (모든 provider에서 사용)
    llm_system_prompt: str = "당신은 회의록을 요약하는 전문가입니다. 모든 응답은 반드시 한글로 작성하세요. 마크다운 형식으로 명확하고 간결한 요약을 제공하되, 지시사항이나 프롬프트는 절대 포함하지 마세요."
    llm_context_length: int = (
        15000  # 컨텍스트 길이 (토큰 수) - 메모리 사용량 최적화를 위해 15000으로 제한
    )
    llm_temperature: float = 0.4
    llm_top_p: float = 0.9
    llm_max_tokens: int = 3072

    # 문서 변환용 외부 바이너리 경로 (PDF/Office → 이미지)
    poppler_path: str = Field(
        "", validation_alias="POPPLER_PATH"
    )  # poppler bin 디렉토리 경로 (예: C:\poppler\bin)
    libreoffice_path: str = Field(
        "", validation_alias="LIBREOFFICE_PATH"
    )  # LibreOffice 실행 파일 경로

    # CORS 설정
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,https://asr.timblo.io,http://asr.timblo.io:3000"

    # JWT 인증 설정
    jwt_secret_key: str = Field("", validation_alias="JWT_SECRET_KEY")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = Field("playful-planet", validation_alias="JWT_ISSUER")
    jwt_audience: str = Field("playful-planet-api", validation_alias="JWT_AUDIENCE")
    jwt_access_token_ttl_minutes: int = Field(
        10, validation_alias="JWT_ACCESS_TOKEN_TTL_MINUTES"
    )
    jwt_refresh_token_ttl_days: int = Field(
        14, validation_alias="JWT_REFRESH_TOKEN_TTL_DAYS"
    )


@lru_cache
def get_settings() -> Settings:
    """싱글톤 설정 인스턴스."""
    from .logging import logger

    # .env 파일 경로 확인
    project_root = _get_project_root()
    env_file = project_root / ".env"
    logger.info("[Config] .env file path: {}", env_file)
    logger.info("[Config] .env file exists: {}", env_file.exists())

    settings = Settings()

    if not settings.jwt_secret_key:
        settings.jwt_secret_key = secrets.token_urlsafe(64)
        logger.warning(
            "[Auth] JWT_SECRET_KEY not set. Generated temporary in-memory key."
        )

    logger.info("[Config] task_queue_type loaded: {}", settings.task_queue_type)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings
