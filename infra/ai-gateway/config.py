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
# Provider 분류
# ============================================================
# Provider Manager Redis status key 매핑
PROVIDER_REDIS_STATUS_KEY = {
    "whisper-cpp": "whisper-server",
    "insanely-fast": "insanely-fast-server",
    "diarization-server": "diarization-server",
    "llama": "llama-server",
}

# Device Group 매핑 (provider name → device group)
DEVICE_GROUP_MAP = {
    "flm": "npu",
    "llama": "gpu",
    "whisper-cpp": "gpu",
    "insanely-fast": "gpu",
    "diarization-server": "gpu",
}

# ============================================================
# 로컬 GPU/NPU 서버 주소 (local-gpu 모드)
# ============================================================
GPU_API_BASE = os.getenv("GPU_API_BASE", "http://host.docker.internal:8080")
NPU_API_BASE = os.getenv("NPU_API_BASE", "http://host.docker.internal:11434")
GPU_WHISPER_CPP_API_BASE = os.getenv("GPU_WHISPER_CPP_API_BASE", "http://host.docker.internal:8001")
GPU_INSANELY_FAST_API_BASE = os.getenv("GPU_INSANELY_FAST_API_BASE", "http://host.docker.internal:8002")

# ============================================================
# 모델명 (로컬)
# ============================================================
GPU_MODEL = os.getenv("GPU_MODEL", "Qwen3-4B-Instruct-2507-Q4_K_S.gguf")
NPU_MODEL = os.getenv("NPU_MODEL", "qwen3vl-it:4b")
GPU_AUDIO_MODEL = os.getenv("GPU_AUDIO_MODEL", "whisper-turbo")
NPU_AUDIO_MODEL = os.getenv("NPU_AUDIO_MODEL", "flm-audio")

# ============================================================
# OCR 모델명 (Provider Manager / 서버리스 fallback용 — local-gpu 모드는 OCR_BASE_URL 사용)
# ============================================================
NPU_OCR_MODEL = os.getenv("NPU_OCR_MODEL", "qwen3vl-it:4b")
GPU_OCR_MODEL = os.getenv("GPU_OCR_MODEL", "qwen3-vl-8b")

# ============================================================
# 컨테이너 추론 백엔드 (refactor/inference)
# - OCR_BASE_URL: dots.ocr llama-server (asr-ocr 컨테이너)
#   accuracy/speed 분기 없이 단일 모델로 통일
# ============================================================
OCR_BASE_URL = os.getenv("OCR_BASE_URL", "http://asr-ocr:8080")
OCR_MODEL_NAME = os.getenv("OCR_MODEL_NAME", "dots.ocr")
OCR_REQUEST_TIMEOUT = float(os.getenv("OCR_REQUEST_TIMEOUT", "300"))

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://asr-llm:8000")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gemma-4-E4B-it")
LLM_REQUEST_TIMEOUT = float(os.getenv("LLM_REQUEST_TIMEOUT", "300"))

# ============================================================
# Provider Health Check URL
# ============================================================
HEALTH_CHECK_TIMEOUT = float(os.getenv("HEALTH_CHECK_TIMEOUT", "3.0"))

PROVIDER_HEALTH_URLS = {
    "llama": f"{GPU_API_BASE}/health",
    "flm": f"{NPU_API_BASE}/v1/models",
    "whisper-cpp": GPU_WHISPER_CPP_API_BASE,
    "insanely-fast": f"{GPU_INSANELY_FAST_API_BASE}/health",
    "diarization-server": "http://host.docker.internal:8003/health",
}

# ============================================================
# Lock TTL (작업 유형별)
# ============================================================
LOCK_TTL_ASR = 600      # 10분 (heartbeat로 자동 갱신)
LOCK_TTL_DEFAULT = 700   # 기본값

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
