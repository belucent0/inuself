"""AI Gateway 설정 — 환경변수 및 상수 정의."""

import os
import importlib.util

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
OCR_MODEL_NAME = os.getenv("OCR_MODEL_NAME", "gemma4-12b")
OCR_REQUEST_TIMEOUT = float(os.getenv("OCR_REQUEST_TIMEOUT", "300"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://ai-llm:8000")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gemma4-12b")
LLM_REQUEST_TIMEOUT = float(os.getenv("LLM_REQUEST_TIMEOUT", "300"))

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

# ============================================================
# Tier Config (infra/shared/tier_config.py)
# ============================================================
def _import_tier_config():
    """tier_config.py를 경로 기반으로 직접 import."""
    search_paths = [
        "/app/infra/shared/tier_config.py",
        os.path.join(os.path.dirname(__file__), "..", "infra", "shared", "tier_config.py"),
        os.path.join(os.path.dirname(__file__), "..", "..", "shared", "tier_config.py"),
    ]
    for path in search_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            spec = importlib.util.spec_from_file_location("tier_config", abs_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(f"tier_config.py not found in: {search_paths}")

_tier_config = _import_tier_config()
TIER_MODEL_MAP = _tier_config.TIER_MODEL_MAP
resolve_tier_to_model = _tier_config.resolve_tier_to_model
get_routing_policy = _tier_config.get_routing_policy

# ============================================================
# API 인증
# ============================================================
GATEWAY_API_KEY = os.getenv("AI_GATEWAY_API_KEY", "")
