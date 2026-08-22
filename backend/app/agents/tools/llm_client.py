"""LLM 클라이언트 (비동기).

LangGraph 노드에서 사용할 비동기 LLM 호출 함수를 제공합니다.
AI Gateway를 통해 RoutingProfile에 맞는 provider로 라우팅됩니다.
"""
from __future__ import annotations

import asyncio
from loguru import logger
from functools import lru_cache
from typing import Any, AsyncIterator, Iterable, Mapping

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError

from ...core.reasoning import RoutingProfile, routing_profile
from ...services.ai_gateway_client import _gateway_error_type, _retry_after_seconds



class LLMClientError(RuntimeError):
    """LLM 클라이언트 호출 실패 예외."""


async def _create_with_overload_retry(client: AsyncOpenAI, **kwargs):
    for attempt in range(3):
        try:
            return await client.chat.completions.create(**kwargs)
        except APIStatusError as exc:
            overloaded = (
                exc.status_code == 503
                and _gateway_error_type(getattr(exc, "body", None)) == "overloaded"
            )
            if not overloaded or attempt == 2:
                raise
            delay = min(_retry_after_seconds(exc, 5.0), 30.0)
            logger.info(
                "[LLM] Providers overloaded, retrying in {}s ({}/2)",
                delay,
                attempt + 1,
            )
            await asyncio.sleep(delay)


@lru_cache(maxsize=1)
def _get_async_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """AsyncOpenAI 클라이언트 싱글톤."""
    return AsyncOpenAI(
        base_url=f"{base_url.rstrip('/')}/v1",
        api_key=api_key,
        timeout=120.0,
    )


def _build_messages(raw_messages: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    """메시지 시퀀스를 리스트로 변환."""
    converted: list[dict[str, str]] = []
    for idx, message in enumerate(raw_messages):
        # 디버깅: 메시지 타입 로깅
        logger.debug(f"[_build_messages] Message {idx}: type={type(message)}, value={message}")

        role = message.get("role")
        content = message.get("content")

        # content가 리스트인 경우 문자열로 변환
        if isinstance(content, list):
            logger.warning(f"[_build_messages] Message {idx} has list content, converting to string")
            # OpenAI multimodal format: [{"type": "text", "text": "..."}, ...]
            if content and isinstance(content[0], dict) and "text" in content[0]:
                content = content[0]["text"]
            else:
                content = str(content)

        if not role or not content:
            raise LLMClientError(f"LLM API 메시지 {idx}에 role/content가 필요합니다. role={role}, content type={type(content)}")

        converted.append({"role": role, "content": content})

    if not converted:
        raise LLMClientError("LLM API 요청 메시지가 비어 있습니다.")
    return converted


async def async_llm_completion(
    *,
    settings: Any,
    messages: Iterable[Mapping[str, str]],
    model: str | None = None,
    routing: RoutingProfile | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """비동기 LLM 완성 요청.

    Args:
        settings: 애플리케이션 설정
        messages: 대화 메시지 목록
        model: 사용할 LLM 모델명 (None이면 auto)
        routing: Gateway RoutingProfile (미지정 시 chat/none/local_only)
        temperature: 온도 (기본값: settings에서 가져옴)
        max_tokens: 최대 토큰 수 (기본값: settings에서 가져옴)

    Returns:
        LLM 응답 텍스트

    Raises:
        LLMClientError: LLM 호출 실패
    """
    client = _get_async_client(settings.ai_gateway_url, settings.ai_gateway_api_key)

    actual_model = model or "auto"
    profile = routing or routing_profile("chat", "none")
    logger.info(f"[LLM] Async request: model={actual_model}")

    try:
        response = await _create_with_overload_retry(
            client,
            model=actual_model,
            messages=_build_messages(messages),
            temperature=temperature if temperature is not None else getattr(settings, 'llm_temperature', 0.7),
            max_tokens=max_tokens if max_tokens is not None else getattr(settings, 'llm_max_tokens', 2048),
            stream=False,
            extra_body={"routing": profile},
        )

        content = response.choices[0].message.content
        if not content:
            raise LLMClientError("LLM response content is empty")

        logger.info(f"[LLM] Response received: {len(content)} chars")
        return content.strip()

    except APIStatusError as exc:
        error_msg = f"LLM HTTP error ({exc.status_code}): {exc.message}"
        logger.error(error_msg)
        raise LLMClientError(error_msg) from exc

    except APIConnectionError as exc:
        error_msg = f"LLM connection error: {exc}"
        logger.error(error_msg)
        raise LLMClientError(error_msg) from exc

    except APITimeoutError as exc:
        error_msg = f"LLM timeout: {exc}"
        logger.error(error_msg)
        raise LLMClientError(error_msg) from exc

    except Exception as exc:
        import traceback
        error_msg = f"LLM request failed: {exc}"
        logger.error(f"{error_msg}\nTraceback:\n{traceback.format_exc()}")
        raise LLMClientError(error_msg) from exc


async def async_llm_completion_stream(
    *,
    settings: Any,
    messages: Iterable[Mapping[str, str]],
    model: str | None = None,
    routing: RoutingProfile | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    """비동기 스트리밍 LLM 완성 요청.

    Args:
        settings: 애플리케이션 설정
        messages: 대화 메시지 목록
        model: 사용할 LLM 모델명 (None이면 auto)
        routing: Gateway RoutingProfile (미지정 시 chat/none/local_only)
        temperature: 온도
        max_tokens: 최대 토큰 수

    Yields:
        LLM 응답 청크

    Raises:
        LLMClientError: LLM 호출 실패
    """
    client = _get_async_client(settings.ai_gateway_url, settings.ai_gateway_api_key)

    actual_model = model or "auto"
    profile = routing or routing_profile("chat", "none")
    logger.info(f"[LLM] Async stream request: model={actual_model}")

    try:
        response = await _create_with_overload_retry(
            client,
            model=actual_model,
            messages=_build_messages(messages),
            temperature=temperature if temperature is not None else getattr(settings, 'llm_temperature', 0.7),
            max_tokens=max_tokens if max_tokens is not None else getattr(settings, 'llm_max_tokens', 2048),
            stream=True,
            extra_body={"routing": profile},
        )

        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    except APIStatusError as exc:
        error_msg = f"LLM HTTP error ({exc.status_code}): {exc.message}"
        logger.error(error_msg)
        raise LLMClientError(error_msg) from exc

    except APIConnectionError as exc:
        error_msg = f"LLM connection error: {exc}"
        logger.error(error_msg)
        raise LLMClientError(error_msg) from exc

    except APITimeoutError as exc:
        error_msg = f"LLM timeout: {exc}"
        logger.error(error_msg)
        raise LLMClientError(error_msg) from exc

    except Exception as exc:
        import traceback
        error_msg = f"LLM request failed: {exc}"
        logger.error(f"{error_msg}\nTraceback:\n{traceback.format_exc()}")
        raise LLMClientError(error_msg) from exc
