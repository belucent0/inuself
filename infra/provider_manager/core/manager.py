"""Provider Manager - GPU/NPU 서버 프로세스 관리.

Architecture V7.3: 프로바이더 프로세스 Lifecycle 관리
- 좀비 프로세스 자동 정리
- 그룹별 순차 시작/종료
- 포트 해제 확인 후 재시작
- Graceful Shutdown 지원
- 확장 가능한 프로바이더 그룹 관리

Provider Groups:
- flm: FLM NPU Servers - Ports 11434, 11435, 11436
- gpu-llm: GPU LLM/OCR Servers (llama.cpp) - Ports 8080, 8081
- gpu-asr: GPU ASR Servers (Python) - Ports 8001, 8002, 8003
"""

import os
import re
import sys
import json
import time
import asyncio
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Set
from dataclasses import dataclass, field
from enum import Enum

import httpx
import psutil
import redis.asyncio as aioredis

from core.config import settings
from core.log_rotator import create_process_output_handler, ProcessOutputHandler


# ==========================================
# Provider Status Enum
# ==========================================

class ProviderStatus(str, Enum):
    """프로바이더 상태."""
    UP = "up"
    DOWN = "down"
    STARTING = "starting"
    STOPPING = "stopping"
    RECOVERING = "recovering"
    COOLDOWN = "cooldown"  # 복구 실패 후 대기 중


@dataclass
class ProviderState:
    """프로바이더 런타임 상태 추적."""
    status: ProviderStatus = ProviderStatus.DOWN
    last_check: Optional[datetime] = None
    last_status_change: Optional[datetime] = None
    recovery_attempts: int = 0
    consecutive_failures: int = 0  # 연속 헬스체크 실패 횟수
    cooldown_until: Optional[datetime] = None
    error_message: Optional[str] = None
    active_jobs: int = 0
    current_ram: float = 0.0  # 현재 RAM 사용량 (GB)

# ==========================================
# Configuration
# ==========================================
PROJECT_ROOT = settings.project_root
LOG_DIR = settings.log_dir

logger = logging.getLogger("ProviderManager")


# ==========================================
# Provider Configuration Classes
# ==========================================

@dataclass
class ProviderConfig:
    """단일 프로바이더 설정."""
    name: str
    cmd: List[str]
    port: int
    health: str
    enabled: bool = True
    estimated_ram: float = 0.0  # GB 단위 예상 RAM 사용량


@dataclass
class ProviderGroup:
    """프로바이더 그룹 설정."""
    name: str
    providers: List[ProviderConfig]
    order: int = 0  # 시작 순서 (낮을수록 먼저 시작)
    enabled: bool = True


# ==========================================
# Environment Variables Helper
# ==========================================

def _load_env_vars() -> Dict[str, str]:
    """Load environment variables from .env file."""
    env_vars = {}
    env_path = PROJECT_ROOT / ".env"

    if env_path.exists():
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    env_vars[key] = value

    return env_vars


# ==========================================
# Default Provider Configurations
# ==========================================

def _estimate_ram_from_model(cmd: List[str]) -> float:
    """명령어 라인에서 모델 파일을 찾아 예상 RAM 사용량 계산."""
    try:
        model_path = None
        for i, arg in enumerate(cmd):
            # llama-server: -m /path/to/model
            if arg == "-m" and i + 1 < len(cmd):
                model_path = cmd[i + 1]
                break
            # flm: serve model_name (flm은 내부 경로 사용하므로 추정 어려움, 일단 패스)
        
        if model_path:
            p = Path(model_path)
            if p.exists():
                size_gb = p.stat().st_size / (1024 ** 3)
                # 모델 크기 + 20% 오버헤드 + 컨텍스트 여유분 (약 0.5GB)
                return round(size_gb * 1.2 + 0.5, 2)
    except Exception:
        pass
    return 0.0

def get_default_provider_configs() -> Dict[str, ProviderConfig]:
    """기본 프로바이더 설정들을 반환."""
    # .env 파일에서 직접 로드 (os.getenv는 시스템 환경변수만 읽음)
    env_vars = _load_env_vars()

    LLM_SERVER_PATH = env_vars.get("LLM_SERVER_PATH", "llama-server")
    LLM_MODEL = env_vars.get("LLM_MODEL", str(PROJECT_ROOT / "models" / "Qwen3-4B-Instruct-2507-Q4_K_S.gguf"))
    LLM_SERVER_PORT = env_vars.get("LLM_SERVER_PORT", "8082")  # 8080은 Nginx와 충돌 가능성 있음
    LLM_CONTEXT_LENGTH = env_vars.get("LLM_CONTEXT_LENGTH", "15000")
    LLM_N_GPU_LAYERS = env_vars.get("LLM_N_GPU_LAYERS", "99")
    LLM_N_THREADS = env_vars.get("LLM_N_THREADS", "8")

    OCR_SERVER_MODEL = env_vars.get("OCR_SERVER_MODEL", str(PROJECT_ROOT / "models" / "Qwen3-VL-8B-Instruct" / "Qwen3-VL-8B-Instruct-Q8_0.gguf"))
    OCR_SERVER_MMPROJ = env_vars.get("OCR_SERVER_MMPROJ", str(PROJECT_ROOT / "models" / "Qwen3-VL-8B-Instruct" / "mmproj-F32.gguf"))
    OCR_SERVER_PORT = env_vars.get("OCR_SERVER_PORT", "8081")
    OCR_CONTEXT_LENGTH = env_vars.get("OCR_CONTEXT_LENGTH", "10000")
    OCR_SERVER_GPU_LAYERS = env_vars.get("OCR_SERVER_GPU_LAYERS", "75")
    OCR_SERVER_THREADS = env_vars.get("OCR_SERVER_THREADS", "4")

    ROCM_ENV_PATH = env_vars.get("ROCM_ENV_PATH", str(PROJECT_ROOT / "rocm_env"))
    ROCM_PYTHONW = str(Path(ROCM_ENV_PATH) / "Scripts" / "pythonw.exe")
    # python.exe 사용 시 -u 옵션 추가를 위해 기본 커맨드에서는 python.exe만 지정
    ROCM_PYTHON = str(Path(ROCM_ENV_PATH) / "Scripts" / "python.exe")
    SCRIPTS_DIR = PROJECT_ROOT / "scripts"

    # FLM 모델 설정 (환경변수 기반 - Tier-based Routing)
    # flm-llm 서버: tier-simple 요청 처리 (간단한 작업)
    # flm-llm-thinking 서버: tier-complex/reasoning/thinking 요청 처리 (복잡한 분석 + CoT 추론)
    FLM_LLM_SIMPLE_MODEL = env_vars.get("FLM_LLM_SIMPLE_MODEL", "lfm2:2.6b")
    FLM_THINKING_MODEL = env_vars.get("FLM_THINKING_MODEL", "qwen3-tk:4b")
    FLM_OCR_MODEL = env_vars.get("FLM_OCR_MODEL", "qwen3vl-it:4b")

    logger.info(f"[Config] FLM Models - Simple: {FLM_LLM_SIMPLE_MODEL}, Thinking: {FLM_THINKING_MODEL}, OCR: {FLM_OCR_MODEL}")

    return {
        # FLM NPU 서버들 (RAM 사용량 - NPU는 시스템 RAM 사용)
        # 모델명은 환경변수에서 로드 (동적 변경 지원)
        "flm-asr": ProviderConfig(
            name="flm-asr",
            cmd=["flm", "serve", "--asr", "1", "--port", "11434"],
            port=11434,
            health="/v1/models",
            estimated_ram=1.5,  # whisper-v3:turbo 실측 ~1.2GB
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
        "flm-llm": ProviderConfig(
            name="flm-llm",
            cmd=["flm", "serve", FLM_LLM_SIMPLE_MODEL, "--embed", "1", "--port", "11435"],
            port=11435,
            health="/v1/models",
            estimated_ram=2.0,  # tier-simple용 (lfm2:2.6b)
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
        "flm-llm-thinking": ProviderConfig(
            name="flm-llm-thinking",
            cmd=["flm", "serve", FLM_THINKING_MODEL, "--port", "11437"],
            port=11437,
            health="/v1/models",
            estimated_ram=2.0,  # tier-complex/reasoning/thinking용 (qwen3-tk:4b)
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
        "flm-ocr": ProviderConfig(
            name="flm-ocr",
            cmd=["flm", "serve", FLM_OCR_MODEL, "--port", "11436"],
            port=11436,
            health="/v1/models",
            estimated_ram=2.0,  # qwen3vl-it:4b 실측 ~1.7GB
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
        # GPU LLM/OCR 서버들 (llama.cpp)
        "llama-server": ProviderConfig(
            name="llama-server",
            cmd=[
                LLM_SERVER_PATH, "-m", LLM_MODEL,
                "--port", LLM_SERVER_PORT, "--ctx-size", LLM_CONTEXT_LENGTH,
                "--n-gpu-layers", LLM_N_GPU_LAYERS, "--threads", LLM_N_THREADS, "--host", "0.0.0.0"
            ],
            port=int(LLM_SERVER_PORT),
            health="/health",
            estimated_ram=_estimate_ram_from_model([LLM_SERVER_PATH, "-m", LLM_MODEL]) or 3.0,
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
        "llama-ocr-server": ProviderConfig(
            name="llama-ocr-server",
            cmd=[
                LLM_SERVER_PATH, "-m", OCR_SERVER_MODEL,
                "--mmproj", OCR_SERVER_MMPROJ,
                "--port", OCR_SERVER_PORT, "--ctx-size", OCR_CONTEXT_LENGTH,
                "--n-gpu-layers", OCR_SERVER_GPU_LAYERS, "--threads", OCR_SERVER_THREADS, "--host", "0.0.0.0"
            ],
            port=int(OCR_SERVER_PORT),
            health="/health",
            estimated_ram=_estimate_ram_from_model([LLM_SERVER_PATH, "-m", OCR_SERVER_MODEL]) or 9.0,
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
        # GPU ASR 서버들 (Python)
        "whisper-server": ProviderConfig(
            name="whisper-server",
            cmd=[ROCM_PYTHON, "-u", str(SCRIPTS_DIR / "whisper_cpp_server.py")],
            port=8001,
            health="/health",
            estimated_ram=2.0,
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
        "insanely-fast-server": ProviderConfig(
            name="insanely-fast-server",
            cmd=[ROCM_PYTHON, "-u", str(SCRIPTS_DIR / "insanely_fast_server.py")],
            port=8002,
            health="/health",
            estimated_ram=4.0,
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
        "diarization-server": ProviderConfig(
            name="diarization-server",
            cmd=[ROCM_PYTHON, "-u", str(SCRIPTS_DIR / "diarization_server.py")],
            port=8003,
            health="/health",
            estimated_ram=2.0,
            enabled=False,  # On-Demand: 요청 시에만 로드
        ),
    }


def get_default_groups() -> List[ProviderGroup]:
    """기본 프로바이더 그룹 설정 반환."""
    configs = get_default_provider_configs()

    return [
        ProviderGroup(
            name="flm",
            providers=[configs["flm-asr"], configs["flm-llm"], configs["flm-llm-thinking"], configs["flm-ocr"]],
            order=1
        ),
        ProviderGroup(
            name="gpu-llm",
            providers=[configs["llama-server"], configs["llama-ocr-server"]],
            order=2
        ),
        ProviderGroup(
            name="gpu-asr",
            providers=[configs["whisper-server"], configs["insanely-fast-server"], configs["diarization-server"]],
            order=3
        ),
    ]


class ProviderManager:
    """프로바이더 프로세스 관리 (FLM NPU, GPU LLM/ASR/OCR 서버).

    V7.2 기능:
    - 좀비 프로세스 자동 정리
    - 그룹별 순차 시작/종료 (순서 지정 가능)
    - 그룹 내 병렬 시작으로 빠른 초기화
    - 헬스체크 기반 Ready 확인
    - Graceful Shutdown (그룹별 역순 종료, 포트 해제 확인)
    - 동적 그룹/프로바이더 추가/삭제

    Usage:
        manager = ProviderManager()

        # 모든 프로바이더 시작
        await manager.start_all_providers()

        # 특정 그룹만 시작
        await manager.start_groups(["flm", "gpu-asr"])

        # 특정 그룹만 종료
        await manager.stop_groups(["gpu-asr"])

        # 그룹 추가
        manager.add_group(ProviderGroup(name="custom", providers=[...], order=4))

        # 프로바이더 비활성화
        manager.disable_provider("flm-asr")
    """

    def __init__(self, groups: List[ProviderGroup] = None):
        """ProviderManager 초기화.

        Args:
            groups: 프로바이더 그룹 목록 (None이면 기본 설정 사용)
        """
        self.groups: Dict[str, ProviderGroup] = {}
        self.processes: Dict[str, subprocess.Popen] = {}
        self.log_handles: Dict[str, any] = {}  # 로그 파일 핸들 관리
        self.http_client: Optional[httpx.AsyncClient] = None

        # 기본 그룹 설정 또는 사용자 지정 그룹
        if groups is None:
            groups = get_default_groups()

        for group in groups:
            self.groups[group.name] = group

        # PID 파일 경로
        self.pid_file = LOG_DIR / "provider_pids.json"

        # Redis 클라이언트 (lazy init)
        self.redis: Optional[aioredis.Redis] = None

        # 프로바이더별 런타임 상태 추적
        self.provider_states: Dict[str, ProviderState] = {}
        for group in self.groups.values():
            for provider in group.providers:
                self.provider_states[provider.name] = ProviderState()

        # Recovery 락 (프로바이더별 동시 recovery 방지)
        self._recovery_locks: Dict[str, asyncio.Lock] = {}
        for group in self.groups.values():
            for provider in group.providers:
                self._recovery_locks[provider.name] = asyncio.Lock()

        # 헬스 모니터링 태스크
        self._monitor_task: Optional[asyncio.Task] = None
        self._monitor_running = False

    # ==========================================
    # PID File Management
    # ==========================================

    def _save_pids(self) -> None:
        """현재 관리 중인 프로세스 PID를 파일에 저장."""
        data = {
            "updated_at": datetime.now().isoformat(),
            "providers": {
                name: proc.pid for name, proc in self.processes.items()
            }
        }
        try:
            self.pid_file.write_text(json.dumps(data, indent=2))
            logger.debug(f"Saved PIDs to {self.pid_file}: {list(data['providers'].keys())}")
        except Exception as e:
            logger.warning(f"Failed to save PIDs: {e}")

    def _load_and_kill_saved_pids(self) -> int:
        """저장된 PID 파일을 읽어서 해당 프로세스들 종료.

        Returns:
            종료된 프로세스 수
        """
        if not self.pid_file.exists():
            logger.debug("No saved PID file found")
            return 0

        killed = 0
        try:
            data = json.loads(self.pid_file.read_text())
            saved_pids = data.get("providers", {})
            updated_at = data.get("updated_at", "unknown")

            if not saved_pids:
                return 0

            logger.info(f"Found saved PIDs from {updated_at}: {list(saved_pids.keys())}")

            for name, pid in saved_pids.items():
                try:
                    # 프로세스가 아직 존재하는지 확인 후 종료
                    result = subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid), "/T"],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='ignore'
                    )
                    if result.returncode == 0:
                        logger.info(f"Killed saved process: {name} (PID: {pid})")
                        killed += 1
                    else:
                        logger.debug(f"Process {name} (PID: {pid}) already terminated")
                except Exception as e:
                    logger.debug(f"Error killing {name} (PID: {pid}): {e}")

            # 파일 삭제 (정리 완료)
            self.pid_file.unlink(missing_ok=True)
            logger.info(f"Cleaned up {killed} saved processes")

        except Exception as e:
            logger.warning(f"Failed to load/process PID file: {e}")

        return killed

    # ==========================================
    # Redis Connection & Status Publishing
    # ==========================================

    async def _ensure_redis(self) -> aioredis.Redis:
        """Redis 연결 확보."""
        if self.redis is None:
            self.redis = aioredis.from_url(
                settings.redis_url,
                decode_responses=True
            )
        return self.redis

    async def _publish_status(self, provider_name: str, state: ProviderState) -> None:
        """프로바이더 상태를 Redis Hash에 발행."""
        try:
            redis = await self._ensure_redis()
            status_data = {
                "status": state.status.value,
                "last_check": state.last_check.isoformat() if state.last_check else "",
                "last_status_change": state.last_status_change.isoformat() if state.last_status_change else "",
                "recovery_attempts": state.recovery_attempts,
                "consecutive_failures": state.consecutive_failures,
                "cooldown_until": state.cooldown_until.isoformat() if state.cooldown_until else "",
                "error_message": state.error_message or "",
                "active_jobs": state.active_jobs,
                "pid": self.processes[provider_name].pid if provider_name in self.processes else 0,
                "current_ram": state.current_ram
            }
            # 프로바이더 config 정보 추가
            for group in self.groups.values():
                for p in group.providers:
                    if p.name == provider_name:
                        status_data["port"] = p.port
                        status_data["group"] = group.name
                        status_data["estimated_ram"] = p.estimated_ram
                        break

            await redis.hset(
                settings.status_hash_key,
                provider_name,
                json.dumps(status_data)
            )
            logger.debug(f"Published status for {provider_name}: {state.status.value}")
        except Exception as e:
            logger.warning(f"Failed to publish status for {provider_name}: {e}")

    async def _publish_event(
        self,
        event_type: str,
        provider_name: str,
        data: Optional[dict] = None
    ) -> None:
        """프로바이더 이벤트를 Redis Stream에 발행."""
        try:
            redis = await self._ensure_redis()
            event = {
                "type": event_type,
                "provider": provider_name,
                "timestamp": datetime.now().isoformat(),
                **(data or {})
            }
            # Remove None values to prevent Redis error
            event = {k: v for k, v in event.items() if v is not None}

            await redis.xadd(
                settings.events_stream_key,
                event,
                maxlen=settings.events_stream_maxlen
            )
            logger.debug(f"Published event: {event_type} for {provider_name}")
        except Exception as e:
            logger.warning(f"Failed to publish event: {e}")

    async def _update_state(
        self,
        provider_name: str,
        new_status: ProviderStatus,
        error_message: Optional[str] = None
    ) -> None:
        """프로바이더 상태 업데이트 및 발행."""
        state = self.provider_states[provider_name]
        old_status = state.status

        state.status = new_status
        state.last_check = datetime.now()
        state.error_message = error_message

        if old_status != new_status:
            state.last_status_change = datetime.now()
            await self._publish_event(
                "status_change",
                provider_name,
                {"from": old_status.value, "to": new_status.value, "error": error_message}
            )
            logger.info(f"Provider {provider_name} status changed: {old_status.value} -> {new_status.value}")

        await self._publish_status(provider_name, state)

    async def publish_all_statuses(self) -> None:
        """모든 프로바이더 상태를 Redis에 발행."""
        for name, state in self.provider_states.items():
            await self._publish_status(name, state)
        logger.info(f"Published status for {len(self.provider_states)} providers")

    # ==========================================
    # Health Monitoring & Auto Recovery
    # ==========================================

    async def _check_provider_health(self, provider: ProviderConfig) -> bool:
        """단일 프로바이더 헬스체크 (빠른 체크용)."""
        url = f"http://localhost:{provider.port}{provider.health}"
        try:
            if not self.http_client:
                self.http_client = httpx.AsyncClient(timeout=settings.health_check_timeout)
            response = await self.http_client.get(url)
            return response.status_code == 200
        except Exception:
            return False

    async def _recover_provider(self, provider: ProviderConfig) -> bool:
        """프로바이더 복구 시도 (락 적용)."""
        # Recovery 락 획득 시도 (동시 recovery 방지)
        lock = self._recovery_locks.get(provider.name)
        if lock is None:
            # 동적으로 추가된 프로바이더를 위한 락 생성
            lock = asyncio.Lock()
            self._recovery_locks[provider.name] = lock

        if lock.locked():
            logger.debug(f"{provider.name} recovery already in progress, skipping")
            return False

        async with lock:
            return await self._do_recover_provider(provider)

    async def _do_recover_provider(self, provider: ProviderConfig) -> bool:
        """프로바이더 복구 실제 로직 (락 내부에서 실행)."""
        state = self.provider_states[provider.name]

        # 쿨다운 체크
        if state.cooldown_until and datetime.now() < state.cooldown_until:
            remaining = (state.cooldown_until - datetime.now()).seconds
            logger.debug(f"{provider.name} in cooldown, {remaining}s remaining")
            return False

        # 최대 복구 시도 횟수 체크
        if state.recovery_attempts >= settings.max_recovery_attempts:
            # 쿨다운 진입
            state.cooldown_until = datetime.now() + timedelta(seconds=settings.recovery_cooldown)
            state.status = ProviderStatus.COOLDOWN
            await self._publish_event(
                "recovery_exhausted",
                provider.name,
                {"attempts": state.recovery_attempts, "cooldown_seconds": settings.recovery_cooldown}
            )
            await self._publish_status(provider.name, state)
            logger.warning(f"{provider.name} recovery exhausted ({state.recovery_attempts} attempts), entering cooldown")
            return False

        # 복구 시도
        state.recovery_attempts += 1
        state.status = ProviderStatus.RECOVERING
        await self._publish_event(
            "recovery_started",
            provider.name,
            {"attempt": state.recovery_attempts, "max_attempts": settings.max_recovery_attempts}
        )
        await self._publish_status(provider.name, state)
        logger.info(f"Recovering {provider.name} (attempt {state.recovery_attempts}/{settings.max_recovery_attempts})")

        # 기존 프로세스 정리
        if provider.name in self.processes:
            try:
                self.processes[provider.name].terminate()
                self.processes[provider.name].wait(timeout=5)
            except Exception:
                pass
            del self.processes[provider.name]

        # 포트 정리
        await self._kill_process_on_port(provider.port)
        await asyncio.sleep(2)

        # 재시작 (On-Demand 프로바이더도 복구되도록 force=True)
        success = await self.start_provider(provider, force=True)

        if success:
            state.recovery_attempts = 0  # 성공 시 카운터 리셋
            state.cooldown_until = None
            await self._publish_event("recovery_success", provider.name)
            logger.info(f"{provider.name} recovered successfully!")
        else:
            await self._publish_event(
                "recovery_failed",
                provider.name,
                {"attempt": state.recovery_attempts}
            )
            logger.warning(f"{provider.name} recovery failed (attempt {state.recovery_attempts})")

        return success

    def _log_provider_summary(self):
        """현재 프로바이더 상태 요약 로깅."""
        try:
            logger.info("=" * 80)
            logger.info(f"PROVIDER STATUS REPORT ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
            logger.info("-" * 80)
            logger.info(f"{'Name':<25} {'Port':<6} {'Status':<10} {'PID':<8} {'RAM(GB)':<10} {'Jobs':<5}")
            logger.info("-" * 80)
            
            for group in self.get_enabled_groups():
                for provider in group.providers:
                    state = self.provider_states.get(provider.name)
                    if not state:
                        continue
                    
                    status_str = state.status.value.upper()
                    pid_str = str(self.processes[provider.name].pid) if provider.name in self.processes else "-"
                    
                    # RAM 표시 (현재/예상)
                    ram_val = state.current_ram if hasattr(state, 'current_ram') else 0.0
                    est_val = provider.estimated_ram if hasattr(provider, 'estimated_ram') else 0.0
                    ram_str = f"{ram_val:.1f} / {est_val:.1f}"
                    
                    logger.info(f"{provider.name:<25} {provider.port:<6} {status_str:<10} {pid_str:<8} {ram_str:<10} {state.active_jobs:<5}")
            logger.info("=" * 80)
        except Exception as e:
            logger.error(f"Failed to log provider summary: {e}")

    async def _health_monitor_loop(self) -> None:
        """백그라운드 헬스 모니터링 루프."""
        logger.info(f"Health monitor started (interval: {settings.health_check_interval}s)")
        
        last_report_time = 0  # 시작하자마자 한 번 찍기 위해 0으로 초기화

        while self._monitor_running:
            try:
                # 주기적 상태 리포트 (5분마다)
                # settings에 report_interval이 없으므로 하드코딩하거나 300초 사용
                current_time = time.time()
                if current_time - last_report_time >= 300:
                    self._log_provider_summary()
                    last_report_time = current_time

                for group in self.groups.values():
                    if not group.enabled:
                        continue

                    for provider in group.providers:
                        # On-Demand (enabled=False) 프로바이더도 로드되어 있으면 헬스 체크 필요
                        if not provider.enabled:
                            state = self.provider_states.get(provider.name)
                            # 상태가 DOWN/COOLDOWN이면 (로드되지 않음) 스킵
                            if not state or state.status in (ProviderStatus.DOWN, ProviderStatus.COOLDOWN):
                                continue

                        state = self.provider_states[provider.name]

                        # 쿨다운 체크 및 리셋
                        if state.status == ProviderStatus.COOLDOWN:
                            if state.cooldown_until and datetime.now() >= state.cooldown_until:
                                state.cooldown_until = None
                                state.recovery_attempts = 0
                                state.status = ProviderStatus.DOWN
                                logger.info(f"{provider.name} cooldown expired, ready for recovery")
                            else:
                                continue  # 쿨다운 중이면 스킵

                        # STARTING/STOPPING/RECOVERING 상태면 스킵
                        if state.status in (
                            ProviderStatus.STARTING,
                            ProviderStatus.STOPPING,
                            ProviderStatus.RECOVERING
                        ):
                            continue

                        # 헬스체크 실행
                        # 헬스체크 성공 여부와 상관없이 프로세스가 살아있는지 확인하여 RAM 업데이트 시도
                        # 이유: 헬스체크가 타임아웃 되더라도 프로세스가 initializing 중일 수 있음
                        if provider.name in self.processes:
                            try:
                                proc = psutil.Process(self.processes[provider.name].pid)
                                # RSS (Resident Set Size): 실제 물리 메모리 점유량 (byte)
                                rss_bytes = proc.memory_info().rss
                                state.current_ram = round(rss_bytes / (1024 ** 3), 2)  # GB 변환
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                state.current_ram = 0.0

                        is_healthy = await self._check_provider_health(provider)

                        if is_healthy:
                            # 성공 시 연속 실패 카운터 리셋
                            if state.consecutive_failures > 0:
                                logger.info(
                                    f"{provider.name} recovered after "
                                    f"{state.consecutive_failures} consecutive failures"
                                )
                                state.consecutive_failures = 0
                            if state.status != ProviderStatus.UP:
                                state.recovery_attempts = 0
                                await self._update_state(provider.name, ProviderStatus.UP)
                            else:
                                state.last_check = datetime.now()
                                await self._publish_status(provider.name, state)
                        else:
                            # 연속 실패 카운터 증가
                            state.consecutive_failures += 1

                            # 임계값 미만이면 경고만 로깅하고 recovery 스킵
                            if state.consecutive_failures < settings.consecutive_failure_threshold:
                                logger.warning(
                                    f"{provider.name} health check failed "
                                    f"({state.consecutive_failures}/{settings.consecutive_failure_threshold}), "
                                    f"waiting for more failures before recovery"
                                )
                                continue  # recovery 스킵, 다음 프로바이더로

                            # 임계값 도달 시 recovery 시작 (단, 작업 처리 중이면 스킵)

                            # active_jobs > 0이면 recovery 스킵 (요청 처리 중이라 health check 실패한 것)
                            if state.active_jobs > 0:
                                logger.warning(
                                    f"{provider.name} has {state.active_jobs} active jobs, "
                                    f"skipping recovery (health check failed due to high load)"
                                )
                                state.consecutive_failures = 0  # 카운터 리셋
                                continue

                            logger.warning(
                                f"{provider.name} reached failure threshold "
                                f"({state.consecutive_failures} consecutive failures), starting recovery"
                            )

                            if state.status == ProviderStatus.UP:
                                await self._update_state(
                                    provider.name,
                                    ProviderStatus.DOWN,
                                    f"Health check failed {state.consecutive_failures} times"
                                )

                            # 자동 복구 시도 (항상 시도하되 _recover_provider 내부에서 쿨다운/최대시도 관리)
                            # 초기 시작 실패 등으로 self.processes에 없어도 enabled면 복구 시도
                            await self._recover_provider(provider)

                            # recovery 시도 후 연속 실패 카운터 리셋 (recovery 성공 여부와 관계없이)
                            state.consecutive_failures = 0

            except Exception as e:
                logger.error(f"Health monitor error: {e}")

            # 다음 체크까지 대기
            await asyncio.sleep(settings.health_check_interval)

        logger.info("Health monitor stopped")

    async def start_health_monitor(self) -> None:
        """헬스 모니터링 시작."""
        if self._monitor_running:
            logger.warning("Health monitor already running")
            return

        self._monitor_running = True
        self._monitor_task = asyncio.create_task(self._health_monitor_loop())
        logger.info("Health monitor task created")

    async def stop_health_monitor(self) -> None:
        """헬스 모니터링 중지."""
        if not self._monitor_running:
            return

        self._monitor_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        logger.info("Health monitor stopped")

    # ==========================================
    # Job Tracking
    # ==========================================

    async def register_job(self, provider_name: str, job_id: str) -> None:
        """프로바이더에 작업 등록."""
        if provider_name not in self.provider_states:
            return

        state = self.provider_states[provider_name]
        state.active_jobs += 1

        try:
            redis = await self._ensure_redis()
            jobs_key = f"{settings.jobs_hash_key}:{provider_name}"
            await redis.sadd(jobs_key, job_id)
            await redis.hset(
                settings.jobs_hash_key,
                provider_name,
                json.dumps({"active_jobs": state.active_jobs})
            )
        except Exception as e:
            logger.warning(f"Failed to register job: {e}")

    async def complete_job(self, provider_name: str, job_id: str) -> None:
        """프로바이더 작업 완료 처리."""
        if provider_name not in self.provider_states:
            return

        state = self.provider_states[provider_name]
        state.active_jobs = max(0, state.active_jobs - 1)

        try:
            redis = await self._ensure_redis()
            jobs_key = f"{settings.jobs_hash_key}:{provider_name}"
            await redis.srem(jobs_key, job_id)
            await redis.hset(
                settings.jobs_hash_key,
                provider_name,
                json.dumps({"active_jobs": state.active_jobs})
            )
        except Exception as e:
            logger.warning(f"Failed to complete job: {e}")

    async def get_provider_jobs(self, provider_name: str) -> List[str]:
        """프로바이더의 활성 작업 목록 조회."""
        try:
            redis = await self._ensure_redis()
            jobs_key = f"{settings.jobs_hash_key}:{provider_name}"
            return list(await redis.smembers(jobs_key))
        except Exception as e:
            logger.warning(f"Failed to get jobs: {e}")
            return []

    # ==========================================
    # Group Management
    # ==========================================

    def add_group(self, group: ProviderGroup) -> None:
        """그룹 추가."""
        self.groups[group.name] = group
        logger.info(f"Added group: {group.name}")

    def remove_group(self, group_name: str) -> bool:
        """그룹 삭제."""
        if group_name in self.groups:
            del self.groups[group_name]
            logger.info(f"Removed group: {group_name}")
            return True
        return False

    def enable_group(self, group_name: str) -> bool:
        """그룹 활성화."""
        if group_name in self.groups:
            self.groups[group_name].enabled = True
            logger.info(f"Enabled group: {group_name}")
            return True
        return False

    def disable_group(self, group_name: str) -> bool:
        """그룹 비활성화."""
        if group_name in self.groups:
            self.groups[group_name].enabled = False
            logger.info(f"Disabled group: {group_name}")
            return True
        return False

    def get_enabled_groups(self) -> List[ProviderGroup]:
        """활성화된 그룹들을 순서대로 반환."""
        enabled = [g for g in self.groups.values() if g.enabled]
        return sorted(enabled, key=lambda g: g.order)

    # ==========================================
    # Provider Management
    # ==========================================

    def add_provider(self, group_name: str, provider: ProviderConfig) -> bool:
        """그룹에 프로바이더 추가."""
        if group_name in self.groups:
            self.groups[group_name].providers.append(provider)
            logger.info(f"Added provider {provider.name} to group {group_name}")
            return True
        return False

    def remove_provider(self, provider_name: str) -> bool:
        """프로바이더 삭제."""
        for group in self.groups.values():
            for i, p in enumerate(group.providers):
                if p.name == provider_name:
                    del group.providers[i]
                    logger.info(f"Removed provider: {provider_name}")
                    return True
        return False

    def enable_provider(self, provider_name: str) -> bool:
        """프로바이더 활성화."""
        for group in self.groups.values():
            for p in group.providers:
                if p.name == provider_name:
                    p.enabled = True
                    logger.info(f"Enabled provider: {provider_name}")
                    return True
        return False

    def disable_provider(self, provider_name: str) -> bool:
        """프로바이더 비활성화."""
        for group in self.groups.values():
            for p in group.providers:
                if p.name == provider_name:
                    p.enabled = False
                    logger.info(f"Disabled provider: {provider_name}")
                    return True
        return False

    # ==========================================
    # Process Lifecycle
    # ==========================================

    async def cleanup_zombie_processes(self):
        """기존 프로바이더 프로세스 종료 (좀비 방지)."""
        logger.info("Cleaning up zombie processes...")

        # Redis 연결 확보 (상태 확인용)
        try:
            await self._ensure_redis()
        except Exception as e:
            logger.warning(f"Failed to connect to Redis during cleanup: {e}")

        # 1. 저장된 PID 파일 기반 정리 (가장 정확)
        self._load_and_kill_saved_pids()

        # 2. 실행 파일 기반 정리 (추가 안전장치)
        # FLM은 쉘 스크립트 등으로 실행될 수 있어 프로세스 트리 전체를 정리해야 함
        targets = ["flm.exe", "llama-server.exe", "python.exe", "pythonw.exe"]
        
        # 포트 기반으로 먼저 확인하여 정확한 타겟만 종료
        all_ports = set()
        port_to_provider = {}
        for group in self.groups.values():
            for p in group.providers:
                all_ports.add(p.port)
                port_to_provider[p.port] = p.name

        logger.info(f"Checking ports for cleanup: {sorted(all_ports)}")
        
        for port in sorted(all_ports):
            provider_name = port_to_provider.get(port)
            
            # Redis에서 작업 상태 확인 (사용자 요청 반영)
            if provider_name and self.redis:
                try:
                    # JobTracker 정보를 직접 조회하는 것이 정확하지만, 
                    # 여기서는 Redis에 저장된 상태 정보를 활용
                    status_data = await self.redis.hget(settings.status_hash_key, provider_name)
                    if status_data:
                        status_json = json.loads(status_data)
                        active_jobs = status_json.get("active_jobs", 0)
                        if active_jobs > 0:
                            logger.warning(f"Provider {provider_name} has {active_jobs} active jobs via Redis status.")
                            # 정책: 예기치 않은 재시작 시에는 이전 작업이 유효하지 않을 가능성이 높으므로
                            # 로그만 남기고 정리를 진행합니다. (좀비 프로세스 제거 우선)
                except Exception as e:
                    logger.warning(f"Failed to check active jobs for {provider_name}: {e}")

            # 포트 점유 프로세스 확인 및 종료
            killed = await self._kill_process_on_port(port)
            if killed > 0:
                logger.info(f"Cleaned {killed} zombie process(es) on port {port} ({provider_name})")

        # 3. 명시적 타겟 종료 (포트 점유와 무관하게 남아있는 좀비 프로세스 정리 - 위험할 수 있으므로 생략하거나 매우 신중해야 함)
        # 여기서는 포트 기반 정리가 실패했을 경우를 대비해 특정 이름의 프로세스만 제한적으로 정리
        # FLM의 경우 포트를 놓치더라도 실행 중이면 문제될 수 있음
        try:
            # taskkill /IM flm.exe /F 로 강제 종료 (주의: 다른 FLM 인스턴스가 있다면 영향받을 수 있음)
            # 현재는 포트 기반 정리를 우선하고, 이 부분은 보수적으로 접근
            pass
        except Exception:
            pass

        await asyncio.sleep(2)
        logger.info("Zombie cleanup completed")

    async def _kill_process_on_port(self, port: int) -> int:
        """특정 포트를 사용하는 모든 프로세스 종료.

        Returns:
            종료된 프로세스 수
        """
        killed = 0
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            killed_pids = set()
            for line in result.stdout.splitlines():
                # 정확한 포트 매칭: 포트 번호 뒤에 공백이나 탭이 와야 함
                if re.search(rf":{port}\s", line) and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit() and pid not in killed_pids:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid, "/T"],
                            capture_output=True,
                            text=True,
                            encoding='utf-8',
                            errors='ignore'
                        )
                        killed_pids.add(pid)
                        killed += 1
                        logger.info(f"Killed process on port {port} (PID: {pid})")
        except Exception as e:
            logger.debug(f"Port {port} cleanup error: {e}")
        return killed

    async def health_check(self, port: int, path: str, timeout: float = 60.0) -> bool:
        """헬스체크 대기 (최대 timeout초)."""
        url = f"http://localhost:{port}{path}"
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                if not self.http_client:
                    self.http_client = httpx.AsyncClient(timeout=5.0)
                response = await self.http_client.get(url)
                if response.status_code == 200:
                    return True
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout):
                pass
            except Exception as e:
                logger.debug(f"Health check error for port {port}: {e}")
            await asyncio.sleep(1)
        return False

    async def wait_for_port_release(self, port: int, timeout: float = 30.0) -> bool:
        """포트가 해제될 때까지 대기."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ["netstat", "-ano"],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore'
                )
                port_in_use = any(
                    f":{port}" in line and "LISTENING" in line
                    for line in result.stdout.splitlines()
                )
                if not port_in_use:
                    return True
            except Exception as e:
                logger.debug(f"Port check error for {port}: {e}")
            await asyncio.sleep(1)
        return False

    # ==========================================
    # Start Operations
    # ==========================================

    async def start_provider(self, provider: ProviderConfig, force: bool = False) -> bool:
        """단일 프로바이더 시작.

        Args:
            provider: 프로바이더 설정
            force: True면 enabled=False여도 강제 시작 (On-Demand 로드용)
        """
        if not provider.enabled and not force:
            logger.info(f"Skipping disabled provider: {provider.name}")
            return True

        # 이미 실행 중인 경우 중복 시작 방지
        if provider.name in self.processes:
            proc = self.processes[provider.name]
            if proc.poll() is None:  # 프로세스가 아직 실행 중
                logger.info(f"Provider {provider.name} is already running (PID: {proc.pid}), skipping start")
                return True
            else:
                # 프로세스가 종료되었으면 목록에서 제거
                logger.warning(f"Provider {provider.name} process terminated (exit code: {proc.returncode}), removing from list")
                del self.processes[provider.name]
                self._save_pids()

        # 상태 업데이트: STARTING
        await self._update_state(provider.name, ProviderStatus.STARTING)

        logger.info(f"Starting provider: {provider.name} (port {provider.port})...")

        try:
            # Windows: CREATE_NO_WINDOW로 콘솔 창 숨김, CREATE_NEW_PROCESS_GROUP으로 시그널 분리
            creationflags = 0
            if sys.platform == 'win32':
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

            # 환경 변수 설정 (Unbuffered 출력 강제 + .env 주입)
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"

            # .env 파일의 환경변수 로드 및 주입
            loaded_env = _load_env_vars()
            env.update(loaded_env)

            # stdout=PIPE로 출력을 받아 ProcessOutputHandler로 처리
            # (로그 로테이션 + FLM 스팸 필터링 적용)
            proc = subprocess.Popen(
                provider.cmd,
                stdin=subprocess.DEVNULL,  # stdin 블록 방지 (CREATE_NO_WINDOW에서 중요)
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # stderr도 stdout으로 합침
                cwd=str(PROJECT_ROOT),
                env=env,
                creationflags=creationflags
            )
            self.processes[provider.name] = proc

            # 로그 로테이션 핸들러 생성 및 시작 (50MB, 3개 백업, FLM 스팸 필터링)
            output_handler = create_process_output_handler(
                process=proc,
                provider_name=provider.name,
                log_dir=LOG_DIR,
                max_mb=50,
                backup_count=3,
            )
            output_handler.start()
            self.log_handles[provider.name] = output_handler  # ProcessOutputHandler 저장

            logger.info(f"Started {provider.name} (PID: {proc.pid})")
            self._save_pids()  # PID 파일 즉시 저장
        except Exception as e:
            logger.error(f"Failed to start {provider.name}: {e}")
            await self._update_state(provider.name, ProviderStatus.DOWN, f"Process start error: {e}")
            return False

        logger.info(f"Waiting for {provider.name} to be ready...")
        if await self.health_check(provider.port, provider.health, timeout=120.0):
            logger.info(f"{provider.name} is ready on port {provider.port}")
            # 상태 업데이트: UP
            await self._update_state(provider.name, ProviderStatus.UP)
            return True
        else:
            logger.error(f"{provider.name} failed to start (health check timeout)")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Error terminating {provider.name}: {e}")
            # 실패한 프로세스는 목록에서 제거
            if provider.name in self.processes:
                logger.info(f"Removing failed provider {provider.name} from processes (was in list)")
                del self.processes[provider.name]
                self._save_pids()  # PID 파일 업데이트
            else:
                logger.warning(f"{provider.name} was not in processes list!")
            # 로그 핸들러 중지
            if provider.name in self.log_handles:
                try:
                    self.log_handles[provider.name].stop()
                except Exception:
                    pass
                del self.log_handles[provider.name]
            # 상태 업데이트: DOWN
            await self._update_state(provider.name, ProviderStatus.DOWN, "Health check timeout")
            logger.info(f"After removal, processes count: {len(self.processes)}")
            return False

    async def start_group(self, group: ProviderGroup) -> int:
        """그룹 내 프로바이더들 병렬 시작.

        Returns:
            성공한 프로바이더 수
        """
        if not group.enabled:
            logger.info(f"Skipping disabled group: {group.name}")
            return 0

        enabled_providers = [p for p in group.providers if p.enabled]
        logger.info(f"Starting provider group: {group.name} ({len(enabled_providers)} providers)...")

        results = await asyncio.gather(
            *[self.start_provider(p) for p in enabled_providers],
            return_exceptions=True
        )

        success_count = sum(1 for r in results if r is True)
        logger.info(f"Group {group.name}: {success_count}/{len(enabled_providers)} providers started successfully!!")
        return success_count

    async def start_groups(self, group_names: List[str] = None) -> None:
        """지정된 그룹들 순차 시작.

        Args:
            group_names: 시작할 그룹 이름 목록 (None이면 모든 활성화 그룹)
        """
        if group_names is None:
            groups = self.get_enabled_groups()
        else:
            groups = [self.groups[name] for name in group_names if name in self.groups]
            groups = sorted(groups, key=lambda g: g.order)

        logger.info(f"Starting {len(groups)} provider groups...")

        for i, group in enumerate(groups):
            logger.info(f"[{i+1}/{len(groups)}] Starting group: {group.name}")
            await self.start_group(group)

            if i < len(groups) - 1:
                logger.info("Waiting before next group...")
                await asyncio.sleep(2)

        logger.info("All specified provider groups started")

        # 헬스 모니터링 시작 (여기서 초기 상태 리포트가 출력됨)
        # start_groups에서의 중복 로그 제거됨

    async def start_all_providers(self) -> None:
        """모든 프로바이더 그룹별 순차 시작."""
        await self.cleanup_zombie_processes()
        await self.start_groups(None)

        # 헬스 모니터링 시작
        await self.start_health_monitor()

        # 초기 상태 Redis에 발행
        await self.publish_all_statuses()

    # ==========================================
    # Stop Operations
    # ==========================================

    async def stop_provider(self, name: str) -> bool:
        """단일 프로바이더 graceful 종료."""
        if name not in self.processes:
            logger.warning(f"{name} not found in processes")
            return True

        # 상태 업데이트: STOPPING
        await self._update_state(name, ProviderStatus.STOPPING)

        proc = self.processes[name]
        try:
            logger.info(f"Stopping {name} (PID: {proc.pid})...")
            proc.terminate()
            proc.wait(timeout=10)
            logger.info(f"{name} stopped gracefully")
            del self.processes[name]
            # 로그 핸들러 중지
            if name in self.log_handles:
                try:
                    self.log_handles[name].stop()
                except Exception:
                    pass
                del self.log_handles[name]
            self._save_pids()  # PID 파일 업데이트
            # 상태 업데이트: DOWN
            await self._update_state(name, ProviderStatus.DOWN)
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"{name} did not stop gracefully, killing...")
            proc.kill()
            proc.wait()
            del self.processes[name]
            # 로그 핸들러 중지
            if name in self.log_handles:
                try:
                    self.log_handles[name].stop()
                except Exception:
                    pass
                del self.log_handles[name]
            self._save_pids()  # PID 파일 업데이트
            # 상태 업데이트: DOWN
            await self._update_state(name, ProviderStatus.DOWN)
            return True
        except Exception as e:
            logger.error(f"Error stopping {name}: {e}")
            return False

    async def stop_group(self, group: ProviderGroup) -> bool:
        """그룹 내 프로바이더들 순차 종료 및 포트 해제 확인."""
        logger.info(f"Stopping provider group: {group.name} ({len(group.providers)} providers)...")

        all_stopped = True
        for provider in group.providers:
            if not await self.stop_provider(provider.name):
                all_stopped = False
                continue

            logger.info(f"Waiting for port {provider.port} to be released...")
            if await self.wait_for_port_release(provider.port, timeout=15.0):
                logger.info(f"Port {provider.port} released")
            else:
                logger.warning(f"Port {provider.port} still in use, force killing...")
                await self._kill_process_on_port(provider.port)
                if not await self.wait_for_port_release(provider.port, timeout=5.0):
                    all_stopped = False

        status = "all providers stopped successfully" if all_stopped else "some providers failed to stop"
        logger.info(f"Group {group.name}: {status}")
        return all_stopped

    async def stop_groups(self, group_names: List[str] = None) -> None:
        """지정된 그룹들 역순 종료.

        Args:
            group_names: 종료할 그룹 이름 목록 (None이면 모든 활성화 그룹)
        """
        if group_names is None:
            groups = self.get_enabled_groups()
        else:
            groups = [self.groups[name] for name in group_names if name in self.groups]
            groups = sorted(groups, key=lambda g: g.order)

        # 역순으로 종료
        groups = list(reversed(groups))
        logger.info(f"Stopping {len(groups)} provider groups...")

        for i, group in enumerate(groups):
            logger.info(f"[{i+1}/{len(groups)}] Stopping group: {group.name}")
            await self.stop_group(group)

            if i < len(groups) - 1:
                logger.info("Waiting before next group...")
                await asyncio.sleep(2)

    async def stop_all_providers(self) -> None:
        """모든 프로바이더 그룹별 역순 종료 (Graceful Shutdown)."""
        logger.info("=== Graceful Shutdown Started ===")

        # 헬스 모니터링 중지
        await self.stop_health_monitor()

        # 모든 프로바이더 상태를 STOPPING으로 업데이트
        for name in self.provider_states:
            await self._update_state(name, ProviderStatus.STOPPING)

        await self.stop_groups(None)

        # 모든 프로바이더 상태를 DOWN으로 업데이트
        for name in self.provider_states:
            await self._update_state(name, ProviderStatus.DOWN)

        self.processes.clear()

        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None

        # Redis 연결 종료
        if self.redis:
            await self.redis.close()
            self.redis = None

        logger.info("=== Graceful Shutdown Completed ===")

    # ==========================================
    # Restart Operations
    # ==========================================

    async def restart_groups(self, group_names: List[str] = None) -> None:
        """지정된 그룹들 Graceful 재시작."""
        logger.info("=== Graceful Restart Started ===")

        await self.stop_groups(group_names)

        # 포트 해제 최종 확인
        if group_names is None:
            groups = self.get_enabled_groups()
        else:
            groups = [self.groups[name] for name in group_names if name in self.groups]

        all_ports = [p.port for g in groups for p in g.providers]
        logger.info("Verifying all ports are released...")
        for port in all_ports:
            if not await self.wait_for_port_release(port, timeout=10.0):
                logger.warning(f"Port {port} still in use after shutdown")

        await self.start_groups(group_names)
        logger.info("=== Graceful Restart Completed ===")

    async def restart_all_providers(self) -> None:
        """모든 프로바이더 Graceful 재시작."""
        await self.restart_groups(None)

    # ==========================================
    # Individual Provider Operations (API용)
    # ==========================================

    async def load_provider(self, provider_name: str) -> bool:
        """개별 프로바이더 로드 (시작).

        Args:
            provider_name: 프로바이더 이름

        Returns:
            성공 여부
        """
        # 프로바이더 설정 찾기
        for group in self.groups.values():
            for provider in group.providers:
                if provider.name == provider_name:
                    logger.info(f"Loading provider: {provider_name} (On-Demand)")
                    return await self.start_provider(provider, force=True)

        logger.warning(f"Provider not found: {provider_name}")
        return False

    async def unload_provider(self, provider_name: str) -> bool:
        """개별 프로바이더 언로드 (종료).

        Args:
            provider_name: 프로바이더 이름

        Returns:
            성공 여부
        """
        # 프로바이더 설정 찾기
        for group in self.groups.values():
            for provider in group.providers:
                if provider.name == provider_name:
                    logger.info(f"Unloading provider: {provider_name}")

                    # 프로세스 종료
                    if not await self.stop_provider(provider_name):
                        return False

                    # 포트 해제 대기
                    if not await self.wait_for_port_release(provider.port, timeout=15.0):
                        logger.warning(f"Port {provider.port} still in use, force killing...")
                        await self._kill_process_on_port(provider.port)

                    return True

        logger.warning(f"Provider not found: {provider_name}")
        return False

    async def reload_provider(self, provider_name: str) -> bool:
        """개별 프로바이더 리로드 (재시작).

        Args:
            provider_name: 프로바이더 이름

        Returns:
            성공 여부
        """
        logger.info(f"Reloading provider: {provider_name}")

        # 언로드
        await self.unload_provider(provider_name)

        # 로드
        return await self.load_provider(provider_name)

    def get_provider_info(self, provider_name: str) -> Optional[dict]:
        """프로바이더 정보 조회."""
        for group in self.groups.values():
            for provider in group.providers:
                if provider.name == provider_name:
                    return {
                        "name": provider.name,
                        "port": provider.port,
                        "health": provider.health,
                        "enabled": provider.enabled,
                        "running": provider.name in self.processes,
                        "group": group.name
                    }
        return None

    def list_providers(self) -> List[dict]:
        """모든 프로바이더 목록 반환."""
        providers = []
        for group in self.groups.values():
            for provider in group.providers:
                providers.append({
                    "name": provider.name,
                    "port": provider.port,
                    "enabled": provider.enabled,
                    "running": provider.name in self.processes,
                    "group": group.name
                })
        return providers

    # ==========================================
    # Process Inspection (포트 기반 프로세스 조회)
    # ==========================================

    def get_processes_on_port(self, port: int) -> List[dict]:
        """포트를 사용하는 모든 프로세스 조회.

        Args:
            port: 조회할 포트 번호

        Returns:
            프로세스 정보 리스트 [{"pid": str, "port": int, "state": str}, ...]
        """
        processes = []
        try:
            # Windows CP949 인코딩 문제 해결: errors='ignore'
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        local_addr = parts[1]
                        state = parts[3] if len(parts) > 3 else "UNKNOWN"
                        pid = parts[-1]
                        if pid.isdigit():
                            processes.append({
                                "pid": pid,
                                "port": port,
                                "local_address": local_addr,
                                "state": state
                            })
        except Exception as e:
            logger.error(f"Error getting processes on port {port}: {e}")
        return processes

    def get_all_provider_processes(self) -> Dict[str, List[dict]]:
        """모든 프로바이더 포트의 프로세스 조회.

        Returns:
            {provider_name: [프로세스 정보 리스트], ...}
        """
        result = {}
        for group in self.groups.values():
            for provider in group.providers:
                processes = self.get_processes_on_port(provider.port)
                # LISTENING 상태만 필터링 (실제 서버 프로세스)
                listening = [p for p in processes if p.get("state") == "LISTENING"]
                expected_pid = None
                if provider.name in self.processes:
                    expected_pid = str(self.processes[provider.name].pid)

                result[provider.name] = {
                    "port": provider.port,
                    "expected_pid": expected_pid,
                    "processes": listening,
                    "process_count": len(listening),
                    "has_zombie": len(listening) > 1 or (
                        len(listening) == 1 and
                        expected_pid is not None and
                        listening[0]["pid"] != expected_pid
                    )
                }
        return result

    def get_provider_processes(self, provider_name: str) -> Optional[dict]:
        """특정 프로바이더의 프로세스 정보 조회.

        Args:
            provider_name: 프로바이더 이름

        Returns:
            프로세스 정보 또는 None
        """
        for group in self.groups.values():
            for provider in group.providers:
                if provider.name == provider_name:
                    processes = self.get_processes_on_port(provider.port)
                    listening = [p for p in processes if p.get("state") == "LISTENING"]
                    expected_pid = None
                    if provider_name in self.processes:
                        expected_pid = str(self.processes[provider_name].pid)

                    return {
                        "name": provider_name,
                        "port": provider.port,
                        "expected_pid": expected_pid,
                        "processes": listening,
                        "process_count": len(listening),
                        "has_zombie": len(listening) > 1 or (
                            len(listening) == 1 and
                            expected_pid is not None and
                            listening[0]["pid"] != expected_pid
                        )
                    }
        return None

    async def kill_zombie_on_port(self, port: int) -> int:
        """특정 포트의 좀비 프로세스 정리.

        Args:
            port: 정리할 포트 번호

        Returns:
            종료된 프로세스 수
        """
        processes = self.get_processes_on_port(port)
        listening = [p for p in processes if p.get("state") == "LISTENING"]

        # 관리 중인 프로바이더의 예상 PID 찾기
        expected_pid = None
        for group in self.groups.values():
            for provider in group.providers:
                if provider.port == port and provider.name in self.processes:
                    expected_pid = str(self.processes[provider.name].pid)
                    break

        killed = 0
        for proc in listening:
            pid = proc["pid"]
            # 예상 PID가 아닌 프로세스만 종료 (좀비)
            if expected_pid is None or pid != expected_pid:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid, "/T"],
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='ignore'
                    )
                    logger.info(f"Killed zombie process on port {port} (PID: {pid})")
                    killed += 1
                except Exception as e:
                    logger.error(f"Failed to kill process {pid}: {e}")

        return killed

    # ==========================================
    # Prometheus Metrics
    # ==========================================

    def get_prometheus_metrics(self) -> str:
        """Prometheus 형식의 메트릭 반환."""
        lines = []

        # HELP 및 TYPE 정의
        lines.append("# HELP provider_status Provider status (1=up, 0=down)")
        lines.append("# TYPE provider_status gauge")

        lines.append("# HELP provider_recovery_attempts Number of recovery attempts")
        lines.append("# TYPE provider_recovery_attempts gauge")

        lines.append("# HELP provider_active_jobs Number of active jobs")
        lines.append("# TYPE provider_active_jobs gauge")

        lines.append("# HELP provider_up Total number of running providers")
        lines.append("# TYPE provider_up gauge")

        # 프로바이더별 메트릭
        up_count = 0
        for group in self.groups.values():
            for provider in group.providers:
                if not provider.enabled:
                    continue

                state = self.provider_states.get(provider.name)
                if state:
                    is_up = 1 if state.status == ProviderStatus.UP else 0
                    up_count += is_up

                    labels = f'provider="{provider.name}",group="{group.name}",port="{provider.port}"'
                    lines.append(f'provider_status{{{labels}}} {is_up}')
                    lines.append(f'provider_recovery_attempts{{{labels}}} {state.recovery_attempts}')
                    lines.append(f'provider_active_jobs{{{labels}}} {state.active_jobs}')

        # 전체 UP 카운트
        total_enabled = sum(
            1 for g in self.groups.values()
            for p in g.providers if p.enabled
        )
        lines.append(f'provider_up{{total="{total_enabled}"}} {up_count}')

        return "\n".join(lines) + "\n"