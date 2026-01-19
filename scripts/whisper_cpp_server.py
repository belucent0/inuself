"""Whisper.cpp Server - OpenAI-compatible wrapper for whisper.cpp.

Port 8001에서 실행되며 whisper-cli.exe를 사용하여 ASR을 수행합니다.
OpenAI-compatible /v1/audio/transcriptions 엔드포인트를 제공합니다.
"""
import sys
import logging

# 디버깅용 즉시 출력
print("DEBUG: whisper_cpp_server.py starting...", file=sys.stderr, flush=True)

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import librosa
import soundfile as sf
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WhisperCppServer")

# Environment variables
PORT = int(os.getenv("PORT", 8001))
WHISPER_CLI_PATH = os.getenv(
    "WHISPER_CLI_PATH",
    "C:/whisper-cpp/build/bin/Release/whisper-cli.exe"
)
WHISPER_MODELS_DIR = os.getenv(
    "WHISPER_MODELS_DIR",
    "C:/whisper-cpp/models"
)
DEFAULT_MODEL = os.getenv("WHISPER_MODEL", "large-v3-turbo")

app = FastAPI(title="Whisper.cpp Server (OpenAI Compatible)", version="1.0")


def _parse_whispercpp_json(json_data: dict) -> dict:
    """Convert whisper.cpp JSON output to OpenAI format."""
    result = {
        "text": "",
        "language": json_data.get("result", {}).get("language", "ko"),
        "segments": []
    }

    transcription = json_data.get("transcription", [])
    all_texts = []

    for i, seg in enumerate(transcription):
        from_ts = seg.get("timestamps", {}).get("from", "00:00:00,000")
        to_ts = seg.get("timestamps", {}).get("to", "00:00:00,000")

        def parse_timestamp(ts_str: str) -> float:
            """Convert 00:00:06,000 format to seconds."""
            parts = ts_str.split(",")
            time_part = parts[0]
            h, m, s = map(int, time_part.split(":"))
            seconds = h * 3600 + m * 60 + s
            if len(parts) > 1:
                milliseconds = int(parts[1])
                seconds += milliseconds / 1000.0
            return seconds

        start = parse_timestamp(from_ts)
        end = parse_timestamp(to_ts)
        text = seg.get("text", "").strip()

        all_texts.append(text)

        result["segments"].append({
            "id": i,
            "seek": 0,
            "start": start,
            "end": end,
            "text": text,
            "tokens": [],
            "temperature": 0.0,
            "avg_logprob": 0.0,
            "compression_ratio": 0.0,
            "no_speech_prob": 0.0,
        })

    result["text"] = " ".join(all_texts)
    return result


def _get_model_path(model_size: str = "turbo") -> str:
    """Get model file path."""
    model_mapping = {
        "tiny": "ggml-tiny.bin",
        "base": "ggml-base.bin",
        "small": "ggml-small.bin",
        "medium": "ggml-medium.bin",
        "large": "ggml-large-v3.bin",
        "large-v3": "ggml-large-v3.bin",
        "turbo": "ggml-large-v3-turbo.bin",
        "large-v3-turbo": "ggml-large-v3-turbo.bin",
        "whisper-turbo": "ggml-large-v3-turbo.bin",
    }

    filename = model_mapping.get(model_size, "ggml-large-v3-turbo.bin")
    model_path = os.path.join(WHISPER_MODELS_DIR, filename)

    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found: {model_path}")

    return model_path


def _transcribe(audio_path: str, language: Optional[str], model_size: str) -> dict:
    """Run whisper-cli.exe and return OpenAI-format result."""
    logger.info(f"Transcribing with whisper.cpp ({model_size}): {audio_path}")
    start_time = time.time()

    # Get model path
    model_path = _get_model_path(model_size)
    logger.info(f"Using model: {model_path}")

    # Convert to WAV if needed
    audio_path_obj = Path(audio_path)
    temp_wav_path = None
    use_temp_wav = False

    if audio_path_obj.suffix.lower() not in ['.wav', '.wave']:
        logger.info("Converting audio to WAV format...")
        temp_wav_fd, temp_wav_path = tempfile.mkstemp(suffix='.wav')
        os.close(temp_wav_fd)

        try:
            waveform, sample_rate = librosa.load(str(audio_path), sr=16000)
            sf.write(temp_wav_path, waveform, sample_rate)
            use_temp_wav = True
            actual_audio_path = temp_wav_path
        except Exception as e:
            logger.warning(f"WAV conversion failed: {e}")
            actual_audio_path = str(audio_path)
            if temp_wav_path and os.path.exists(temp_wav_path):
                os.unlink(temp_wav_path)
                temp_wav_path = None
    else:
        actual_audio_path = str(audio_path)

    # Temp JSON output file
    json_fd, json_output_base = tempfile.mkstemp(suffix='')
    os.close(json_fd)
    os.unlink(json_output_base)  # whisper.cpp adds .json extension

    try:
        # Build whisper-cli.exe command
        cmd = [
            WHISPER_CLI_PATH,
            "-m", model_path,
            "-l", language or "ko",
            "--output-json-full",
            "--output-file", json_output_base,
            actual_audio_path,
        ]

        # Environment variables (Vulkan GPU)
        env = os.environ.copy()
        env.setdefault("GGML_VULKAN_DEVICE", "0")

        # Windows process creation flags
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        # Execute
        logger.info("Running whisper-cli.exe...")
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            creationflags=creation_flags,
            timeout=1800,  # 30 minutes timeout
        )

        if result.returncode != 0:
            raise RuntimeError(f"whisper-cli.exe failed: {result.stderr}")

        # Read JSON file
        json_file = json_output_base + '.json'
        if not os.path.exists(json_file):
            raise FileNotFoundError(f"JSON output not found: {json_file}")

        with open(json_file, 'rb') as f:
            content = f.read()
        if content.startswith(b'\xef\xbb\xbf'):
            content = content[3:]  # Remove BOM
        json_data = json.loads(content.decode('utf-8', errors='replace'))

        # Convert to OpenAI format
        parsed = _parse_whispercpp_json(json_data)

        transcribe_time = time.time() - start_time
        logger.info(f"Transcription completed in {transcribe_time:.2f}s")

        return parsed

    finally:
        # Cleanup temp files
        json_file = json_output_base + '.json'
        if os.path.exists(json_file):
            try:
                os.unlink(json_file)
            except:
                pass
        if use_temp_wav and temp_wav_path and os.path.exists(temp_wav_path):
            try:
                os.unlink(temp_wav_path)
            except:
                pass


@app.post("/v1/audio/transcriptions")
async def transcribe_audio(
    file: UploadFile = File(...),
    model: str = Form("whisper-turbo"),
    language: Optional[str] = Form(None),
    response_format: str = Form("verbose_json"),
    temperature: float = Form(0.0),
):
    """OpenAI-compatible transcription endpoint."""
    temp_path = None

    try:
        logger.info(f"Received transcription request: {file.filename}, model: {model}")

        # Save to temp file
        suffix = Path(file.filename).suffix or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_path = temp_file.name

        logger.info(f"Saved temp file: {temp_path} ({len(content)} bytes)")

        # Determine model size from model name
        model_lower = model.lower()
        if "turbo" in model_lower:
            model_size = "turbo"
        elif "large-v3" in model_lower:
            model_size = "large-v3"
        elif "large" in model_lower:
            model_size = "large"
        else:
            model_size = DEFAULT_MODEL

        # Transcribe
        result = _transcribe(temp_path, language, model_size)

        # Calculate duration from segments
        duration = None
        if result.get("segments"):
            duration = max(seg["end"] for seg in result["segments"])

        # Return OpenAI-compatible response
        response = {
            "task": "transcribe",
            "language": result.get("language", language or "ko"),
            "duration": duration,
            "text": result["text"],
            "segments": result["segments"],
        }

        logger.info(f"Returning {len(result['segments'])} segments")
        return response

    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass


@app.get("/health")
def health_check():
    """Health check endpoint."""
    cli_exists = os.path.exists(WHISPER_CLI_PATH)
    return {
        "status": "ok" if cli_exists else "error",
        "whisper_cli": WHISPER_CLI_PATH,
        "cli_exists": cli_exists,
    }


@app.get("/")
def root():
    """Root endpoint."""
    return {
        "service": "Whisper.cpp Server",
        "version": "1.0",
        "endpoints": {
            "transcription": "/v1/audio/transcriptions",
            "health": "/health",
        }
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
