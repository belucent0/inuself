"""Shared upstream failure classification and cancellation-safe cleanup."""

import asyncio
import inspect
import logging

import anyio
import httpx
import openai

logger = logging.getLogger(__name__)
_STREAM_CLOSE_TIMEOUT = 5


async def _close_stream(stream: object | None) -> None:
    if stream is None:
        return
    closer = getattr(stream, "close", None) or getattr(stream, "aclose", None)
    if closer:
        try:
            result = closer()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.debug("failed to close upstream stream exception=%s", type(exc).__name__)


async def _cleanup_stream(stream, release=None):
    await _await_cleanup(asyncio.create_task(_do_cleanup_stream(stream, release)))


async def _await_cleanup(task):
    cancellation = None
    with anyio.CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancellation = exc
    task.result()
    if cancellation is not None:
        raise cancellation


async def _do_cleanup_stream(stream, release=None):
    with anyio.CancelScope(shield=True):
        try:
            if stream is not None:
                await asyncio.wait_for(_close_stream(stream), _STREAM_CLOSE_TIMEOUT)
        except TimeoutError:
            logger.debug("upstream stream close timed out")
        finally:
            if release:
                await release()


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
