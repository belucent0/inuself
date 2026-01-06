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
    lang: str = "ko", # 쿼리 파라미터로 언어 설정 받음
):
    """실시간 ASR 스트리밍 WebSocket.
    
    클라이언트로부터 오디오 청크를 수신하고 whisper-server로 전송하여
    전사 결과를 반환합니다.
    """
    await websocket.accept()
    
    # 서버 실행 여부 확인
    # 서버 실행 (On-Demand)
    logger.info(f"[WebSocket] Starting Whisper Server for session {session_id}...")
    stream_asr_worker.start()
    
    # 서버 준비 대기 (VRAM 로딩 등)
    if not await stream_asr_worker.wait_for_ready(timeout=60.0):
        logger.error("[WebSocket] Failed to start Whisper Server (Timeout)")
        await websocket.close(code=1011, reason="Server failed to start")
        return
        
    logger.info(f"[WebSocket] Whisper Server is ready for session {session_id}")
        
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
        
        # 서버 준비 완료 알림 (프론트엔드 Red Button 활성화용)
        await websocket.send_json({
            "type": "ready",
            "message": "Model loaded, ready to record"
        })
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 오디오 데이터 누적을 위한 버퍼 (WebM 헤더 문제 해결 위함)
            audio_buffer = bytearray()
            webm_header = b"" # 헤더 저장용 (첫 청크)
            last_transcription = "" # 마지막 전사 내용 저장용
            committed_history = ""  # 문맥 유지를 위한 히스토리 저장
            silence_counter = 0     # 연속 침묵 카운터
            
            while True:
                # 클라이언트로부터 오디오 데이터 수신 (bytes)
                # JS MediaRecorder는 Blob(bytes) 전송 가능
                data = await websocket.receive_bytes()
                logger.info(f"[ASR Stream] Received audio chunk: {len(data)} bytes")
                
                # 첫 번째 청크라면 헤더로 간주하고 저장
                if not webm_header:
                     webm_header = data
                     logger.debug(f"[ASR Stream] WebM Header captured: {len(data)} bytes")
                
                # 데이터 누적
                audio_buffer.extend(data)
                
                # 안전 장치: 버퍼가 너무 커지면 (예: 5MB 약 3-5분?) 강제 클리어 고민
                # 하지만 아래 침묵 로직으로 해소될 것
                
                # whisper-server로 전송
                # /inference 엔드포인트 사용 (multipart/form-data)
                try:
                    # WebM -> WAV 변환 (whisper.cpp 서버 호환성 위함)
                    wav_data = convert_webm_to_wav(bytes(audio_buffer))
                    
                    if not wav_data:
                        logger.error("[ASR Stream] Failed to convert audio data")
                        continue
                        
                    # VAD: RMS 에너지 기반 침묵 감지
                    # 디코딩된 WAV 데이터의 마지막 0.5초 볼륨을 체크
                    # Threshold 상향: 500 -> 1000 (마이크 노이즈 대응)
                    if is_silence(wav_data, threshold=1000, check_duration_sec=0.5): 
                         logger.debug("[ASR Stream] Silence detected (last 0.5s), skipping inference")
                         
                         # 침묵이 감지되면 '문장 확정(Commit)' 로직 수행
                         # (단, 텍스트가 있을 경우에만)
                         if silence_counter == 0: # 침묵 진입 시점
                             logger.debug("[ASR Stream] Silence accumulation started")
                             
                         silence_counter += 1
                         
                         # 약 0.5초 이상 침묵이 지속되면 문장 확정
                         # 현재 JS에서 0.5초단위 전송 중. (1 chunk = 0.5 sec)
                         # threshold 1 = 0.5초 침묵
                         if silence_counter >= 1:
                             if last_transcription:
                                 logger.info(f"[ASR Stream] Committing sentence: {last_transcription}")
                                 
                                 # 히스토리 업데이트 (최근 1000자 유지)
                                 committed_history += " " + last_transcription
                                 if len(committed_history) > 1000:
                                     committed_history = committed_history[-1000:]
                                     
                                 await websocket.send_json({
                                     "type": "commit",
                                     "text": last_transcription
                                 })
                                 
                             # 버퍼 및 상태 초기화
                             audio_buffer.clear()
                             if webm_header: # 헤더 복구
                                 audio_buffer.extend(webm_header)
                                 
                             last_transcription = ""
                             silence_counter = 0
                             logger.debug("[ASR Stream] Buffer cleared due to silence")
                         
                         continue
                    
                    # 소리가 나면 침묵 카운터 리셋
                    silence_counter = 0

                    files = {'file': ('audio.wav', wav_data, 'audio/wav')}
                    
                    # 프롬프트 생성 (최근 문맥 200자 전달)
                    prompt = committed_history[-200:].strip()
                    if prompt:
                        logger.debug(f"[ASR Stream] Using prompt: {prompt}")

                    response = await client.post(
                        f"{stream_asr_worker.base_url}/inference",
                        files=files,
                        data={
                            "temperature": "0.0",
                            "temperature_inc": "0.2",
                            "response_format": "json",
                            "language": lang,
                            "prompt": prompt, 
                        }
                    )
                    
                    if response.status_code == 200:
                        import json
                        # 응답 텍스트 로깅 (디버깅)
                        logger.info(f"[ASR Stream] Server response: {response.text[:200]}...") # 너무 길면 잘림
                        
                        try:
                            result = response.json()
                            text = result.get("text", "")
                            if text:
                                # [BLANK_AUDIO] 등의 불필요한 텍스트 필터링 가능
                                text = text.strip()
                                
                                # 환각 필터링 (Whisper가 침묵 시 자주 뱉는 문자열)
                                hallucinations = ["(끝)", "(End)", "[BLANK_AUDIO]", "MBC", "SBS", "뉴스", "자막", "(뿌링)", "o", "O", ".", "감사합니다", "고맙습니다", "수고하셨습니다"]
                                if any(h in text for h in hallucinations):
                                     logger.debug(f"[ASR Stream] Hallucination detected: {text}")
                                     
                                     # 환각 발생 시 버퍼가 오염되었거나 무한 루프 징조일 수 있으므로 버퍼 클리어
                                     if "뿌링" in text or "(끝)" in text or "감사합니다" in text or "고맙습니다" in text:
                                         audio_buffer.clear()
                                         if webm_header: # 헤더 복구
                                             audio_buffer.extend(webm_header)
                                             
                                         last_transcription = ""
                                         logger.info(f"[ASR Stream] Clearing buffer due to strong hallucination: {text}")
                                         
                                     continue
                                
                                last_transcription = text # 최신 전사 내용 저장 (Commit용)

                                # 누적된 오디오에 대한 전체 텍스트이므로, type을 full_transcription으로 변경하여 프론트엔드가 덮어쓰도록 유도
                                if text and not text.startswith("["): 
                                     await websocket.send_json({
                                         "type": "full_transcription", 
                                         "text": text
                                     })
                        except json.JSONDecodeError:
                             logger.error(f"[ASR Stream] Invalid JSON response: {response.text}")
                    else:
                        logger.error(f"[ASR Stream] Server error: {response.status_code} {response.text}")
                except Exception as req_exc:
                    logger.error(f"[ASR Stream] Request failed: {req_exc}")

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] ASR Stream disconnected: session_id={session_id}")
    except Exception as exc:
        logger.exception(f"[WebSocket] ASR Stream error: {exc}")
    finally:
        await manager.disconnect(websocket)
        await manager.disconnect(websocket)
        logger.info(f"[WebSocket] ASR Stream cleaned up: session_id={session_id}")
        
        # 연결 종료 시 서버도 종료 (On-Demand)
        # 여러 클라이언트가 붙을 경우 카운팅이 필요하지만, 현재는 1:1 가정
        if stream_asr_worker.is_running():
            logger.info("[WebSocket] Stopping Whisper Server...")
            stream_asr_worker.stop()

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
