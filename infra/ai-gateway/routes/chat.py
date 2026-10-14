"""OpenAI-compatible chat routing with profile-based provider selection."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import anyio
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
from utils.inputs import InputError, read_body, validate_chat
from utils.routing import RoutingValidationError, _extract_routing, has_routing
from utils.upstream import _await_cleanup, _cleanup_stream, _do_cleanup_stream, _classify_upstream_error
from middleware.telemetry import annotate_routing, annotate_error

logger = logging.getLogger(__name__)
router = APIRouter()

_openai_clients: dict[tuple[str, str, float], AsyncOpenAI] = {}
_CODEX_MODELS = set(CODEX_REASONING_EFFORT_MAP)
_POLICY_SETTINGS = ProviderPool.load_settings()


class EmptyUpstreamStream(RuntimeError):
    pass


class DeadlineStreamingResponse(StreamingResponse):
    def __init__(self, content, headers, deadline, cleanup):
        super().__init__(content, media_type="text/event-stream", headers=headers)
        self.deadline, self.cleanup = deadline, cleanup

    async def finish(self):
        with anyio.CancelScope(shield=True):
            try:
                await self.body_iterator.aclose()
            finally:
                await self.cleanup()

    async def __call__(self, scope, receive, send):
        try:
            if asyncio.get_running_loop().time() >= self.deadline:
                await self.finish()
                await _timed_out()(scope, receive, send)
                return
            async with asyncio.timeout_at(self.deadline):
                await super().__call__(scope, receive, send)
        except TimeoutError:
            annotate_error("timed_out")
            logger.info("Gateway stream deadline reached")
        finally:
            await self.finish()


def _get_openai_client(base_url: str, api_key: str, timeout: float) -> AsyncOpenAI:
    key = (base_url, api_key, timeout)
    if key not in _openai_clients:
        _openai_clients[key] = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "none",
            timeout=timeout,
            max_retries=0,
        )
    return _openai_clients[key]


async def close_openai_clients() -> None:
    clients = list(_openai_clients.values())
    _openai_clients.clear()
    for client in clients:
        await client.close()


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
        body = await read_body(request)
        validate_chat(body)
    except InputError as exc:
        return openai_error(str(exc), "invalid_request", exc.status_code)
    extra_body = body.get("extra_body")
    task_type = extra_body.get("task_type") if isinstance(extra_body, dict) else None

    try:
        profile = _extract_routing(body)
    except RoutingValidationError as exc:
        return openai_error(str(exc), "invalid_request", 400)

    # Legacy media has no RoutingProfile; an explicit scope always applies.
    if task_type in ("asr", "diarization", "ocr") or _is_vision_request(body.get("messages", [])):
        annotate_routing(
            workload=task_type if task_type in ("asr", "diarization", "ocr") else "ocr",
            execution_scope=profile["execution_scope"] if has_routing(body) else None,
        )
        if has_routing(body) and profile["execution_scope"] == "local_only" and DEPLOY_MODE == "serverless":
            return openai_error("local_only forbids remote execution", "forbidden", 403)
        if not task_type:
            extra_body = extra_body if isinstance(extra_body, dict) else {}
            extra_body["task_type"] = "ocr"
            body["extra_body"] = extra_body
        from routes.media import handle_media_request

        return await handle_media_request(body, getattr(request.app.state, "provider_pool", None))

    annotate_routing(workload=profile["workload"], execution_scope=profile["execution_scope"])

    try:
        model = _validate_model(body)
    except RoutingValidationError as exc:
        return openai_error(str(exc), "invalid_request", 400)

    if profile["execution_scope"] == "local_only" and (
        model in _CODEX_MODELS or DEPLOY_MODE == "serverless"
    ):
        return openai_error("local_only forbids remote execution", "forbidden", 403)

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
    deadline = asyncio.get_running_loop().time() + pool.settings.request_timeout_seconds
    attempted: set[str] = set()
    explicit = model in _CODEX_MODELS
    use_explicit = explicit
    fell_back = False
    saw_overload = False

    try:
        async with asyncio.timeout_at(deadline):
            while True:
                _check_deadline(deadline)
                try:
                    if use_explicit:
                        lease = await pool.acquire_explicit("codex")
                    else:
                        route_profile = (
                            {"workload": "chat", "reasoning": "high", "execution_scope": "local_only"}
                            if explicit else profile
                        )
                        lease = await pool.acquire(
                            route_profile["workload"], route_profile["reasoning"],
                            route_profile["execution_scope"], frozenset(attempted),
                        )
                except AdmissionTimeout:
                    return _overloaded()
                except (NoEligibleProvider, NoHealthyProvider):
                    if use_explicit:
                        attempted.add("codex")
                        use_explicit, fell_back = False, True
                        continue
                    if saw_overload:
                        return _overloaded()
                    return openai_error("no eligible provider is available", "unavailable", 503)

                opened, handed_off = None, False
                attempted.add(lease.spec.name)
                effort = CODEX_REASONING_EFFORT_MAP[model] if use_explicit else (
                    "high" if lease.spec.name == "codex" else None
                )
                try:
                    try:
                        _check_deadline(deadline)
                        opened = await _open_upstream(
                            body, base_url=_openai_base(lease.spec.base_url),
                            api_key=lease.spec.api_key, model=lease.spec.model,
                            reasoning_effort=effort,
                            request_timeout=pool.settings.request_timeout_seconds,
                            first_output_timeout=min(pool.settings.first_output_timeout_seconds, deadline - asyncio.get_running_loop().time()),
                        )
                    except Exception as exc:
                        kind, status = _classify_upstream_error(exc)
                        logger.warning("upstream request failed status=%s exception=%s", status, type(exc).__name__)
                        if kind == "invalid_request":
                            return openai_error("upstream rejected the request", kind, status)
                        if kind == "retryable":
                            await pool.record_failure(lease.spec.name)
                        else:
                            saw_overload = True
                        fell_back, use_explicit = True, False
                        continue

                    headers = _provider_headers(lease.spec.name, opened["model"], "error-fallback" if fell_back else lease.reason)
                    if opened["payload"] is not None:
                        await pool.record_success(lease.spec.name)
                        _check_deadline(deadline)
                        return JSONResponse(opened["payload"], headers=headers)
                    response = _stream_response(
                        opened, headers, deadline=deadline,
                        on_success=lambda: pool.record_success(lease.spec.name),
                        on_failure=lambda: pool.record_failure(lease.spec.name),
                        release=lease.release,
                    )
                    handed_off = True
                    return response
                finally:
                    if not handed_off:
                        await _cleanup_stream(opened["upstream"] if opened else None, lease.release)
    except TimeoutError:
        return _timed_out()


async def _handle_serverless(body: dict, model: str, profile: dict[str, str]):
    deadline = asyncio.get_running_loop().time() + _POLICY_SETTINGS.request_timeout_seconds
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
    try:
        async with asyncio.timeout_at(deadline):
            for provider in providers:
                _check_deadline(deadline)
                if not provider.api_base:
                    failed = True
                    continue
                opened, handed_off = None, False
                api_key = CODEX_API_KEY if provider.name == "codex" else RUNPOD_API_KEY
                try:
                    try:
                        reason = "error-fallback" if failed else ("explicit-codex" if explicit else "preferred")
                        annotate_routing(provider=provider.name, reason=reason)
                        opened = await _open_upstream(
                            body, base_url=provider.api_base, api_key=api_key,
                            model=provider.model, reasoning_effort=provider.reasoning_effort,
                            request_timeout=_POLICY_SETTINGS.request_timeout_seconds,
                            first_output_timeout=min(_POLICY_SETTINGS.first_output_timeout_seconds, deadline - asyncio.get_running_loop().time()),
                        )
                    except Exception as exc:
                        kind, status = _classify_upstream_error(exc)
                        logger.warning("upstream request failed status=%s exception=%s", status, type(exc).__name__)
                        if kind == "invalid_request":
                            return openai_error("upstream rejected the request", kind, status)
                        saw_overload |= kind == "overloaded"
                        failed = True
                        continue
                    headers = _provider_headers(provider.name, opened["model"], reason)
                    if opened["payload"] is not None:
                        _check_deadline(deadline)
                        return JSONResponse(opened["payload"], headers=headers)
                    response = _stream_response(opened, headers, deadline=deadline)
                    handed_off = True
                    return response
                finally:
                    if not handed_off:
                        await _cleanup_stream(opened["upstream"] if opened else None)
    except TimeoutError:
        return _timed_out()

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
    annotate_routing(model=model)
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
            first_payload = first.model_dump()
    except BaseException:
        await _cleanup_stream(upstream)
        raise
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
    deadline: float | None = None,
) -> StreamingResponse:
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + _POLICY_SETTINGS.request_timeout_seconds
    cleanup_task = None

    async def cleanup():
        nonlocal cleanup_task
        if cleanup_task is None:
            cleanup_task = asyncio.create_task(_do_cleanup_stream(opened["upstream"], release))
        await _await_cleanup(cleanup_task)

    async def generate() -> AsyncIterator[str]:
        try:
            _check_deadline(deadline)
            yield _sse(opened["first"])
            while True:
                try:
                    async with asyncio.timeout_at(deadline):
                        chunk = await anext(opened["iterator"])
                except StopAsyncIteration:
                    break
                _check_deadline(deadline)
                yield _sse(chunk)
            if on_success:
                async with asyncio.timeout_at(deadline):
                    await on_success()
            _check_deadline(deadline)
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                annotate_error("timed_out")
            if on_failure:
                async with asyncio.timeout_at(deadline):
                    await on_failure()
            logger.error("upstream stream failed after first output exception=%s", type(exc).__name__)
            if isinstance(exc, TimeoutError):
                raise TimeoutError("Gateway stream deadline reached") from None
            raise RuntimeError("upstream stream failed after first output") from None
        finally:
            await cleanup()

    return DeadlineStreamingResponse(generate(), headers, deadline, cleanup)


def _sse(chunk: object) -> str:
    payload = chunk.model_dump() if hasattr(chunk, "model_dump") else chunk
    return f"data: {json.dumps(payload)}\n\n"


def _check_deadline(deadline):
    if asyncio.get_running_loop().time() >= deadline:
        raise TimeoutError


def _timed_out():
    return openai_error("Gateway request deadline exceeded", "timed_out", 503, {"x-should-retry": "false"})


def _provider_headers(provider: str, model: str, reason: str) -> dict[str, str]:
    annotate_routing(provider=provider, reason=reason)
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
