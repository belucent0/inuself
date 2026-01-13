"""LiteLLM 프록시를 통한 LLM 요청 클라이언트.

GPU/NPU 자원 상태에 따라 자동으로 라우팅되는 LiteLLM 프록시를 통해
LLM 요청을 처리합니다.

백엔드(Docker)에서 LiteLLM 프록시(Docker)로 직접 호출합니다.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable, Mapping

import httpx

from ..core.config import Settings

logger = logging.getLogger(__name__)


class LiteLLMClientError(RuntimeError):
    """LiteLLM 프록시 호출 실패 시 사용하는 예외."""


def _build_messages(raw_messages: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    """httpx가 직렬화할 수 있도록 메시지 시퀀스를 리스트로 변환."""
    converted: list[dict[str, str]] = []
    for message in raw_messages:
        role = message.get("role")
        content = message.get("content")
        if not role or not content:
            raise LiteLLMClientError("LLM API 메시지에 role/content가 필요합니다.")
        converted.append({"role": role, "content": content})
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
) -> str:
    """LiteLLM 프록시를 통한 Chat Completion 요청.
    
    LiteLLM 프록시가 GPU/NPU 자원 상태에 따라 자동으로 라우팅합니다.
    """
    base_url = settings.litellm_base_url.rstrip("/")
    url = f"{base_url}/v1/chat/completions"
    
    payload = {
        "model": settings.litellm_model,
        "messages": _build_messages(messages),
        "temperature": temperature if temperature is not None else settings.llm_temperature,
        "max_tokens": max_tokens if max_tokens is not None else settings.llm_max_tokens,
        "stream": stream,
    }
    
    headers = {
        "Authorization": f"Bearer {settings.litellm_api_key}",
        "Content-Type": "application/json",
    }
    
    logger.info(
        "[LiteLLM] Request: url=%s model=%s", 
        url, 
        settings.litellm_model
    )
    
    # 모델 로드 대기를 위한 재시도 로직
    max_retry_time = 180
    retry_interval = 3
    elapsed = 0
    result = None
    
    while elapsed < max_retry_time:
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(url, json=payload, headers=headers)
            
            # 503 에러인 경우 재시도
            if response.status_code == 503:
                try:
                    error_body = response.json()
                    error_message = error_body.get("error", {}).get("message", "")
                    if "Loading" in error_message or "busy" in error_message.lower():
                        logger.info("[LiteLLM] Providers busy or loading, waiting... (%ds)", elapsed)
                        time.sleep(retry_interval)
                        elapsed += retry_interval
                        continue
                except Exception:
                    pass
            
            response.raise_for_status()
            result = response.json()
            break
            
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 503:
                try:
                    error_body = exc.response.json()
                    error_message = error_body.get("error", {}).get("message", "")
                    if "Loading" in error_message or "busy" in error_message.lower():
                        logger.info("[LiteLLM] Providers busy or loading, waiting... (%ds)", elapsed)
                        time.sleep(retry_interval)
                        elapsed += retry_interval
                        continue
                except Exception:
                    pass
            
            error_msg = f"LiteLLM HTTP error ({exc.response.status_code})"
            try:
                error_body = exc.response.json()
                error_msg = f"{error_msg}: {error_body}"
            except Exception:
                pass
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc
            
        except (httpx.ReadError, httpx.ConnectError) as exc:
            if elapsed < max_retry_time - retry_interval:
                logger.warning("[LiteLLM] Connection error (will retry): %s (%ds)", exc, elapsed)
                time.sleep(retry_interval)
                elapsed += retry_interval
                continue
            error_msg = f"LiteLLM connection error (retry timeout): {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc
            
        except Exception as exc:
            error_msg = f"LiteLLM request failed: {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc
    
    if elapsed >= max_retry_time:
        raise LiteLLMClientError(f"LiteLLM request timed out after {max_retry_time} seconds")
    
    if result is None:
        raise LiteLLMClientError("No result received from LiteLLM proxy")
    
    choices = result.get("choices")
    if not choices:
        raise LiteLLMClientError("LiteLLM response has no choices")
    
    message = choices[0].get("message") or {}
    content = message.get("content", "").strip()
    finish_reason = choices[0].get("finish_reason", "")
    
    if not content:
        # reasoning이 있으면 사용
        reasoning = message.get("reasoning", "").strip()
        if reasoning:
            logger.info("[LiteLLM] Response: content empty but reasoning exists (length: %d)", len(reasoning))
            return reasoning[:200] if len(reasoning) > 200 else reasoning
        
        if finish_reason == "length":
            logger.warning("[LiteLLM] Response: content empty, finish_reason='length'")
            return "Response generation in progress (token limit)"
        
        raise LiteLLMClientError("LiteLLM response content is empty")
    
    logger.info(
        "[LiteLLM] Response received (length: %d chars, finish_reason: %s)", 
        len(content), 
        finish_reason
    )
    return content
