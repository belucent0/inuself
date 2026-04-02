"""Provider Health Check 서비스."""

import json
import logging
from typing import Optional

import httpx

from config import (
    HEALTH_CHECK_TIMEOUT,
    PROVIDER_HEALTH_URLS,
    PROVIDER_REDIS_STATUS_KEY,
)
from core.redis import get_async_redis

logger = logging.getLogger(__name__)

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=HEALTH_CHECK_TIMEOUT)
    return _http_client


async def check_provider_health(provider: str) -> bool:
    """Provider HTTP 엔드포인트 Health Check.

    Args:
        provider: "llama", "flm", "whisper-cpp", "insanely-fast", "diarization-server"

    Returns:
        healthy 여부
    """
    url = PROVIDER_HEALTH_URLS.get(provider)
    if not url:
        return False

    try:
        client = _get_http_client()
        response = await client.get(url)
        healthy = response.status_code == 200
        if healthy:
            logger.debug(f"[Health] {provider} healthy")
        else:
            logger.warning(f"[Health] {provider} unhealthy: {response.status_code}")
        return healthy
    except Exception as e:
        logger.debug(f"[Health] {provider} unreachable: {e}")
        return False


async def check_provider_status(provider: str) -> str:
    """Redis providers:status 해시에서 프로바이더 상태 조회.

    Provider Manager가 갱신하는 상태 정보입니다.

    Args:
        provider: provider 이름 (custom_handler 규칙, e.g. "whisper-cpp")

    Returns:
        상태 문자열 ("up", "down", "cooldown", "unknown")
    """
    redis_key = PROVIDER_REDIS_STATUS_KEY.get(provider)
    if not redis_key:
        return "unknown"

    try:
        r = await get_async_redis()
        status_json = await r.hget("providers:status", redis_key)
        if status_json:
            status_data = json.loads(status_json)
            return status_data.get("status", "unknown")
        return "unknown"
    except Exception as e:
        logger.warning(f"[Health] Failed to get provider status: {e}")
        return "unknown"
