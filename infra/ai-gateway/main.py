"""AI Gateway — FastAPI 기반 LLM/ASR/OCR/Embedding 라우팅 게이트웨이.

OpenAI-compatible API를 제공하면서 local-gpu 모드는 추론 컨테이너를
직접 httpx로 호출, serverless 모드는 RunPod / Codex 외부 API로 위임.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from config import DEPLOY_MODE, LLM_MODEL_NAME, RUNPOD_LLM_BASE_URL
from core.redis import close_async_redis
from routes.chat import close_openai_clients, router as chat_router
from routes.embeddings import router as embeddings_router
from services.provider_pool import ProviderPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ai-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 리소스 관리."""
    logger.info("[AI Gateway] Starting up...")
    pool = None
    try:
        if DEPLOY_MODE == "local-gpu":
            pool = ProviderPool()
            app.state.provider_pool = pool
            await pool.start()
        yield
    finally:
        try:
            if pool:
                await pool.close()
        finally:
            try:
                await close_openai_clients()
            finally:
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


@app.get("/health/readiness")
async def health_readiness():
    """Report initial-probe, route, circuit, and provider readiness."""
    if DEPLOY_MODE == "serverless":
        ready = bool(RUNPOD_LLM_BASE_URL.strip())
        status = "ready" if ready else "unavailable"
        route = "ready" if ready else "unavailable"
        return JSONResponse(
            {
                "status": status,
                "mode": DEPLOY_MODE,
                "providers": {
                    "runpod-llm": {
                        "health": "configured" if ready else "disabled",
                        "inflight": 0,
                        "max_inflight": None,
                        "model": LLM_MODEL_NAME,
                        "circuit_open": False,
                    }
                },
                "routes": {"chat": route, "summary": route},
            },
            status_code=200 if ready else 503,
        )

    pool: ProviderPool | None = getattr(app.state, "provider_pool", None)
    if pool is None:
        return JSONResponse(
            {
                "status": "unavailable",
                "mode": DEPLOY_MODE,
                "providers": {},
                "routes": {"chat": "unavailable", "summary": "unavailable"},
            },
            status_code=503,
        )

    providers = await pool.snapshot()
    routes = await pool.route_readiness(local_only=True)
    chat_ready = routes.get("chat") == "ready"
    local_degraded = any(
        providers[name]["health"] != "healthy" or providers[name]["circuit_open"]
        for name, spec in pool.specs.items()
        if spec.scope == "local"
    )
    if not chat_ready:
        status, status_code = "unavailable", 503
    elif local_degraded or any(value == "unavailable" for value in routes.values()):
        status, status_code = "degraded", 200
    else:
        status, status_code = "ready", 200
    return JSONResponse(
        {
            "status": status,
            "mode": DEPLOY_MODE,
            "providers": providers,
            "routes": routes,
        },
        status_code=status_code,
    )
