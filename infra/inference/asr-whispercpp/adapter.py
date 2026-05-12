"""whisper.cpp HTTP adapter — wraps whisper-server /inference into /transcribe.

기존 ai-asr (transformers) FastAPI 인터페이스와 호환되도록 응답 변환:
  /transcribe (multipart: file, language, return_timestamps)
  → POST localhost:8002/inference
  → transform → JSON {text, chunks, duration_seconds, wall_seconds, rtf, model}

ai-gateway는 ASR_BASE_URL만 바뀌고 코드 변경 불필요.

whisper-server는 동일 컨테이너 안에서 별도 process로 실행 (entrypoint.sh 참고).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger("asr-whispercpp-adapter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

_WHISPER_HOST = os.getenv("WHISPER_BACKEND_HOST", "127.0.0.1")
_WHISPER_PORT = int(os.getenv("WHISPER_BACKEND_PORT", "8002"))
_WHISPER_URL = f"http://{_WHISPER_HOST}:{_WHISPER_PORT}"
_MODEL_NAME = os.getenv("WHISPER_MODEL", "whisper-large-v3-turbo")
# ai-gateway/config.py의 ASR_REQUEST_TIMEOUT과 동일한 env 이름·기본값 사용.
_REQUEST_TIMEOUT_S = float(os.getenv("ASR_REQUEST_TIMEOUT", "1800"))


async def _wait_whisper_ready(timeout_s: float = 600.0) -> None:
    """Block until whisper-server responds with HTTP 200 on /."""
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(_WHISPER_URL + "/")
                if r.status_code == 200:
                    logger.info(f"whisper-server ready at {_WHISPER_URL}")
                    return
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            await asyncio.sleep(0.2)
    raise RuntimeError(f"whisper-server did not become ready at {_WHISPER_URL} within {timeout_s}s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(f"waiting for whisper-server at {_WHISPER_URL} ...")
    await _wait_whisper_ready()
    yield


app = FastAPI(title="asr-whispercpp-adapter", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(_WHISPER_URL + "/")
            return {"status": "ok", "backend": "whisper.cpp", "model": _MODEL_NAME, "upstream": r.status_code}
    except Exception as exc:
        return {"status": "degraded", "backend": "whisper.cpp", "model": _MODEL_NAME, "error": str(exc)}


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("ko"),
    return_timestamps: bool = Form(True),
) -> JSONResponse:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio")

    # Duration은 header만 파싱 (waveform 전체 디코드 X — 14:45 = ~115MB 절약).
    # whisper-server는 ffmpeg로 자체 디코드하므로 mp4/m4a 등은 sf.info 실패 — 0.0으로 fallback.
    try:
        info = sf.info(io.BytesIO(raw))
        duration_s = float(info.frames / info.samplerate)
    except Exception:
        duration_s = 0.0

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            files = {"file": (file.filename or "audio.wav", raw, file.content_type or "audio/wav")}
            data = {
                "language": language,
                "response_format": "verbose_json",  # segments 포함 (worker 화자분리 merge에 필수)
                "temperature": "0.0",
            }
            resp = await client.post(f"{_WHISPER_URL}/inference", files=files, data=data)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"whisper-server upstream error: {exc}") from exc

    elapsed = time.perf_counter() - t0

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"whisper-server returned {resp.status_code}: {resp.text[:200]}")

    payload = resp.json()
    # worker/pipelines/asr/pipeline.py:290 의 `asr_result.get("segments")` 와 매칭되는 스키마.
    # whisper-server verbose_json은 segment.start/end를 float로 제공 (가끔 첫 segment의 start가 null → prev_end).
    segments: list[dict[str, Any]] = []
    prev_end = 0.0
    for idx, seg in enumerate(payload.get("segments", []) or []):
        start = seg.get("start")
        end = seg.get("end")
        t_from = float(start) if start is not None else prev_end
        t_to = float(end) if end is not None else t_from
        segments.append({"id": idx, "start": t_from, "end": t_to, "text": seg.get("text", "")})
        prev_end = t_to

    return JSONResponse(
        {
            "text": payload.get("text", ""),
            "segments": segments,
            "language": language,
            "duration_seconds": round(duration_s, 2),
            "wall_seconds": round(elapsed, 2),
            "rtf": round(elapsed / duration_s, 3) if duration_s > 0 else None,
            "model": _MODEL_NAME,
            "backend": "whisper.cpp",
        }
    )
