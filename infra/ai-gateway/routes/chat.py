"""LLM Chat Completion 라우트.

POST /v1/chat/completions — LLM 채팅 (스트리밍/비스트리밍)
Codex 모델, Tier 기반 로컬 모델, 서버리스 모델을 라우팅합니다.
"""

import json
import logging
import time
import uuid
from typing import Any

import openai
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import AsyncOpenAI

from clients.stream_client import get_async_gpu_stream_client
from config import (
    DEPLOY_MODE,
    CODEX_API_KEY,
    RUNPOD_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL_NAME,
    LLM_REQUEST_TIMEOUT,
    resolve_tier_to_model,
)
from services.device_lock import acquire_device_lock, release_device_lock
from services.routing import select_provider, get_codex_provider
from utils.response import build_openai_response

logger = logging.getLogger(__name__)
router = APIRouter()

# AsyncOpenAI 클라이언트 풀
_openai_clients: dict[str, AsyncOpenAI] = {}


def _get_openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    key = f"{base_url}:{api_key}"
    if key not in _openai_clients:
        _openai_clients[key] = AsyncOpenAI(base_url=base_url, api_key=api_key or "none")
    return _openai_clients[key]


async def _stream_sse(request_body: dict, provider) -> Any:
    """Redis Stream → SSE 스트리밍 변환."""
    model = request_body.get("model", "")
    messages = request_body.get("messages", [])
    resolved_model = resolve_tier_to_model(model) if model.startswith("tier-") else model

    # 로컬 GPU/NPU일 경우 디바이스 락 획득
    lock_id = None
    try:
        if provider.device_group not in ("codex", "serverless"):
            lock_id = await acquire_device_lock(provider.device_group)

        # Codex/서버리스: openai SDK로 직접 스트리밍
        if provider.name in ("codex", "runpod-llm"):
            api_key = CODEX_API_KEY if provider.name == "codex" else RUNPOD_API_KEY
            client = _get_openai_client(provider.api_base, api_key)

            stream = await client.chat.completions.create(
                model=provider.model,
                messages=messages,
                stream=True,
                max_tokens=request_body.get("max_tokens", 4096),
                temperature=request_body.get("temperature", 0.7),
            )

            async for chunk in stream:
                chunk_data = chunk.model_dump()
                yield f"data: {json.dumps(chunk_data)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 로컬 GPU/NPU: Redis Stream 스트리밍
        stream_client = get_async_gpu_stream_client()
        ttfb_logged = False
        start_ts = time.time()

        target_server = provider.name if provider.name in ("flm", "llama") else "auto"

        # stream_client에는 원래 tier명 전달 (RECAP_STREAM 분기를 위해)
        # Provider Manager의 stream_processor가 tier → 실제 모델 변환 수행
        stream_model = model if model.startswith("tier-") else resolved_model

        async for chunk_data in stream_client.request_llm_completion_stream(
            messages=messages,
            model=stream_model,
            max_tokens=request_body.get("max_tokens", 4096),
            temperature=request_body.get("temperature", 0.7),
            target_server=target_server,
            timeout=600.0,
        ):
            if "chunk" in chunk_data:
                if not ttfb_logged:
                    logger.info(f"[Chat] TTFB: {time.time() - start_ts:.3f}s")
                    ttfb_logged = True

                sse_chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": chunk_data["chunk"]},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(sse_chunk)}\n\n"

            if "result" in chunk_data:
                result = chunk_data["result"]
                finish_reason = "stop"
                if "choices" in result and result["choices"]:
                    finish_reason = result["choices"][0].get("finish_reason", "stop")

                final_chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": finish_reason,
                        }
                    ],
                }
                yield f"data: {json.dumps(final_chunk)}\n\n"
                yield "data: [DONE]\n\n"

    finally:
        if lock_id:
            await release_device_lock(provider.device_group, lock_id)


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

    # refactor/inference: local-gpu 모드의 일반 LLM은 asr-llm 컨테이너 직결
    # (Provider Manager + Redis Stream 우회, vLLM OpenAI API 직접 사용)
    if DEPLOY_MODE == "local-gpu":
        return await _handle_local_llm_container(body, stream)

    # 일반 LLM (tier-simple, tier-recap, 직접 모델명)
    tier = model if model.startswith("tier-") else None
    resolved_model = resolve_tier_to_model(model) if tier else model

    provider = await select_provider(task_type="chat", tier=tier)

    if stream:
        return StreamingResponse(
            _stream_sse(body, provider),
            media_type="text/event-stream",
        )

    # 비스트리밍
    lock_id = None
    try:
        if provider.device_group not in ("codex", "serverless"):
            lock_id = await acquire_device_lock(provider.device_group)

        if DEPLOY_MODE == "serverless" or provider.name.startswith("runpod"):
            return await _call_openai_compatible(body, provider)

        stream_client = get_async_gpu_stream_client()
        target_server = provider.name if provider.name in ("flm", "llama") else "auto"

        # stream_client에는 원래 tier명 전달 (RECAP_STREAM 분기를 위해)
        stream_model = model if model.startswith("tier-") else resolved_model

        result = await stream_client.request_llm_completion(
            messages=body.get("messages", []),
            model=stream_model,
            max_tokens=body.get("max_tokens", 4096),
            temperature=body.get("temperature", 0.7),
            target_server=target_server,
            timeout=300.0,
        )
        return JSONResponse(build_openai_response(result, model))
    finally:
        if lock_id:
            await release_device_lock(provider.device_group, lock_id)


async def _handle_local_llm_container(body: dict, stream: bool):
    """asr-llm 컨테이너(vLLM) 직접 호출 — Provider Manager / Redis Stream 우회.

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
        logger.warning(f"[Chat] Codex failed ({e}), falling back to local")

        # 로컬 fallback
        provider = await select_provider(task_type="chat", tier="tier-thinking")
        if body.get("stream", False):
            return StreamingResponse(
                _stream_sse(body, provider),
                media_type="text/event-stream",
            )

        lock_id = None
        try:
            lock_id = await acquire_device_lock(provider.device_group)
            resolved = resolve_tier_to_model("tier-thinking")
            stream_client = get_async_gpu_stream_client()
            target_server = provider.name if provider.name in ("flm", "llama") else "auto"

            result = await stream_client.request_llm_completion(
                messages=body.get("messages", []),
                model=resolved,
                max_tokens=body.get("max_tokens", 4096),
                temperature=body.get("temperature", 0.7),
                target_server=target_server,
                timeout=300.0,
            )
            return JSONResponse(build_openai_response(result, "tier-thinking"))
        finally:
            if lock_id:
                await release_device_lock(provider.device_group, lock_id)


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
