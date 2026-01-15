"""LiteLLM Custom Callback - On-Demand Provider Manager 연동.

Audio transcription 요청 시 Provider Manager에 "start" 신호를 전송합니다.
LiteLLM의 retry 메커니즘과 함께 사용하여 on-demand 서버 시작을 지원합니다.

Architecture V6: Worker → LiteLLM (callback) → Provider Manager → Audio Server
"""
import json
import logging
import os
from typing import Any, Literal, Optional

import redis
import redis.asyncio as redis_async

from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger(__name__)

# Redis for Provider Manager communication
REDIS_URL = os.getenv("REDIS_URL", "redis://asr-redis:6379/0")

# Model → Provider 매핑
MODEL_PROVIDER_MAP = {
    # ASR Models
    "whisper-turbo": "whisper-cpp",
    "openai/whisper-turbo": "whisper-cpp",
    "whisper-large-v3": "insanely-fast",
    "openai/whisper-large-v3": "insanely-fast",
    "flm-audio": "flm",
    "openai/flm-audio": "flm",
    # Diarization
    "pyannote": "diarization-server",
    "openai/pyannote": "diarization-server",
}

# Redis clients (lazy init)
_redis_client_sync: Optional[redis.Redis] = None
_redis_client_async: Optional[redis_async.Redis] = None


def _get_redis_client_sync() -> Optional[redis.Redis]:
    """동기 Redis 클라이언트 반환 (lazy initialization)."""
    global _redis_client_sync
    if _redis_client_sync is None:
        try:
            _redis_client_sync = redis.from_url(REDIS_URL, decode_responses=True)
            logger.info(f"[ProviderCallback] Redis (sync) connected: {REDIS_URL}")
        except Exception as e:
            logger.warning(f"[ProviderCallback] Redis (sync) connection failed: {e}")
    return _redis_client_sync


async def _get_redis_client_async() -> Optional[redis_async.Redis]:
    """비동기 Redis 클라이언트 반환 (lazy initialization)."""
    global _redis_client_async
    if _redis_client_async is None:
        try:
            _redis_client_async = redis_async.from_url(REDIS_URL, decode_responses=True)
            logger.info(f"[ProviderCallback] Redis (async) connected: {REDIS_URL}")
        except Exception as e:
            logger.warning(f"[ProviderCallback] Redis (async) connection failed: {e}")
    return _redis_client_async


def _send_provider_signal_sync(provider: str, action: str = "start"):
    """Provider Manager에게 제어 신호 전송 (동기)."""
    client = _get_redis_client_sync()
    if client:
        try:
            message = {"action": action, "provider": provider}
            client.publish("provider.control", json.dumps(message))
            logger.info(f"[ProviderCallback] Sent signal: {provider} -> {action}")
        except Exception as e:
            logger.warning(f"[ProviderCallback] Failed to send signal: {e}")


async def _send_provider_signal_async(provider: str, action: str = "start"):
    """Provider Manager에게 제어 신호 전송 (비동기)."""
    client = await _get_redis_client_async()
    if client:
        try:
            message = {"action": action, "provider": provider}
            await client.publish("provider.control", json.dumps(message))
            logger.info(f"[ProviderCallback] Sent signal (async): {provider} -> {action}")
        except Exception as e:
            logger.warning(f"[ProviderCallback] Failed to send signal (async): {e}")


class ProviderManagerCallback(CustomLogger):
    """On-Demand Provider Manager 연동 Callback.

    Audio transcription 요청 전에 Provider Manager에 "start" 신호를 전송합니다.
    서버가 이미 실행 중이면 Provider Manager가 무시합니다 (idempotent).
    """

    def log_pre_api_call(self, model, messages, kwargs):
        """API 호출 전에 실행되는 hook (동기).

        transcription 요청 시 Provider Manager에 서버 시작 신호를 전송합니다.
        """
        # transcription 요청인지 확인 (call_type 또는 endpoint로 판단)
        call_type = kwargs.get("call_type", "")

        # model 이름으로 provider 찾기
        provider = MODEL_PROVIDER_MAP.get(model)

        if provider:
            logger.info(f"[ProviderCallback] Pre-call: model={model}, provider={provider}, call_type={call_type}")
            # 동기 함수에서 비동기 호출 - 별도 스레드 또는 동기 Redis 사용
            _send_provider_signal_sync(provider, "start")
        else:
            logger.debug(f"[ProviderCallback] Pre-call: model={model}, no provider mapping")

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        """요청 성공 시 Provider에 touch 신호 전송 (idle timeout 리셋)."""
        model = kwargs.get("model", "")
        provider = MODEL_PROVIDER_MAP.get(model)

        if provider:
            logger.info(f"[ProviderCallback] Success: model={model}, provider={provider}")
            _send_provider_signal_sync(provider, "touch")

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """요청 실패 시 로깅."""
        model = kwargs.get("model", "")
        exception = kwargs.get("exception", "unknown")
        logger.warning(f"[ProviderCallback] Failure: model={model}, exception={exception}")


# LiteLLM에 등록할 callback 인스턴스
provider_manager_callback = ProviderManagerCallback()


def input_callback_handler(kwargs, completion_response, start_time, end_time):
    """litellm.input_callback - 모든 API 호출 전에 실행.

    transcription 요청 시 Provider Manager에 서버 시작 신호를 전송합니다.
    """
    model = kwargs.get("model", "")
    call_type = kwargs.get("call_type", "")

    logger.info(f"[InputCallback] model={model}, call_type={call_type}")

    provider = MODEL_PROVIDER_MAP.get(model)
    if provider:
        logger.info(f"[InputCallback] Sending start signal: {provider}")
        _send_provider_signal_sync(provider, "start")

    return kwargs
