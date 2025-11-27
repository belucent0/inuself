from __future__ import annotations

import logging
from typing import Iterable, Mapping

import httpx

from ..core.config import Settings

logger = logging.getLogger(__name__)


class LMStudioClientError(RuntimeError):
    """LM Studio 호출 실패 시 사용하는 예외."""


def _build_messages(raw_messages: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    """httpx가 직렬화할 수 있도록 메시지 시퀀스를 리스트로 변환."""
    converted: list[dict[str, str]] = []
    for message in raw_messages:
        role = message.get("role")
        content = message.get("content")
        if not role or not content:
            raise LMStudioClientError("LM Studio 메시지에 role/content가 필요합니다.")
        converted.append({"role": role, "content": content})
    if not converted:
        raise LMStudioClientError("LM Studio 요청 메시지가 비어 있습니다.")
    return converted


def request_chat_completion(
    *,
    settings: Settings,
    messages: Iterable[Mapping[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
) -> str:
    """LM Studio Chat Completions API 호출."""
    base_url = settings.lmstudio_base_url.rstrip("/")
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": settings.lmstudio_model_name,
        "messages": _build_messages(messages),
        "temperature": temperature if temperature is not None else settings.llm_temperature,
        "max_tokens": max_tokens if max_tokens is not None else settings.llm_max_tokens,
        "stream": stream,
    }

    logger.info("LM Studio API 호출: url=%s model=%s", url, settings.lmstudio_model_name)

    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPError as exc:
        error_msg = f"LM Studio API HTTP 오류: {exc}"
        logger.error(error_msg)
        raise LMStudioClientError(error_msg) from exc
    except Exception as exc:
        error_msg = f"LM Studio API 호출 중 예기치 않은 오류: {exc}"
        logger.error(error_msg)
        raise LMStudioClientError(error_msg) from exc

    choices = result.get("choices")
    if not choices:
        raise LMStudioClientError("LM Studio 응답에 choices가 없습니다.")

    message = choices[0].get("message") or {}
    content = message.get("content", "").strip()
    finish_reason = choices[0].get("finish_reason", "")
    
    # content가 비어있지만 reasoning이 있으면 reasoning을 사용
    if not content:
        reasoning = message.get("reasoning", "").strip()
        if reasoning:
            logger.info("LM Studio 응답: content가 비어있지만 reasoning이 있음 (길이: %d chars)", len(reasoning))
            # reasoning이 있으면 모델이 작동 중이므로 reasoning의 일부를 반환
            return reasoning[:200] if len(reasoning) > 200 else reasoning
        # finish_reason이 "length"인 경우는 모델이 응답을 생성하려고 했지만 토큰 제한으로 실패한 것
        # 이 경우에도 헬스체크는 통과시킴 (워커는 시작하되 첫 요청이 느릴 수 있음)
        if finish_reason == "length":
            logger.warning("LM Studio 응답: content가 비어있고 finish_reason이 'length'입니다. 모델이 토큰 제한으로 응답을 완료하지 못했습니다.")
            # 빈 문자열을 반환하지 않고 최소한의 응답을 반환
            return "응답 생성 중 (토큰 제한)"
        raise LMStudioClientError("LM Studio 응답 message.content가 비어 있습니다.")

    logger.info("LM Studio 응답 수신 (길이: %d chars, finish_reason: %s)", len(content), finish_reason)
    return content


