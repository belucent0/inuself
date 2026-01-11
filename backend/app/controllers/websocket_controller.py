"""WebSocket 엔드포인트."""
import asyncio
import json
import os
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from ..core.config import get_settings
from ..core.logging import logger
from ..websocket.dependencies import ConnectionManagerDep
from .websocket_helper import process_llm_background


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
    """실시간 ASR 스트리밍 WebSocket.
    
    오디오 데이터를 실시간으로 전사하여 결과를 반환합니다.
    FLM 서버를 사용하여 전사를 수행합니다.
    
    클라이언트 프로토콜:
    - 바이너리: WebM 오디오 데이터 (직접 전송)
    - 텍스트: {"type": "audio_chunk", "chunk_id": int, "is_last": bool}  (5초마다)
    - 텍스트: {"type": "finish"}  (전사 완료 요청), {"type": "ping"}
    - 수신: {"type": "result", "text": "...", "is_final": false}
    - 수신: {"type": "result", "text": "...", "is_final": true}
    - 수신: {"type": "error", "message": "..."}
    """
    await websocket.accept()
    
    settings = get_settings()
    flm_base_url = os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434")
    flm_url = f"{flm_base_url}/v1/audio/transcriptions"
    
    # FLM 서버 사용 가능 여부 확인
    try:
        import socket
        from urllib.parse import urlparse
        parsed = urlparse(flm_base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434
        
        logger.info(f"[WebSocket ASR] Checking FLM server: {flm_base_url} (host={host}, port={port})")
        
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            result = s.connect_ex((host, port))
            if result != 0:
                logger.error(f"[WebSocket ASR] FLM server connection failed: result={result}")
                await websocket.send_json({
                    "type": "error",
                    "message": "FLM 서버에 연결할 수 없습니다. FLM 서버가 실행 중인지 확인해주세요.",
                })
                await websocket.close(code=1011, reason="FLM server unavailable")
                return
        
        logger.info(f"[WebSocket ASR] FLM server check passed, sending ready message")
        
        await websocket.send_json({
            "type": "ready",
            "status": "ready",
            "message": "실시간 ASR 서버가 준비되었습니다.",
        })
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "message": f"서버 초기화 오류: {str(e)}",
        })
        await websocket.close(code=1011, reason="Server initialization error")
        return
    
    # 오디오 데이터 버퍼
    audio_chunks = []
    total_chunks = 0
    
    # 전사 결과 버퍼
    transcription_buffer = ""
    
    logger.info(f"[WebSocket ASR] Client connected: session_id={session_id}, lang={lang}")
    
    async def _transcribe_audio_chunk(chunk_data: bytes, chunk_id: int, is_last: bool) -> dict:
        """단일 오디오 청크를 전사하고 결과를 반환합니다."""
        temp_dir = Path(tempfile.gettempdir())
        webm_path = temp_dir / f"asr_chunk_{session_id}_{chunk_id}.webm"
        wav_path = temp_dir / f"asr_chunk_{session_id}_{chunk_id}.wav"
        
        try:
            # WebM 파일 저장
            with open(webm_path, "wb") as f:
                f.write(chunk_data)
                
            # ffmpeg로 WebM → WAV 변환 (run_in_executor로 블로킹 방지)
            import subprocess
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(webm_path),
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(wav_path)
            ]
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            )
            
            # FLM 서버에 전사 요청
            with open(wav_path, "rb") as f:
                audio_file_data = f.read()
                
            files = {"file": ("audio.wav", audio_file_data, "audio/wav")}
            data = {
                "model": "whisper-v3",
                "language": lang,
                "response_format": "verbose_json",
            }
            
            async with httpx.AsyncClient(timeout=30.0, headers={"Authorization": "Bearer flm"}) as client:
                response = await client.post(flm_url, files=files, data=data)
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("text", "").strip()
                    segments = result.get("segments", [])
                    
                    return {
                        "text": text,
                        "segments": segments,
                        "success": True
                    }
                else:
                    return {
                        "success": False,
                        "error": f"FLM 서버 오류: {response.status_code}"
                    }
        
        except Exception as e:
            logger.error(f"[WebSocket ASR] Chunk transcribe error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            # 임시 파일 삭제
            if webm_path.exists():
                webm_path.unlink()
            if wav_path.exists():
                wav_path.unlink()
    
    try:
        while True:
            # 메시지 수신 (텍스트 또는 바이너리)
            message = await websocket.receive()
            
            # 바이너리 데이터 (오디오 WebM)
            if "bytes" in message:
                audio_data = message["bytes"]
                if audio_data:
                    audio_chunks.append(audio_data)
                    total_chunks += 1
                    
                    # 진행 상태 전송 (매 10번째 청크)
                    if total_chunks % 10 == 0:
                        await websocket.send_json({
                            "type": "progress",
                            "chunks": total_chunks,
                            "message": f"{total_chunks}개의 오디오 청크 수신 중...",
                        })
            
            # 텍스트 데이터 (JSON 제어 메시지)
            elif "text" in message:
                data = message["text"]
                
                try:
                    parsed = json.loads(data)
                    
                    if parsed.get("type") == "audio_chunk":
                        # 실시간 스트리밍: 개별 오디오 청크 전사
                        chunk_id = parsed.get("chunk_id")
                        is_last = parsed.get("is_last", False)
                        
                        if audio_chunks:
                            # 5초 분량의 오디오 청크 전사
                            chunk_data = b"".join(audio_chunks)
                            audio_chunks = []  # 버퍼 초기화
                            
                            logger.info(f"[WebSocket ASR] Transcribing chunk {chunk_id}, size={len(chunk_data)} bytes")
                            
                            result = await _transcribe_audio_chunk(chunk_data, chunk_id, is_last)
                            
                            if result.get("success"):
                                text = result["text"]
                                segments = result["segments"]
                                
                                # 실시간 결과 전송 (클라이언트는 "commit" 타입 기대)
                                if text:
                                    segment_id = f"{session_id}_{chunk_id}"
                                    await websocket.send_json({
                                        "type": "commit",
                                        "text": text,
                                        "segment_id": segment_id,
                                    })
                                    logger.info(f"[WebSocket ASR] Sent commit: segment_id={segment_id}, text_length={len(text)}")
                                    
                                    # LLM 후처리 (백그라운드): 언어 필터링 + 문법 교정
                                    asyncio.create_task(
                                        process_llm_background(websocket, text, segment_id, flm_base_url)
                                    )
                                
                                # 전체 텍스트 버퍼 업데이트
                                if transcription_buffer:
                                    transcription_buffer += " " + text
                                else:
                                    transcription_buffer = text
                            else:
                                await websocket.send_json({
                                    "type": "error",
                                    "message": result.get("error", "전사 실패"),
                                })
                    
                    elif parsed.get("type") == "finish":
                        # 전사 완료 요청 (batch 모드)
                        logger.info(f"[WebSocket ASR] Finish message received: session_id={session_id}, audio_chunks={len(audio_chunks)}")
                        
                        if not audio_chunks:
                            await websocket.send_json({
                                "type": "error",
                                "message": "전사할 오디오 데이터가 없습니다.",
                            })
                            continue
                        
                        # 오디오 청크를 하나의 파일로 합치기
                        temp_dir = Path(tempfile.gettempdir())
                        webm_path = temp_dir / f"asr_stream_{session_id}.webm"
                        wav_path = temp_dir / f"asr_stream_{session_id}.wav"
                        
                        logger.info(f"[WebSocket ASR] Saving WebM: {len(audio_chunks)} chunks, {sum(len(c) for c in audio_chunks)} bytes")
                        
                        try:
                            with open(webm_path, "wb") as f:
                                audio_buffer = b"".join(audio_chunks)
                                f.write(audio_buffer)
                            
                            # ffmpeg로 WebM → WAV 변환 (run_in_executor로 블로킹 방지)
                            import subprocess
                            cmd = [
                                "ffmpeg", "-y", "-v", "error",
                                "-i", str(webm_path),
                                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                                str(wav_path)
                            ]
                            logger.info(f"[WebSocket ASR] Running ffmpeg: {' '.join(cmd)}")
                            
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None,
                                lambda: subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                            )
                            logger.info(f"[WebSocket ASR] ffmpeg completed: wav_path={wav_path}")
                            
                            # FLM 서버에 전사 요청
                            await websocket.send_json({
                                "type": "status",
                                "message": "전사 중...",
                            })
                            
                            try:
                                with open(wav_path, "rb") as f:
                                    audio_file_data = f.read()
                                    
                                logger.info(f"[WebSocket ASR] Sending to FLM: wav_size={len(audio_file_data)} bytes")
                                    
                                files = {"file": ("audio.wav", audio_file_data, "audio/wav")}
                                data = {
                                    "model": "whisper-v3",
                                    "language": lang,
                                    "response_format": "verbose_json",
                                }
                                
                                async with httpx.AsyncClient(timeout=120.0, headers={"Authorization": "Bearer flm"}) as client:
                                    response = await client.post(flm_url, files=files, data=data)
                                
                                logger.info(f"[WebSocket ASR] FLM response: status={response.status_code}")
                                
                                if response.status_code == 200:
                                    result = response.json()
                                    text = result.get("text", "").strip()
                                    segments = result.get("segments", [])
                                    
                                    # 중간 결과 전송
                                    await websocket.send_json({
                                        "type": "result",
                                        "text": text,
                                        "is_final": False,
                                        "segments": segments[:5] if segments else [],
                                    })
                                    
                                    # 최종 결과 전송
                                    await websocket.send_json({
                                        "type": "result",
                                        "text": text,
                                        "is_final": True,
                                        "segments": segments,
                                    })
                                    
                                    logger.info(f"[WebSocket ASR] Transcription completed: session_id={session_id}, text_length={len(text)}")
                                else:
                                    await websocket.send_json({
                                        "type": "error",
                                        "message": f"FLM 서버 오류: {response.status_code} - {response.text}",
                                    })
                            
                            finally:
                                # 임시 파일 삭제
                                if webm_path.exists():
                                    webm_path.unlink()
                                if wav_path.exists():
                                    wav_path.unlink()
                        
                        except Exception as e:
                            logger.error(f"[WebSocket ASR] Transcription error: {e}")
                            await websocket.send_json({
                                "type": "error",
                                "message": f"전사 중 오류 발생: {str(e)}",
                            })
                        
                        # 버퍼 초기화
                        audio_chunks = []
                        transcription_buffer = ""
                        total_chunks = 0
                    
                    elif parsed.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                
                except json.JSONDecodeError as e:
                    logger.error(f"[WebSocket ASR] JSON parsing error: {e}")
                except Exception as e:
                    logger.error(f"[WebSocket ASR] Message processing error: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "message": f"메시지 처리 오류: {str(e)}",
                    })
    
    except WebSocketDisconnect:
        logger.info(f"[WebSocket ASR] Client disconnected: session_id={session_id}")
    except Exception as exc:
        logger.exception(f"[WebSocket ASR] Error: {exc}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"서버 오류: {str(exc)}",
            })
        except:
            pass
    finally:
        # 연결 종료
        logger.info(f"[WebSocket ASR] Connection cleaned up: session_id={session_id}")
