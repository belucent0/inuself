"""미디어 처리 라우트 — ASR, OCR, Diarization.

POST /v1/chat/completions 에서 task_type으로 분기되어 호출됩니다.
local-gpu 모드에서는 ai-gateway가 추론 컨테이너를 httpx로 직접 호출,
serverless 모드에서는 RunPod API로 위임합니다.
"""

import base64
import json
import logging
import sys
import tempfile
import wave
from array import array
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
from fastapi.responses import JSONResponse

from config import (
    DEPLOY_MODE,
    OCR_BASE_URL,
    OCR_MODEL_NAME,
    OCR_REQUEST_TIMEOUT,
    ASR_BASE_URL,
    ASR_MODEL_NAME,
    ASR_REQUEST_TIMEOUT,
    DIARIZE_BASE_URL,
    DIARIZE_REQUEST_TIMEOUT,
    RUNPOD_API_KEY,
    RUNPOD_ASR_BASE_URL,
    RUNPOD_VISION_BASE_URL,
)
from utils.response import build_openai_response

logger = logging.getLogger(__name__)

# ponytail: mirrors vLLM 0.20.1rc1 defaults; remove when upstream uses real chunk starts.
_VLLM_CHUNK_SECONDS = 30
_VLLM_SPLIT_SEARCH_SECONDS = 1
_VLLM_ENERGY_WINDOW_SECONDS = 0.1


def _vllm_chunk_timeline(audio_bytes: bytes) -> tuple[list[float], float] | None:
    """Reproduce vLLM's low-energy chunk boundaries for 16 kHz PCM WAV."""
    try:
        with wave.open(BytesIO(audio_bytes), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getframerate() != 16000
            ):
                return None
            sample_rate = wav.getframerate()
            total_samples = wav.getnframes()
            chunk_size = sample_rate * _VLLM_CHUNK_SECONDS
            search_size = sample_rate * _VLLM_SPLIT_SEARCH_SECONDS
            window_size = int(sample_rate * _VLLM_ENERGY_WINDOW_SECONDS)
            starts = [0.0]
            cursor = 0

            while cursor + chunk_size < total_samples:
                search_start = cursor + chunk_size - search_size
                wav.setpos(search_start)
                samples = array("h")
                samples.frombytes(wav.readframes(search_size))
                if sys.byteorder != "little":
                    samples.byteswap()

                offset = min(
                    range(0, len(samples) - window_size, window_size),
                    key=lambda start: sum(
                        sample * sample
                        for sample in samples[start : start + window_size]
                    ),
                )
                cursor = search_start + offset
                starts.append(cursor / sample_rate)

            return starts, total_samples / sample_rate
    except (EOFError, wave.Error):
        return None


def _correct_vllm_chunk_timestamps(
    segments: list[dict], audio_bytes: bytes
) -> list[dict]:
    """Correct vLLM offsets that use chunk_index * 30 instead of real starts."""
    timeline = _vllm_chunk_timeline(audio_bytes)
    if timeline is None:
        return segments

    starts, duration = timeline
    corrected = []
    max_shift = 0.0
    for original in segments:
        segment = dict(original)
        try:
            seek = float(segment["seek"])
        except (KeyError, TypeError, ValueError):
            corrected.append(segment)
            continue

        chunk_index = round(seek / _VLLM_CHUNK_SECONDS)
        nominal_start = chunk_index * _VLLM_CHUNK_SECONDS
        if chunk_index >= len(starts) or abs(seek - nominal_start) > 0.001:
            corrected.append(segment)
            continue

        delta = starts[chunk_index] - nominal_start
        max_shift = max(max_shift, abs(delta))
        chunk_end = (
            starts[chunk_index + 1]
            if chunk_index + 1 < len(starts)
            else duration
        )
        for key in ("start", "end"):
            if segment.get(key) is not None:
                segment[key] = min(
                    chunk_end,
                    max(starts[chunk_index], float(segment[key]) + delta),
                )
        segment["seek"] = starts[chunk_index]
        corrected.append(segment)

    if max_shift:
        logger.info(
            "[ASR] Corrected vLLM chunk timestamps: chunks=%d, max_shift=%.2fs",
            len(starts),
            max_shift,
        )
    return corrected


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

    ai-asr-vllm 컨테이너의 OpenAI 호환 endpoint(/v1/audio/transcriptions) 호출.
    vLLM이 verbose_json 응답을 반환하면 worker 호환 포맷(text/segments/language/model)으로 매핑한다.

    extra_body:
        audio_base64, language, accuracy_mode (호환만, 분기 안 함)
    """
    extra = body.get("extra_body", {})
    accuracy_mode = extra.get("accuracy_mode", "")  # 호환만, 분기 안 함
    language = extra.get("language", "ko")
    audio_base64 = extra.get("audio_base64", "")

    if not audio_base64:
        return JSONResponse({"error": "audio_base64 is required"}, status_code=400)

    # 서버리스 모드 (RunPod) — 별도 라우팅 유지
    if DEPLOY_MODE == "serverless":
        return await _handle_asr_serverless(audio_base64, language, accuracy_mode or "speed")

    audio_bytes = base64.b64decode(audio_base64)

    try:
        async with httpx.AsyncClient(timeout=ASR_REQUEST_TIMEOUT) as client:
            files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
            data = {
                "model": ASR_MODEL_NAME,
                "response_format": "verbose_json",
                "temperature": "0",
            }
            # language가 빈 값/None/auto이면 vLLM 자동 감지에 위임 (다국어 콘텐츠 환각 방지).
            # 명시 지정 시에만 강제 (한국어 단일 콘텐츠 등 운영 정책에 따른 hint).
            if language and language.lower() not in ("", "auto", "none"):
                data["language"] = language

            r = await client.post(
                f"{ASR_BASE_URL}/v1/audio/transcriptions", files=files, data=data
            )
            r.raise_for_status()
            vllm_result = r.json()

        # vLLM verbose_json → worker 호환 포맷 변환
        # vLLM 응답: {text, language, duration, segments: [{id, start, end, text, ...}]}
        # worker 기대: {text, segments: [{id, start, end, text}], language, model}
        # id 보존: frontend가 segment.id로 unique key/active 비교에 사용. 누락 시 모든 segment 동시 활성화 회귀.
        raw_segments = _correct_vllm_chunk_timestamps(
            vllm_result.get("segments", []), audio_bytes
        )
        segments = [
            {
                "id": seg.get("id", idx),
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "text": seg.get("text", ""),
            }
            for idx, seg in enumerate(raw_segments)
        ]
        worker_result = {
            "text": vllm_result.get("text", ""),
            "segments": segments,
            "language": vllm_result.get("language", language),
            "model": ASR_MODEL_NAME,
        }

        content = json.dumps(worker_result, ensure_ascii=False)
        return JSONResponse(build_openai_response(content, ASR_MODEL_NAME))

    except httpx.HTTPStatusError as e:
        logger.error(f"[ASR] upstream {e.response.status_code}: {e.response.text[:300]}")
        return JSONResponse({"error": f"ASR upstream error: {e.response.status_code}"}, status_code=502)
    except Exception as e:
        logger.error(f"[ASR] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def _handle_diarization(body: dict) -> JSONResponse:
    """Diarization (화자 분리) 요청 처리.

    refactor/inference: asr-diarize 컨테이너(FastAPI) 직접 호출.
    """
    extra = body.get("extra_body", {})
    audio_base64 = extra.get("audio_base64", "")
    min_speakers = extra.get("min_speakers")
    max_speakers = extra.get("max_speakers")

    if not audio_base64:
        return JSONResponse({"error": "audio_base64 is required"}, status_code=400)

    audio_bytes = base64.b64decode(audio_base64)

    try:
        async with httpx.AsyncClient(timeout=DIARIZE_REQUEST_TIMEOUT) as client:
            files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
            data: dict[str, str] = {}
            if min_speakers is not None:
                data["min_speakers"] = str(int(min_speakers))
            if max_speakers is not None:
                data["max_speakers"] = str(int(max_speakers))

            r = await client.post(f"{DIARIZE_BASE_URL}/diarize", files=files, data=data)
            r.raise_for_status()
            result = r.json()

        content = json.dumps(result, ensure_ascii=False)
        return JSONResponse(build_openai_response(content, result.get("model", "diarization")))

    except httpx.HTTPStatusError as e:
        logger.error(f"[Diarize] upstream {e.response.status_code}: {e.response.text[:300]}")
        return JSONResponse({"error": f"Diarize upstream error: {e.response.status_code}"}, status_code=502)
    except Exception as e:
        logger.error(f"[Diarize] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


async def _handle_ocr(body: dict) -> JSONResponse:
    """OCR (이미지 텍스트 추출) 요청 처리.

    local-gpu에서는 ai-llm의 Gemma 4 vision으로 처리한다.
    accuracy_mode는 이미지 전처리 품질 힌트이며 모델 분기에는 사용하지 않는다.
    """
    messages = body.get("messages", [])

    # 이미지가 있는지 형식만 확인 (직접 호출이라 base64 추출 불필요)
    image_url, _ = _extract_vision_content(messages)
    if not image_url:
        return JSONResponse({"error": "No image found in messages"}, status_code=400)

    # 서버리스 모드 (RunPod) — 별도 라우팅 유지
    if DEPLOY_MODE == "serverless":
        return await _handle_ocr_serverless(body)

    # 로컬 컨테이너 모드: ai-llm Gemma 4 vision 직접 호출
    payload = {
        "model": OCR_MODEL_NAME,
        "messages": messages,
        "max_tokens": body.get("max_tokens", 2048),
        "temperature": body.get("temperature", 0.1),
    }

    try:
        async with httpx.AsyncClient(timeout=OCR_REQUEST_TIMEOUT) as client:
            r = await client.post(f"{OCR_BASE_URL}/v1/chat/completions", json=payload)
            r.raise_for_status()
            return JSONResponse(r.json())

    except httpx.HTTPStatusError as e:
        logger.error(f"[OCR] upstream {e.response.status_code}: {e.response.text[:300]}")
        return JSONResponse({"error": f"OCR upstream error: {e.response.status_code}"}, status_code=502)
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


async def _handle_ocr_serverless(body: dict) -> JSONResponse:
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
