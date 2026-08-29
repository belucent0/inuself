"""asr-diarize FastAPI server.

pyannote.audio (community-1) speaker diarization. Same path as
bench/pyannote_smoke.py — soundfile decode + in-memory dict to bypass
torchcodec ABI mismatch with ROCm torch.

Endpoints:
- GET  /health    → liveness + model id + load state
- POST /diarize   → multipart audio file → speaker turns JSON
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse

logger = logging.getLogger("diarize")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

app = FastAPI(title="asr-diarize", version="1.0.0")

_MODEL_ID = os.getenv("DIAR_MODEL", "pyannote/speaker-diarization-community-1")
_WARMUP_AUDIO = os.getenv(
    "DIAR_WARMUP_AUDIO",
    "/opt/venv/lib/python3.12/site-packages/pyannote/audio/sample/sample.wav",
)
_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_pipe: Any = None


def _find_token() -> Optional[str]:
    tok = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if tok:
        return tok
    for p in ("/root/.cache/huggingface/token", os.path.expanduser("~/.cache/huggingface/token")):
        try:
            return open(p).read().strip()
        except FileNotFoundError:
            pass
    return None


def _get_pipe() -> Any:
    global _pipe
    if _pipe is not None:
        return _pipe

    logger.info(f"loading diarization pipeline (model={_MODEL_ID}, device={_DEVICE})")
    t0 = time.perf_counter()
    from pyannote.audio import Pipeline

    _pipe = Pipeline.from_pretrained(_MODEL_ID, token=_find_token())
    if _DEVICE == "cuda":
        _pipe.to(torch.device("cuda"))
    logger.info(f"pipeline ready in {time.perf_counter() - t0:.1f}s")
    return _pipe


def _load_audio(path: str | Path) -> tuple[dict[str, Any], float]:
    waveform, sr = sf.read(path, dtype="float32", always_2d=True)
    waveform_t = torch.from_numpy(waveform.T)
    return {"waveform": waveform_t, "sample_rate": sr}, float(waveform_t.shape[1] / sr)


@app.on_event("startup")
def load_pipeline() -> None:
    pipe = _get_pipe()
    audio_input, _ = _load_audio(_WARMUP_AUDIO)
    t0 = time.perf_counter()
    with torch.inference_mode():
        pipe(audio_input)
    logger.info(f"pipeline warm-up ready in {time.perf_counter() - t0:.1f}s")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "model": _MODEL_ID,
        "device": _DEVICE,
        "loaded": _pipe is not None,
    }


@app.post("/diarize")
async def diarize(
    file: UploadFile = File(...),
    min_speakers: Optional[int] = Form(None),
    max_speakers: Optional[int] = Form(None),
) -> JSONResponse:
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # soundfile decode — bypass torchcodec
        audio_input, duration_s = _load_audio(tmp_path)

        pipe = _get_pipe()
        kwargs: dict[str, Any] = {}
        if min_speakers is not None:
            kwargs["min_speakers"] = int(min_speakers)
        if max_speakers is not None:
            kwargs["max_speakers"] = int(max_speakers)

        t0 = time.perf_counter()
        out = pipe(audio_input, **kwargs)
        elapsed = time.perf_counter() - t0

        # pyannote 4.x returns DiarizeOutput with .speaker_diarization Annotation
        annotation = getattr(out, "speaker_diarization", out)

        segments = []
        speakers: set[str] = set()
        total_speech = 0.0
        for turn, _track, speaker in annotation.itertracks(yield_label=True):
            segments.append(
                {
                    "start": float(turn.start),
                    "end": float(turn.end),
                    "speaker": str(speaker),
                }
            )
            speakers.add(str(speaker))
            total_speech += turn.end - turn.start

        return JSONResponse(
            {
                "segments": segments,
                "speakers": sorted(speakers),
                "num_speakers": len(speakers),
                "total_speech_seconds": round(total_speech, 2),
                "duration_seconds": round(duration_s, 2),
                "wall_seconds": round(elapsed, 2),
                "rtf": round(elapsed / duration_s, 3) if duration_s > 0 else None,
                "model": _MODEL_ID,
            }
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
