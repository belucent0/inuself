"""LLM Chat Completion 라우트.

POST /v1/chat/completions — LLM 채팅 (스트리밍/비스트리밍).
local-gpu 모드는 ai-llm 컨테이너(vLLM)로 직결, serverless 모드는
RunPod로, codex/tier-thinking은 Codex(CLIProxyAPI)로 라우팅.
"""

import json
import logging

import openai
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import AsyncOpenAI

from config import (
    DEPLOY_MODE,
    CODEX_API_KEY,
    RUNPOD_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL_NAME,
)
from services.routing import select_provider, get_codex_provider

logger = logging.getLogger(__name__)
router = APIRouter()

# AsyncOpenAI 클라이언트 풀
_openai_clients: dict[str, AsyncOpenAI] = {}


def _get_openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    key = f"{base_url}:{api_key}"
    if key not in _openai_clients:
        _openai_clients[key] = AsyncOpenAI(base_url=base_url, api_key=api_key or "none")
    return _openai_clients[key]


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions 엔드포인트.

    task_type에 따라 media.py로 분기하거나, LLM 처리를 수행합니다.
    """
    body = await request.json()
    model = body.get("model", "")
    extra_body = body.get("extra_body", {})
    task_type = extra_body.get("task_type")
    stream = body.get("stream", False)

    # 미디어 작업 분기 (ASR, OCR, Diarization)
    if task_type in ("asr", "diarization", "ocr"):
        from routes.media import handle_media_request
        return await handle_media_request(body)

    # Vision 메시지 감지 → OCR 분기
    if _is_vision_request(body.get("messages", [])):
        if not task_type:
            extra_body["task_type"] = "ocr"
            body["extra_body"] = extra_body
            from routes.media import handle_media_request
            return await handle_media_request(body)

    # Codex 모델 직접 요청
    if model.startswith("codex-"):
        return await _handle_codex(body)

    # Tier-thinking: Codex primary → 로컬 fallback
    if model == "tier-thinking":
        return await _handle_tier_thinking(body)

    # 로컬 모드: ai-llm 컨테이너(vLLM) 직결
    if DEPLOY_MODE == "local-gpu":
        return await _handle_local_llm_container(body, stream)

    # 서버리스 모드: RunPod OpenAI-compatible 엔드포인트
    tier = model if model.startswith("tier-") else None
    provider = await select_provider(task_type="chat", tier=tier)
    return await _call_openai_compatible(body, provider)


async def _handle_local_llm_container(body: dict, stream: bool):
    """ai-llm 컨테이너(vLLM) 직접 호출 — Provider Manager / Redis Stream 우회.

    refactor/inference: chat·summary 모두 단일 모델(LLM_MODEL_NAME)로 통일.
    Codex / tier-thinking은 별도 처리되어 여기 도달하지 않는다.
    """
    requested_model = body.get("model", "")
    client = _get_openai_client(f"{LLM_BASE_URL}/v1", "none")

    common_kwargs = {
        "model": LLM_MODEL_NAME,  # vLLM serve 시 --served-model-name 와 일치해야 함
        "messages": body.get("messages", []),
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 0.7),
    }

    if stream:
        async def _vllm_stream():
            response = await client.chat.completions.create(stream=True, **common_kwargs)
            async for chunk in response:
                # vLLM이 돌려준 model 필드를 클라이언트 요청 그대로 보존
                d = chunk.model_dump()
                d["model"] = requested_model or LLM_MODEL_NAME
                yield f"data: {json.dumps(d)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_vllm_stream(), media_type="text/event-stream")

    response = await client.chat.completions.create(stream=False, **common_kwargs)
    payload = response.model_dump()
    payload["model"] = requested_model or LLM_MODEL_NAME
    return JSONResponse(payload)


async def _handle_codex(body: dict) -> JSONResponse:
    """Codex 모델 요청 처리 (CLIProxyAPI 경유)."""
    provider = get_codex_provider(body["model"])
    return await _call_openai_compatible(body, provider)


async def _handle_tier_thinking(body: dict):
    """tier-thinking: Codex primary, 로컬 GPU/NPU fallback."""
    # Codex 시도
    codex = get_codex_provider("codex-medium")
    try:
        client = _get_openai_client(codex.api_base, CODEX_API_KEY)
        stream = body.get("stream", False)

        if stream:
            response = await client.chat.completions.create(
                model=codex.model,
                messages=body.get("messages", []),
                stream=True,
                max_tokens=body.get("max_tokens", 4096),
                temperature=body.get("temperature", 0.7),
            )

            async def _codex_stream():
                async for chunk in response:
                    yield f"data: {json.dumps(chunk.model_dump())}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(_codex_stream(), media_type="text/event-stream")

        response = await client.chat.completions.create(
            model=codex.model,
            messages=body.get("messages", []),
            stream=False,
            max_tokens=body.get("max_tokens", 4096),
            temperature=body.get("temperature", 0.7),
        )
        return JSONResponse(response.model_dump())

    except (openai.APIError, openai.APITimeoutError, openai.APIConnectionError) as e:
        logger.warning(f"[Chat] Codex failed ({e}), falling back to ai-llm container")
        # refactor/inference: Provider Manager 우회 — ai-llm 컨테이너로 직결.
        # 로컬 Gemma 4 12B는 Codex 실패 시 fallback 안전망 역할.
        return await _handle_local_llm_container(body, body.get("stream", False))


async def _call_openai_compatible(body: dict, provider) -> JSONResponse:
    """openai SDK로 OpenAI-compatible 엔드포인트 호출."""
    api_key = CODEX_API_KEY if provider.name == "codex" else RUNPOD_API_KEY
    client = _get_openai_client(provider.api_base, api_key)

    response = await client.chat.completions.create(
        model=provider.model,
        messages=body.get("messages", []),
        stream=False,
        max_tokens=body.get("max_tokens", 4096),
        temperature=body.get("temperature", 0.7),
    )
    return JSONResponse(response.model_dump())


def _is_vision_request(messages: list) -> bool:
    """메시지에 이미지 콘텐츠가 포함되어 있는지 확인."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    return True
    return False
