"""Langfuse LLM Observability 연동 모듈.

V8.1: LLM 호출의 Semantic Observability를 위한 Langfuse 통합.
프롬프트/응답 전문, 토큰 사용량, 비용 추적을 제공합니다.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from langfuse.callback import CallbackHandler


# Langfuse 초기화 상태 캐싱
_langfuse_enabled: bool | None = None


def is_langfuse_enabled() -> bool:
    """Langfuse 활성화 여부 확인."""
    global _langfuse_enabled

    if _langfuse_enabled is not None:
        return _langfuse_enabled

    # 환경변수로 명시적 비활성화 가능
    if os.getenv("LANGFUSE_ENABLED", "true").lower() == "false":
        _langfuse_enabled = False
        return False

    # 필수 환경변수 확인
    host = os.getenv("LANGFUSE_HOST")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    _langfuse_enabled = bool(host and public_key and secret_key)

    if _langfuse_enabled:
        logger.info(f"[Langfuse] Enabled: host={host}")
    else:
        logger.debug("[Langfuse] Disabled: missing configuration")

    return _langfuse_enabled


@lru_cache(maxsize=1)
def get_langfuse_client():
    """Langfuse 클라이언트 싱글톤."""
    if not is_langfuse_enabled():
        return None

    try:
        from langfuse import Langfuse

        client = Langfuse(
            host=os.getenv("LANGFUSE_HOST"),
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        )
        logger.info("[Langfuse] Client initialized")
        return client
    except ImportError:
        logger.warning("[Langfuse] langfuse package not installed")
        return None
    except Exception as e:
        logger.warning(f"[Langfuse] Failed to initialize: {e}")
        return None


def get_langfuse_handler(
    user_id: str | None = None,
    session_id: str | None = None,
    trace_name: str = "ai-chat",
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> "CallbackHandler | None":
    """LangChain/LangGraph용 Langfuse CallbackHandler 생성.

    Args:
        user_id: 사용자 식별자
        session_id: 대화 세션 ID (thread_id를 Langfuse session_id로 매핑)
        trace_name: 트레이스 이름 (기본: ai-chat)
        tags: 추가 태그
        metadata: 추가 메타데이터

    Returns:
        CallbackHandler 또는 None (비활성화 시)

    Note:
        - Langfuse에서는 session_id가 표준 용어
        - 우리 시스템의 thread_id를 session_id로 매핑하여 전달
    """
    if not is_langfuse_enabled():
        return None

    try:
        from langfuse.callback import CallbackHandler

        handler = CallbackHandler(
            host=os.getenv("LANGFUSE_HOST"),
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            user_id=user_id,
            session_id=session_id,
            trace_name=trace_name,
            tags=tags or ["ai-chat-mode"],
            metadata=metadata or {},
        )
        return handler
    except ImportError:
        logger.debug("[Langfuse] CallbackHandler not available")
        return None
    except Exception as e:
        logger.warning(f"[Langfuse] Failed to create handler: {e}")
        return None


def flush_langfuse() -> None:
    """Langfuse 버퍼 플러시 (앱 종료 시 호출)."""
    client = get_langfuse_client()
    if client:
        try:
            client.flush()
            logger.debug("[Langfuse] Flushed")
        except Exception as e:
            logger.warning(f"[Langfuse] Flush failed: {e}")


def shutdown_langfuse() -> None:
    """Langfuse 종료 (앱 종료 시 호출)."""
    client = get_langfuse_client()
    if client:
        try:
            client.shutdown()
            logger.info("[Langfuse] Shutdown complete")
        except Exception as e:
            logger.warning(f"[Langfuse] Shutdown failed: {e}")
