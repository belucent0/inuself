"""Audio Gateway - FastAPI 진입점.

LiteLLM 기반 ASR 라우팅을 위한 Audio Gateway 서버.
- /v1/audio/transcriptions: OpenAI 호환 ASR API
- /v1/audio/diarization: 화자분리 API
"""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import transcription, diarization

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 리소스 관리."""
    settings = get_settings()

    # 시작 시 초기화
    logger.info("=" * 60)
    logger.info("[AudioGateway] Starting up...")
    logger.info(f"[AudioGateway] Host: {settings.host}:{settings.port}")
    logger.info(f"[AudioGateway] Whisper model: {settings.whisper_model}")
    logger.info(f"[AudioGateway] Diarization model: {settings.diarization_model}")
    logger.info(f"[AudioGateway] Temp directory: {settings.temp_dir}")
    logger.info("=" * 60)

    # 임시 디렉토리 생성
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    # 모델은 첫 요청 시 lazy loading (메모리 절약)
    yield

    # 종료 시 정리
    logger.info("[AudioGateway] Shutting down...")

    # GPU 메모리 해제
    try:
        from .services.whisper_service import get_whisper_service
        from .services.diarization_service import get_diarization_service

        get_whisper_service().unload_model()
        get_diarization_service().unload_pipeline()
    except Exception as e:
        logger.warning(f"[AudioGateway] Error during cleanup: {e}")

    logger.info("[AudioGateway] Shutdown complete")


def create_app() -> FastAPI:
    """FastAPI 앱 생성."""
    settings = get_settings()

    app = FastAPI(
        title="Audio Gateway",
        description="LiteLLM 기반 ASR 라우팅을 위한 Audio Gateway",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS 설정
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 라우터 등록
    app.include_router(transcription.router, prefix="/v1/audio", tags=["ASR"])
    app.include_router(diarization.router, prefix="/v1/audio", tags=["Diarization"])

    @app.get("/health")
    async def health_check():
        """헬스체크 엔드포인트."""
        return {
            "status": "ok",
            "service": "audio-gateway",
            "whisper_model": settings.whisper_model,
        }

    @app.get("/")
    async def root():
        """루트 엔드포인트."""
        return {
            "service": "Audio Gateway",
            "version": "1.0.0",
            "endpoints": {
                "transcription": "/v1/audio/transcriptions",
                "diarization": "/v1/audio/diarization",
                "health": "/health",
            },
        }

    return app


# FastAPI 앱 인스턴스
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "audio_gateway.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
