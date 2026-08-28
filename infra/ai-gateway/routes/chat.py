"""OpenAI-compatible chat routing with profile-based provider selection."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import openai
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import AsyncOpenAI

from config import CODEX_API_KEY, DEPLOY_MODE, RUNPOD_API_KEY
from services.provider_pool import (
    AdmissionTimeout,
    NoEligibleProvider,
    NoHealthyProvider,
    ProviderPool,
)
from services.routing import (
    CODEX_REASONING_EFFORT_MAP,
    ProviderResult,
    get_codex_provider,
    get_serverless_llm_provider,
)
from utils.response import openai_error

logger = logging.getLogger(__name__)
router = APIRouter()

_openai_clients: dict[tuple[str, str, float], AsyncOpenAI] = {}
_ROUTING_KEYS = {"workload", "reasoning", "execution_scope"}
_WORKLOADS = {"chat", "summary"}
_REASONING = {"auto", "none", "low", "medium", "high"}
_SCOPES = {"local_only", "remote_allowed"}
_CODEX_MODELS = set(CODEX_REASONING_EFFORT_MAP)
_POLICY_SETTINGS = ProviderPool.load_settings()


class RoutingValidationError(ValueError):
    pass


class EmptyUpstreamStream(RuntimeError):
    pass


def _get_openai_client(base_url: str, api_key: str, timeout: float) -> AsyncOpenAI:
    key = (base_url, api_key, timeout)
    if key not in _openai_clients:
        _openai_clients[key] = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "none",
            timeout=timeout,
        )
    return _openai_clients[key]


async def close_openai_clients() -> None:
    clients = list(_openai_clients.values())
    _openai_clients.clear()
    for client in clients:
        await client.close()


def _extract_routing(body: dict) -> dict[str, str]:
    if "routing" in body:
        raw = body["routing"]
    else:
        extra = body.get("extra_body")
        raw = extra.get("routing") if isinstance(extra, dict) and "routing" in extra else {}
    if not isinstance(raw, dict):
        raise RoutingValidationError("routing must be an object")
    unknown = set(raw) - _ROUTING_KEYS
    if unknown:
        raise RoutingValidationError(f"unknown routing fields: {sorted(unknown)}")

    profile = {
        "workload": raw.get("workload", "chat"),
        "reasoning": raw.get("reasoning", "medium"),
        "execution_scope": raw.get("execution_scope", "local_only"),
    }
    if profile["workload"] not in _WORKLOADS:
        raise RoutingValidationError("routing.workload must be chat or summary")
    if profile["reasoning"] not in _REASONING:
        raise RoutingValidationError("routing.reasoning has an invalid value")
    if profile["execution_scope"] not in _SCOPES:
        raise RoutingValidationError("routing.execution_scope has an invalid value")
    if profile["reasoning"] == "auto":
        profile["reasoning"] = "medium"
    return profile


def _validate_model(body: dict) -> str:
    raw = body["model"] if "model" in body else "auto"
    if not isinstance(raw, str):
        raise RoutingValidationError("model must be a string")
    model = raw.strip() or "auto"
    if model != "auto" and model not in _CODEX_MODELS:
        raise RoutingValidationError(
            "model must be auto, codex-high, codex-medium, or codex-low"
        )
    return model


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return openai_error("request body must be valid JSON", "invalid_request", 400)
    if not isinstance(body, dict):
        return openai_error("request body must be a JSON object", "invalid_request", 400)
    extra_body = body.get("extra_body")
    task_type = extra_body.get("task_type") if isinstance(extra_body, dict) else None

    # Media and vision contracts predate LLM routing and must remain first.
    if task_type in ("asr", "diarization", "ocr"):
        from routes.media import handle_media_request

        return await handle_media_request(body)
    if _is_vision_request(body.get("messages", [])):
        if not task_type:
            extra_body = extra_body if isinstance(extra_body, dict) else {}
            extra_body["task_type"] = "ocr"
            body["extra_body"] = extra_body
        from routes.media import handle_media_request

        return await handle_media_request(body)

    try:
        model = _validate_model(body)
        profile = _extract_routing(body)
    except RoutingValidationError as exc:
        return openai_error(str(exc), "invalid_request", 400)

    if DEPLOY_MODE == "local-gpu":
        pool: ProviderPool | None = getattr(request.app.state, "provider_pool", None)
        if pool is None:
            return openai_error("provider pool is not initialized", "unavailable", 503)
        return await _handle_local(body, model, profile, pool)
    return await _handle_serverless(body, model, profile)


async def _handle_local(
    body: dict,
    model: str,
    profile: dict[str, str],
    pool: ProviderPool,
):
    attempted: set[str] = set()
    explicit = model in _CODEX_MODELS
    use_explicit = explicit
    fell_back = False
    saw_overload = False

    while True:
        try:
            if use_explicit:
                lease = await pool.acquire_explicit("codex")
            else:
                route_profile = (
                    {"workload": "chat", "reasoning": "high", "execution_scope": "local_only"}
                    if explicit
                    else profile
                )
                lease = await pool.acquire(
                    route_profile["workload"],
                    route_profile["reasoning"],
                    route_profile["execution_scope"],
                    frozenset(attempted),
                )
        except AdmissionTimeout:
            return _overloaded()
        except (NoEligibleProvider, NoHealthyProvider) as exc:
            if use_explicit:
                attempted.add("codex")
                use_explicit = False
                fell_back = True
                continue
            if saw_overload:
                return _overloaded()
            return openai_error(str(exc), "unavailable", 503)

        attempted.add(lease.spec.name)
        effort = CODEX_REASONING_EFFORT_MAP[model] if use_explicit else (
            "high" if lease.spec.name == "codex" else None
        )
        try:
            opened = await _open_upstream(
                body,
                base_url=_openai_base(lease.spec.base_url),
                api_key=lease.spec.api_key,
                model=lease.spec.model,
                reasoning_effort=effort,
                request_timeout=pool.settings.request_timeout_seconds,
                first_output_timeout=pool.settings.first_output_timeout_seconds,
            )
        except asyncio.CancelledError:
            await lease.release()
            raise
        except Exception as exc:
            kind, status = _classify_upstream_error(exc)
            if kind == "invalid_request":
                await lease.release()
                return openai_error(str(exc), kind, status)
            if kind == "retryable":
                await pool.record_failure(lease.spec.name)
            else:
                saw_overload = True
            await lease.release()
            fell_back = True
            use_explicit = False
            continue

        reason = "error-fallback" if fell_back else lease.reason
        headers = _provider_headers(lease.spec.name, opened["model"], reason)
        if opened["payload"] is not None:
            await pool.record_success(lease.spec.name)
            await lease.release()
            return JSONResponse(opened["payload"], headers=headers)
        return _stream_response(
            opened,
            headers,
            on_success=lambda: pool.record_success(lease.spec.name),
            on_failure=lambda: pool.record_failure(lease.spec.name),
            release=lease.release,
        )


async def _handle_serverless(body: dict, model: str, profile: dict[str, str]):
    explicit = model in _CODEX_MODELS
    providers: list[ProviderResult]
    if explicit:
        providers = [get_codex_provider(model), get_serverless_llm_provider()]
    else:
        providers = [get_serverless_llm_provider()]
        if profile["reasoning"] == "high" and profile["execution_scope"] == "remote_allowed":
            providers.append(get_codex_provider("codex-high"))

    saw_overload = False
    failed = False
    for provider in providers:
        if not provider.api_base:
            failed = True
            continue
        api_key = CODEX_API_KEY if provider.name == "codex" else RUNPOD_API_KEY
        try:
            opened = await _open_upstream(
                body,
                base_url=provider.api_base,
                api_key=api_key,
                model=provider.model,
                reasoning_effort=provider.reasoning_effort,
                request_timeout=_POLICY_SETTINGS.request_timeout_seconds,
                first_output_timeout=_POLICY_SETTINGS.first_output_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            kind, status = _classify_upstream_error(exc)
            if kind == "invalid_request":
                return openai_error(str(exc), kind, status)
            saw_overload |= kind == "overloaded"
            failed = True
            continue

        reason = "error-fallback" if failed else (
            "explicit-codex" if explicit else "preferred"
        )
        headers = _provider_headers(provider.name, opened["model"], reason)
        if opened["payload"] is not None:
            return JSONResponse(opened["payload"], headers=headers)
        return _stream_response(opened, headers)

    if saw_overload:
        return _overloaded()
    return openai_error("no serverless provider is available", "unavailable", 503)


async def _open_upstream(
    body: dict,
    *,
    base_url: str,
    api_key: str,
    model: str,
    reasoning_effort: str | None,
    request_timeout: float,
    first_output_timeout: float,
) -> dict:
    client = _get_openai_client(base_url, api_key, request_timeout)
    kwargs = {
        "model": model,
        "messages": body.get("messages", []),
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 0.7),
    }
    if reasoning_effort:
        kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}

    if not body.get("stream", False):
        response = await client.chat.completions.create(stream=False, **kwargs)
        payload = response.model_dump()
        return {
            "payload": payload,
            "model": payload.get("model") or model,
            "upstream": None,
            "iterator": None,
            "first": None,
        }

    upstream = None
    try:
        async with asyncio.timeout(first_output_timeout):
            upstream = await client.chat.completions.create(stream=True, **kwargs)
            iterator = upstream.__aiter__()
            try:
                first = await anext(iterator)
            except StopAsyncIteration as exc:
                raise EmptyUpstreamStream("upstream stream ended before first output") from exc
    except BaseException:
        await _close_stream(upstream)
        raise
    first_payload = first.model_dump()
    return {
        "payload": None,
        "model": first_payload.get("model") or model,
        "upstream": upstream,
        "iterator": iterator,
        "first": first,
    }


def _stream_response(
    opened: dict,
    headers: dict[str, str],
    *,
    on_success: Callable[[], Awaitable[None]] | None = None,
    on_failure: Callable[[], Awaitable[None]] | None = None,
    release: Callable[[], Awaitable[None]] | None = None,
) -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        try:
            yield _sse(opened["first"])
            async for chunk in opened["iterator"]:
                yield _sse(chunk)
            if on_success:
                await on_success()
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            if on_failure:
                await on_failure()
            logger.exception("upstream stream failed after first output")
            raise
        finally:
            await _close_stream(opened["upstream"])
            if release:
                await release()

    return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)


def _sse(chunk: object) -> str:
    payload = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
    return f"data: {json.dumps(payload)}\n\n"


async def _close_stream(stream: object | None) -> None:
    if stream is None:
        return
    closer = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if closer:
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("failed to close upstream stream", exc_info=True)


def _classify_upstream_error(exc: BaseException) -> tuple[str, int]:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.NetworkError)):
        return "retryable", 503
    status = getattr(exc, "status_code", None)
    if status is None and isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    if status in (400, 413, 422):
        return "invalid_request", 400
    if status == 429:
        return "overloaded", 503
    if status in (401, 403, 404) or (status is not None and status >= 500):
        return "retryable", 503
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.APIError)):
        return "retryable", 503
    return "retryable", 503


def _provider_headers(provider: str, model: str, reason: str) -> dict[str, str]:
    return {
        "X-Inference-Provider": provider,
        "X-Inference-Model": model,
        "X-Routing-Reason": reason,
    }


def _overloaded() -> JSONResponse:
    return openai_error(
        "all eligible providers are busy",
        "overloaded",
        503,
        {"Retry-After": "5"},
    )


def _openai_base(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _is_vision_request(messages: list) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "image_url"
            for item in content
        ):
            return True
    return False
