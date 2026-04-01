"""LiteLLM 프록시를 통한 LLM 요청 클라이언트.

OpenAI SDK를 사용하여 LiteLLM 프록시와 통신합니다.
GPU/NPU 자원 상태에 따라 LiteLLM이 자동으로 라우팅합니다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache
from typing import Iterable, Mapping

from openai import (
    OpenAI,
    AsyncOpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
)
from openai.types.chat import ChatCompletionMessageParam

from ..core.config import Settings

logger = logging.getLogger(__name__)


class LiteLLMClientError(RuntimeError):
    """LiteLLM 프록시 호출 실패 시 사용하는 예외."""


@lru_cache(maxsize=1)
def get_openai_client(base_url: str, api_key: str) -> OpenAI:
    """OpenAI 동기 클라이언트 싱글톤 (캐시됨)."""
    return OpenAI(
        base_url=f"{base_url.rstrip('/')}/v1",
        api_key=api_key,
        timeout=120.0,
    )


@lru_cache(maxsize=1)
def get_async_openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """OpenAI 비동기 클라이언트 싱글톤 (캐시됨)."""
    return AsyncOpenAI(
        base_url=f"{base_url.rstrip('/')}/v1",
        api_key=api_key,
        timeout=120.0,
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
            raise LiteLLMClientError("LLM API 메시지에 role/content가 필요합니다.")
        if role == "system":
            converted.append({"role": "system", "content": content})
        elif role == "user":
            converted.append({"role": "user", "content": content})
        elif role == "assistant":
            converted.append({"role": "assistant", "content": content})
        elif role == "developer":
            converted.append({"role": "developer", "content": content})
        else:
            raise LiteLLMClientError(f"지원하지 않는 role입니다: {role}")
    if not converted:
        raise LiteLLMClientError("LLM API 요청 메시지가 비어 있습니다.")
    return converted


def request_litellm_completion(
    *,
    settings: Settings,
    messages: Iterable[Mapping[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
    model: str | None = None,
    request_timeout_seconds: float | None = None,
    max_retry_time: int | None = None,
    retry_interval: int | None = None,
) -> str:
    """LiteLLM 프록시를 통한 Chat Completion 요청.

    OpenAI SDK를 사용하여 LiteLLM 프록시와 통신합니다.
    LiteLLM이 GPU/NPU 자원 상태에 따라 자동으로 라우팅합니다.
    """
    client = get_openai_client(settings.ai_gateway_url, settings.ai_gateway_api_key)

    model_name = model or settings.ai_gateway_model

    logger.info("[LiteLLM] Request: model=%s", model_name)

    # 재시도 로직 (모델 로딩 대기)
    effective_request_timeout = (
        request_timeout_seconds if request_timeout_seconds is not None else 120.0
    )
    effective_max_retry_time = max_retry_time if max_retry_time is not None else 180
    effective_retry_interval = retry_interval if retry_interval is not None else 3

    if effective_request_timeout <= 0:
        raise LiteLLMClientError("LiteLLM request timeout must be greater than 0")
    if effective_max_retry_time < 0:
        raise LiteLLMClientError("LiteLLM max retry time cannot be negative")
    if effective_retry_interval <= 0:
        raise LiteLLMClientError("LiteLLM retry interval must be greater than 0")

    elapsed = 0

    while elapsed <= effective_max_retry_time:
        try:
            if stream:
                raise LiteLLMClientError(
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
            )

            # 스트리밍이 아닌 경우 응답 처리
            if not stream:
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason

                # 토큰 한계로 잘린 경우 경고
                if finish_reason == "length" and content:
                    logger.warning(
                        "[LiteLLM] Response truncated (finish_reason='length'): "
                        "content_length=%d, consider increasing max_tokens",
                        len(content),
                    )

                if not content:
                    # reasoning 필드 확인 (일부 모델)
                    reasoning = getattr(response.choices[0].message, "reasoning", None)
                    if reasoning:
                        logger.info(
                            "[LiteLLM] Response: using reasoning (length: %d)",
                            len(reasoning),
                        )
                        return reasoning[:200] if len(reasoning) > 200 else reasoning

                    if finish_reason == "length":
                        logger.warning(
                            "[LiteLLM] Response: content empty, finish_reason='length'"
                        )
                        return "Response generation in progress (token limit)"

                    raise LiteLLMClientError("LiteLLM response content is empty")

                logger.info(
                    "[LiteLLM] Response received (length: %d, finish_reason: %s)",
                    len(content),
                    finish_reason,
                )
                return content.strip()

        except APIStatusError as exc:
            # 503 에러: 모델 로딩 중
            if exc.status_code == 503:
                error_body = getattr(exc, "body", {}) or {}
                error_message = str(error_body)
                if "Loading" in error_message or "busy" in error_message.lower():
                    if elapsed + effective_retry_interval > effective_max_retry_time:
                        break
                    logger.info(
                        "[LiteLLM] Providers busy or loading, waiting... (%ds)", elapsed
                    )
                    time.sleep(effective_retry_interval)
                    elapsed += effective_retry_interval
                    continue

            error_msg = f"LiteLLM HTTP error ({exc.status_code}): {exc.message}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc

        except APITimeoutError as exc:
            error_msg = f"LiteLLM timeout: {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc

        except APIConnectionError as exc:
            if elapsed + effective_retry_interval <= effective_max_retry_time:
                logger.warning(
                    "[LiteLLM] Connection error (will retry): %s (%ds)", exc, elapsed
                )
                time.sleep(effective_retry_interval)
                elapsed += effective_retry_interval
                continue
            error_msg = f"LiteLLM connection error (retry timeout): {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc

        except Exception as exc:
            error_msg = f"LiteLLM request failed: {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc

    raise LiteLLMClientError(
        f"LiteLLM request timed out after {effective_max_retry_time} seconds"
    )


async def request_litellm_completion_async(
    *,
    settings: Settings,
    messages: Iterable[Mapping[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
    model: str | None = None,
    request_timeout_seconds: float | None = None,
    max_retry_time: int | None = None,
    retry_interval: int | None = None,
) -> str:
    """LiteLLM 프록시를 통한 비동기 Chat Completion 요청.

    AsyncOpenAI SDK를 사용하여 LiteLLM 프록시와 통신합니다.
    이벤트 루프를 블로킹하지 않으므로 FastAPI와 함께 사용하기에 적합합니다.
    """
    client = get_async_openai_client(
        settings.ai_gateway_url, settings.ai_gateway_api_key
    )

    model_name = model or settings.ai_gateway_model

    logger.info("[LiteLLM/Async] Request: model=%s", model_name)

    # 재시도 로직 (모델 로딩 대기)
    effective_request_timeout = (
        request_timeout_seconds if request_timeout_seconds is not None else 120.0
    )
    effective_max_retry_time = max_retry_time if max_retry_time is not None else 180
    effective_retry_interval = retry_interval if retry_interval is not None else 3

    if effective_request_timeout <= 0:
        raise LiteLLMClientError("LiteLLM request timeout must be greater than 0")
    if effective_max_retry_time < 0:
        raise LiteLLMClientError("LiteLLM max retry time cannot be negative")
    if effective_retry_interval <= 0:
        raise LiteLLMClientError("LiteLLM retry interval must be greater than 0")

    elapsed = 0

    while elapsed <= effective_max_retry_time:
        try:
            if stream:
                raise LiteLLMClientError(
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
            )

            # 스트리밍이 아닌 경우 응답 처리
            if not stream:
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason

                # 토큰 한계로 잘린 경우 경고
                if finish_reason == "length" and content:
                    logger.warning(
                        "[LiteLLM/Async] Response truncated (finish_reason='length'): "
                        "content_length=%d, consider increasing max_tokens",
                        len(content),
                    )

                if not content:
                    # reasoning 필드 확인 (일부 모델)
                    reasoning = getattr(response.choices[0].message, "reasoning", None)
                    if reasoning:
                        logger.info(
                            "[LiteLLM/Async] Response: using reasoning (length: %d)",
                            len(reasoning),
                        )
                        return reasoning[:200] if len(reasoning) > 200 else reasoning

                    if finish_reason == "length":
                        logger.warning(
                            "[LiteLLM/Async] Response: content empty, finish_reason='length'"
                        )
                        return "Response generation in progress (token limit)"

                    raise LiteLLMClientError("LiteLLM response content is empty")

                logger.info(
                    "[LiteLLM/Async] Response received (length: %d, finish_reason: %s)",
                    len(content),
                    finish_reason,
                )
                return content.strip()

        except APIStatusError as exc:
            # 503 에러: 모델 로딩 중
            if exc.status_code == 503:
                error_body = getattr(exc, "body", {}) or {}
                error_message = str(error_body)
                if "Loading" in error_message or "busy" in error_message.lower():
                    if elapsed + effective_retry_interval > effective_max_retry_time:
                        break
                    logger.info(
                        "[LiteLLM/Async] Providers busy or loading, waiting... (%ds)",
                        elapsed,
                    )
                    await asyncio.sleep(effective_retry_interval)
                    elapsed += effective_retry_interval
                    continue

            error_msg = f"LiteLLM HTTP error ({exc.status_code}): {exc.message}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc

        except APITimeoutError as exc:
            error_msg = f"LiteLLM timeout: {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc

        except APIConnectionError as exc:
            if elapsed + effective_retry_interval <= effective_max_retry_time:
                logger.warning(
                    "[LiteLLM/Async] Connection error (will retry): %s (%ds)",
                    exc,
                    elapsed,
                )
                await asyncio.sleep(effective_retry_interval)
                elapsed += effective_retry_interval
                continue
            error_msg = f"LiteLLM connection error (retry timeout): {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc

        except Exception as exc:
            error_msg = f"LiteLLM request failed: {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc

    raise LiteLLMClientError(
        f"LiteLLM request timed out after {effective_max_retry_time} seconds"
    )
