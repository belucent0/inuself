"""SSE(Server-Sent Events) 엔드포인트.

파일 진행 상태를 SSE를 통해 실시간으로 스트리밍합니다.
"""
import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from ..core.config import get_settings
from ..core.logging import logger

router = APIRouter(prefix="/api", tags=["events"])
settings = get_settings()


async def file_progress_stream() -> AsyncGenerator[str, None]:
    """파일 진행 상태를 SSE로 스트리밍합니다.

    Redis Pub/Sub을 구독하여 file_progress 이벤트를 받아 클라이언트에게 전달합니다.
    """
    redis: Redis | None = None
    pubsub = None

    try:
        # Redis 연결
        redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            encoding="utf-8"
        )
        pubsub = redis.pubsub()

        # 파일 진행 상태 채널 구독 (모든 파일 + 글로벌 채널)
        # - events:file_progress:{file_id}
        # - events:file_progress:global
        await pubsub.psubscribe("events:file_progress:*")
        logger.info("[SSE] File progress subscriber connected")

        # 초기 연결 메시지
        yield f"data: {json.dumps({'type': 'connection', 'status': 'connected'})}\n\n"

        # Keep-alive ping (30초마다)
        ping_task = asyncio.create_task(_keep_alive_ping())
        listen_task = asyncio.create_task(_listen_messages(pubsub))

        try:
            while True:
                done, pending = await asyncio.wait(
                    [ping_task, listen_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                for task in done:
                    if task is ping_task:
                        # Ping 완료 → 새 ping 태스크 생성
                        yield "data: [PING]\n\n"
                        ping_task = asyncio.create_task(_keep_alive_ping())
                    elif task is listen_task:
                        # 메시지 수신 완료
                        message = task.result()
                        if message:
                            yield f"data: {message}\n\n"
                        # 새 listen 태스크 생성
                        listen_task = asyncio.create_task(_listen_messages(pubsub))
        finally:
            ping_task.cancel()
            listen_task.cancel()

    except asyncio.CancelledError:
        logger.info("[SSE] File progress stream cancelled")
    except Exception as e:
        logger.exception(f"[SSE] Error in file progress stream: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    finally:
        if pubsub:
            try:
                await pubsub.unsubscribe()
                await pubsub.close()
            except Exception as e:
                logger.warning(f"[SSE] Error closing pubsub: {e}")

        if redis:
            try:
                await redis.close()
            except Exception as e:
                logger.warning(f"[SSE] Error closing redis: {e}")

        logger.info("[SSE] File progress stream closed")


async def _keep_alive_ping() -> None:
    """30초마다 ping을 보냅니다."""
    await asyncio.sleep(30)


async def _listen_messages(pubsub) -> str | None:
    """Redis Pub/Sub 메시지를 수신합니다."""
    try:
        message = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=35)

        if message and message.get("type") == "pmessage":
            try:
                channel = message["channel"]
                data = message["data"]

                # JSON 파싱
                if isinstance(data, str):
                    event = json.loads(data)
                else:
                    event = data

                logger.debug(f"[SSE] Forwarding event from {channel}: {event.get('type')}")
                return json.dumps(event)
            except json.JSONDecodeError as e:
                logger.warning(f"[SSE] Invalid JSON from Redis: {e}")
                return None
    except asyncio.TimeoutError:
        # Timeout은 정상 - keep-alive ping을 위함
        return None
    except Exception as e:
        logger.exception(f"[SSE] Error listening to messages: {e}")
        return None


@router.get("/events/file-progress/stream")
async def stream_file_progress(request: Request) -> StreamingResponse:
    """파일 진행 상태를 SSE로 스트리밍합니다.

    클라이언트는 EventSource API를 통해 연결합니다:
    ```javascript
    const eventSource = new EventSource('/api/events/file-progress/stream');
    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      // 진행 상태 업데이트
    };
    ```

    메시지 포맷:
    ```json
    {
      "type": "file_progress",
      "file_id": "uuid",
      "status": "PULLING|PROCESSING|COMPLETED|...",
      "progress": 45.0,
      "message": "YouTube 다운로드 중...",
      ...
    }
    ```
    """
    return StreamingResponse(
        file_progress_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
