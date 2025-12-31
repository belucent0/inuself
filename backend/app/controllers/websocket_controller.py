"""WebSocket 엔드포인트."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from ..core.logging import logger
from ..websocket.dependencies import ConnectionManagerDep

router = APIRouter(prefix="/ws", tags=["websocket"])


@router.get("/test")
async def test_router():
    """라우터 테스트용 엔드포인트"""
    return {"status": "ok", "message": "WebSocket router is working"}


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





@router.websocket("/asr-stream/{session_id}")
async def websocket_asr_stream(
    websocket: WebSocket,
    session_id: str,
    manager: ConnectionManagerDep,
):
    """실시간 ASR 스트리밍 WebSocket (향후 사용).

    Args:
        websocket: WebSocket 연결
        session_id: 세션 ID
        manager: ConnectionManager (의존성 주입)
    """
    await websocket.accept()
    channel = f"events:asr_stream:{session_id}"

    logger.info(
        "[WebSocket] ASR Stream connected: session_id={}, channel={}",
        session_id,
        channel,
    )

    try:
        await manager.subscribe(channel, websocket)

        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "channel": channel,
            "session_id": session_id,
        })

        while True:
            # 클라이언트로부터 오디오 청크 수신 (향후 구현)
            data = await websocket.receive_text()

            if data == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(
            "[WebSocket] ASR Stream disconnected: session_id={}",
            session_id,
        )
    except Exception as exc:
        logger.exception(
            "[WebSocket] ASR Stream error: session_id={}, error={}",
            session_id,
            exc,
        )
    finally:
        await manager.disconnect(websocket)
        logger.info(
            "[WebSocket] ASR Stream cleaned up: session_id={}",
            session_id,
        )
