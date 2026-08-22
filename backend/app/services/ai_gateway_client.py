"""AI Gateway를 통한 LLM 요청 클라이언트.

OpenAI SDK를 사용하여 AI Gateway와 통신합니다.
AI Gateway가 RoutingProfile을 배포된 provider로 라우팅합니다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache
from typing import Any, Iterable, Mapping

from openai import (
    OpenAI,
    AsyncOpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
)
from openai.types.chat import ChatCompletionMessageParam

from ..core.config import Settings
from ..core.reasoning import RoutingProfile

logger = logging.getLogger(__name__)


class AIGatewayClientError(RuntimeError):
    """AI Gateway 호출 실패 시 사용하는 예외."""


def _gateway_error_type(body: Any) -> str | None:
    if not isinstance(body, Mapping):
        return None
    error = body.get("error")
    return error.get("type") if isinstance(error, Mapping) else None


def _retry_after_seconds(exc: APIStatusError, fallback: float) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    raw = headers.get("Retry-After")
    try:
        value = float(raw)
        return value if value > 0 else fallback
    except (TypeError, ValueError):
        return fallback


@lru_cache(maxsize=1)
def get_openai_client(base_url: str, api_key: str) -> OpenAI:
    """OpenAI 동기 클라이언트 싱글톤 (캐시됨)."""
    return OpenAI(
        base_url=f"{base_url.rstrip('/')}/v1",
        api_key=api_key,
        timeout=300.0,
    )


@lru_cache(maxsize=1)
def get_async_openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """OpenAI 비동기 클라이언트 싱글톤 (캐시됨)."""
    return AsyncOpenAI(
        base_url=f"{base_url.rstrip('/')}/v1",
        api_key=api_key,
        timeout=300.0,
    )


def _build_messages(
    raw_messages: Iterable[Mapping[str, str]],
) -> list[ChatCompletionMessageParam]:
    """메시지 시퀀스를 리스트로 변환."""
    converted: list[ChatCompletionMessageParam] = []
    for message in raw_messages:
        role = message.get("role")
        content = message.get("content")
        if not role or not content:
            raise AIGatewayClientError("LLM API 메시지에 role/content가 필요합니다.")
        if role == "system":
            converted.append({"role": "system", "content": content})
        elif role == "user":
            converted.append({"role": "user", "content": content})
        elif role == "assistant":
            converted.append({"role": "assistant", "content": content})
        elif role == "developer":
            converted.append({"role": "developer", "content": content})
        else:
            raise AIGatewayClientError(f"지원하지 않는 role입니다: {role}")
    if not converted:
        raise AIGatewayClientError("LLM API 요청 메시지가 비어 있습니다.")
    return converted


def request_ai_gateway_completion(
    *,
    settings: Settings,
    messages: Iterable[Mapping[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
    model: str | None = None,
    routing: RoutingProfile | None = None,
    request_timeout_seconds: float | None = None,
    max_retry_time: int | None = None,
    retry_interval: int | None = None,
) -> str:
    """AI Gateway를 통한 Chat Completion 요청.

    OpenAI SDK를 사용하여 AI Gateway와 통신합니다.
    AI Gateway가 RoutingProfile을 배포된 provider로 라우팅합니다.
    """
    client = get_openai_client(settings.ai_gateway_url, settings.ai_gateway_api_key)

    model_name = model or "auto"
    if model_name == "auto" and routing is None:
        raise AIGatewayClientError("RoutingProfile is required for model=auto")

    logger.info("[AIGateway] Request: model=%s", model_name)
    routing_kwargs = (
        {"extra_body": {"routing": routing}} if routing is not None else {}
    )

    # 재시도 로직 (모델 로딩 대기)
    effective_request_timeout = (
        request_timeout_seconds if request_timeout_seconds is not None else 300.0
    )
    effective_max_retry_time = max_retry_time if max_retry_time is not None else 180
    effective_retry_interval = retry_interval if retry_interval is not None else 3

    if effective_request_timeout <= 0:
        raise AIGatewayClientError("AI Gateway request timeout must be greater than 0")
    if effective_max_retry_time < 0:
        raise AIGatewayClientError("AI Gateway max retry time cannot be negative")
    if effective_retry_interval <= 0:
        raise AIGatewayClientError("AI Gateway retry interval must be greater than 0")

    elapsed = 0

    while elapsed <= effective_max_retry_time:
        try:
            if stream:
                raise AIGatewayClientError(
                    "Streaming response is not supported in this service"
                )

            response = client.chat.completions.create(
                model=model_name,
                messages=_build_messages(messages),
                temperature=temperature
                if temperature is not None
                else settings.llm_temperature,
                max_tokens=max_tokens
                if max_tokens is not None
                else settings.llm_max_tokens,
                stream=False,
                timeout=effective_request_timeout,
                **routing_kwargs,
            )

            # 스트리밍이 아닌 경우 응답 처리
            if not stream:
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason

                # 토큰 한계로 잘린 경우 경고
                if finish_reason == "length" and content:
                    logger.warning(
                        "[AIGateway] Response truncated (finish_reason='length'): "
                        "content_length=%d, consider increasing max_tokens",
                        len(content),
                    )

                if not content:
                    # reasoning 필드 확인 (일부 모델)
                    reasoning = getattr(response.choices[0].message, "reasoning", None)
                    if reasoning:
                        logger.info(
                            "[AIGateway] Response: using reasoning (length: %d)",
                            len(reasoning),
                        )
                        return reasoning[:200] if len(reasoning) > 200 else reasoning

                    if finish_reason == "length":
                        logger.warning(
                            "[AIGateway] Response: content empty, finish_reason='length'"
                        )
                        return "Response generation in progress (token limit)"

                    raise AIGatewayClientError("AI Gateway response content is empty")

                logger.info(
                    "[AIGateway] Response received (length: %d, finish_reason: %s)",
                    len(content),
                    finish_reason,
                )
                return content.strip()

        except APIStatusError as exc:
            if (
                exc.status_code == 503
                and _gateway_error_type(getattr(exc, "body", None)) == "overloaded"
            ):
                delay = _retry_after_seconds(exc, effective_retry_interval)
                if elapsed + delay > effective_max_retry_time:
                    break
                logger.info(
                    "[AIGateway] Providers overloaded, retrying in %ss (%ss)",
                    delay,
                    elapsed,
                )
                time.sleep(delay)
                elapsed += delay
                continue

            error_msg = f"AI Gateway HTTP error ({exc.status_code}): {exc.message}"
            logger.error(error_msg)
            raise AIGatewayClientError(error_msg) from exc

        except APITimeoutError as exc:
            error_msg = f"AI Gateway timeout: {exc}"
            logger.error(error_msg)
            raise AIGatewayClientError(error_msg) from exc

        except APIConnectionError as exc:
            if elapsed + effective_retry_interval <= effective_max_retry_time:
                logger.warning(
                    "[AIGateway] Connection error (will retry): %s (%ds)", exc, elapsed
                )
                time.sleep(effective_retry_interval)
                elapsed += effective_retry_interval
                continue
            error_msg = f"AI Gateway connection error (retry timeout): {exc}"
            logger.error(error_msg)
            raise AIGatewayClientError(error_msg) from exc

        except Exception as exc:
            error_msg = f"AI Gateway request failed: {exc}"
            logger.error(error_msg)
            raise AIGatewayClientError(error_msg) from exc

    raise AIGatewayClientError(
        f"AI Gateway request timed out after {effective_max_retry_time} seconds"
    )


async def request_ai_gateway_completion_async(
    *,
    settings: Settings,
    messages: Iterable[Mapping[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
    model: str | None = None,
    routing: RoutingProfile | None = None,
    request_timeout_seconds: float | None = None,
    max_retry_time: int | None = None,
    retry_interval: int | None = None,
) -> str:
    """AI Gateway를 통한 비동기 Chat Completion 요청.

    AsyncOpenAI SDK를 사용하여 AI Gateway와 통신합니다.
    이벤트 루프를 블로킹하지 않으므로 FastAPI와 함께 사용하기에 적합합니다.

    모든 auto 요청은 AI Gateway가 RoutingProfile로 provider를 선택한다.
    """
    client = get_async_openai_client(
        settings.ai_gateway_url, settings.ai_gateway_api_key
    )

    model_name = model or "auto"
    if model_name == "auto" and routing is None:
        raise AIGatewayClientError("RoutingProfile is required for model=auto")

    logger.info(
        "[AI Gateway/Async] Request: model=%s, base=%s",
        model_name,
        settings.ai_gateway_url,
    )
    routing_kwargs = (
        {"extra_body": {"routing": routing}} if routing is not None else {}
    )

    # 재시도 로직 (모델 로딩 대기)
    effective_request_timeout = (
        request_timeout_seconds if request_timeout_seconds is not None else 300.0
    )
    effective_max_retry_time = max_retry_time if max_retry_time is not None else 180
    effective_retry_interval = retry_interval if retry_interval is not None else 3

    if effective_request_timeout <= 0:
        raise AIGatewayClientError("AI Gateway request timeout must be greater than 0")
    if effective_max_retry_time < 0:
        raise AIGatewayClientError("AI Gateway max retry time cannot be negative")
    if effective_retry_interval <= 0:
        raise AIGatewayClientError("AI Gateway retry interval must be greater than 0")

    elapsed = 0

    while elapsed <= effective_max_retry_time:
        try:
            if stream:
                raise AIGatewayClientError(
                    "Streaming response is not supported in this service"
                )

            response = await client.chat.completions.create(
                model=model_name,
                messages=_build_messages(messages),
                temperature=temperature
                if temperature is not None
                else settings.llm_temperature,
                max_tokens=max_tokens
                if max_tokens is not None
                else settings.llm_max_tokens,
                stream=False,
                timeout=effective_request_timeout,
                **routing_kwargs,
            )

            # 스트리밍이 아닌 경우 응답 처리
            if not stream:
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason

                # 토큰 한계로 잘린 경우 경고
                if finish_reason == "length" and content:
                    logger.warning(
                        "[AI Gateway/Async] Response truncated (finish_reason='length'): "
                        "content_length=%d, consider increasing max_tokens",
                        len(content),
                    )

                if not content:
                    # reasoning 필드 확인 (일부 모델)
                    reasoning = getattr(response.choices[0].message, "reasoning", None)
                    if reasoning:
                        logger.info(
                            "[AI Gateway/Async] Response: using reasoning (length: %d)",
                            len(reasoning),
                        )
                        return reasoning[:200] if len(reasoning) > 200 else reasoning

                    if finish_reason == "length":
                        logger.warning(
                            "[AI Gateway/Async] Response: content empty, finish_reason='length'"
                        )
                        return "Response generation in progress (token limit)"

                    raise AIGatewayClientError("AI Gateway response content is empty")

                logger.info(
                    "[AI Gateway/Async] Response received (length: %d, finish_reason: %s)",
                    len(content),
                    finish_reason,
                )
                return content.strip()

        except APIStatusError as exc:
            if (
                exc.status_code == 503
                and _gateway_error_type(getattr(exc, "body", None)) == "overloaded"
            ):
                delay = _retry_after_seconds(exc, effective_retry_interval)
                if elapsed + delay > effective_max_retry_time:
                    break
                logger.info(
                    "[AI Gateway/Async] Providers overloaded, retrying in %ss (%ss)",
                    delay,
                    elapsed,
                )
                await asyncio.sleep(delay)
                elapsed += delay
                continue
            error_msg = f"AI Gateway HTTP error ({exc.status_code}): {exc.message}"
            logger.error(error_msg)
            raise AIGatewayClientError(error_msg) from exc

        except APITimeoutError as exc:
            error_msg = f"AI Gateway timeout: {exc}"
            logger.error(error_msg)
            raise AIGatewayClientError(error_msg) from exc

        except APIConnectionError as exc:
            if elapsed + effective_retry_interval <= effective_max_retry_time:
                logger.warning(
                    "[AI Gateway/Async] Connection error (will retry): %s (%ds)",
                    exc,
                    elapsed,
                )
                await asyncio.sleep(effective_retry_interval)
                elapsed += effective_retry_interval
                continue
            error_msg = f"AI Gateway connection error (retry timeout): {exc}"
            logger.error(error_msg)
            raise AIGatewayClientError(error_msg) from exc

        except Exception as exc:
            error_msg = f"AI Gateway request failed: {exc}"
            logger.error(error_msg)
            raise AIGatewayClientError(error_msg) from exc

    raise AIGatewayClientError(
        f"AI Gateway request timed out after {effective_max_retry_time} seconds"
    )
