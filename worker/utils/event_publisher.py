"""워커 전용 이벤트 발행 헬퍼 (백엔드 독립).

이 모듈은 워커에서 Redis Pub/Sub으로 직접 이벤트를 발행합니다.
백엔드 services 레이어에 의존하지 않아 워커 분리 시 그대로 사용 가능합니다.
"""
import json
from datetime import datetime
from typing import Any

from redis import Redis

from worker.config import get_settings
from worker.logging_config import logger

settings = get_settings()

# Redis 클라이언트 싱글톤 (워커 프로세스당 1개)
_redis_client: Redis | None = None


def _get_redis() -> Redis:
    """Redis 클라이언트를 가져옵니다 (싱글톤)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def publish_file_progress(
    file_id: int,
    status: str,
    step: str,
    progress: float,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """파일 처리 진행 상태를 Redis Pub/Sub으로 발행합니다.

    Args:
        file_id: 파일 ID
        status: 파일 상태 (queued, processing, summary_queued, completed, failed 등)
        step: 현재 처리 단계 (download, asr_pipeline, postprocess, save 등)
        progress: 진행률 (0-100)
        message: 사용자에게 표시할 메시지
        metadata: 추가 메타데이터 (선택사항)

    채널: events:file_progress:{file_id}

    메시지 포맷:
        {
            "type": "file_progress",
            "file_id": 123,
            "status": "processing",
            "step": "download",
            "progress": 20.0,
            "message": "파일 다운로드 완료",
            "metadata": {...},
            "timestamp": "2024-01-01T12:00:00.000000"
        }
    """
    try:
        channel = f"events:file_progress:{file_id}"
        event = {
            "type": "file_progress",
            "file_id": file_id,
            "status": status,
            "step": step,
            "progress": progress,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if metadata:
            event["metadata"] = metadata

        redis = _get_redis()
        redis.publish(channel, json.dumps(event))

        logger.debug(
            "[EventPublisher] Published file_progress: file_id={}, step={}, progress={}%",
            file_id,
            step,
            progress,
        )
    except Exception as exc:
        # 이벤트 발행 실패는 메인 로직에 영향을 주지 않음
        logger.warning(
            "[EventPublisher] Failed to publish file_progress: file_id={}, error={}",
            file_id,
            exc,
        )


def publish_asr_stream(file_id: int, segment: dict[str, Any]) -> None:
    """실시간 ASR 세그먼트를 발행합니다 (향후 사용).

    Args:
        file_id: 파일 ID
        segment: ASR 세그먼트 정보
            {
                "text": "안녕하세요",
                "start": 0.0,
                "end": 1.5,
                "is_final": False
            }

    채널: events:asr_stream:{file_id}
    """
    try:
        channel = f"events:asr_stream:{file_id}"
        event = {
            "type": "asr_stream",
            "file_id": file_id,
            "segment": segment,
            "timestamp": datetime.utcnow().isoformat(),
        }

        redis = _get_redis()
        redis.publish(channel, json.dumps(event))

        logger.debug(
            "[EventPublisher] Published asr_stream: file_id={}, is_final={}",
            file_id,
            segment.get("is_final", False),
        )
    except Exception as exc:
        logger.warning(
            "[EventPublisher] Failed to publish asr_stream: file_id={}, error={}",
            file_id,
            exc,
        )


def publish_llm_stream(file_id: int, token: str, is_final: bool = False) -> None:
    """LLM 스트리밍 토큰을 발행합니다 (향후 사용).

    Args:
        file_id: 파일 ID
        token: 생성된 토큰
        is_final: 마지막 토큰 여부

    채널: events:llm_stream:{file_id}
    """
    try:
        channel = f"events:llm_stream:{file_id}"
        event = {
            "type": "llm_stream",
            "file_id": file_id,
            "token": token,
            "is_final": is_final,
            "timestamp": datetime.utcnow().isoformat(),
        }

        redis = _get_redis()
        redis.publish(channel, json.dumps(event))

        logger.debug(
            "[EventPublisher] Published llm_stream: file_id={}, is_final={}",
            file_id,
            is_final,
        )
    except Exception as exc:
        logger.warning(
            "[EventPublisher] Failed to publish llm_stream: file_id={}, error={}",
            file_id,
            exc,
        )


def publish_content_created(
    content_id: int,
    filename: str,
    content_type: str,
    status: str = "QUEUED",
) -> None:
    """새 콘텐츠 생성 이벤트를 Redis Pub/Sub으로 발행합니다.

    Args:
        content_id: 생성된 콘텐츠 ID
        filename: 파일명
        content_type: 콘텐츠 타입 (AUDIO, DOCUMENT, PORTRAY)
        status: 초기 상태 (기본: QUEUED)

    채널: events:content_created
    """
    try:
        channel = "events:content_created"
        event = {
            "type": "content_created",
            "content_id": content_id,
            "filename": filename,
            "content_type": content_type,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
        }

        redis = _get_redis()
        redis.publish(channel, json.dumps(event))

        logger.info(
            "[EventPublisher] Published content_created: content_id={}, filename={}",
            content_id,
            filename,
        )
    except Exception as exc:
        logger.warning(
            "[EventPublisher] Failed to publish content_created: content_id={}, error={}",
            content_id,
            exc,
        )


class ProgressReporter:
    """파일 진행 상태 보고 헬퍼 클래스.
    
    file_id를 캡슐화하여 반복적인 인자 전달을 줄이고,
    메서드 체이닝이나 직관적인 메서드 명으로 상태를 보고합니다.
    """
    
    def __init__(self, file_id: int):
        self.file_id = file_id

    def report(
        self,
        status: str,
        step: str,
        progress: float,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """진행 상태를 발행합니다."""
        publish_file_progress(
            file_id=self.file_id,
            status=status,
            step=step,
            progress=progress,
            message=message,
            metadata=metadata,
        )

    def processing(
        self, 
        step: str, 
        progress: float, 
        message: str, 
        metadata: dict[str, Any] | None = None
    ) -> None:
        """처리 중 상태 보고"""
        self.report("processing", step, progress, message, metadata)

    def summarizing(
        self, 
        step: str, 
        progress: float, 
        message: str, 
        metadata: dict[str, Any] | None = None
    ) -> None:
        """요약 중 상태 보고"""
        self.report("summarizing", step, progress, message, metadata)

    def summary_queued(
        self, 
        step: str = "summary_queued", 
        progress: float = 50.0, 
        message: str = "요약 대기 중", 
        metadata: dict[str, Any] | None = None
    ) -> None:
        """요약 대기 상태 보고"""
        self.report("summary_queued", step, progress, message, metadata)

    def complete(self, message: str = "모든 처리가 완료되었습니다.") -> None:
        """완료 상태 보고"""
        self.report("completed", "completed", 100.0, message)

    def fail(self, error_message: str) -> None:
        """실패 상태 보고"""
        self.report("failed", "error", 0.0, error_message)
