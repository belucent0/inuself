
import os
import sys
import time
import json
import signal
import asyncio
import logging
import subprocess
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Third-party imports
try:
    import redis.asyncio as redis
except ImportError:
    print("Error: 'redis' package is required. Install with: pip install redis")
    sys.exit(1)

# ==========================================
# Configuration
# ==========================================
PROJECT_ROOT = Path(__file__).parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_DIR / "provider-manager.log", maxBytes=10*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ProviderManager")

# Environment Variables (Defaults)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FLM_MODEL = os.getenv("FLM_LLM_MODEL", "qwen3-it:4b")
FLM_PORT = os.getenv("FLM_PORT", "11434")

# Llama Server Config (LLM Chat/요약)
LLM_SERVER_PATH = os.getenv("LLM_SERVER_PATH", "llama-server")
LLM_MODELS_DIR = os.getenv("LLM_MODELS_DIR", "models")
LLM_PORT = os.getenv("LLM_SERVER_PORT", "8080")
LLM_CTX = os.getenv("LLM_CONTEXT_LENGTH", "15000")
LLM_GPU_LAYERS = os.getenv("LLM_N_GPU_LAYERS", "99")
LLM_THREADS = os.getenv("LLM_N_THREADS", "8")

# Llama OCR Server Config (GPU Vision OCR)
OCR_SERVER_PORT = os.getenv("OCR_SERVER_PORT", "8081")

# Whisper Server Config (GPU Speed)
WHISPER_CPP_PATH = os.getenv("WHISPER_CPP_PATH", "C:/whisper-cpp/build/bin/Release/whisper-server.exe")
WHISPER_CPP_MODEL = os.getenv("WHISPER_CPP_MODEL", "C:/whisper-cpp/models/ggml-large-v3-turbo.bin")
WHISPER_CPP_PORT = os.getenv("WHISPER_CPP_PORT", "8001")

# Insanely Fast Whisper Config (GPU Accuracy)
INSANELY_FAST_SCRIPT = os.getenv("INSANELY_FAST_SCRIPT", "scripts/insanely_fast_server.py")
INSANELY_FAST_PORT = os.getenv("INSANELY_FAST_PORT", "8002")

# Diarization Server Config
DIARIZATION_SCRIPT = os.getenv("DIARIZATION_SCRIPT", "scripts/diarization_server.py")
DIARIZATION_PORT = os.getenv("DIARIZATION_PORT", "8003")

# ROCm 환경 Python 경로 (insanely-fast, diarization 서버용)
ROCM_PYTHON_PATH = os.getenv("ROCM_PYTHON_PATH", "C:/timblo/torch-test/rocm_env/Scripts/python.exe")

# PM2 경로
PM2_PATH = os.getenv("PM2_PATH", "C:/Users/jg/AppData/Roaming/npm/pm2.cmd")

# Channel
CONTROL_CHANNEL = "provider.control"

# Idle Timeout (seconds) - 이 시간동안 사용되지 않으면 프로세스 자동 종료
# 모든 프로바이더 60초로 통일
IDLE_TIMEOUT = int(os.getenv("PROVIDER_IDLE_TIMEOUT", "60"))

# Provider별 Idle Timeout 설정
# insanely-fast는 모델 로딩(~50s) + 처리(~60s) 시간이 길어서 180초로 설정
PROVIDER_IDLE_TIMEOUTS = {
    "flm-asr-server": int(os.getenv("FLM_ASR_IDLE_TIMEOUT", "60")),
    "flm-llm-server": int(os.getenv("FLM_LLM_IDLE_TIMEOUT", "60")),
    "flm-ocr-server": int(os.getenv("FLM_OCR_IDLE_TIMEOUT", "60")),
    "llama-server": int(os.getenv("LLAMA_IDLE_TIMEOUT", "60")),
    "llama-ocr-server": int(os.getenv("LLAMA_OCR_IDLE_TIMEOUT", "120")),  # Vision 모델 로딩 시간 고려
    "whisper-server": int(os.getenv("WHISPER_IDLE_TIMEOUT", "60")),
    "insanely-fast-server": int(os.getenv("INSANELY_FAST_IDLE_TIMEOUT", "120")),
    "diarization-server": int(os.getenv("DIARIZATION_IDLE_TIMEOUT", "60")),
}

# Provider 이름 → Process 이름 매핑
PROVIDER_TO_PROCESS = {
    "flm-asr": "flm-asr-server",      # NPU ASR (whisper-v3:turbo)
    "flm-llm": "flm-llm-server",      # NPU LLM (qwen3-it:4b)
    "flm-ocr": "flm-ocr-server",      # NPU OCR Vision (qwen3vl-it:4b)
    "flm": "flm-ocr-server",          # 호환성: 기존 "flm" 요청은 OCR로 라우팅
    "llama": "llama-server",          # GPU LLM (Qwen3-4B)
    "llama-ocr": "llama-ocr-server",  # GPU OCR Vision (Qwen3-VL-8B)
    "whisper-cpp": "whisper-server",
    "insanely-fast": "insanely-fast-server",
    "diarization-server": "diarization-server",
}

# Process별 Port 매핑 (시작 전 포트 정리용)
PROCESS_PORTS = {
    "diarization-server": int(DIARIZATION_PORT),
    "insanely-fast-server": int(INSANELY_FAST_PORT),
    "whisper-server": int(WHISPER_CPP_PORT),
    "llama-ocr-server": int(OCR_SERVER_PORT),  # 8081 (GPU Vision OCR)
    "flm-asr-server": int(FLM_PORT),       # 11434 (ASR)
    "flm-llm-server": int(FLM_PORT) + 1,   # 11435 (LLM)
    "flm-ocr-server": int(FLM_PORT) + 2,   # 11436 (OCR)
}

# 모델 언로드 지원 서버 (프로세스 종료 대신 /unload API 호출)
# 이 서버들은 PM2 프로세스는 상시 실행, 모델만 언로드하여 VRAM 해제
MODEL_UNLOAD_SERVERS = {
    "diarization-server",
    "insanely-fast-server",
    # "whisper-server" - whisper_cpp_server는 요청마다 cli 호출하므로 상시 VRAM 사용 없음
}

# 주기적 상태 검증 간격 (초) - 10분
PERIODIC_HEALTH_CHECK_INTERVAL = int(os.getenv("PERIODIC_HEALTH_CHECK_INTERVAL", "600"))

# 온디맨드 프로세스 종료 대상 (FLM, llama 서버)
ON_DEMAND_STOP_SERVERS = {
    "flm-asr-server",
    "flm-llm-server",
    "flm-ocr-server",
    "llama-server",
    "llama-ocr-server",
}

# 리소스 락 키 매핑 (provider -> resource gate keys)
PROVIDER_LOCK_KEYS = {
    "flm-asr": ["resource:gate:npu:asr"],
    "flm-llm": ["resource:gate:npu:llm"],
    "flm-ocr": ["resource:gate:npu:ocr"],
    "llama": ["resource:gate:gpu:llm"],
    "llama-ocr": ["resource:gate:gpu:ocr"],
}


def get_hidden_startupinfo():
    """Windows에서 콘솔 창을 숨기기 위한 startupinfo 반환."""
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        return startupinfo
    return None


def kill_process_on_port(port: int) -> bool:
    """특정 포트를 사용하는 프로세스를 강제 종료."""
    if sys.platform != "win32":
        return False

    startupinfo = get_hidden_startupinfo()

    try:
        # netstat으로 포트 사용 프로세스 찾기
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=10,
            startupinfo=startupinfo
        )

        pids_to_kill = set()
        for line in result.stdout.splitlines():
            if f":{port}" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                parts = line.split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        if pid > 0:
                            pids_to_kill.add(pid)
                    except (ValueError, IndexError):
                        pass

        if not pids_to_kill:
            logger.debug(f"No process found on port {port}")
            return False

        for pid in pids_to_kill:
            logger.info(f"Killing process {pid} on port {port}...")
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo
            )

        return True
    except Exception as e:
        logger.warning(f"Error killing process on port {port}: {e}")
        return False


def check_server_health(port: int, timeout: float = 2.0) -> bool:
    """서버가 health check에 응답하는지 확인."""
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{port}/health"
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def unload_model_via_api(port: int, timeout: float = 30.0) -> bool:
    """서버의 /unload API를 호출하여 모델 언로드."""
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{port}/unload"
    try:
        req = urllib.request.Request(url, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                result = json.loads(response.read().decode('utf-8'))
                logger.info(f"Model unloaded via API (port {port}): {result}")
                return True
    except Exception as e:
        logger.warning(f"Failed to unload model via API (port {port}): {e}")
    return False


def check_model_status(port: int, timeout: float = 5.0) -> dict:
    """서버의 /status API를 호출하여 모델 상태 확인."""
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{port}/status"
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                return json.loads(response.read().decode('utf-8'))
    except Exception:
        pass
    return {"model_loaded": False}


# ==========================================
# Process Manager Class
# ==========================================
class ProviderManager:
    def __init__(self):
        self.processes = {}  # process_name -> subprocess.Popen
        self.pm2_processes = {}  # process_name -> bool (running status)
        self.last_activity = {}  # process_name -> timestamp (마지막 활동 시간)
        self.last_periodic_check = 0  # 마지막 주기적 검증 시간
        self.redis = None
        self.pubsub = None
        self.is_running = True

    async def connect_redis(self):
        """Connect to Redis and subscribe to control channel."""
        try:
            self.redis = redis.from_url(REDIS_URL, decode_responses=True)
            self.pubsub = self.redis.pubsub()
            await self.pubsub.subscribe(CONTROL_CHANNEL)
            logger.info(f"Connected to Redis and subscribed to '{CONTROL_CHANNEL}'")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            sys.exit(1)

    def start_process(self, name: str, cmd: list, log_prefix: str):
        """Start a subprocess if not already running."""
        # 시작 후 grace period (초) - 이 시간 동안은 health check 건너뜀
        STARTUP_GRACE_PERIOD = 30

        if name in self.processes:
            proc = self.processes[name]
            if proc.poll() is None:
                # 프로세스가 running으로 보임
                # 시작 후 grace period 내라면 health check 건너뜀
                start_time = self.last_activity.get(name, 0)
                elapsed = time.time() - start_time

                if elapsed < STARTUP_GRACE_PERIOD:
                    # Grace period 내 → 아직 시작 중으로 간주
                    logger.debug(f"Process '{name}' is within startup grace period ({elapsed:.1f}s < {STARTUP_GRACE_PERIOD}s). Skipping health check.")
                    return

                # Grace period 지남 → health check로 실제 동작 확인
                port = PROCESS_PORTS.get(name)
                if port and not check_server_health(port):
                    # Health check 실패 → 좀비 프로세스로 판단, 강제 종료 후 재시작
                    logger.warning(f"Process '{name}' (PID: {proc.pid}) is zombie (health check failed after {elapsed:.1f}s). Killing...")
                    try:
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       startupinfo=get_hidden_startupinfo())
                        proc.wait(timeout=5)
                    except Exception:
                        pass
                    del self.processes[name]
                    if name in self.last_activity:
                        del self.last_activity[name]
                    # 포트 추가 정리
                    kill_process_on_port(port)
                    time.sleep(1)
                else:
                    # 정상 동작 중 → last_activity만 갱신
                    self.last_activity[name] = time.time()
                    logger.debug(f"Process '{name}' is already running (PID: {proc.pid}). Updated last_activity.")
                    return
            else:
                logger.info(f"Process '{name}' was dead. Restarting...")
                # 죽은 프로세스 정보 정리
                del self.processes[name]
                if name in self.last_activity:
                    del self.last_activity[name]

        # ROCm 프로세스: 시작 전 포트 정리 (좀비 프로세스 방지)
        rocm_processes = {"insanely-fast-server", "diarization-server"}
        if name in rocm_processes and name in PROCESS_PORTS:
            port = PROCESS_PORTS[name]
            if kill_process_on_port(port):
                logger.info(f"Cleaned up port {port} before starting '{name}', waiting 2s...")
                time.sleep(2)  # 포트 해제 대기

        logger.info(f"Starting process '{name}': {' '.join(cmd)}")

        # Log files
        out_log = open(LOG_DIR / f"{log_prefix}-out.log", "a", encoding="utf-8")
        err_log = open(LOG_DIR / f"{log_prefix}-error.log", "a", encoding="utf-8")

        try:
            # Creation flags for Windows
            creation_flags = 0
            startupinfo = None
            if sys.platform == "win32":
                # ROCm/PyTorch 프로세스는 CREATE_NEW_PROCESS_GROUP만 사용
                # DETACHED_PROCESS는 ROCm 초기화를 방해하므로 제외
                # CREATE_NEW_PROCESS_GROUP은 CTRL_BREAK 시그널 전송을 위해 필요
                rocm_processes = {"insanely-fast-server", "diarization-server"}

                if name in rocm_processes:
                    # ROCm 프로세스: CREATE_NEW_PROCESS_GROUP만 사용 (graceful shutdown 지원)
                    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
                    startupinfo = None
                    logger.info(f"Using ROCm-compatible mode for '{name}' (CREATE_NEW_PROCESS_GROUP only)")
                else:
                    # 일반 프로세스: CREATE_NO_WINDOW로 콘솔 창 완전 숨김
                    CREATE_NO_WINDOW = 0x08000000
                    creation_flags = CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                    # STARTUPINFO로 창 숨김 (SW_HIDE) - 이중 보험
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = 0  # SW_HIDE

            # 환경변수 상속 (ROCm/PyTorch 필요)
            env = os.environ.copy()

            proc = subprocess.Popen(
                cmd,
                stdout=out_log,
                stderr=err_log,
                cwd=str(PROJECT_ROOT),
                creationflags=creation_flags,
                startupinfo=startupinfo,
                env=env
            )
            self.processes[name] = proc
            self.last_activity[name] = time.time()  # 시작 시 last_activity 설정
            logger.info(f"Started '{name}' successfully (PID: {proc.pid})")
        except Exception as e:
            logger.error(f"Failed to start '{name}': {e}")

    def stop_process(self, name: str):
        """Stop a running subprocess with graceful shutdown."""
        if name not in self.processes:
            logger.debug(f"Process '{name}' is not managed. Ignoring stop command.")
            return

        proc = self.processes[name]
        port = PROCESS_PORTS.get(name)

        try:
            if proc.poll() is None:
                logger.info(f"Stopping '{name}' (PID: {proc.pid})...")

                # 1단계: Graceful shutdown 시도 (CTRL_BREAK_EVENT)
                if sys.platform == "win32":
                    startupinfo = get_hidden_startupinfo()
                    try:
                        # CTRL_BREAK_EVENT로 graceful shutdown 시도
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                        logger.debug(f"Sent CTRL_BREAK to '{name}', waiting for graceful shutdown...")
                        proc.wait(timeout=5)
                        logger.info(f"Stopped '{name}' gracefully.")
                    except subprocess.TimeoutExpired:
                        # 2단계: Graceful 실패 시 강제 종료
                        logger.warning(f"Graceful shutdown timeout for '{name}', forcing kill...")
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       startupinfo=startupinfo)
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            pass
                    except Exception as e:
                        # CTRL_BREAK 실패 시 바로 강제 종료
                        logger.debug(f"CTRL_BREAK failed for '{name}': {e}, using taskkill...")
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       startupinfo=startupinfo)
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            pass
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()

                logger.info(f"Stopped '{name}'.")
            else:
                logger.debug(f"Process '{name}' is already stopped.")
        except Exception as e:
            logger.error(f"Error stopping '{name}': {e}")

        # 프로세스와 last_activity 정보 삭제
        if name in self.processes:
            del self.processes[name]
        if name in self.last_activity:
            del self.last_activity[name]

        # ROCm 프로세스: 포트 확인 및 추가 정리 (좀비 방지)
        if port:
            time.sleep(1)  # VRAM 해제 대기
            if kill_process_on_port(port):
                logger.info(f"Cleaned up remaining process on port {port} after stopping '{name}'")

    # PM2로 관리되는 프로세스 목록
    PM2_MANAGED = {"diarization-server", "whisper-server", "flm-asr-server", "flm-llm-server", "flm-ocr-server", "insanely-fast-server", "llama-server", "llama-ocr-server"}

    def start_pm2_process(self, name: str):
        """PM2로 관리되는 프로세스 시작."""
        startupinfo = get_hidden_startupinfo()

        if name in self.pm2_processes and self.pm2_processes[name]:
            # 이미 실행 중인지 확인
            result = subprocess.run(
                [PM2_PATH, "jlist"],
                capture_output=True, text=True, timeout=10,
                startupinfo=startupinfo,
                encoding='utf-8', errors='replace'
            )
            try:
                processes = json.loads(result.stdout)
                for proc in processes:
                    if proc.get("name") == name and proc.get("pm2_env", {}).get("status") == "online":
                        # 이미 실행 중
                        self.last_activity[name] = time.time()
                        logger.debug(f"PM2 process '{name}' is already running. Updated last_activity.")
                        return
            except Exception:
                pass

        logger.info(f"Starting PM2 process '{name}'...")
        # diarization-server는 pyannote 모델 로딩이 오래 걸림 (~35초)
        pm2_start_timeout = 60 if name == "diarization-server" else 30
        result = subprocess.run(
            [PM2_PATH, "start", name],
            capture_output=True, text=True, timeout=pm2_start_timeout,
            startupinfo=startupinfo,
            encoding='utf-8', errors='replace'
        )
        if result.returncode == 0:
            self.pm2_processes[name] = True
            self.last_activity[name] = time.time()
            logger.info(f"Started PM2 process '{name}' successfully")
        else:
            logger.error(f"Failed to start PM2 process '{name}': {result.stderr}")

    def stop_pm2_process(self, name: str):
        """PM2로 관리되는 프로세스 중지."""
        logger.info(f"Stopping PM2 process '{name}'...")
        startupinfo = get_hidden_startupinfo()
        result = subprocess.run(
            [PM2_PATH, "stop", name],
            capture_output=True, text=True, timeout=30,
            startupinfo=startupinfo,
            encoding='utf-8', errors='replace'
        )
        if result.returncode == 0:
            self.pm2_processes[name] = False
            if name in self.last_activity:
                del self.last_activity[name]
            logger.info(f"Stopped PM2 process '{name}' successfully")
        else:
            logger.error(f"Failed to stop PM2 process '{name}': {result.stderr}")

    def is_pm2_process_running(self, name: str) -> bool:
        """PM2 프로세스가 실행 중인지 확인."""
        try:
            startupinfo = get_hidden_startupinfo()
            result = subprocess.run(
                [PM2_PATH, "jlist"],
                capture_output=True, text=True, timeout=10,
                startupinfo=startupinfo,
                encoding='utf-8', errors='replace'
            )
            processes = json.loads(result.stdout)
            for proc in processes:
                if proc.get("name") == name and proc.get("pm2_env", {}).get("status") == "online":
                    return True
        except Exception as e:
            logger.warning(f"Failed to check PM2 process '{name}': {e}")
        return False

    def touch_process(self, name: str):
        """Update last_activity timestamp for a process (keep alive)."""
        # PM2 관리 프로세스 처리
        if name in self.PM2_MANAGED:
            if self.is_pm2_process_running(name):
                self.last_activity[name] = time.time()
                logger.debug(f"Touch PM2 '{name}': updated last_activity")
            return

        if name in self.processes and self.processes[name].poll() is None:
            self.last_activity[name] = time.time()
            logger.debug(f"Touch '{name}': updated last_activity")
        else:
            logger.debug(f"Touch '{name}': process not running, ignoring")

    async def handle_message(self, message):
        """Handle incoming Redis message."""
        try:
            data = json.loads(message["data"])
            action = data.get("action")
            provider = data.get("provider")  # 'flm', 'llama', 'whisper-cpp', 'insanely-fast', 'diarization-server'

            # touch는 자주 발생하므로 debug 레벨로
            if action == "touch":
                logger.debug(f"Received command: action={action}, provider={provider}")
            else:
                logger.info(f"Received command: action={action}, provider={provider}")

            if action == "start":
                if provider == "flm-asr":
                    # PM2로 flm-asr-server 관리 (NPU ASR)
                    self.start_pm2_process("flm-asr-server")

                elif provider == "flm-llm":
                    # PM2로 flm-llm-server 관리 (NPU LLM)
                    self.start_pm2_process("flm-llm-server")

                elif provider in ("flm-ocr", "flm"):
                    # PM2로 flm-ocr-server 관리 (NPU OCR Vision)
                    # "flm"은 호환성을 위해 flm-ocr로 라우팅
                    self.start_pm2_process("flm-ocr-server")

                elif provider == "llama":
                    # PM2로 llama-server 관리 (GPU LLM)
                    self.start_pm2_process("llama-server")

                elif provider == "llama-ocr":
                    # PM2로 llama-ocr-server 관리 (GPU OCR Vision)
                    self.start_pm2_process("llama-ocr-server")

                elif provider == "whisper-cpp":
                    # PM2로 whisper-server 관리 (graceful shutdown 지원)
                    self.start_pm2_process("whisper-server")

                elif provider == "insanely-fast":
                    # PM2로 insanely-fast-server 관리
                    self.start_pm2_process("insanely-fast-server")

                elif provider == "diarization-server":
                    # PM2로 diarization-server 관리 (graceful shutdown 지원)
                    self.start_pm2_process("diarization-server")

            elif action == "stop":
                # 명시적 stop 요청 (일반적으로 사용하지 않음, touch + idle timeout 권장)
                process_name = PROVIDER_TO_PROCESS.get(provider)
                if process_name:
                    if process_name in self.PM2_MANAGED:
                        self.stop_pm2_process(process_name)
                    else:
                        self.stop_process(process_name)
                elif provider == "all":
                    # subprocess 관리 프로세스 중지
                    for proc_name in list(self.processes.keys()):
                        self.stop_process(proc_name)
                    # PM2 관리 프로세스 중지
                    for proc_name in self.PM2_MANAGED:
                        if self.is_pm2_process_running(proc_name):
                            self.stop_pm2_process(proc_name)

            elif action == "touch":
                # 활동 타임스탬프 갱신 (idle timeout 리셋)
                process_name = PROVIDER_TO_PROCESS.get(provider)
                if process_name:
                    self.touch_process(process_name)

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON message: {message['data']}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def get_active_count(self, provider: str) -> int:
        """Redis에서 provider의 활성 요청 카운트 조회."""
        try:
            key = f"provider:{provider}:active_count"
            value = await self.redis.get(key)
            return int(value) if value else 0
        except Exception as e:
            logger.warning(f"Failed to get active count for {provider}: {e}")
            return 0

    async def check_idle_timeouts(self):
        """Idle timeout이 지난 프로세스 종료 (active_count == 0인 경우만)."""
        current_time = time.time()

        # 1. subprocess로 관리되는 프로세스 체크
        for process_name in list(self.processes.keys()):
            # 프로세스가 실행 중인지 확인
            proc = self.processes.get(process_name)
            if not proc or proc.poll() is not None:
                continue

            # last_activity 확인
            last_activity = self.last_activity.get(process_name, current_time)
            idle_seconds = current_time - last_activity

            # Provider별 idle timeout 적용 (없으면 기본값 사용)
            provider_idle_timeout = PROVIDER_IDLE_TIMEOUTS.get(process_name, IDLE_TIMEOUT)

            if idle_seconds < provider_idle_timeout:
                continue  # 아직 idle timeout 안 됨

            # Provider 이름 찾기 (역방향 매핑)
            provider = None
            for prov, proc_name in PROVIDER_TO_PROCESS.items():
                if proc_name == process_name:
                    provider = prov
                    break

            if not provider:
                continue

            # active_count 확인
            active_count = await self.get_active_count(provider)

            if active_count > 0:
                # 아직 사용 중이므로 종료하지 않음
                logger.debug(f"'{process_name}' idle {idle_seconds:.1f}s but active_count={active_count}, keeping alive")
                continue

            # Idle timeout + active_count == 0 → 종료
            logger.info(f"'{process_name}' idle for {idle_seconds:.1f}s (timeout: {provider_idle_timeout}s) with no active requests. Stopping...")
            self.stop_process(process_name)

        # 2. PM2로 관리되는 프로세스 체크
        for process_name in list(self.PM2_MANAGED):
            if not self.is_pm2_process_running(process_name):
                continue

            # last_activity 확인
            last_activity = self.last_activity.get(process_name, current_time)
            idle_seconds = current_time - last_activity

            # Provider별 idle timeout 적용
            provider_idle_timeout = PROVIDER_IDLE_TIMEOUTS.get(process_name, IDLE_TIMEOUT)

            if idle_seconds < provider_idle_timeout:
                continue

            # Provider 이름 찾기
            provider = None
            for prov, proc_name in PROVIDER_TO_PROCESS.items():
                if proc_name == process_name:
                    provider = prov
                    break

            if not provider:
                continue

            # active_count 확인
            active_count = await self.get_active_count(provider)

            if active_count > 0:
                logger.debug(f"PM2 '{process_name}' idle {idle_seconds:.1f}s but active_count={active_count}, keeping alive")
                continue

            # Idle timeout + active_count == 0
            # 모델 언로드 지원 서버는 프로세스 유지, 모델만 언로드
            if process_name in MODEL_UNLOAD_SERVERS:
                port = PROCESS_PORTS.get(process_name)
                if port:
                    # 먼저 모델이 로드되어 있는지 확인
                    status = check_model_status(port)
                    if status.get("model_loaded", False):
                        logger.info(f"PM2 '{process_name}' idle for {idle_seconds:.1f}s. Unloading model (keeping process)...")
                        if unload_model_via_api(port):
                            # 언로드 성공 - last_activity는 유지하지 않음 (다음 idle check에서 다시 unload 시도 방지)
                            pass
                    else:
                        logger.debug(f"PM2 '{process_name}' model already unloaded, skipping")
            else:
                # 기존 방식: 프로세스 종료
                logger.info(f"PM2 '{process_name}' idle for {idle_seconds:.1f}s (timeout: {provider_idle_timeout}s) with no active requests. Stopping...")
                self.stop_pm2_process(process_name)


    async def check_active_locks(self, provider: str) -> bool:
        """Provider에 활성 리소스 락이 있는지 확인."""
        lock_keys = PROVIDER_LOCK_KEYS.get(provider, [])
        
        for key in lock_keys:
            try:
                lock_data = await self.redis.get(key)
                if lock_data:
                    logger.debug(f"Active lock found for {provider}: {key}")
                    return True
            except Exception as e:
                logger.warning(f"Failed to check lock {key}: {e}")
        
        return False

    async def reset_stale_active_counts(self):
        """정체된 active_count 리셋 (실제 활성 락 없이 count > 0인 경우)."""
        # 온디맨드 서버 대상 provider만 확인
        target_providers = ["flm-asr", "flm-llm", "flm-ocr", "llama", "llama-ocr"]
        reset_count = 0
        
        for provider in target_providers:
            try:
                key = f"provider:{provider}:active_count"
                count_str = await self.redis.get(key)
                count = int(count_str) if count_str else 0
                
                if count > 0:
                    # 실제 활성 락이 있는지 확인
                    has_active_lock = await self.check_active_locks(provider)
                    
                    if not has_active_lock:
                        # 락 없이 count > 0 → 정체 상태, 리셋
                        logger.warning(
                            f"[Periodic Check] Resetting stale active_count for {provider}: "
                            f"{count} -> 0 (no active locks found)"
                        )
                        await self.redis.set(key, "0")
                        reset_count += 1
                    else:
                        logger.debug(f"[Periodic Check] {provider} has active locks, keeping count={count}")
            except Exception as e:
                logger.warning(f"[Periodic Check] Failed to check/reset {provider}: {e}")
        
        return reset_count

    async def periodic_health_check(self):
        """10분마다 리소스 상태 검증 및 정리."""
        logger.info("[Periodic Check] Starting health check...")
        
        try:
            # 1. 정체된 active_count 리셋
            reset_count = await self.reset_stale_active_counts()
            
            # 2. 온디맨드 서버 상태 확인 및 강제 종료
            stopped_count = 0
            for process_name in ON_DEMAND_STOP_SERVERS:
                if not self.is_pm2_process_running(process_name):
                    continue
                
                # Provider 이름 찾기
                provider = None
                for prov, proc_name in PROVIDER_TO_PROCESS.items():
                    if proc_name == process_name:
                        provider = prov
                        break
                
                if not provider:
                    continue
                
                # active_count와 락 모두 확인
                active_count = await self.get_active_count(provider)
                has_active_lock = await self.check_active_locks(provider)
                
                # 둘 다 없으면 프로세스 종료 가능
                if active_count == 0 and not has_active_lock:
                    last_activity = self.last_activity.get(process_name, 0)
                    idle_seconds = time.time() - last_activity if last_activity else float('inf')
                    
                    # idle timeout 지났으면 종료
                    provider_idle_timeout = PROVIDER_IDLE_TIMEOUTS.get(process_name, IDLE_TIMEOUT)
                    if idle_seconds >= provider_idle_timeout:
                        logger.info(
                            f"[Periodic Check] Stopping idle process '{process_name}' "
                            f"(idle: {idle_seconds:.0f}s, no active work)"
                        )
                        self.stop_pm2_process(process_name)
                        stopped_count += 1
            
            logger.info(
                f"[Periodic Check] Completed: reset {reset_count} stale counts, "
                f"stopped {stopped_count} idle processes"
            )
            
        except Exception as e:
            logger.error(f"[Periodic Check] Error during health check: {e}")

    async def run(self):
        """Main loop."""
        await self.connect_redis()

        logger.info(f"Provider Manager is running. Idle timeout: {IDLE_TIMEOUT}s, Periodic check: {PERIODIC_HEALTH_CHECK_INTERVAL}s")
        self.last_periodic_check = time.time()

        # Keep alive loop
        while self.is_running:
            try:
                message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    await self.handle_message(message)

                # Check for dead processes and cleanup dictionary
                dead_procs = [name for name, proc in self.processes.items() if proc.poll() is not None]
                for name in dead_procs:
                    logger.warning(f"Process '{name}' exited unexpectedly with code {self.processes[name].returncode}")
                    if name in self.processes:
                        del self.processes[name]
                    if name in self.last_activity:
                        del self.last_activity[name]

                # Idle timeout 체크 (1초마다)
                await self.check_idle_timeouts()

                # 주기적 상태 검증 (10분마다)
                current_time = time.time()
                if current_time - self.last_periodic_check >= PERIODIC_HEALTH_CHECK_INTERVAL:
                    await self.periodic_health_check()
                    self.last_periodic_check = current_time

                await asyncio.sleep(1.0)  # 1초 간격으로 체크
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)



    def shutdown(self):
        """Cleanup processes on shutdown."""
        logger.info("Shutting down Provider Manager...")
        self.is_running = False
        # subprocess 관리 프로세스 중지
        for name in list(self.processes.keys()):
            self.stop_process(name)
        # PM2 관리 프로세스 중지
        for name in self.PM2_MANAGED:
            if self.is_pm2_process_running(name):
                self.stop_pm2_process(name)

# ==========================================
# Main
# ==========================================
if __name__ == "__main__":
    manager = ProviderManager()
    
    # Handle signals
    def signal_handler(sig, frame):
        manager.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        if sys.platform == 'win32':
             # Windows specific event loop policy
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            
        asyncio.run(manager.run())
    except KeyboardInterrupt:
        manager.shutdown()
