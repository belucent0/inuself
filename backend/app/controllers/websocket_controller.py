"""WebSocket 엔드포인트."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from ..core.logging import logger
from ..websocket.dependencies import ConnectionManagerDep
from ..worker.stream_asr import stream_asr_worker
import httpx
import subprocess
import tempfile
import os
import asyncio
from typing import Optional


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
    lang: str = "ko",
):
    """실시간 ASR 스트리밍 WebSocket (NPU/FastFlowLM 연동).
    
    클라이언트로부터 오디오 청크를 수신하고(약 0.5초 단위), 
    일정 시간(약 5초) 모아서 FastFlowLM 서버(NPU)로 전송합니다.
    """
    await websocket.accept()
    
    # 서버 실행 (On-Demand)
    # 이미 켜져있으면 Pass, 안 켜져있으면 flm 실행
    stream_asr_worker.start()
    
    # 서버 준비 확인
    if not await stream_asr_worker.wait_for_ready(timeout=20.0):
        logger.error("[WebSocket] FastFlowLM Server failed to start or is not ready.")
        await websocket.close(code=1011, reason="Server failed to start")
        return
        
    channel = f"events:asr_stream:{session_id}"
    logger.info(f"[WebSocket] ASR Stream connected: session_id={session_id}, lang={lang}")

    try:
        await manager.subscribe(channel, websocket)
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "channel": channel,
            "session_id": session_id,
        })
        
        # 준비 완료 알림
        await websocket.send_json({
            "type": "ready",
            "message": "NPU Server Ready"
        })
        
        async with httpx.AsyncClient(timeout=120.0, headers={"Authorization": "Bearer flm"}) as client:
            audio_buffer = bytearray()
            webm_header = b"" 
            chunk_count = 0
            TARGET_CHUNKS = 10 # 0.5초 * 10 = 5초 (안정성 확보)
            
            while True:
                # 1. 청크 수신
                data = await websocket.receive_bytes()
                
                # 헤더 저장 (첫 청크)
                if not webm_header:
                     webm_header = data
                
                audio_buffer.extend(data)
                chunk_count += 1
                
                # 2. 목표량(약 5초) 도달 시 처리
                if chunk_count >= TARGET_CHUNKS:
                    # WebM -> WAV 변환
                    wav_data = convert_webm_to_wav(bytes(audio_buffer))
                    
                    if wav_data:
                        # 무음 감지 (전체 버퍼에 대해)
                        # Threshold 800 (샘플 코드 기준)
                        if is_silence(wav_data, threshold=800, check_duration_sec=5.0):
                            logger.debug("[ASR Stream] Silence detected, skipping...")
                        else:
                            try:
                                # NPU 서버로 전송 (OpenAI API 포맷)
                                files = {'file': ('audio.wav', wav_data, 'audio/wav')}
                                
                                # FastFlowLM NPU 서버 요청
                                response = await client.post(
                                    f"{stream_asr_worker.base_url}/audio/transcriptions",
                                    files=files,
                                    data={
                                        "model": "whisper-v3", # 샘플 코드 기준
                                        "language": lang,
                                    }
                                )
                                
                                if response.status_code == 200:
                                    result = response.json()
                                    text = result.get("text", "").strip()
                                    
                                    # 환각/노이즈 필터링 (샘플 코드 기준 + 추가)
                                    hallucinations = ["Okay.", "Thank you.", "MBC 뉴스.", "You", "(끝)", "O", "o"]
                                    if not text or len(text) < 2 or text in hallucinations:
                                        pass
                                    else:
                                        logger.info(f"[ASR Stream] Result: {text}")
                                        # 클라이언트에 'commit' 메시지로 전송 (라인 단위 출력용)
                                        await websocket.send_json({
                                            "type": "commit",
                                            "text": text
                                        })
                                else:
                                    logger.error(f"[ASR Stream] Server error: {response.text}")
                                    
                            except Exception as api_exc:
                                logger.exception(f"[ASR Stream] API Request failed: {api_exc}")

                    # 버퍼 초기화 (다음 청크를 위해)
                    # 오버랩 없이 단순 초기화 (샘플 코드 방식)
                    audio_buffer.clear()
                    if webm_header:
                        audio_buffer.extend(webm_header)
                    chunk_count = 0

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] ASR Stream disconnected: session_id={session_id}")
    except Exception as exc:
        logger.exception(f"[WebSocket] ASR Stream error: {exc}")
    finally:
        await manager.disconnect(websocket)
        logger.info(f"[WebSocket] ASR Stream cleaned up: session_id={session_id}")

def convert_webm_to_wav(webm_data: bytes) -> Optional[bytes]:
    """WebM(Opus) 데이터를 16kHz Mono WAV로 변환."""
    try:
        # 임시 파일 생성
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as temp_webm:
            temp_webm.write(webm_data)
            temp_webm_path = temp_webm.name
            
        temp_wav_path = temp_webm_path.replace(".webm", ".wav")
        
        # ffmpeg 변환
        # -y: 덮어쓰기
        # -i: 입력
        # -ar 16000: 샘플링 레이트 16kHz
        # -ac 1: 채널 1 (Mono)
        # -c:a pcm_s16le: 16비트 PCM
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", temp_webm_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            temp_wav_path
        ]
        
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if os.path.exists(temp_wav_path):
            with open(temp_wav_path, "rb") as f:
                wav_data = f.read()
            
            # 정리
            os.remove(temp_wav_path)
            os.remove(temp_webm_path)
            return wav_data
            
        os.remove(temp_webm_path)
        return None
        
    except Exception as e:
        logger.error(f"[Convert] Error converting audio: {e}")
        # 파일 정리 시도
        if 'temp_webm_path' in locals() and os.path.exists(temp_webm_path):
            os.remove(temp_webm_path)
        if 'temp_wav_path' in locals() and os.path.exists(temp_wav_path):
            os.remove(temp_wav_path)
        return None

def is_silence(wav_bytes: bytes, threshold: int = 500, check_duration_sec: float = 1.0) -> bool:
    """WAV 바이트 데이터의 RMS를 계산하여 침묵 여부 판단."""
    import audioop
    try:
        # WAV 헤더(44바이트) 건너뛰기
        if len(wav_bytes) < 44:
            return True
            
        # raw PCM data (16-bit mono)
        pcm_data = wav_bytes[44:]
        if not pcm_data:
            return True
            
        # 마지막 부분만 체크 (최신 입력에 대한 반응성 확보)
        sample_rate = 16000
        bytes_per_sample = 2
        check_bytes = int(sample_rate * bytes_per_sample * check_duration_sec)
        
        if len(pcm_data) > check_bytes:
             pcm_data = pcm_data[-check_bytes:]
             
        # RMS 계산 (width=2 for 16-bit)
        rms = audioop.rms(pcm_data, 2)
        
        logger.debug(f"[VAD] Audio RMS (last {check_duration_sec}s): {rms}") 
        return rms < threshold
    except Exception as e:
        logger.error(f"VAD Error: {e}")
        return False
