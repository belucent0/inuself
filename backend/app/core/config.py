from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    """싱글톤 설정 인스턴스."""
    settings = Settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings

