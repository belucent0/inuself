"""AI Gateway 설정 — 환경변수 및 상수 정의."""

import os

# ============================================================
# 배포 모드
# ============================================================
DEPLOY_MODE = os.getenv("DEPLOY_MODE", "local-gpu")  # "local-gpu" | "serverless"

# ============================================================
# Redis
# ============================================================
REDIS_URL = os.getenv("REDIS_URL", "redis://asr-valkey:6379/0")

# ============================================================
# 컨테이너 추론 백엔드 (refactor/inference)
# - OCR_BASE_URL: Gemma 4 multimodal vLLM (ai-llm 컨테이너)
#   chat/summary/OCR을 단일 모델로 통일
# ============================================================
OCR_BASE_URL = os.getenv("OCR_BASE_URL", "http://ai-llm:8000")
OCR_MODEL_NAME = os.getenv("OCR_MODEL_NAME", "gemma4-a4b")
OCR_REQUEST_TIMEOUT = float(os.getenv("OCR_REQUEST_TIMEOUT", "300"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://ai-llm:8000")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gemma4-a4b")
LLM_REQUEST_TIMEOUT = float(os.getenv("LLM_REQUEST_TIMEOUT", "300"))
NPU_LLM_BASE_URL = os.getenv("NPU_LLM_BASE_URL", "")
NPU_LLM_MODEL_NAME = os.getenv("NPU_LLM_MODEL_NAME", "gemma4-it:e4b")

ASR_BASE_URL = os.getenv("ASR_BASE_URL", "http://ai-asr-vllm:8000")
ASR_MODEL_NAME = os.getenv("ASR_MODEL_NAME", "whisper-large-v3-turbo")
ASR_REQUEST_TIMEOUT = float(os.getenv("ASR_REQUEST_TIMEOUT", "1800"))

DIARIZE_BASE_URL = os.getenv("DIARIZE_BASE_URL", "http://ai-diarize:8003")
DIARIZE_REQUEST_TIMEOUT = float(os.getenv("DIARIZE_REQUEST_TIMEOUT", "1800"))

EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://ai-embedding:8000")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "embeddinggemma-300m")
EMBEDDING_REQUEST_TIMEOUT = float(os.getenv("EMBEDDING_REQUEST_TIMEOUT", "30"))

# ============================================================
# 서버리스 모드 설정 (DEPLOY_MODE=serverless)
# ============================================================
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "")
RUNPOD_LLM_BASE_URL = os.getenv("RUNPOD_LLM_BASE_URL", "")
RUNPOD_ASR_BASE_URL = os.getenv("RUNPOD_ASR_BASE_URL", "")
RUNPOD_VISION_BASE_URL = os.getenv("RUNPOD_VISION_BASE_URL", "")
RUNPOD_EMBED_BASE_URL = os.getenv("RUNPOD_EMBED_BASE_URL", "")

# ============================================================
# Codex (CLIProxyAPI) — 양쪽 모드 공통
# ============================================================
CODEX_API_BASE = os.getenv("CODEX_API_BASE", "http://cli-proxy-api:8317/v1")
CODEX_API_KEY = os.getenv("CLIPROXY_API_KEY", "")
# ChatGPT 구독 OAuth 계정은 계정 유형상 이 모델 하나만 허용됨(2026-08 확인).
# 새 모델이 나오면 코드 수정 없이 env var만 바꾸면 되도록 분리.
CODEX_MODEL = os.getenv("CODEX_MODEL", "gpt-5.3-codex-spark")

# ============================================================
# API 인증
# ============================================================
GATEWAY_API_KEY = os.getenv("AI_GATEWAY_API_KEY", "")
