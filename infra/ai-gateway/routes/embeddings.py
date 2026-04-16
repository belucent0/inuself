"""임베딩 라우트.

POST /v1/embeddings — 텍스트 임베딩 벡터 생성
Redis Stream을 통해 FLM 서버로 전달합니다.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from clients.stream_client import get_async_gpu_stream_client
from config import DEPLOY_MODE, RUNPOD_API_KEY, RUNPOD_EMBED_BASE_URL

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/embeddings")
async def embeddings(request: Request):
    """OpenAI-compatible embeddings 엔드포인트."""
    body = await request.json()
    text = body.get("input", "")
    model = body.get("model", "flm-embeddings")

    if isinstance(text, list):
        text = text[0] if text else ""

    # 서버리스 모드
    if DEPLOY_MODE == "serverless":
        return await _handle_serverless(text, model)

    # 로컬: Redis Stream 경유
    try:
        stream_client = get_async_gpu_stream_client()
        result = await stream_client.request_embedding(
            text=text,
            model=model,
            timeout=15.0,
        )

        # 결과가 이미 OpenAI 포맷이면 그대로 반환
        if "data" in result:
            return JSONResponse(result)

        # 벡터만 있으면 래핑
        embedding = result.get("embedding", [])
        return JSONResponse({
            "object": "list",
            "data": [{"object": "embedding", "embedding": embedding, "index": 0}],
            "model": model,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        })

    except Exception as e:
        logger.error(f"[Embedding] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def _handle_serverless(text: str, model: str) -> JSONResponse:
    """서버리스 임베딩 (RunPod)."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=RUNPOD_EMBED_BASE_URL, api_key=RUNPOD_API_KEY)
    response = await client.embeddings.create(model=model, input=text)
    return JSONResponse(response.model_dump())
