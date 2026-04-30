"""워커 결과를 Redis Stream으로 발행.

워커가 작업 완료 후 결과를 Redis Stream에 발행하면,
백엔드의 StreamConsumer가 이를 구독하여 DB에 저장합니다.

분산 추적: traceparent를 메시지에 포함하여 Backend에서 trace context를 복원.
"""

import json
from datetime import datetime
from typing import Any
from uuid import UUID


class UUIDEncoder(json.JSONEncoder):
    """UUID를 문자열로 직렬화하는 JSON Encoder."""

    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


from redis import Redis

from worker.config import get_settings
from worker.logging_config import logger
from worker.telemetry import inject_trace_context

settings = get_settings()

# Redis 클라이언트 싱글톤
_redis_client: Redis | None = None

# Stream 이름
RESULT_STREAM = "stream:worker:results"


def _get_redis() -> Redis:
    """Redis 클라이언트를 가져옵니다 (싱글톤)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _publish_result(data: dict[str, Any]) -> str:
    """Redis Stream에 결과를 발행합니다.

    Args:
        data: 발행할 데이터

    Returns:
        Stream entry ID
    """
    redis = _get_redis()

    # timestamp 추가
    data["timestamp"] = datetime.utcnow().isoformat()

    # 분산 추적: traceparent 주입 (Backend에서 trace context 복원용)
    trace_carrier = {}
    inject_trace_context(trace_carrier)
    if trace_carrier.get("traceparent"):
        data["traceparent"] = trace_carrier["traceparent"]

    # Redis Stream은 flat한 key-value만 지원하므로 JSON으로 직렬화
    entry_id = redis.xadd(RESULT_STREAM, {"data": json.dumps(data, cls=UUIDEncoder)})

    logger.info(
        "[ResultPublisher] Published to {}: type={}, file_id={}, entry_id={}",
        RESULT_STREAM,
        data.get("type"),
        data.get("file_id"),
        entry_id,
    )
    return entry_id


# =============================================================================
# ASR 결과 발행
# =============================================================================


def publish_asr_started(file_id: int) -> str:
    """ASR 작업 시작을 발행합니다."""
    return _publish_result(
        {
            "type": "asr",
            "event": "started",
            "file_id": file_id,
        }
    )


def publish_asr_completed(
    file_id: int,
    *,
    result_s3_key: str,
    duration_seconds: float,
    num_speakers: int,
    speaker_labels: list[str],
) -> str:
    """ASR 작업 완료를 발행합니다.

    Args:
        file_id: 파일 ID
        result_s3_key: 결과 JSON이 저장된 S3 key
        duration_seconds: 오디오 길이 (초)
        num_speakers: 화자 수
        speaker_labels: 화자 레이블 목록
    """
    return _publish_result(
        {
            "type": "asr",
            "event": "completed",
            "file_id": file_id,
            "result_s3_key": result_s3_key,
            "duration_seconds": duration_seconds,
            "num_speakers": num_speakers,
            "speaker_labels": speaker_labels,
        }
    )


def publish_asr_failed(file_id: int, *, error: str) -> str:
    """ASR 작업 실패를 발행합니다."""
    return _publish_result(
        {
            "type": "asr",
            "event": "failed",
            "file_id": file_id,
            "error": error,
        }
    )


# =============================================================================
# LLM 결과 발행
# =============================================================================


def publish_llm_started(file_id: int) -> str:
    """LLM 작업 시작을 발행합니다."""
    return _publish_result(
        {
            "type": "llm",
            "event": "started",
            "file_id": file_id,
        }
    )


def publish_llm_completed(
    file_id: int,
    *,
    result_s3_key: str,
    image_s3_key: str | None = None,
) -> str:
    """LLM 작업 완료를 발행합니다.

    Args:
        file_id: 파일 ID
        result_s3_key: 결과 JSON이 저장된 S3 key
        image_s3_key: 커버 이미지가 저장된 S3 key (선택)
    """
    data = {
        "type": "llm",
        "event": "completed",
        "file_id": file_id,
        "result_s3_key": result_s3_key,
    }
    if image_s3_key:
        data["image_s3_key"] = image_s3_key
    return _publish_result(data)


def publish_llm_failed(file_id: int, *, error: str) -> str:
    """LLM 작업 실패를 발행합니다."""
    return _publish_result(
        {
            "type": "llm",
            "event": "failed",
            "file_id": file_id,
            "error": error,
        }
    )


# =============================================================================
# WPI AI 리포트 결과 발행
# =============================================================================


def publish_wpi_report_started(scan_result_id: str) -> str:
    """WPI AI 리포트 작업 시작을 발행합니다."""

    return _publish_result(
        {
            "type": "wpi_report",
            "event": "started",
            "scan_result_id": scan_result_id,
        }
    )


def publish_wpi_report_completed(
    scan_result_id: str,
    *,
    result_s3_key: str,
) -> str:
    """WPI AI 리포트 작업 완료를 발행합니다."""

    return _publish_result(
        {
            "type": "wpi_report",
            "event": "completed",
            "scan_result_id": scan_result_id,
            "result_s3_key": result_s3_key,
        }
    )


def publish_wpi_report_failed(scan_result_id: str, *, error: str) -> str:
    """WPI AI 리포트 작업 실패를 발행합니다."""

    return _publish_result(
        {
            "type": "wpi_report",
            "event": "failed",
            "scan_result_id": scan_result_id,
            "error": error,
        }
    )


# =============================================================================
# OCR 결과 발행
# =============================================================================


def publish_ocr_started(file_id: int) -> str:
    """OCR 작업 시작을 발행합니다."""
    return _publish_result(
        {
            "type": "ocr",
            "event": "started",
            "file_id": file_id,
        }
    )


def publish_ocr_completed(
    file_id: int,
    *,
    result_s3_key: str,
    page_count: int,
    text_length: int,
) -> str:
    """OCR 작업 완료를 발행합니다.

    Args:
        file_id: 파일 ID
        result_s3_key: 결과 JSON이 저장된 S3 key
        page_count: 페이지 수
        text_length: 추출된 텍스트 길이
    """
    return _publish_result(
        {
            "type": "ocr",
            "event": "completed",
            "file_id": file_id,
            "result_s3_key": result_s3_key,
            "page_count": page_count,
            "text_length": text_length,
        }
    )


def publish_ocr_failed(file_id: int, *, error: str) -> str:
    """OCR 작업 실패를 발행합니다."""
    return _publish_result(
        {
            "type": "ocr",
            "event": "failed",
            "file_id": file_id,
            "error": error,
        }
    )
