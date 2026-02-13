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

    # 워커 타입 설정
    worker_type: str = Field("llm", validation_alias="WORKER_TYPE")

    # LLM 요약 설정 (llama.cpp 서버)
    llm_provider: str = "llamacpp_server"  # "llamacpp_server" | "flm" | "litellm"

    # LiteLLM 프록시 설정
    litellm_base_url: str = Field(
        "http://asr-litellm:4000",  # Docker 내부 통신
        validation_alias="LITELLM_BASE_URL",
    )
    litellm_api_key: str = Field("", validation_alias="LITELLM_API_KEY")
    litellm_model: str = Field("tier-simple", validation_alias="LITELLM_MODEL")
    litellm_model_summarize: str = Field(
        "tier-simple", validation_alias="LITELLM_MODEL_SUMMARIZE"
    )

    # AI Agent 검색 품질 튜닝 파라미터
    agent_search_web_limit: int = Field(
        15,
        validation_alias="AGENT_SEARCH_WEB_LIMIT",
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
    llm_max_tokens: int = 3072  # V6.6: JSON 응답 + 상세 요약을 위해 증가
    llm_n_threads: int = 8

    # LLM API 서버 설정 (공통 - 모든 OpenAI 호환 API 사용, provider와 무관)
    llm_base_url: str = "http://localhost:8080"
    llm_model_name: str = ""

    # LLM 서버 설정 (요청마다 시작/종료, provider와 무관)
    llm_server_path: str = Field(
        "", validation_alias="LLM_SERVER_PATH"
    )  # 서버 실행 파일 경로
    llm_server_model: str = Field(
        "", validation_alias="LLM_SERVER_MODEL"
    )  # 모델 파일 경로
    ocr_server_mmproj: str = Field(
        "", validation_alias="OCR_SERVER_MMPROJ"
    )  # Vision 모델용 mmproj 파일 경로
    llm_server_port: int = Field(8080, validation_alias="LLM_SERVER_PORT")  # 서버 포트
    llm_server_threads: int = Field(
        8, validation_alias="LLM_SERVER_THREADS"
    )  # 스레드 수
    llm_server_gpu_layers: int = Field(
        99, validation_alias="LLM_SERVER_GPU_LAYERS"
    )  # GPU 레이어 수
    llm_server_batch_size: int = Field(
        512, validation_alias="LLM_SERVER_BATCH_SIZE"
    )  # 배치 크기

    # llama.cpp 모델 경로
    ocr_model_path: str = Field(
        "", validation_alias="OCR_SERVER_MODEL"
    )  # OCR 비전 모델 경로

    # OCR 설정 (poppler 경로)
    poppler_path: str = Field(
        "", validation_alias="POPPLER_PATH"
    )  # poppler bin 디렉토리 경로 (예: C:\poppler\bin)

    # LibreOffice 설정 (Office 문서 변환용)
    libreoffice_path: str = Field(
        "", validation_alias="LIBREOFFICE_PATH"
    )  # LibreOffice 실행 파일 경로 (예: C:\Program Files\LibreOffice\program\soffice.exe 또는 /usr/bin/libreoffice)

    @property
    def llm_api_base_url(self) -> str:
        """LLM API 서버 URL (환경변수 LLM_BASE_URL 사용)"""
        return self.llm_base_url

    @property
    def llm_api_model_name(self) -> str:
        """LLM API 모델 이름 (환경변수 LLM_MODEL_NAME 사용)"""
        return self.llm_model_name

    @property
    def is_ocr_worker(self) -> bool:
        """워커 타입이 OCR인지 여부."""
        return self.worker_type.lower() == "ocr"

    @property
    def worker_model_path(self) -> str:
        """
        워커 타입에 맞는 llama.cpp 모델 경로 반환.

        우선순위:
        1. OCR 워커: OCR_SERVER_MODEL
        2. LLM 워커: LLM_SERVER_MODEL
        """
        if self.is_ocr_worker and self.ocr_model_path:
            return self.ocr_model_path
        return self.llm_server_model

    @property
    def worker_mmproj_path(self) -> str:
        """
        Vision 모델용 mmproj 경로 반환.

        OCR 워커일 때만 OCR_SERVER_MMPROJ 사용. LLM 워커는 기본적으로 mmproj 없이 텍스트 모델을 사용.
        """
        if self.is_ocr_worker:
            return self.ocr_server_mmproj
        return ""

    # 관리자 인증 설정
    admin_username: str = Field("admin", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field("", validation_alias="ADMIN_PASSWORD")

    # CORS 설정
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,https://asr.timblo.io,http://asr.timblo.io:3000"


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
