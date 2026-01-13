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

# Environment Variables (Defaults from ecosystem.config.js)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FLM_MODEL = os.getenv("FLM_LLM_MODEL", "qwen3-it:4b")
FLM_PORT = os.getenv("FLM_PORT", "11434")

# Llama Server Config
LLM_SERVER_PATH = os.getenv("LLM_SERVER_PATH", "llama-server")
LLM_MODEL_PATH = os.getenv("LLM_SERVER_MODEL", "models/Qwen3-4B-Instruct-2507-Q4_K_S.gguf")
LLM_PORT = os.getenv("LLM_SERVER_PORT", "8080")
LLM_CTX = os.getenv("LLM_CONTEXT_LENGTH", "15000")
LLM_GPU_LAYERS = os.getenv("LLM_N_GPU_LAYERS", "99")
LLM_THREADS = os.getenv("LLM_N_THREADS", "8")

# Channel
CONTROL_CHANNEL = "provider.control"

# ==========================================
# Process Manager Class
# ==========================================
class ProviderManager:
    def __init__(self):
        self.processes = {}  # name -> subprocess.Popen
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
        if name in self.processes:
            proc = self.processes[name]
            if proc.poll() is None:
                logger.info(f"Process '{name}' is already running (PID: {proc.pid}). Ignoring start command.")
                return
            else:
                logger.info(f"Process '{name}' was dead. Restarting...")

        logger.info(f"Starting process '{name}': {' '.join(cmd)}")
        
        # Log files
        out_log = open(LOG_DIR / f"{log_prefix}-out.log", "a", encoding="utf-8")
        err_log = open(LOG_DIR / f"{log_prefix}-error.log", "a", encoding="utf-8")

        try:
            # Creation flags for Windows (DETACHED_PROCESS equivalent not strictly needed inside PM2 user mode)
            # But we want to hide window if run manually in some contexts
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            proc = subprocess.Popen(
                cmd,
                stdout=out_log,
                stderr=err_log,
                cwd=str(PROJECT_ROOT),
                creationflags=creation_flags
            )
            self.processes[name] = proc
            logger.info(f"Started '{name}' successfully (PID: {proc.pid})")
        except Exception as e:
            logger.error(f"Failed to start '{name}': {e}")

    def stop_process(self, name: str):
        """Stop a running subprocess."""
        if name not in self.processes:
            logger.info(f"Process '{name}' is not managed. Ignoring stop command.")
            return

        proc = self.processes[name]
        if proc.poll() is None:
            logger.info(f"Stopping '{name}' (PID: {proc.pid})...")
            
            if sys.platform == "win32":
                # Force kill process tree on Windows
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], 
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            
            logger.info(f"Stopped '{name}'.")
        else:
            logger.info(f"Process '{name}' is already stopped.")
        
        del self.processes[name]

    async def handle_message(self, message):
        """Handle incoming Redis message."""
        try:
            data = json.loads(message["data"])
            action = data.get("action")
            provider = data.get("provider")  # 'flm' or 'llama'

            logger.info(f"Received command: action={action}, provider={provider}")

            if action == "start":
                if provider == "flm":
                    cmd = ["flm", "serve", FLM_MODEL, "--asr", "1", "--port", FLM_PORT, "--ctx-len", "4096", "--host", "0.0.0.0"]
                    self.start_process("flm-server", cmd, "flm-server")
                
                elif provider == "llama":
                    cmd = [
                        LLM_SERVER_PATH,
                        "-m", LLM_MODEL_PATH,
                        "--port", LLM_PORT,
                        "--ctx-size", LLM_CTX,
                        "--n-gpu-layers", LLM_GPU_LAYERS,
                        "--threads", LLM_THREADS,
                        "--host", "0.0.0.0"
                    ]
                    self.start_process("llama-server", cmd, "llama-server")

            elif action == "stop":
                if provider == "flm":
                    self.stop_process("flm-server")
                elif provider == "llama":
                    self.stop_process("llama-server")
                elif provider == "all":
                    self.stop_process("flm-server")
                    self.stop_process("llama-server")

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON message: {message['data']}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def run(self):
        """Main loop."""
        await self.connect_redis()

        logger.info("Provider Manager is running. Waiting for commands...")

        # Keep alive loop
        while self.is_running:
            try:
                message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    await self.handle_message(message)
                
                # Check for dead processes and cleanup dictionary
                # (Optional: Auto-restart logic could go here, but this is 'on-demand' manager)
                dead_procs = [name for name, proc in self.processes.items() if proc.poll() is not None]
                for name in dead_procs:
                    logger.warning(f"Process '{name}' exited unexpectedly with code {self.processes[name].returncode}")
                    del self.processes[name]

                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)

    def shutdown(self):
        """Cleanup processes on shutdown."""
        logger.info("Shutting down Provider Manager...")
        self.is_running = False
        for name in list(self.processes.keys()):
            self.stop_process(name)

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
