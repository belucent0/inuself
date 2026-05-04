"""AI Gateway — FastAPI 기반 LLM/ASR/OCR/Embedding 라우팅 게이트웨이.

OpenAI-compatible API를 제공하면서 local-gpu 모드는 추론 컨테이너를
직접 httpx로 호출, serverless 모드는 RunPod / Codex 외부 API로 위임.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.redis import close_async_redis
from routes.chat import router as chat_router
from routes.embeddings import router as embeddings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ai-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 리소스 관리."""
    logger.info("[AI Gateway] Starting up...")
    yield
    await close_async_redis()
    logger.info("[AI Gateway] Shut down.")


app = FastAPI(
    title="AI Gateway",
    description="OpenAI-compatible LLM/ASR/OCR routing gateway",
    version="1.0.0",
    lifespan=lifespan,
)

# 라우터 등록
# chat_router가 /v1/chat/completions를 처리하며, task_type에 따라 media.py로 내부 분기
app.include_router(chat_router)
app.include_router(embeddings_router)


@app.get("/health/liveliness")
async def health_liveliness():
    """Docker healthcheck 용."""
    return {"status": "ok"}


@app.get("/health")
async def health():
    """일반 health check."""
    return {"status": "ok"}
