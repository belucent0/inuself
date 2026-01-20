import sys
import os
import logging

# --- Python path 설정 (litellm import 전에 해야 함) ---
# LiteLLM이 config에서 custom_handler를 찾을 수 있도록
_base_dir = os.path.dirname(os.path.abspath(__file__))
if _base_dir not in sys.path:
    sys.path.insert(0, _base_dir)
print(f"Added {_base_dir} to sys.path", flush=True)

# --- DATABASE_URL 제거 (LiteLLM Prisma 에러 방지) ---
# LiteLLM은 DATABASE_URL이 존재하면 Prisma DB 연결을 시도함
# 이 프로젝트에서는 LiteLLM DB 기능을 사용하지 않으므로 제거
if "DATABASE_URL" in os.environ:
    del os.environ["DATABASE_URL"]
    print("REMOVED DATABASE_URL from environment (LiteLLM DB disabled)", flush=True)

# --- MONKEYPATCH START ---
# Must run before litellm.proxy.proxy_cli is imported
try:
    print("APPLYING LITELLM PATCH...", flush=True)
    import litellm
    import litellm.types.llms
    import litellm.main
    import litellm.utils
    
    from litellm.types.llms import LlmProviders as OriginalLlmProviders

    class MockLlmProviders:
        def __init__(self, value):
            self.value = value
        
        def __eq__(self, other):
            return self.value == other or self.value == str(other)

        def __repr__(self):
            return f"MockLlmProviders({self.value})"

    def PatchedLlmProviders(value):
        if value == "prometheus-router":
            return MockLlmProviders(value)
        return OriginalLlmProviders(value)
    
    # Patch the class/callable in modules
    litellm.main.LlmProviders = PatchedLlmProviders
    litellm.utils.LlmProviders = PatchedLlmProviders
    litellm.types.llms.LlmProviders = PatchedLlmProviders
    
    # Also patch provider_list
    if not hasattr(litellm, "provider_list"):
        litellm.provider_list = []
    if "prometheus-router" not in litellm.provider_list:
        litellm.provider_list.append("prometheus-router")

    # Patch get_optional_params_transcription
    if hasattr(litellm.utils, "get_optional_params_transcription"):
        original_get_params = litellm.utils.get_optional_params_transcription
        def patched_get_params(*args, **kwargs):
            try:
                return original_get_params(*args, **kwargs)
            except ValueError:
                return {}
        litellm.utils.get_optional_params_transcription = patched_get_params
        if hasattr(litellm.main, "get_optional_params_transcription"):
            litellm.main.get_optional_params_transcription = patched_get_params

    print("PATCH APPLIED SUCCESSFULLY", flush=True)

    # Background health checks 비활성화
    litellm.background_health_checks = False
    if hasattr(litellm, 'health_check_interval'):
        litellm.health_check_interval = 86400
    print("BACKGROUND HEALTH CHECKS DISABLED", flush=True)

except Exception as e:
    print(f"PATCH FAILED: {e}", flush=True)
# --- MONKEYPATCH END ---

# --- MONKEYPATCH END ---

# --- MONKEYPATCH END ---

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CustomProxyRunner")

# Initialize OpenTelemetry - MOVED later to pass 'app' object
# try:
#     from custom.telemetry import setup_litellm_telemetry
#     setup_litellm_telemetry(service_name="asr-litellm")
#     logger.info("OpenTelemetry initialized for LiteLLM")
# except Exception as e:
#     logger.warning(f"OpenTelemetry initialization failed (tracing disabled): {e}")

try:
    # Import custom handler
    # infra/litellm 디렉토리를 path에 추가
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(base_dir))

    sys.path.append(base_dir)
    from custom_handler import prometheus_router

    logger.info("Registering custom provider map...")
    litellm.custom_provider_map = [
        {"provider": "prometheus-router", "custom_handler": prometheus_router}
    ]

    # Import and register Provider Manager callback for on-demand server management
    from provider_callback import provider_manager_callback, input_callback_handler
    litellm.callbacks = [provider_manager_callback]
    litellm.input_callback = [input_callback_handler]
    logger.info("Registered Provider Manager callback for on-demand servers")
    logger.info("Registered input_callback for pre-call signal")

except Exception as e:
    logger.error(f"Failed to register custom provider: {e}")
    sys.exit(1)

# Set arguments for the CLI
if os.getenv("LITELLM_CONFIG_PATH"):
    config_path = os.getenv("LITELLM_CONFIG_PATH")
    logger.info(f"Using config from Env: {config_path}")
else:
    config_path = os.path.join(project_root, "litellm_config.yaml")
    logger.info(f"Using default config path: {config_path}")

# Explicitly set env vars for LiteLLM startup to ensure consistency
os.environ["LITELLM_CONFIG_PATH"] = config_path

# Set sys.argv so run_server picks up the config
sys.argv = ["litellm", "--config", config_path, "--port", "4000"]

# Apply Deep Patch to Function Globals (Must be done after imports are settled)
try:
    if hasattr(litellm.main, "transcription"):
        litellm.main.transcription.__globals__["LlmProviders"] = litellm.main.LlmProviders
        logger.info("Deep patched litellm.main.transcription globals")
    
    if hasattr(litellm.main, "atranscription"):
        litellm.main.atranscription.__globals__["LlmProviders"] = litellm.main.LlmProviders
        logger.info("Deep patched litellm.main.atranscription globals")
        
    if hasattr(litellm.utils, "get_optional_params_transcription"):
         litellm.utils.get_optional_params_transcription.__globals__["LlmProviders"] = litellm.utils.LlmProviders
         logger.info("Deep patched litellm.utils.get_optional_params_transcription globals")

except Exception as e:
    logger.warning(f"Deep patch failed: {e}")


logger.info("Starting LiteLLM Proxy Server via run_server...")

# ============================================================
# Resource Management Endpoints (중앙집중 리소스 관리)
# ============================================================
from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import redis
import json
import asyncio

# Resource Management Router
resource_router = APIRouter(prefix="/resource", tags=["resource"])

# Redis 클라이언트
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
try:
    resource_redis = redis.from_url(REDIS_URL, decode_responses=True)
    logger.info(f"Resource Redis connected: {REDIS_URL}")
except Exception as e:
    logger.error(f"Resource Redis connection failed: {e}")
    resource_redis = None

# 리소스 타입별 Gate Key 매핑
RESOURCE_GATE_KEYS = {
    "gpu": "resource:gate:gpu",
    "npu": "resource:gate:npu",
    "gpu-asr": "resource:gate:gpu:asr",
    "gpu-ocr": "resource:gate:gpu:ocr",
    "gpu-llm": "resource:gate:gpu:llm",
    "gpu-diarization": "resource:gate:gpu:diarization",
    "npu-asr": "resource:gate:npu:asr",
    "npu-ocr": "resource:gate:npu:ocr",
    "npu-llm": "resource:gate:npu:llm",
}

# 리소스 타입 -> Provider 매핑
RESOURCE_TO_PROVIDER = {
    "gpu-asr-speed": "whisper-cpp",
    "gpu-asr-accuracy": "insanely-fast",
    "gpu-llm": "llama",
    "gpu-diarization": "diarization-server",
    "npu-asr": "flm-asr",
    "npu-llm": "flm-llm",
    "npu-ocr": "flm-ocr",
}

# Gate Lock TTL (데드락 방지)
GATE_LOCK_TTL = 600  # 10분 (최대 작업 시간)


class AcquireResourceRequest(BaseModel):
    resource_type: str  # "gpu" or "npu"
    task_type: str      # "asr", "ocr", "llm", "diarization"
    task_id: str        # 고유 작업 ID
    accuracy_mode: Optional[str] = "speed"  # "speed" or "accuracy" (ASR용)
    timeout: Optional[float] = 60.0  # 최대 대기 시간 (초)


class AcquireResourceResponse(BaseModel):
    success: bool
    provider: Optional[str] = None
    api_base: Optional[str] = None
    message: str
    wait_time: Optional[float] = None


class ReleaseResourceRequest(BaseModel):
    resource_type: str
    task_type: str
    task_id: str


class ReleaseResourceResponse(BaseModel):
    success: bool
    message: str


def get_gate_key(resource_type: str, task_type: str) -> str:
    """리소스 타입과 작업 타입으로 Gate Key 생성."""
    return f"resource:gate:{resource_type}:{task_type}"


def get_provider_info(resource_type: str, task_type: str, accuracy_mode: str = "speed") -> tuple[str, str]:
    """리소스 타입과 작업 타입으로 Provider 정보 반환."""
    if resource_type == "gpu":
        if task_type == "asr":
            if accuracy_mode == "accuracy":
                return "insanely-fast", os.getenv("GPU_INSANELY_FAST_API_BASE", "http://host.docker.internal:8002")
            else:
                return "whisper-cpp", os.getenv("GPU_WHISPER_CPP_API_BASE", "http://host.docker.internal:8001")
        elif task_type == "llm":
            return "llama", os.getenv("GPU_API_BASE", "http://host.docker.internal:8082")
        elif task_type == "diarization":
            return "diarization-server", "http://host.docker.internal:8003"
        elif task_type == "ocr":
            return "llama-ocr", os.getenv("GPU_OCR_API_BASE", "http://host.docker.internal:8081")
    elif resource_type == "npu":
        if task_type == "asr":
            return "flm-asr", os.getenv("FLM_ASR_URL", "http://host.docker.internal:11434")
        elif task_type == "llm":
            return "flm-llm", os.getenv("FLM_LLM_URL", "http://host.docker.internal:11435")
        elif task_type == "ocr":
            return "flm-ocr", os.getenv("FLM_OCR_URL", "http://host.docker.internal:11436")

    return "", ""


async def send_provider_signal(provider: str, action: str = "start"):
    """Provider Manager에게 제어 신호 전송."""
    if not resource_redis:
        return
    try:
        message = {"action": action, "provider": provider}
        resource_redis.publish("provider.control", json.dumps(message))
        logger.debug(f"[Resource] Sent provider signal: {provider} -> {action}")
    except Exception as e:
        logger.warning(f"[Resource] Failed to send provider signal: {e}")


def increment_provider_active_count(provider: str):
    """Provider 활성 요청 카운트 증가."""
    if not resource_redis:
        return
    try:
        key = f"provider:{provider}:active_count"
        resource_redis.incr(key)
        logger.debug(f"[Resource] INCR {key}")
    except Exception as e:
        logger.warning(f"[Resource] Failed to increment active count: {e}")


def decrement_provider_active_count(provider: str):
    """Provider 활성 요청 카운트 감소."""
    if not resource_redis:
        return
    try:
        key = f"provider:{provider}:active_count"
        new_val = resource_redis.decr(key)
        if new_val < 0:
            resource_redis.set(key, 0)
        logger.debug(f"[Resource] DECR {key} -> {max(0, new_val)}")
    except Exception as e:
        logger.warning(f"[Resource] Failed to decrement active count: {e}")


@resource_router.post("/acquire", response_model=AcquireResourceResponse)
async def acquire_resource(request: AcquireResourceRequest):
    """
    리소스 획득 (Gate Semaphore).

    SETNX를 사용하여 원자적으로 리소스를 획득합니다.
    이미 다른 작업이 사용 중이면 timeout까지 대기 후 실패를 반환합니다.
    """
    if not resource_redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    gate_key = get_gate_key(request.resource_type, request.task_type)
    provider, api_base = get_provider_info(
        request.resource_type,
        request.task_type,
        request.accuracy_mode
    )

    if not provider:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid resource/task combination: {request.resource_type}/{request.task_type}"
        )

    start_time = asyncio.get_event_loop().time()
    wait_interval = 0.5  # 500ms 간격으로 재시도

    while True:
        elapsed = asyncio.get_event_loop().time() - start_time

        # SETNX 시도 (원자적 락 획득)
        lock_value = json.dumps({
            "task_id": request.task_id,
            "acquired_at": asyncio.get_event_loop().time(),
            "resource_type": request.resource_type,
            "task_type": request.task_type,
        })

        acquired = resource_redis.set(gate_key, lock_value, nx=True, ex=GATE_LOCK_TTL)

        if acquired:
            # 락 획득 성공
            logger.info(f"[Resource] ACQUIRED: {gate_key} by task {request.task_id}")

            # Provider 시작 신호 전송 + 활성 카운트 증가
            await send_provider_signal(provider, "start")
            increment_provider_active_count(provider)

            return AcquireResourceResponse(
                success=True,
                provider=provider,
                api_base=api_base,
                message=f"Resource acquired: {request.resource_type}/{request.task_type}",
                wait_time=elapsed
            )

        # 타임아웃 체크
        if elapsed >= request.timeout:
            # 현재 락 소유자 확인
            current_lock = resource_redis.get(gate_key)
            current_owner = "unknown"
            if current_lock:
                try:
                    lock_data = json.loads(current_lock)
                    current_owner = lock_data.get("task_id", "unknown")
                except:
                    pass

            logger.warning(
                f"[Resource] TIMEOUT: {gate_key} for task {request.task_id} "
                f"(current owner: {current_owner})"
            )

            return AcquireResourceResponse(
                success=False,
                provider=None,
                api_base=None,
                message=f"Resource busy (owner: {current_owner}), timeout after {elapsed:.1f}s",
                wait_time=elapsed
            )

        # 대기 후 재시도
        await asyncio.sleep(wait_interval)


@resource_router.post("/release", response_model=ReleaseResourceResponse)
async def release_resource(request: ReleaseResourceRequest):
    """
    리소스 해제.

    해당 task_id가 소유한 락만 해제합니다.
    """
    if not resource_redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    gate_key = get_gate_key(request.resource_type, request.task_type)
    provider, _ = get_provider_info(request.resource_type, request.task_type)

    # 현재 락 확인
    current_lock = resource_redis.get(gate_key)

    if not current_lock:
        logger.warning(f"[Resource] RELEASE: {gate_key} - No lock exists")
        return ReleaseResourceResponse(
            success=True,
            message="No lock to release"
        )

    try:
        lock_data = json.loads(current_lock)
        owner_task_id = lock_data.get("task_id")
    except:
        owner_task_id = None

    # 소유권 확인
    if owner_task_id != request.task_id:
        logger.warning(
            f"[Resource] RELEASE DENIED: {gate_key} - "
            f"task {request.task_id} is not owner (owner: {owner_task_id})"
        )
        return ReleaseResourceResponse(
            success=False,
            message=f"Not the lock owner (owner: {owner_task_id})"
        )

    # 락 해제
    resource_redis.delete(gate_key)
    logger.info(f"[Resource] RELEASED: {gate_key} by task {request.task_id}")

    # Provider 활성 카운트 감소 + touch (idle timeout 리셋)
    if provider:
        decrement_provider_active_count(provider)
        await send_provider_signal(provider, "touch")

    return ReleaseResourceResponse(
        success=True,
        message=f"Resource released: {request.resource_type}/{request.task_type}"
    )


class ForceReleaseRequest(BaseModel):
    resource_type: str
    task_type: str


@resource_router.post("/force-release")
async def force_release_resource(request: ForceReleaseRequest):
    """
    관리자용 강제 락 해제.

    task_id 확인 없이 락을 강제로 해제합니다.
    주의: 실제로 작업이 진행 중일 수 있으므로 신중하게 사용해야 합니다.
    """
    if not resource_redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    gate_key = get_gate_key(request.resource_type, request.task_type)
    provider, _ = get_provider_info(request.resource_type, request.task_type)

    # 현재 락 확인
    current_lock = resource_redis.get(gate_key)

    if not current_lock:
        return {
            "success": True,
            "message": "No lock to release",
            "gate_key": gate_key,
        }

    try:
        lock_data = json.loads(current_lock)
        owner_task_id = lock_data.get("task_id", "unknown")
        acquired_at = lock_data.get("acquired_at", 0)
    except:
        owner_task_id = "unknown"
        acquired_at = 0

    # 강제 락 해제
    resource_redis.delete(gate_key)
    logger.warning(f"[Resource] FORCE RELEASED: {gate_key} (was owned by: {owner_task_id})")

    # Provider 활성 카운트 감소
    if provider:
        decrement_provider_active_count(provider)

    return {
        "success": True,
        "message": f"Force released: {request.resource_type}/{request.task_type}",
        "gate_key": gate_key,
        "previous_owner": owner_task_id,
        "acquired_at": acquired_at,
    }


@resource_router.post("/force-release-all")
async def force_release_all_resources():
    """
    관리자용 모든 락 강제 해제.

    모든 리소스 락을 강제로 해제하고 카운트를 리셋합니다.
    """
    if not resource_redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    released = []

    # 모든 Gate Lock 해제
    for resource_type in ["gpu", "npu"]:
        for task_type in ["asr", "ocr", "llm", "diarization"]:
            gate_key = get_gate_key(resource_type, task_type)
            if resource_redis.exists(gate_key):
                resource_redis.delete(gate_key)
                released.append(gate_key)
                logger.warning(f"[Resource] FORCE RELEASED ALL: {gate_key}")

    # Provider active_count 리셋
    providers = ["whisper-cpp", "insanely-fast", "llama", "diarization-server",
                 "flm-asr", "flm-llm", "flm-ocr"]
    for provider in providers:
        key = f"provider:{provider}:active_count"
        resource_redis.set(key, 0)

    return {
        "success": True,
        "message": f"Force released {len(released)} locks and reset all active counts",
        "released_locks": released,
    }


@resource_router.get("/status")
async def get_resource_status():
    """현재 리소스 상태 조회."""
    if not resource_redis:
        raise HTTPException(status_code=503, detail="Redis not available")

    status = {}

    # 모든 Gate Key 조회
    for resource_type in ["gpu", "npu"]:
        for task_type in ["asr", "ocr", "llm", "diarization"]:
            gate_key = get_gate_key(resource_type, task_type)
            lock_data = resource_redis.get(gate_key)

            if lock_data:
                try:
                    parsed = json.loads(lock_data)
                    status[f"{resource_type}-{task_type}"] = {
                        "locked": True,
                        "task_id": parsed.get("task_id"),
                        "acquired_at": parsed.get("acquired_at"),
                    }
                except:
                    status[f"{resource_type}-{task_type}"] = {"locked": True, "data": lock_data}
            else:
                status[f"{resource_type}-{task_type}"] = {"locked": False}

    # Provider active_count 조회
    providers = ["whisper-cpp", "insanely-fast", "llama", "diarization-server",
                 "flm-asr", "flm-llm", "flm-ocr"]
    active_counts = {}
    for provider in providers:
        key = f"provider:{provider}:active_count"
        count = resource_redis.get(key) or "0"
        active_counts[provider] = int(count)

    return {
        "gates": status,
        "active_counts": active_counts
    }


# LiteLLM app에 라우터 등록
def register_resource_router():
    """LiteLLM FastAPI app에 Resource Router 등록."""
    try:
        from litellm.proxy.proxy_server import app as litellm_app
        litellm_app.include_router(resource_router)
        logger.info("Resource Management Router registered at /resource/*")
        
        # Telemetry 초기화 (FastAPI app 객체 전달)
        try:
            from custom.telemetry import setup_litellm_telemetry
            setup_litellm_telemetry(service_name="asr-litellm", app=litellm_app)
            logger.info("OpenTelemetry initialized with FastAPI instrumentation")
        except Exception as e:
            logger.warning(f"OpenTelemetry initialization failed in register hook: {e}")
            
    except Exception as e:
        logger.error(f"Failed to register Resource Router: {e}")

# ============================================================

# Background health check 강제 비활성화 패치
try:
    from litellm.proxy import health_check

    # perform_health_check를 빈 함수로 교체 (실제 health check 실행 함수)
    async def _disabled_perform_health_check(*args, **kwargs):
        logger.info("[PATCH] perform_health_check is DISABLED - returning empty")
        return [], []  # healthy_endpoints, unhealthy_endpoints

    health_check.perform_health_check = _disabled_perform_health_check
    logger.info("PATCHED: perform_health_check disabled")
except Exception as e:
    logger.warning(f"Failed to patch health check: {e}")

if __name__ == "__main__":
    # Resource Router 등록 (LiteLLM app이 import 시점에 생성됨)
    register_resource_router()

    from litellm.proxy.proxy_cli import run_server
    run_server()
