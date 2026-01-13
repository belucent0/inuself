"""LiteLLM 프록시를 통한 LLM 요청 클라이언트.

OpenAI SDK를 사용하여 LiteLLM 프록시와 통신합니다.
GPU/NPU 자원 상태에 따라 LiteLLM이 자동으로 라우팅합니다.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Iterable, Mapping

from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError

from ..core.config import Settings

logger = logging.getLogger(__name__)


class LiteLLMClientError(RuntimeError):
    """LiteLLM 프록시 호출 실패 시 사용하는 예외."""


@lru_cache(maxsize=1)
def get_openai_client(base_url: str, api_key: str) -> OpenAI:
    """OpenAI 클라이언트 싱글톤 (캐시됨)."""
    return OpenAI(
        base_url=f"{base_url.rstrip('/')}/v1",
        api_key=api_key,
        timeout=120.0,
    )


def _build_messages(raw_messages: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    """메시지 시퀀스를 리스트로 변환."""
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
    
    OpenAI SDK를 사용하여 LiteLLM 프록시와 통신합니다.
    LiteLLM이 GPU/NPU 자원 상태에 따라 자동으로 라우팅합니다.
    """
    client = get_openai_client(settings.litellm_base_url, settings.litellm_api_key)
    
    logger.info(
        "[LiteLLM] Request: model=%s", 
        settings.litellm_model
    )
    
    # 재시도 로직 (모델 로딩 대기)
    max_retry_time = 180
    retry_interval = 3
    elapsed = 0
    
    while elapsed < max_retry_time:
        try:
            response = client.chat.completions.create(
                model=settings.litellm_model,
                messages=_build_messages(messages),
                temperature=temperature if temperature is not None else settings.llm_temperature,
                max_tokens=max_tokens if max_tokens is not None else settings.llm_max_tokens,
                stream=stream,
            )
            
            # 스트리밍이 아닌 경우 응답 처리
            if not stream:
                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason
                
                if not content:
                    # reasoning 필드 확인 (일부 모델)
                    reasoning = getattr(response.choices[0].message, 'reasoning', None)
                    if reasoning:
                        logger.info("[LiteLLM] Response: using reasoning (length: %d)", len(reasoning))
                        return reasoning[:200] if len(reasoning) > 200 else reasoning
                    
                    if finish_reason == "length":
                        logger.warning("[LiteLLM] Response: content empty, finish_reason='length'")
                        return "Response generation in progress (token limit)"
                    
                    raise LiteLLMClientError("LiteLLM response content is empty")
                
                logger.info(
                    "[LiteLLM] Response received (length: %d, finish_reason: %s)", 
                    len(content), 
                    finish_reason
                )
                return content.strip()
            
            # 스트리밍인 경우 청크 합치기
            chunks = []
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    chunks.append(chunk.choices[0].delta.content)
            return "".join(chunks).strip()
            
        except APIStatusError as exc:
            # 503 에러: 모델 로딩 중
            if exc.status_code == 503:
                error_body = getattr(exc, 'body', {}) or {}
                error_message = str(error_body)
                if "Loading" in error_message or "busy" in error_message.lower():
                    logger.info("[LiteLLM] Providers busy or loading, waiting... (%ds)", elapsed)
                    time.sleep(retry_interval)
                    elapsed += retry_interval
                    continue
            
            error_msg = f"LiteLLM HTTP error ({exc.status_code}): {exc.message}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc
            
        except APIConnectionError as exc:
            if elapsed < max_retry_time - retry_interval:
                logger.warning("[LiteLLM] Connection error (will retry): %s (%ds)", exc, elapsed)
                time.sleep(retry_interval)
                elapsed += retry_interval
                continue
            error_msg = f"LiteLLM connection error (retry timeout): {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc
            
        except APITimeoutError as exc:
            error_msg = f"LiteLLM timeout: {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc
            
        except Exception as exc:
            error_msg = f"LiteLLM request failed: {exc}"
            logger.error(error_msg)
            raise LiteLLMClientError(error_msg) from exc
    
    raise LiteLLMClientError(f"LiteLLM request timed out after {max_retry_time} seconds")
