"""Diarization Server - pyannote.audio 기반 화자 분리 서버.

Host 머신에서 실행되며 GPU/ROCm을 사용하여 화자 분리를 수행합니다.
Architecture V6: 모델 On-Demand 로딩/언로딩으로 VRAM 관리.
"""
import sys
import os
from pathlib import Path
import logging
import time
import gc
import threading

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
import uvicorn
import torch
import librosa
import numpy as np
import tempfile
import shutil

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DiarizationServer")

# pyannote 모델 (lazy loading)
_diarization_pipeline = None
_model_lock = threading.Lock()

app = FastAPI(title="Diarization API Server", version="1.1")


@app.on_event("startup")
async def startup_event():
    """서버 시작 - 모델은 첫 요청 시 로드됨 (On-Demand)."""
    logger.info("Server started. Model will be loaded on first request (On-Demand mode).")


def get_diarization_pipeline():
    """pyannote diarization 파이프라인 로드 (lazy loading with thread safety)."""
    global _diarization_pipeline

    if _diarization_pipeline is None:
        with _model_lock:
            if _diarization_pipeline is None:  # Double-check locking
                logger.info("Loading pyannote diarization pipeline...")
                start_time = time.time()

                try:
                    from pyannote.audio import Pipeline

                    # HuggingFace 토큰 (환경변수에서 가져옴)
                    hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")

                    # 최신 pyannote는 'token' 파라미터 사용 (use_auth_token 대신)
                    _diarization_pipeline = Pipeline.from_pretrained(
                        "pyannote/speaker-diarization-3.1",
                        token=hf_token
                    )

                    # GPU 사용 설정
                    if torch.cuda.is_available():
                        _diarization_pipeline.to(torch.device("cuda"))
                        logger.info("Diarization pipeline loaded on CUDA")
                    else:
                        logger.info("Diarization pipeline loaded on CPU")

                    load_time = time.time() - start_time
                    logger.info(f"Pipeline loaded in {load_time:.2f}s")

                except Exception as e:
                    logger.error(f"Failed to load pyannote pipeline: {e}")
                    raise

    return _diarization_pipeline


def unload_diarization_pipeline():
    """모델 언로드 및 VRAM 해제."""
    global _diarization_pipeline

    with _model_lock:
        if _diarization_pipeline is not None:
            logger.info("Unloading diarization pipeline...")
            del _diarization_pipeline
            _diarization_pipeline = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
            logger.info("Pipeline unloaded, VRAM released")
            return True
    return False


def is_model_loaded() -> bool:
    """모델 로드 상태 확인."""
    return _diarization_pipeline is not None


def run_diarization(
    waveform,
    sample_rate: int,
    num_speakers: int = None,
    min_speakers: int = None,
    max_speakers: int = None,
):
    """
    pyannote를 사용하여 화자 분리를 수행합니다.

    Args:
        waveform: numpy array (samples,) or (channels, samples)
        sample_rate: 샘플링 레이트
        num_speakers: 화자 수 (지정 시 고정)
        min_speakers: 최소 화자 수
        max_speakers: 최대 화자 수

    Returns:
        (diarization_result, load_time, process_time)
    """
    load_start = time.time()
    pipeline = get_diarization_pipeline()
    load_time = time.time() - load_start

    # numpy -> torch tensor
    if not isinstance(waveform, torch.Tensor):
        waveform = torch.from_numpy(waveform)

    # (samples,) -> (1, samples)
    if waveform.dim() == 1:
        waveform = waveform.unsqueeze(0)

    # Stereo -> Mono
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)

    # pyannote 입력 형식
    audio_input = {"waveform": waveform, "sample_rate": sample_rate}

    # 파라미터 설정
    params = {}
    if num_speakers is not None:
        params["num_speakers"] = num_speakers
    if min_speakers is not None:
        params["min_speakers"] = min_speakers
    if max_speakers is not None:
        params["max_speakers"] = max_speakers

    logger.info(f"Running diarization with params: {params}")

    process_start = time.time()
    diarization_result = pipeline(audio_input, **params)
    process_time = time.time() - process_start

    return diarization_result, load_time, process_time


def extract_speaker_segments(diarization_result, include_metadata: bool = False):
    """
    화자 분리 결과에서 세그먼트 추출.

    Args:
        diarization_result: pyannote Annotation 또는 DiarizeOutput 객체
        include_metadata: True일 경우 딕셔너리 형태로 메타데이터 포함

    Returns:
        세그먼트 리스트
    """
    segments = []

    logger.info(f"Diarization result type: {type(diarization_result)}")
    logger.info(f"Diarization result class name: {type(diarization_result).__name__}")

    # pyannote 3.1+ DiarizeOutput 처리
    # DiarizeOutput은 speaker_diarization (Annotation) 속성을 가짐
    annotation_obj = None

    if hasattr(diarization_result, 'speaker_diarization'):
        # DiarizeOutput 객체 - .speaker_diarization 속성에 실제 Annotation 저장
        annotation_obj = diarization_result.speaker_diarization
        logger.info(f"Found .speaker_diarization attribute, type: {type(annotation_obj)}")
    elif hasattr(diarization_result, 'itertracks'):
        # 직접 Annotation 객체 (legacy 모드 또는 이전 버전)
        annotation_obj = diarization_result
        logger.info(f"Direct Annotation object with itertracks")
    else:
        # 알 수 없는 타입 - 가능한 속성 로깅
        attrs = [a for a in dir(diarization_result) if not a.startswith('_')]
        logger.warning(f"Unknown diarization result type. Available attrs: {attrs}")

    # Annotation 객체에서 세그먼트 추출
    if annotation_obj is not None and hasattr(annotation_obj, 'itertracks'):
        try:
            for turn, _, speaker in annotation_obj.itertracks(yield_label=True):
                if include_metadata:
                    segments.append({
                        "start": turn.start,
                        "end": turn.end,
                        "speaker": speaker,
                        "duration": turn.end - turn.start,
                    })
                else:
                    segments.append((turn.start, turn.end, speaker))
            logger.info(f"Extracted {len(segments)} segments via itertracks()")
        except Exception as e:
            logger.error(f"Failed to extract via itertracks: {e}")

    # 대체 방법: 직접 이터레이션 (annotation_obj가 없거나 itertracks 실패 시)
    if not segments:
        logger.warning("Trying direct iteration on diarization_result...")
        try:
            for item in diarization_result:
                if isinstance(item, tuple) and len(item) >= 3:
                    segment, _, speaker = item
                    if include_metadata:
                        segments.append({
                            "start": segment.start,
                            "end": segment.end,
                            "speaker": speaker,
                            "duration": segment.end - segment.start,
                        })
                    else:
                        segments.append((segment.start, segment.end, speaker))
                elif hasattr(item, 'start') and hasattr(item, 'end'):
                    speaker = getattr(item, 'speaker', getattr(item, 'label', 'SPEAKER_00'))
                    if include_metadata:
                        segments.append({
                            "start": item.start,
                            "end": item.end,
                            "speaker": str(speaker),
                            "duration": item.end - item.start,
                        })
                    else:
                        segments.append((item.start, item.end, str(speaker)))
            if segments:
                logger.info(f"Extracted {len(segments)} segments via direct iteration")
        except Exception as e:
            logger.error(f"Direct iteration failed: {e}")

    logger.info(f"Extracted {len(segments)} speaker segments")

    if include_metadata:
        segments.sort(key=lambda x: x["start"])
    else:
        segments.sort(key=lambda x: x[0])

    return segments


@app.get("/health")
def health_check():
    """서버 상태 확인 - 항상 200 반환 (모델은 lazy loading)."""
    return {
        "status": "ok",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
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
        "model": "pyannote/speaker-diarization-3.1",
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        **vram_info,
    }


@app.post("/unload")
def unload_model():
    """모델 언로드 및 VRAM 해제."""
    if unload_diarization_pipeline():
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
    get_diarization_pipeline()
    load_time = time.time() - start_time

    return {"status": "loaded", "load_time_seconds": round(load_time, 2)}


@app.post("/v1/audio/transcriptions")
async def transcriptions_openai_compat(
    file: UploadFile = File(...),
    model: str = Form("pyannote"),
    language: str = Form(None),
    prompt: str = Form(None),
    response_format: str = Form("verbose_json"),
    temperature: float = Form(None),
    timestamp_granularities: str = Form(None),
):
    """OpenAI-compatible transcription endpoint for LiteLLM proxy.

    This endpoint wraps diarization results in OpenAI transcription response format.
    LiteLLM calls this endpoint for audio transcription requests.
    """
    temp_path = None
    try:
        logger.info(f"[OpenAI Compat] Received transcription request: {file.filename}, model: {model}")

        # 임시 파일 저장
        suffix = Path(file.filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            shutil.copyfileobj(file.file, temp_file)
            temp_path = temp_file.name

        # 오디오 로드
        waveform_np, sample_rate = librosa.load(temp_path, sr=16000, mono=True)
        duration = len(waveform_np) / sample_rate
        logger.info(f"Audio loaded: {duration:.2f}s")

        # Diarization 실행
        diarization_result, load_time, process_time = run_diarization(waveform_np, sample_rate)

        # 세그먼트 추출
        segments = extract_speaker_segments(diarization_result, include_metadata=True)

        # OpenAI transcription 응답 형식으로 변환
        openai_segments = []
        text_parts = []

        for idx, seg in enumerate(segments):
            openai_segments.append({
                "id": idx,
                "seek": 0,
                "start": seg["start"],
                "end": seg["end"],
                "text": f"[{seg['speaker']}]",
                "speaker": seg["speaker"],
            })
            text_parts.append(f"[{seg['speaker']} {seg['start']:.2f}-{seg['end']:.2f}]")

        response = {
            "task": "diarize",
            "language": language or "unknown",
            "duration": duration,
            "text": " ".join(text_parts) if text_parts else "",
            "segments": openai_segments,
        }

        logger.info(f"[OpenAI Compat] Returning {len(openai_segments)} segments")
        return response

    except Exception as e:
        logger.error(f"[OpenAI Compat] Diarization failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp file: {e}")


@app.post("/v1/audio/diarization")
async def diarize_audio(
    file: UploadFile = File(...),
    model: str = Form("pyannote"),
    num_speakers: int = Form(None),
    min_speakers: int = Form(None),
    max_speakers: int = Form(None),
    return_embeddings: str = Form("false"),
):
    temp_path = None
    try:
        logger.info(f"Received diarization request for file: {file.filename}, model: {model}")

        # 임시 파일 저장 (Windows 호환성을 위해 NamedTemporaryFile delete=False 사용)
        suffix = Path(file.filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            shutil.copyfileobj(file.file, temp_file)
            temp_path = temp_file.name

        logger.info(f"Saved temp file to: {temp_path}")

        # 오디오 로드 (librosa 사용 - torchaudio보다 안정적)
        try:
            # librosa는 자동으로 mono로 변환하고, sr 파라미터로 리샘플링
            waveform_np, sample_rate = librosa.load(temp_path, sr=16000, mono=True)
            logger.info(f"Audio loaded: {len(waveform_np)/sample_rate:.2f}s, sr={sample_rate}")
        except Exception as e:
            logger.error(f"Failed to load audio file: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid audio file: {e}")

        # Diarization 실행
        logger.info("Running diarization...")
        diarization_result, load_time, process_time = run_diarization(
            waveform_np,
            sample_rate,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers
        )
        logger.info(f"Diarization completed. Load: {load_time:.2f}s, Process: {process_time:.2f}s")

        # 결과 변환
        segments = extract_speaker_segments(diarization_result, include_metadata=True)

        return {
            "model": model,
            "segments": segments,
            "metrics": {
                "load_time": load_time,
                "process_time": process_time
            }
        }

    except Exception as e:
        logger.error(f"Diarization failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 임시 파일 삭제
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                logger.info(f"Deleted temp file: {temp_path}")
            except Exception as e:
                logger.warning(f"Failed to delete temp file: {e}")


if __name__ == "__main__":
    logger.info("Starting server on port 8003 (model will be loaded on first request)")
    uvicorn.run(app, host="0.0.0.0", port=8003)
