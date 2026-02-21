import os
import uvicorn
import torch
import logging
import gc
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from transformers import pipeline
from pydantic import BaseModel
from typing import List, Optional
import time
import shutil
import threading

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InsanelyFastServer")

# 환경 설정
PORT = int(os.getenv("PORT", 12011))
MODEL_ID = "openai/whisper-large-v3"
DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"
TORCH_DTYPE = torch.float16 if torch.cuda.is_available() else torch.float32

app = FastAPI(title="Insanely Fast Whisper Server", version="1.1")

# 모델 상태 관리 (Lazy Loading)
_pipe = None
_model_lock = threading.Lock()


def get_pipeline():
    """모델 파이프라인 반환 (Lazy Loading)."""
    global _pipe

    if _pipe is None:
        with _model_lock:
            if _pipe is None:  # Double-check locking
                logger.info(f"Loading model: {MODEL_ID} on {DEVICE}...")
                start_time = time.time()
                _pipe = pipeline(
                    "automatic-speech-recognition",
                    model=MODEL_ID,
                    torch_dtype=TORCH_DTYPE,
                    device=DEVICE,
                    model_kwargs={"attn_implementation": "sdpa"},
                )
                load_time = time.time() - start_time
                logger.info(f"Model loaded successfully in {load_time:.2f}s")

    return _pipe


def unload_pipeline():
    """모델 언로드 및 VRAM 해제."""
    global _pipe

    with _model_lock:
        if _pipe is not None:
            logger.info("Unloading model...")
            del _pipe
            _pipe = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
            logger.info("Model unloaded, VRAM released")
            return True
    return False


def is_model_loaded() -> bool:
    """모델 로드 상태 확인."""
    return _pipe is not None

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("whisper-large-v3"),
    language: Optional[str] = Form(None),
    response_format: str = Form("json"),
):
    """OpenAI 호환 Transcription API"""
    temp_filename = f"temp_{file.filename}"

    try:
        # 파일 저장
        with open(temp_filename, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        start_time = time.time()

        # 모델 로드 (Lazy Loading)
        pipe = get_pipeline()

        # 추론 실행 (Whisper 네이티브 long-form transcription 사용)
        # chunk_length_s 제거 → Whisper가 자체 청킹 메커니즘으로 정확한 타임스탬프 생성
        # 참고: https://huggingface.co/blog/asr-chunking
        result = pipe(
            temp_filename,
            batch_size=24,
            return_timestamps="word",  # 단어 수준 타임스탬프 (정확도 향상)
            generate_kwargs={"language": language, "task": "transcribe"} if language else {"task": "transcribe"}
        )

        process_time = time.time() - start_time
        print(f"Processed in {process_time:.2f}s")

        # OpenAI 포맷 변환 (word-level → segment-level 집계)
        text = result["text"]
        segments = []

        if "chunks" in result:
            # 단어 수준 타임스탬프를 문장 단위로 그룹화 (구두점 기준)
            current_segment = {"text": "", "start": None, "end": None}

            for chunk in result["chunks"]:
                word_text = chunk["text"]
                ts = chunk.get("timestamp", (None, None))
                word_start, word_end = ts if ts else (None, None)

                if current_segment["start"] is None and word_start is not None:
                    current_segment["start"] = word_start

                current_segment["text"] += word_text
                if word_end is not None:
                    current_segment["end"] = word_end

                # 문장 종결 (. ? ! 또는 일정 길이) 시 세그먼트 완료
                if any(p in word_text for p in ['.', '?', '!', '。', '？', '！']) or len(current_segment["text"]) > 200:
                    if current_segment["text"].strip() and current_segment["start"] is not None:
                        segments.append({
                            "id": len(segments),
                            "seek": 0,
                            "start": current_segment["start"],
                            "end": current_segment["end"] or current_segment["start"] + 1.0,
                            "text": current_segment["text"].strip(),
                            "tokens": [],
                            "temperature": 0.0,
                            "avg_logprob": 0.0,
                            "compression_ratio": 0.0,
                            "no_speech_prob": 0.0,
                        })
                    current_segment = {"text": "", "start": None, "end": None}

            # 남은 텍스트 처리
            if current_segment["text"].strip() and current_segment["start"] is not None:
                segments.append({
                    "id": len(segments),
                    "seek": 0,
                    "start": current_segment["start"],
                    "end": current_segment["end"] or current_segment["start"] + 1.0,
                    "text": current_segment["text"].strip(),
                    "tokens": [],
                    "temperature": 0.0,
                    "avg_logprob": 0.0,
                    "compression_ratio": 0.0,
                    "no_speech_prob": 0.0,
                })

        print(f"Generated {len(segments)} segments from word-level timestamps")

        return {
            "text": text,
            "segments": segments,
            "language": language or "unknown"
        }

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 임시 파일 삭제
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

@app.get("/health")
def health_check():
    """서버 상태 확인 - 항상 200 반환 (모델은 lazy loading)."""
    return {
        "status": "ok",
        "device": DEVICE,
        "model": MODEL_ID,
        "model_loaded": is_model_loaded(),
    }


@app.get("/status")
def model_status():
    """모델 로드 상태 상세 정보."""
    vram_info = {}
    if torch.cuda.is_available():
        vram_info = {
            "vram_allocated_mb": round(torch.cuda.memory_allocated() / 1024 / 1024, 2),
            "vram_reserved_mb": round(torch.cuda.memory_reserved() / 1024 / 1024, 2),
        }
    return {
        "model_loaded": is_model_loaded(),
        "model_id": MODEL_ID,
        "device": DEVICE,
        **vram_info,
    }


@app.post("/unload")
def unload_model():
    """모델 언로드 및 VRAM 해제."""
    if unload_pipeline():
        vram_info = {}
        if torch.cuda.is_available():
            vram_info = {
                "vram_allocated_mb": round(torch.cuda.memory_allocated() / 1024 / 1024, 2),
                "vram_reserved_mb": round(torch.cuda.memory_reserved() / 1024 / 1024, 2),
            }
        return {"status": "unloaded", **vram_info}
    return {"status": "already_unloaded"}


@app.post("/load")
def load_model():
    """모델 명시적 로드 (선택적)."""
    if is_model_loaded():
        return {"status": "already_loaded"}

    start_time = time.time()
    get_pipeline()
    load_time = time.time() - start_time

    return {"status": "loaded", "load_time_seconds": round(load_time, 2)}


if __name__ == "__main__":
    logger.info(f"Starting server on port {PORT} (model will be loaded on first request)")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
