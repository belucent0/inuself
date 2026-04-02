"""미디어 처리 라우트 — ASR, OCR, Diarization.

POST /v1/chat/completions 에서 task_type으로 분기되어 호출됩니다.
Redis Stream을 통해 Provider Manager → GPU/NPU 서버로 전달합니다.
"""

import base64
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi.responses import JSONResponse

from clients.stream_client import get_async_gpu_stream_client
from config import (
    DEPLOY_MODE,
    NPU_OCR_MODEL,
    GPU_OCR_MODEL,
    RUNPOD_API_KEY,
    RUNPOD_ASR_BASE_URL,
    RUNPOD_VISION_BASE_URL,
)
from services.device_lock import (
    acquire_device_lock,
    release_device_lock,
    start_lock_heartbeat,
    stop_lock_heartbeat,
)
from utils.response import build_openai_response

logger = logging.getLogger(__name__)


async def handle_media_request(body: dict) -> JSONResponse:
    """미디어 요청 디스패처."""
    extra_body = body.get("extra_body", {})
    task_type = extra_body.get("task_type", "")

    if task_type == "asr":
        return await _handle_asr(body)
    elif task_type == "diarization":
        return await _handle_diarization(body)
    elif task_type == "ocr":
        return await _handle_ocr(body)
    else:
        return JSONResponse(
            {"error": f"Unknown task_type: {task_type}"},
            status_code=400,
        )


async def _handle_asr(body: dict) -> JSONResponse:
    """ASR (음성인식) 요청 처리.

    extra_body:
        audio_base64, language, accuracy_mode, lock_id, file_id
    """
    extra = body.get("extra_body", {})
    accuracy_mode = extra.get("accuracy_mode", "speed")
    worker_lock_id = extra.get("lock_id")
    language = extra.get("language", "ko")
    file_id = extra.get("file_id")
    audio_base64 = extra.get("audio_base64", "")

    if not audio_base64:
        return JSONResponse({"error": "audio_base64 is required"}, status_code=400)

    # 서버리스 모드
    if DEPLOY_MODE == "serverless":
        return await _handle_asr_serverless(audio_base64, language, accuracy_mode)

    # 모델/프로바이더 선택
    if accuracy_mode == "accuracy":
        asr_model = "whisper-large-v3"
        provider_name = "insanely-fast"
    else:
        asr_model = "whisper-turbo"
        provider_name = "whisper-cpp"

    # 락 처리
    lock_id = worker_lock_id
    heartbeat = None
    own_lock = False
    tmp = None

    if not lock_id:
        lock_id = await acquire_device_lock("gpu", max_wait=3600.0)
        own_lock = True
        if lock_id:
            heartbeat = start_lock_heartbeat(lock_id, device="gpu")

    try:
        # audio_base64 → 임시 파일 (Redis Stream은 파일 경로/내용 전송)
        audio_bytes = base64.b64decode(audio_base64)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(audio_bytes)
        tmp.close()

        stream_client = get_async_gpu_stream_client()
        result = await stream_client.request_transcription(
            audio_file_path=Path(tmp.name),
            model=asr_model,
            language=language,
            timeout=1800.0,
        )

        # 결과를 OpenAI 포맷으로 래핑
        content = json.dumps(result) if isinstance(result, dict) else str(result)
        return JSONResponse(build_openai_response(content, asr_model))

    finally:
        if tmp:
            Path(tmp.name).unlink(missing_ok=True)
        if heartbeat:
            stop_lock_heartbeat(heartbeat)
        if own_lock and lock_id:
            await release_device_lock("gpu", lock_id)


async def _handle_diarization(body: dict) -> JSONResponse:
    """Diarization (화자 분리) 요청 처리."""
    extra = body.get("extra_body", {})
    audio_base64 = extra.get("audio_base64", "")
    worker_lock_id = extra.get("lock_id")
    min_speakers = extra.get("min_speakers")
    max_speakers = extra.get("max_speakers")

    if not audio_base64:
        return JSONResponse({"error": "audio_base64 is required"}, status_code=400)

    lock_id = worker_lock_id
    heartbeat = None
    own_lock = False
    tmp = None

    if not lock_id:
        lock_id = await acquire_device_lock("gpu", max_wait=3600.0)
        own_lock = True
        if lock_id:
            heartbeat = start_lock_heartbeat(lock_id, device="gpu")

    try:
        audio_bytes = base64.b64decode(audio_base64)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(audio_bytes)
        tmp.close()

        stream_client = get_async_gpu_stream_client()
        result = await stream_client.request_diarization(
            audio_file_path=Path(tmp.name),
            min_speakers=int(min_speakers) if min_speakers else None,
            max_speakers=int(max_speakers) if max_speakers else None,
            timeout=1800.0,
        )

        content = json.dumps(result) if isinstance(result, dict) else str(result)
        return JSONResponse(build_openai_response(content, "diarization"))

    finally:
        if tmp:
            Path(tmp.name).unlink(missing_ok=True)
        if heartbeat:
            stop_lock_heartbeat(heartbeat)
        if own_lock and lock_id:
            await release_device_lock("gpu", lock_id)


async def _handle_ocr(body: dict) -> JSONResponse:
    """OCR (이미지 텍스트 추출) 요청 처리."""
    extra = body.get("extra_body", {})
    accuracy_mode = extra.get("accuracy_mode", "speed")
    file_id = extra.get("file_id")
    messages = body.get("messages", [])

    # 이미지 + 프롬프트 추출
    image_base64, text_prompt = _extract_vision_content(messages)

    if not image_base64:
        return JSONResponse({"error": "No image found in messages"}, status_code=400)

    # 서버리스 모드
    if DEPLOY_MODE == "serverless":
        return await _handle_ocr_serverless(body, accuracy_mode)

    # 모델 선택
    if accuracy_mode == "speed":
        ocr_model = NPU_OCR_MODEL
    else:
        ocr_model = GPU_OCR_MODEL

    try:
        # base64 프리앰블 제거 (data:image/jpeg;base64,...)
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]

        image_bytes = base64.b64decode(image_base64)

        stream_client = get_async_gpu_stream_client()
        result = await stream_client.request_ocr(
            image_data=image_bytes,
            model=ocr_model,
            prompt=text_prompt or "Extract all text from this image.",
            accuracy_mode=accuracy_mode,
            timeout=300.0,
            file_id=file_id,
        )

        # 결과 추출
        if isinstance(result, dict):
            content = result.get("text", result.get("content", json.dumps(result)))
            if "choices" in result and result["choices"]:
                content = result["choices"][0].get("message", {}).get("content", content)
        else:
            content = str(result)

        return JSONResponse(build_openai_response(content, ocr_model))

    except Exception as e:
        logger.error(f"[OCR] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


def _extract_vision_content(messages: list) -> tuple[Optional[str], Optional[str]]:
    """메시지에서 이미지 base64와 텍스트 프롬프트 추출."""
    image_base64 = None
    text_prompt = None

    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        image_base64 = url
                    elif item.get("type") == "text":
                        text_prompt = item.get("text")
        elif isinstance(content, str) and not text_prompt:
            text_prompt = content

    return image_base64, text_prompt


async def _handle_asr_serverless(
    audio_base64: str,
    language: str,
    accuracy_mode: str,
) -> JSONResponse:
    """서버리스 ASR 처리 (RunPod Whisper)."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=RUNPOD_ASR_BASE_URL, api_key=RUNPOD_API_KEY)

    audio_bytes = base64.b64decode(audio_base64)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(audio_bytes)
    tmp.close()

    try:
        with open(tmp.name, "rb") as f:
            result = await client.audio.transcriptions.create(
                model="whisper-large-v3-turbo" if accuracy_mode == "speed" else "whisper-large-v3",
                file=f,
                language=language,
            )
        content = json.dumps({"text": result.text, "language": language})
        return JSONResponse(build_openai_response(content, "whisper-serverless"))
    finally:
        Path(tmp.name).unlink(missing_ok=True)


async def _handle_ocr_serverless(body: dict, accuracy_mode: str) -> JSONResponse:
    """서버리스 OCR 처리 (RunPod Vision)."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=RUNPOD_VISION_BASE_URL, api_key=RUNPOD_API_KEY)

    response = await client.chat.completions.create(
        model="Qwen2-VL-7B-Instruct",
        messages=body.get("messages", []),
        max_tokens=body.get("max_tokens", 8192),
        temperature=body.get("temperature", 0.1),
    )
    return JSONResponse(response.model_dump())
