from functools import lru_cache
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_project_root() -> Path:
    """프로젝트 루트 디렉토리를 반환한다 (backend의 부모 디렉토리)."""
    # backend/app/core/config.py -> backend -> 프로젝트 루트
    current_file = Path(__file__)
    backend_dir = current_file.parent.parent.parent
    return backend_dir.parent


class Settings(BaseSettings):
    """환경설정 모델."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Torch ASR Backend"
    api_prefix: str = "/api"
    debug: bool = False

    postgres_dsn: str = "postgresql+asyncpg://user:pass@localhost:5432/asr"
    redis_url: str = "redis://localhost:6379/0"

    upload_dir: Path = Path("data/uploads")
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "torchdev"
    s3_secret_key: str = "torchdev-secret"
    s3_bucket: str = "asr-media"
    s3_prefix: str = "uploads"

    whisper_model_default: str = "large-v3"
    max_workers: int = 2

    # LLM 요약 설정 (Ollama/llama_cpp/LM Studio)
    llm_provider: str = "lmstudio"  # "ollama", "llama_cpp", "lmstudio"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model_name: str = "gpt-oss:20b"  # Ollama에서 사용할 모델 이름
    lmstudio_base_url: str = "http://localhost:1234"
    lmstudio_model_name: str = "gpt-oss-20b"
    lmstudio_system_prompt: str = "Always answer in rhymes. Today is Thursday"
    llm_context_length: int = 4096
    llm_temperature: float = 0.4
    llm_top_p: float = 0.9
    llm_max_tokens: int = 1024
    llm_n_threads: int = 8
    # llama_cpp 직접 사용 시에만 사용
    llm_model_path: Path = Path("models/gpt-oss-20b-Q4_K_S.gguf")

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
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings

