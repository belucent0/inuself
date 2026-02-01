"""WebSocket 연결 관리자.

여러 클라이언트의 WebSocket 연결을 관리하고 채널 기반 구독 모델을 제공합니다.
"""
import asyncio
from typing import Dict, Set

from fastapi import WebSocket

from ..core.logging import logger


class ConnectionManager:
    """WebSocket 연결 관리자.
    
    채널 기반 Pub/Sub 모델로 여러 WebSocket 연결을 관리합니다.
    
    구조:
        - subscriptions: {channel: set(websockets)} - 채널별 구독자 목록
        - reverse_map: {websocket: set(channels)} - 연결별 구독 채널 (빠른 정리용)
    """

    def __init__(self):
        # 채널 → WebSocket 연결 매핑
        self.subscriptions: Dict[str, Set[WebSocket]] = {}
        # WebSocket → 채널 역방향 매핑 (연결 정리 시 사용)
        self.reverse_map: Dict[WebSocket, Set[str]] = {}
        # 동시 접근 제어
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str, websocket: WebSocket) -> None:
        """채널을 구독합니다.

        Args:
            channel: 구독할 채널 (예: "events:file_progress:123")
            websocket: WebSocket 연결
        """
        async with self._lock:
            # 채널에 WebSocket 추가
            if channel not in self.subscriptions:
                self.subscriptions[channel] = set()
            self.subscriptions[channel].add(websocket)

            # 역방향 매핑 업데이트
            if websocket not in self.reverse_map:
                self.reverse_map[websocket] = set()
            self.reverse_map[websocket].add(channel)

            logger.info(
                "[ConnectionManager] Subscribed: channel={}, total_subscribers={}",
                channel,
                len(self.subscriptions[channel]),
            )

    async def unsubscribe(self, channel: str, websocket: WebSocket) -> None:
        """채널 구독을 해제합니다.

        Args:
            channel: 구독 해제할 채널
            websocket: WebSocket 연결
        """
        async with self._lock:
            # 채널에서 WebSocket 제거
            if channel in self.subscriptions:
                self.subscriptions[channel].discard(websocket)
                # 구독자가 없으면 채널 삭제
                if not self.subscriptions[channel]:
                    del self.subscriptions[channel]

            # 역방향 매핑 업데이트
            if websocket in self.reverse_map:
                self.reverse_map[websocket].discard(channel)
                # 구독 채널이 없으면 삭제
                if not self.reverse_map[websocket]:
                    del self.reverse_map[websocket]

            logger.debug(
                "[ConnectionManager] Unsubscribed: channel={}, websocket={}",
                channel,
                id(websocket),
            )

    async def disconnect(self, websocket: WebSocket) -> None:
        """WebSocket 연결을 종료하고 모든 구독을 정리합니다.

        Args:
            websocket: 종료할 WebSocket 연결
        """
        async with self._lock:
            # 이 WebSocket이 구독한 모든 채널 찾기
            channels = self.reverse_map.get(websocket, set()).copy()

            # 모든 채널에서 제거
            for channel in channels:
                if channel in self.subscriptions:
                    self.subscriptions[channel].discard(websocket)
                    if not self.subscriptions[channel]:
                        del self.subscriptions[channel]

            # 역방향 매핑 삭제
            if websocket in self.reverse_map:
                del self.reverse_map[websocket]

            logger.info(
                "[ConnectionManager] Disconnected: websocket={}, unsubscribed_from={} channels",
                id(websocket),
                len(channels),
            )

    async def broadcast_to_channel(self, channel: str, message: dict) -> None:
        """특정 채널의 모든 구독자에게 메시지를 브로드캐스트합니다.

        Args:
            channel: 대상 채널
            message: 전송할 메시지 (dict, JSON으로 변환됨)
        """
        async with self._lock:
            subscribers = self.subscriptions.get(channel, set()).copy()

        if not subscribers:
            logger.debug(
                "[ConnectionManager] No subscribers for channel: {}", channel
            )
            return

        # 연결이 끊긴 WebSocket 추적
        disconnected = []

        # 모든 구독자에게 전송
        for websocket in subscribers:
            try:
                await websocket.send_json(message)
                logger.debug(
                    "[ConnectionManager] Sent to websocket={}: {}",
                    id(websocket),
                    message.get("step", "unknown"),
                )
            except Exception as exc:
                logger.warning(
                    "[ConnectionManager] Failed to send to websocket={}: {}",
                    id(websocket),
                    exc,
                )
                disconnected.append(websocket)

        # 실패한 연결 정리
        for websocket in disconnected:
            await self.disconnect(websocket)

        if len(subscribers) - len(disconnected) > 0:
            logger.debug(
                "[ConnectionManager] Broadcasted to channel={}, sent={}, failed={}",
                channel,
                len(subscribers) - len(disconnected),
                len(disconnected),
            )

    def get_stats(self) -> dict:
        """현재 연결 통계를 반환합니다.

        Returns:
            통계 정보 dict
        """
        return {
            "total_channels": len(self.subscriptions),
            "total_connections": len(self.reverse_map),
            "channels": {
                channel: len(subscribers)
                for channel, subscribers in self.subscriptions.items()
            },
        }
