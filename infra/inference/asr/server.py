"""asr-whisper FastAPI server.

Loads Whisper (transformers pipeline, same path as bench/whisper_smoke.py)
on first request and keeps it resident. Subsequent requests skip cold load.

Endpoints:
- GET  /health      → liveness + model id + load state
- POST /transcribe  → multipart audio file → transcript JSON
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger("asr")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

app = FastAPI(title="asr-whisper", version="1.0.0")

_MODEL_ID = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3-turbo")
_DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"
_DTYPE = torch.float16 if _DEVICE.startswith("cuda") else torch.float32
_pipe: Any = None


def _get_pipe() -> Any:
    """Lazy-load the transformers pipeline; cached after first call."""
    global _pipe
    if _pipe is not None:
        return _pipe

    logger.info(f"loading whisper pipeline (model={_MODEL_ID}, device={_DEVICE}, dtype={_DTYPE})")
    t0 = time.perf_counter()
    from transformers import pipeline

    _pipe = pipeline(
        "automatic-speech-recognition",
        model=_MODEL_ID,
        torch_dtype=_DTYPE,
        device=_DEVICE,
    )
    logger.info(f"pipeline ready in {time.perf_counter() - t0:.1f}s")
    return _pipe


def _load_audio(path: str) -> tuple[Any, int]:
    """soundfile로 파일 디코드 → (mono float32 array, 16000Hz)."""
    waveform, sr = sf.read(path, dtype="float32", always_2d=False)
    if waveform.ndim > 1:
        waveform = waveform.mean(axis=1)
    if sr != 16000:
        import torchaudio.functional as F

        waveform = (
            F.resample(torch.from_numpy(waveform).unsqueeze(0), sr, 16000)
            .squeeze(0)
            .numpy()
        )
        sr = 16000
    return waveform, sr


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model": _MODEL_ID,
        "device": _DEVICE,
        "loaded": _pipe is not None,
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("ko"),
    return_timestamps: bool = Form(True),
) -> JSONResponse:
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        waveform, sr = _load_audio(tmp_path)
        duration_s = float(waveform.shape[0] / sr)
        pipe = _get_pipe()

        t0 = time.perf_counter()
        result = pipe(
            {"array": waveform, "sampling_rate": sr},
            chunk_length_s=30,
            batch_size=8,
            return_timestamps=return_timestamps,
            generate_kwargs={"language": language, "task": "transcribe"},
        )
        elapsed = time.perf_counter() - t0

        text = result.get("text", "") if isinstance(result, dict) else str(result)
        chunks = result.get("chunks", []) if isinstance(result, dict) else []
        return JSONResponse(
            {
                "text": text,
                "chunks": chunks,
                "language": language,
                "duration_seconds": round(duration_s, 2),
                "wall_seconds": round(elapsed, 2),
                "rtf": round(elapsed / duration_s, 3) if duration_s > 0 else None,
                "model": _MODEL_ID,
            }
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
