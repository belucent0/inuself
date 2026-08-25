"""AI Gateway 공통 헬퍼.

여러 controller(chat / search 등)가 동일한 base_url / api_key /
AsyncOpenAI 클라이언트 생성 코드를 중복 정의하던 것을 단일 모듈로
통합. 라우팅 프로필 결정 같은 caller-specific 로직은 각 controller에 유지.
"""
from __future__ import annotations

import os
from functools import lru_cache

from httpx import Timeout
from openai import AsyncOpenAI

DEFAULT_TIMEOUT: float = 120.0
LONG_RUNNING_TIMEOUT: Timeout = Timeout(
    connect=30.0,
    read=600.0,
    write=30.0,
    pool=30.0,
)


def get_ai_gateway_base_url() -> str:
    """AI Gateway URL (Docker 네트워크 hostname 기본)."""
    return os.getenv("AI_GATEWAY_URL", "http://ai-gateway:4000")


def get_ai_gateway_api_key() -> str:
    """AI Gateway API 키."""
    return os.getenv("AI_GATEWAY_API_KEY", "")


@lru_cache(maxsize=2)
def get_async_openai_client(long_running: bool = False) -> AsyncOpenAI:
    """AI Gateway용 AsyncOpenAI 클라이언트 (싱글톤, long_running별 캐시).

    long_running=True: connect 30s + read 600s — 추론 모드의 긴 TTFT 대응.
    long_running=False: 전체 120s — 일반 채팅용.
    """
    return AsyncOpenAI(
        base_url=get_ai_gateway_base_url().rstrip("/"),
        api_key=get_ai_gateway_api_key(),
        timeout=LONG_RUNNING_TIMEOUT if long_running else DEFAULT_TIMEOUT,
    )
