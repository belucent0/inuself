#!/usr/bin/env bash
set -e

: "${WHISPER_MODEL_FILE:=ggml-large-v3-turbo.bin}"
: "${WHISPER_MODEL_URL:=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${WHISPER_MODEL_FILE}}"
: "${WHISPER_THREADS:=8}"
: "${WHISPER_BACKEND_PORT:=8002}"

MODEL_PATH="/models/${WHISPER_MODEL_FILE}"

# named volume(whisper-models)에 모델이 없으면 다운로드. 이미지 layer 1.6GB 굽기 회피.
if [ ! -f "${MODEL_PATH}" ]; then
    echo "[entrypoint] downloading ${WHISPER_MODEL_FILE} → ${MODEL_PATH}"
    wget -q --show-progress --progress=bar:force -O "${MODEL_PATH}.tmp" "${WHISPER_MODEL_URL}"
    mv "${MODEL_PATH}.tmp" "${MODEL_PATH}"
fi

/usr/local/bin/whisper-server \
    --model "${MODEL_PATH}" \
    --host 127.0.0.1 \
    --port "${WHISPER_BACKEND_PORT}" \
    --language auto \
    --threads "${WHISPER_THREADS}" &

WHISPER_PID=$!

# Trap to forward SIGTERM/SIGINT to whisper-server
trap 'kill -TERM "${WHISPER_PID}" 2>/dev/null; wait "${WHISPER_PID}" 2>/dev/null; exit 0' SIGTERM SIGINT

# Start FastAPI adapter in foreground (this is the container's main process)
exec uvicorn adapter:app --host 0.0.0.0 --port 8001
