"""WebSocket 엔드포인트."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from ..core.logging import logger
from ..websocket.dependencies import ConnectionManagerDep
from ..worker.stream_asr import stream_asr_worker
from .websocket_helper import (
    get_audio_rms, 
    convert_webm_to_wav, 
    is_silence, 
    process_llm_background
)
import httpx
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
            # 청킹 설정 (0.5초 단위 청크 가정)
            MIN_CHUNKS = 10   # 5초 (기본 단위)
            MAX_CHUNKS = 20   # 10초 (최대 연장)
            
            # 이전 전사 결과 저장 (맥락 유지용)
            previous_transcriptions = []
            MAX_PROMPT_SEGMENTS = 7  # 테스트: 4개 세그먼트 사용 (더 긴 맥락)
            MAX_HISTORY_SIZE = 10  # 최대 히스토리 크기 (메모리 관리)
            
            while True:
                # 1. 청크 수신
                data = await websocket.receive_bytes()
                
                # 헤더 저장 (첫 청크)
                if not webm_header:
                     webm_header = data
                
                audio_buffer.extend(data)
                chunk_count += 1
                
                should_process = False
                
                # 2. 처리 여부 결정
                if chunk_count >= MAX_CHUNKS:
                    # 최대 시간(10초) 도달 시 강제 처리
                    should_process = True
                    logger.debug(f"[ASR Stream] Force processing at {chunk_count} chunks (Max duration)")
                elif chunk_count >= MIN_CHUNKS:
                    # 기본 시간(5초) 경과 시, 침묵 감지하여 자연스러운 끊기 시도
                    # 현재 버퍼의 마지막 1초 구간 침묵 확인
                    current_wav = convert_webm_to_wav(bytes(audio_buffer))
                    if current_wav:
                        # 마지막 0.8초 정도가 침묵인지 확인 (threshold는 실험적 조정 필요, 여기선 800 유지)
                        if is_silence(current_wav, threshold=800, check_duration_sec=0.8):
                            should_process = True
                            logger.debug(f"[ASR Stream] Silence detected at {chunk_count} chunks. Processing...")
                        else:
                            # 말하고 있으면 처리 미룸 (최대 10초까지)
                            pass
                
                if should_process:
                    # WebM -> WAV 변환
                    wav_data = convert_webm_to_wav(bytes(audio_buffer))
                    
                    if wav_data:
                        # (옵션) 너무 짧은 침묵만 있는 청크는 스킵?
                        # 전체가 침묵인 경우는 처리하지 않도록 기존 로직 유지
                        
                        # RMS 체크 (저음량 필터링)
                        rms = get_audio_rms(wav_data)
                        logger.debug(f"[ASR Stream] Audio RMS: {rms}")
                        
                        # 임계값 조정: 기존 800/1200은 너무 높아서 정상 발화(1000~1100)가 잘리는 문제 발생
                        SILENCE_THRESHOLD = 300
                        MIN_VOLUME_THRESHOLD = 600
                        
                        if rms < SILENCE_THRESHOLD:
                            # 1. 아예 침묵인 경우 (경고 없음, 그냥 스킵)
                            logger.debug("[ASR Stream] Silence detected (full buffer), skipping...")
                        
                        elif rms < MIN_VOLUME_THRESHOLD:
                             # 2. 소리가 있긴 한데 너무 작은 경우 (경고 전송)
                             logger.info(f"[ASR Stream] Low volume ({rms}), skipping to prevent hallucinations.")
                             await websocket.send_json({
                                "type": "warning",
                                "message": "목소리가 너무 작아 인식이 어렵습니다."
                            })
                            
                        else:
                            # 3. 정상 볼륨 (전사 진행)
                            try:
                                # NPU 서버로 전송 (OpenAI API 포맷)
                                files = {'file': ('audio.wav', wav_data, 'audio/wav')}
                                
                                # 이전 전사 내용을 prompt로 생성
                                prompt_text = ""
                                if previous_transcriptions:
                                    # 마지막 N개 세그먼트를 공백으로 연결
                                    prompt_text = " ".join(previous_transcriptions[-MAX_PROMPT_SEGMENTS:])
                                    logger.debug(f"[ASR Stream] Using prompt: {prompt_text[:100]}..." if len(prompt_text) > 100 else f"[ASR Stream] Using prompt: {prompt_text}")
                                
                                # FastFlowLM NPU 서버 요청 데이터 구성
                                request_data = {
                                    "model": "whisper-v3", # 샘플 코드 기준
                                    "language": lang,
                                }
                                
                                # prompt가 있으면 추가
                                if prompt_text:
                                    request_data["prompt"] = prompt_text
                                
                                # FastFlowLM NPU 서버 요청
                                response = await client.post(
                                    f"{stream_asr_worker.base_url}/audio/transcriptions",
                                    files=files,
                                    data=request_data
                                )
                                
                                if response.status_code == 200:
                                    result = response.json()
                                    text = result.get("text", "").strip()
                                    
                                    # 환각/노이즈 필터링 (샘플 코드 기준 + 추가)
                                    hallucinations = ["Okay.", "Thank you.", "MBC 뉴스.", "You", "(끝)", "O", "o"]
                                    if not text or len(text) < 2 or text in hallucinations:
                                        pass
                                    else:
                                        # 1. 원본 텍스트 즉시 전송 (반응성 향상)
                                        import uuid
                                        segment_id = str(uuid.uuid4())
                                        
                                        logger.info(f"[ASR Stream] Sending raw result: {text} (ID: {segment_id})")
                                        await websocket.send_json({
                                            "type": "commit",
                                            "segment_id": segment_id,
                                            "text": text,
                                            "processed_text": None # 초기엔 없음
                                        })
                                        
                                        # 전사 결과를 히스토리에 추가
                                        previous_transcriptions.append(text)
                                        if len(previous_transcriptions) > MAX_HISTORY_SIZE:
                                            previous_transcriptions.pop(0)

                                        # 2. LLM 후처리 (비동기 백그라운드 실행)
                                        # 별도 태스크로 실행하여 다음 오디오 처리를 막지 않음
                                        asyncio.create_task(
                                            process_llm_background(
                                                websocket, 
                                                text, 
                                                segment_id, 
                                                stream_asr_worker.base_url
                                            )
                                        )
                                        
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
