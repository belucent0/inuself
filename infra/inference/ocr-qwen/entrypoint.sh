#!/bin/bash
# llama-server entrypoint — auto-download GGUF model + mmproj from HF cache,
# then exec llama-server with resolved paths.
#
# Override the model via env (defaults: ggml-org/dots.ocr-GGUF Q8_0):
#   OCR_MODEL_REPO   — HF repo id
#   OCR_MODEL_FILE   — main GGUF filename
#   OCR_MMPROJ_FILE  — multimodal projector GGUF filename
#
# Cache is the docker named volume hf-cache-fast (mounted at /root/.cache/huggingface).

set -euo pipefail

: "${OCR_MODEL_REPO:=ggml-org/dots.ocr-GGUF}"
: "${OCR_MODEL_FILE:=dots.ocr-Q8_0.gguf}"
: "${OCR_MMPROJ_FILE:=mmproj-dots.ocr-Q8_0.gguf}"

echo "[entrypoint] resolving ${OCR_MODEL_REPO} (${OCR_MODEL_FILE} + ${OCR_MMPROJ_FILE}) ..."

MODEL=$(python3 -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('${OCR_MODEL_REPO}', '${OCR_MODEL_FILE}'))")
MMPROJ=$(python3 -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('${OCR_MODEL_REPO}', '${OCR_MMPROJ_FILE}'))")

echo "[entrypoint] model:  ${MODEL}"
echo "[entrypoint] mmproj: ${MMPROJ}"

exec llama-server \
  -m "${MODEL}" \
  --mmproj "${MMPROJ}" \
  -ngl 999 \
  --ctx-size "${OCR_CTX_SIZE:-4096}" \
  --host 0.0.0.0 \
  --port 8080 \
  --jinja \
  "$@"
