"""asr-whisper FastAPI server (ROCm-optimized).

Loads Whisper via AutoModelForSpeechSeq2Seq + transformers pipeline. On first
startup the model is preloaded and warmed up so the first /transcribe doesn't
pay the ~140s cold-load tax. SDPA attention is requested explicitly; on ROCm
we fall back to eager if SDPA model load raises (matches
beecave-homelab/insanely-fast-whisper-rocm behavior).

Endpoints:
- GET  /health      → liveness + model id + load state
- POST /transcribe  → multipart audio file → transcript JSON
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger("asr")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

_MODEL_ID = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3-turbo")
_DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"
_DTYPE = torch.float16 if _DEVICE.startswith("cuda") else torch.float32
_BATCH_SIZE = int(os.getenv("WHISPER_BATCH_SIZE", "24"))
_CHUNK_LENGTH_S = int(os.getenv("WHISPER_CHUNK_LENGTH_S", "30"))
_IS_ROCM = getattr(torch.version, "hip", None) is not None
_pipe: Any = None


def _load_pipeline() -> Any:
    """Build the transformers ASR pipeline with explicit SDPA + ROCm fallback."""
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    def _build(attn: str) -> Any:
        logger.info(f"loading whisper (model={_MODEL_ID}, device={_DEVICE}, dtype={_DTYPE}, attn={attn})")
        t0 = time.perf_counter()
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            _MODEL_ID,
            dtype=_DTYPE,
            use_safetensors=True,
            attn_implementation=attn,
        ).to(_DEVICE)
        processor = AutoProcessor.from_pretrained(_MODEL_ID)
        pipe = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=_DEVICE,
        )
        logger.info(f"pipeline ready in {time.perf_counter() - t0:.1f}s (attn={attn})")
        return pipe

    try:
        return _build("sdpa")
    except (RuntimeError, ValueError) as exc:
        if not _IS_ROCM:
            raise
        logger.warning(f"SDPA load failed on ROCm, falling back to eager: {exc}")
        return _build("eager")


def _warmup(pipe: Any) -> None:
    """Run a tiny dummy transcription to compile kernels & warm up GPU caches."""
    try:
        import numpy as np

        dummy = np.zeros(16000, dtype="float32")  # 1s of silence
        t0 = time.perf_counter()
        pipe(
            {"array": dummy, "sampling_rate": 16000},
            chunk_length_s=_CHUNK_LENGTH_S,
            batch_size=_BATCH_SIZE,
            return_timestamps=False,
            generate_kwargs={"language": "en", "task": "transcribe"},
        )
        logger.info(f"warmup done in {time.perf_counter() - t0:.1f}s")
    except Exception as exc:
        logger.warning(f"warmup failed (continuing): {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _pipe
    _pipe = _load_pipeline()
    _warmup(_pipe)
    yield


app = FastAPI(title="asr-whisper", version="1.1.0", lifespan=lifespan)


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
        "batch_size": _BATCH_SIZE,
        "chunk_length_s": _CHUNK_LENGTH_S,
        "rocm": _IS_ROCM,
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

        t0 = time.perf_counter()
        result = _pipe(
            {"array": waveform, "sampling_rate": sr},
            chunk_length_s=_CHUNK_LENGTH_S,
            batch_size=_BATCH_SIZE,
            return_timestamps=return_timestamps,
            generate_kwargs={
                "language": language,
                "task": "transcribe",
                # chunk 경계 반복 환각 방지 (Whisper 고전 이슈)
                "no_repeat_ngram_size": 3,
                "condition_on_prev_tokens": False,
                "compression_ratio_threshold": 2.4,
                "logprob_threshold": -1.0,
                "temperature": 0.0,
            },
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
                "batch_size": _BATCH_SIZE,
            }
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
