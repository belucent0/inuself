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
    LLM_REQUEST_TIMEOUT,
    NPU_LLM_BASE_URL,
    NPU_LLM_MODEL_NAME,
    NPU_LLM_REQUEST_TIMEOUT,
)
from services.routing import select_provider, get_codex_provider

logger = logging.getLogger(__name__)
router = APIRouter()

# AsyncOpenAI 클라이언트 풀
_openai_clients: dict[str, AsyncOpenAI] = {}


def _get_openai_client(
    base_url: str, api_key: str, timeout: float | None = None
) -> AsyncOpenAI:
    key = f"{base_url}:{api_key}:{timeout}"
    if key not in _openai_clients:
        kwargs = {"timeout": timeout, "max_retries": 0} if timeout is not None else {}
        _openai_clients[key] = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "none",
            **kwargs,
        )
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
    """Route tier-simple to NPU, otherwise use ai-llm, with safe fallback."""
    base_url, model_name, timeout = _local_llm_target(body.get("model", ""))
    try:
        return await _call_local_llm(body, stream, base_url, model_name, timeout)
    except (openai.APIError, StopAsyncIteration) as exc:
        if base_url == LLM_BASE_URL:
            raise
        logger.warning(f"[Chat] NPU failed ({exc}), falling back to GPU")
        return await _call_local_llm(
            body,
            stream,
            LLM_BASE_URL,
            LLM_MODEL_NAME,
            LLM_REQUEST_TIMEOUT,
        )


def _local_llm_target(requested_model: str) -> tuple[str, str, float]:
    if requested_model == "tier-simple" and NPU_LLM_BASE_URL:
        return NPU_LLM_BASE_URL, NPU_LLM_MODEL_NAME, NPU_LLM_REQUEST_TIMEOUT
    return LLM_BASE_URL, LLM_MODEL_NAME, LLM_REQUEST_TIMEOUT


async def _call_local_llm(
    body: dict,
    stream: bool,
    base_url: str,
    model_name: str,
    timeout: float,
):
    """Call an OpenAI-compatible local inference endpoint directly.

    refactor/inference: chat·summary 모두 단일 모델(LLM_MODEL_NAME)로 통일.
    Codex / tier-thinking은 별도 처리되어 여기 도달하지 않는다.
    """
    requested_model = body.get("model", "")
    client = _get_openai_client(
        f"{base_url.rstrip('/')}/v1",
        "none",
        timeout,
    )

    common_kwargs = {
        "model": model_name,
        "messages": body.get("messages", []),
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 0.7),
    }

    if stream:
        response = await client.chat.completions.create(stream=True, **common_kwargs)
        iterator = response.__aiter__()
        first_chunk = await anext(iterator)

        async def _vllm_stream():
            first = first_chunk.model_dump()
            first["model"] = requested_model or model_name
            yield f"data: {json.dumps(first)}\n\n"
            try:
                async for chunk in iterator:
                    d = chunk.model_dump()
                    d["model"] = requested_model or model_name
                    yield f"data: {json.dumps(d)}\n\n"
            except openai.APIError as exc:
                logger.error(f"[Chat] Local LLM stream failed after first chunk: {exc}")
                error = {"error": {"type": "upstream_stream_error", "message": str(exc)}}
                yield f"data: {json.dumps(error)}\n\n"
                return
            yield "data: [DONE]\n\n"

        return StreamingResponse(_vllm_stream(), media_type="text/event-stream")

    response = await client.chat.completions.create(stream=False, **common_kwargs)
    payload = response.model_dump()
    payload["model"] = requested_model or model_name
    return JSONResponse(payload)


async def _handle_codex(body: dict) -> JSONResponse:
    """Codex 모델 요청 처리 (CLIProxyAPI 경유)."""
    provider = get_codex_provider(body["model"])
    try:
        return await _call_openai_compatible(body, provider)
    except (openai.APIError, openai.APITimeoutError, openai.APIConnectionError) as e:
        logger.warning(f"[Chat] Codex failed ({e}), falling back to ai-llm container")
        return await _handle_local_llm_container(body, body.get("stream", False))


async def _handle_tier_thinking(body: dict):
    """tier-thinking: Codex primary, 로컬 GPU/NPU fallback."""
    # Codex 시도
    codex = get_codex_provider("codex-medium")
    extra_body = {"reasoning_effort": codex.reasoning_effort} if codex.reasoning_effort else {}
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
                extra_body=extra_body,
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
            extra_body=extra_body,
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
    reasoning_effort = getattr(provider, "reasoning_effort", None)
    extra_body = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}

    response = await client.chat.completions.create(
        model=provider.model,
        messages=body.get("messages", []),
        stream=False,
        max_tokens=body.get("max_tokens", 4096),
        temperature=body.get("temperature", 0.7),
        extra_body=extra_body,
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
