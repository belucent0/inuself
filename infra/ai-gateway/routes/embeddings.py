"""임베딩 라우트.

POST /v1/embeddings — 텍스트 임베딩 벡터 생성.
local-gpu 모드는 inference-embedding 컨테이너(llama-server +
EmbeddingGemma 308M)를 직접 httpx로 호출. serverless 모드는 RunPod로 위임.
"""

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import (
    DEPLOY_MODE,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_REQUEST_TIMEOUT,
    RUNPOD_API_KEY,
    RUNPOD_EMBED_BASE_URL,
)
from utils.response import openai_error
from utils.routing import RoutingValidationError, _extract_routing, has_routing

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/embeddings")
async def embeddings(request: Request):
    """OpenAI-compatible embeddings 엔드포인트."""
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return openai_error("request body must be valid JSON", "invalid_request", 400)
    if not isinstance(body, dict):
        return openai_error("request body must be a JSON object", "invalid_request", 400)
    if has_routing(body):
        try:
            profile = _extract_routing(body)
        except RoutingValidationError as exc:
            return openai_error(str(exc), "invalid_request", 400)
        if profile["execution_scope"] == "local_only" and DEPLOY_MODE == "serverless":
            return openai_error("local_only forbids remote execution", "forbidden", 403)
    text = body.get("input", "")

    if DEPLOY_MODE == "serverless":
        return await _handle_serverless(text, body.get("model", "bge-small-en-v1.5"))

    payload = {"input": text, "model": EMBEDDING_MODEL_NAME}

    try:
        async with httpx.AsyncClient(timeout=EMBEDDING_REQUEST_TIMEOUT) as client:
            r = await client.post(f"{EMBEDDING_BASE_URL}/v1/embeddings", json=payload)
            r.raise_for_status()
            return JSONResponse(r.json())
    except httpx.HTTPStatusError as e:
        logger.error(f"[Embedding] upstream {e.response.status_code}: {e.response.text[:300]}")
        return JSONResponse({"error": f"Embedding upstream error: {e.response.status_code}"}, status_code=502)
    except Exception as e:
        logger.error(f"[Embedding] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def _handle_serverless(text, model: str) -> JSONResponse:
    """서버리스 임베딩 (RunPod)."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=RUNPOD_EMBED_BASE_URL, api_key=RUNPOD_API_KEY)
    response = await client.embeddings.create(model=model, input=text)
    return JSONResponse(response.model_dump())
