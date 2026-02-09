"""사용자 행동 이벤트 추적 서비스.

Phase 1: 개인화 준비를 위한 사용자 행동 데이터 수집.
비동기 fire-and-forget 방식으로 주요 플로우에 영향 없이 이벤트 기록.
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import UserEvent

logger = logging.getLogger(__name__)


# 지원하는 이벤트 타입
EVENT_TYPES = {
    "chat_message": "AI 채팅 메시지 전송",
    "content_upload": "콘텐츠 업로드",
    "content_view": "콘텐츠 상세 조회",
    "feedback": "AI 응답 피드백",
    "search_query": "검색 실행",
    "wpi_test_start": "WPI 검사 시작",
    "wpi_test_complete": "WPI 검사 완료",
    "thread_create": "대화 스레드 생성",
    "thread_delete": "대화 스레드 삭제",
}


class EventTrackingService:
    """비동기 이벤트 수집 서비스 (fire-and-forget)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def track(
        self,
        user_id: UUID,
        event_type: str,
        content_id: UUID | None = None,
        thread_id: UUID | None = None,
        payload: dict | None = None,
    ) -> UserEvent | None:
        """이벤트 기록.

        Args:
            user_id: 사용자 ID
            event_type: 이벤트 유형 (chat_message, content_upload 등)
            content_id: 관련 콘텐츠 ID (선택)
            thread_id: 관련 스레드 ID (선택)
            payload: 추가 데이터 (선택)

        Returns:
            생성된 이벤트 객체 또는 None (실패 시)

        Note:
            실패해도 예외를 던지지 않음 (주요 플로우에 영향 없도록)
        """
        try:
            event = UserEvent(
                user_id=user_id,
                event_type=event_type,
                content_id=content_id,
                thread_id=thread_id,
                payload=payload or {},
            )
            self.session.add(event)
            await self.session.flush()

            logger.debug(
                f"[Event] Tracked: user={user_id}, type={event_type}, "
                f"thread={thread_id}, content={content_id}"
            )
            return event

        except Exception as e:
            # 이벤트 기록 실패는 주요 플로우에 영향 없도록 조용히 로깅
            logger.warning(f"[Event] Failed to track: {event_type}, error={e}")
            return None

    async def track_chat_message(
        self,
        user_id: UUID,
        thread_id: UUID,
        query: str,
        mode: str,
        response_time_ms: int | None = None,
    ) -> UserEvent | None:
        """채팅 메시지 이벤트 기록."""
        return await self.track(
            user_id=user_id,
            event_type="chat_message",
            thread_id=thread_id,
            payload={
                "query": query[:200],  # 쿼리는 처음 200자만
                "mode": mode,
                "response_time_ms": response_time_ms,
            },
        )

    async def track_content_upload(
        self,
        user_id: UUID,
        content_id: UUID,
        file_type: str,
        size_bytes: int,
    ) -> UserEvent | None:
        """콘텐츠 업로드 이벤트 기록."""
        return await self.track(
            user_id=user_id,
            event_type="content_upload",
            content_id=content_id,
            payload={
                "file_type": file_type,
                "size_bytes": size_bytes,
            },
        )

    async def track_content_view(
        self,
        user_id: UUID,
        content_id: UUID,
        dwell_time_seconds: int | None = None,
        tab: str | None = None,
    ) -> UserEvent | None:
        """콘텐츠 조회 이벤트 기록."""
        return await self.track(
            user_id=user_id,
            event_type="content_view",
            content_id=content_id,
            payload={
                "dwell_time_seconds": dwell_time_seconds,
                "tab": tab,
            },
        )

    async def track_feedback(
        self,
        user_id: UUID,
        thread_id: UUID,
        rating: int,
        comment: str | None = None,
    ) -> UserEvent | None:
        """AI 응답 피드백 이벤트 기록."""
        return await self.track(
            user_id=user_id,
            event_type="feedback",
            thread_id=thread_id,
            payload={
                "rating": rating,
                "comment": comment[:500] if comment else None,
            },
        )

    async def track_search_query(
        self,
        user_id: UUID,
        query: str,
        results_count: int,
        thread_id: UUID | None = None,
    ) -> UserEvent | None:
        """검색 쿼리 이벤트 기록."""
        return await self.track(
            user_id=user_id,
            event_type="search_query",
            thread_id=thread_id,
            payload={
                "query": query[:200],
                "results_count": results_count,
            },
        )

    async def track_wpi_test(
        self,
        user_id: UUID,
        event_type: str,  # "wpi_test_start" or "wpi_test_complete"
        test_type: str | None = None,  # "i_test" or "me_test"
        dominant_type: str | None = None,
    ) -> UserEvent | None:
        """WPI 검사 이벤트 기록."""
        return await self.track(
            user_id=user_id,
            event_type=event_type,
            payload={
                "test_type": test_type,
                "dominant_type": dominant_type,
            },
        )


def get_event_tracking_service(session: AsyncSession) -> EventTrackingService:
    """EventTrackingService 팩토리."""
    return EventTrackingService(session)
