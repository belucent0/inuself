"""WebSocket 엔드포인트."""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from ..core.config import get_settings
from ..core.logging import logger
from ..websocket.dependencies import ConnectionManagerDep

router = APIRouter(prefix="/ws", tags=["websocket"])

@router.get("/ws/test")
async def test_router():
    """라우터 테스트용 엔드포인트"""
    return {"status": "ok", "message": "WebSocket router is working"}

async def _send_provider_control(action: str, provider: str):
    """Provider Manager에게 제어 메시지 전송."""
    try:
        import redis.asyncio as redis
        settings = get_settings()
        r = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        message = {
            "action": action,
            "provider": provider
        }
        await r.publish("provider.control", json.dumps(message))
        await r.close()
        logger.info(f"[WebSocket] Sent provider signal: {action} {provider}")
    except Exception as e:
        logger.error(f"[WebSocket] Failed to send provider signal: {e}")

@router.get("/debug-headers")
async def debug_headers(request: Request):
    """요청 헤더 확인용 엔드포인트"""
    return {"headers": dict(request.headers)}

@router.websocket("/test-ws")
async def test_websocket(websocket: WebSocket):
    """WebSocket 테스트 엔드포인트 (의존성 없음)"""
    await websocket.accept()
    await websocket.send_json({"type": "test", "message": "WebSocket is working!"})
    await websocket.close()

@router.websocket("/file-progress-simple/{file_id}")
async def websocket_file_progress_simple(websocket: WebSocket, file_id: int):
    """간단한 파일 진행 WebSocket (의존성 없음, 테스트용)"""
    await websocket.accept()
    logger.info(f"[WebSocket] Simple client connected: file_id={file_id}")
    
    await websocket.send_json({
        "type": "connection",
        "status": "connected",
        "file_id": file_id,
    })
    
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except Exception as e:
        logger.info(f"[WebSocket] Simple client disconnected: {e}")

@router.websocket("/file-progress/global")
async def websocket_file_progress_global(
    websocket: WebSocket,
    manager: ConnectionManagerDep,
):
    """전체 파일 처리 진행 상태 WebSocket (Global).
    
    모든 파일의 진행 상태 이벤트를 수신합니다.
    목록 페이지 등에서 단일 소켓으로 상태를 관리할 때 사용합니다.
    
    채널: events:file_progress:global
    """
    await websocket.accept()
    channel = "events:file_progress:global"

    logger.info(f"[WebSocket] Global client connected: channel={channel}")

    try:
        await manager.subscribe(channel, websocket)

        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "channel": channel,
            "message": "Global File Progress Channel",
        })

        # 채팅/요약용 모델은 실제 요청 시에만 시작 (VRAM 절약)
        # asyncio.create_task(_send_provider_control("start", "llama"))

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] Global client disconnected")
    except Exception as exc:
        logger.exception(f"[WebSocket] Global error: {exc}")
    finally:
        await manager.disconnect(websocket)
        logger.info(f"[WebSocket] Global connection cleaned up")
