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
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
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
    s3_access_key: str = "torchdev"
    s3_secret_key: str = "torchdev-secret"
    s3_bucket: str = "asr-media"
    s3_prefix: str = "uploads"
    
    # 외부 접근용 미디어 URL (nginx 프록시 경로)
    # 환경변수로 설정 가능: MEDIA_BASE_URL=https://asr.timblo.io/media
    media_base_url: str = ""  # 비어있으면 s3_endpoint 사용

    whisper_model_default: str = "large-v3"
    max_workers: int = 2

    # ASR 청킹 설정 (긴 오디오 파일 처리용)
    asr_chunk_duration_minutes: int = 30  # 청크 크기 (분)
    asr_chunk_overlap_seconds: int = 0  # 오버랩 크기 (초) - 테스트용 0으로 설정
    asr_chunk_threshold_minutes: int = 60  # 이 길이 이상인 파일만 청킹 적용

    # LLM 요약 설정 (llama_cpp/LM Studio)
    llm_provider: str = "lmstudio"  # "llama_cpp", "lmstudio"
    lmstudio_base_url: str = "http://localhost:1234"
    lmstudio_model_name: str = "gpt-oss-20b"
    lmstudio_system_prompt: str = "당신은 회의록을 요약하는 전문가입니다. 모든 응답은 반드시 한글로 작성하세요. 마크다운 형식으로 명확하고 간결한 요약을 제공하되, 지시사항이나 프롬프트는 절대 포함하지 마세요."
    llm_context_length: int = 15016  # LM Studio에서 로드된 실제 컨텍스트 길이 (15,016 토큰)
    llm_temperature: float = 0.4
    llm_top_p: float = 0.9
    llm_max_tokens: int = 1024
    llm_n_threads: int = 8
    # llama_cpp 직접 사용 시에만 사용
    llm_model_path: Path = Path("models/gpt-oss-20b-Q4_K_S.gguf")

    # 관리자 인증 설정
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # CORS 설정
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,https://asr.timblo.io"

    @model_validator(mode="after")
    def resolve_llm_model_path(self) -> "Settings":
        """모델 초기화 후 경로를 프로젝트 루트 기준으로 변환."""
        # llm_model_path가 상대 경로인 경우 프로젝트 루트 기준으로 변환
        if not self.llm_model_path.is_absolute():
            project_root = _get_project_root()
            self.llm_model_path = project_root / self.llm_model_path
        return self


@lru_cache
def get_settings() -> Settings:
    """싱글톤 설정 인스턴스."""
    import logging
    logger = logging.getLogger(__name__)
    
    # .env 파일 경로 확인
    project_root = _get_project_root()
    env_file = project_root / ".env"
    logger.info(f"[Config] .env 파일 경로: {env_file}")
    logger.info(f"[Config] .env 파일 존재: {env_file.exists()}")
    
    settings = Settings()
    logger.info(f"[Config] task_queue_type 로드됨: {settings.task_queue_type}")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings

