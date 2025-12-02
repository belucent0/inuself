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
    llm_provider: str = "llama_cpp"  # "llama_cpp", "lmstudio"
    
    # 공통 LLM 설정 (모든 provider에서 사용)
    llm_system_prompt: str = "당신은 회의록을 요약하는 전문가입니다. 모든 응답은 반드시 한글로 작성하세요. 마크다운 형식으로 명확하고 간결한 요약을 제공하되, 지시사항이나 프롬프트는 절대 포함하지 마세요."
    llm_context_length: int = 15016  # 컨텍스트 길이 (토큰 수)
    llm_temperature: float = 0.4
    llm_top_p: float = 0.9
    llm_max_tokens: int = 1024
    llm_n_threads: int = 8
    
    # LM Studio 전용 설정
    lmstudio_base_url: str = "http://localhost:1234"
    lmstudio_model_name: str = "gpt-oss-20b"
    
    # llama_cpp 전용 설정
    llm_model_path: Path = Path("models/gpt-oss-20b-Q4_K_S.gguf")
    llm_n_gpu_layers: int = -1  # GPU 레이어 설정 (-1: 모든 레이어 GPU, 0: CPU만, 양수: 지정된 레이어 수만큼 GPU)

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
    from .logging import logger
    
    # .env 파일 경로 확인
    project_root = _get_project_root()
    env_file = project_root / ".env"
    logger.info("[Config] .env file path: {}", env_file)
    logger.info("[Config] .env file exists: {}", env_file.exists())
    
    settings = Settings()
    logger.info("[Config] task_queue_type loaded: {}", settings.task_queue_type)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings

