#!/bin/bash
# llama-server entrypoint for inference-embedding — EmbeddingGemma 308M Q4 GGUF.
# Google EmbeddingGemma (2025-09): 100+ 언어 multilingual MTEB 상위, 308M 경량.
#
# Override via env (defaults: ggml-org/embeddinggemma-300M-qat-q4_0-GGUF):
#   EMB_MODEL_REPO   — HF repo id
#   EMB_MODEL_FILE   — main GGUF filename
#   EMB_CTX_SIZE     — context window (default 2048)
#   EMB_POOLING      — mean/cls/last/none (default mean)
#
# Cache: docker named volume hf-cache-fast (mounted at /root/.cache/huggingface).

set -euo pipefail

: "${EMB_MODEL_REPO:=ggml-org/embeddinggemma-300M-qat-q4_0-GGUF}"
: "${EMB_MODEL_FILE:=embeddinggemma-300M-qat-Q4_0.gguf}"
: "${EMB_CTX_SIZE:=2048}"
: "${EMB_POOLING:=mean}"

echo "[entrypoint] resolving ${EMB_MODEL_REPO} (${EMB_MODEL_FILE}) ..."

MODEL=$(python3 -c "from huggingface_hub import hf_hub_download; print(hf_hub_download('${EMB_MODEL_REPO}', '${EMB_MODEL_FILE}'))")

echo "[entrypoint] model: ${MODEL}"

exec llama-server \
  -m "${MODEL}" \
  --embeddings \
  --pooling "${EMB_POOLING}" \
  --ctx-size "${EMB_CTX_SIZE}" \
  -ngl 0 \
  --poll 0 \
  --host 0.0.0.0 \
  --port 8000 \
  "$@"
