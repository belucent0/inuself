
import asyncio
import subprocess
import sys
import time
import threading
import httpx
from ..core.logging import logger

# FastFlowLM Server 설정
WHISPER_SERVER_URL = "http://localhost:11434/v1"
# flm 커맨드 (PATH에 있다고 가정)
FLM_COMMAND = ["flm", "serve", "--asr", "1"]

class StreamASRWorker:
    """FastFlowLM(flm) 프로세스를 관리하는 싱글톤 워커 클래스."""
    
    _instance = None
    _process: subprocess.Popen | None = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StreamASRWorker, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance
    
    def __init__(self):
        if self.initialized:
            return
        self.initialized = True
        # PM2로 관리되는 flm 서버에 연결
        # Docker: host.docker.internal, 개발: 127.0.0.1
        import os
        self.host = os.getenv("FLM_HOST", "127.0.0.1")
        self.port = 11434
        
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def start(self):
        """flm 서버 연결 확인 (PM2로 관리됨)."""
        # PM2가 flm-server를 관리하므로 포트만 확인
        if self._is_port_in_use(self.port):
             logger.info(f"StreamASRWorker: flm server is running on port {self.port}")
             return
        
        logger.warning(f"StreamASRWorker: flm server not detected on port {self.port}. Please start with 'pm2 start ecosystem.config.js'")
    def stop(self):
        """서버 종료 (PM2로 관리되므로 별도 종료 불필요)."""
        pass

    def is_running(self) -> bool:
        """서버 실행 여부 확인."""
        if self._is_port_in_use(self.port):
            return True
        if self._process is not None and self._process.poll() is None:
            return True
        return False
    
    def _is_port_in_use(self, port: int) -> bool:
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # 타임아웃 짧게 (빠른 체크)
                s.settimeout(1.0)
                return s.connect_ex((self.host, port)) == 0
        except:
            return False

    async def wait_for_ready(self, timeout: float = 30.0) -> bool:
        """서버 연결 확인 (포트 체크만 수행)."""
        start_time = time.time()
        
        # FastFlowLM은 /v1/models 엔드포인트를 지원하지 않으므로
        # 포트가 열려있는지만 확인
        while time.time() - start_time < timeout:
            # 포트 확인
            if self._is_port_in_use(self.port):
                logger.info("StreamASRWorker: flm server port is open and ready!")
                # 추가로 1초 대기 (서버 완전 초기화 대기)
                await asyncio.sleep(1.0)
                return True
            
            # 프로세스가 관리 중인데 죽었는지 체크
            if self._process and self._process.poll() is not None:
                 logger.error(f"StreamASRWorker: Process died with code {self._process.returncode} while waiting.")
                 self._process = None
                 return False
                 
            await asyncio.sleep(0.5)
                
        logger.warning("StreamASRWorker: Timeout waiting for server ready")
        return False

    def _start_log_monitor(self):
        if not self._process:
            return
            
        def monitor_pipe(pipe, level):
            try:
                for line in iter(pipe.readline, ''):
                    if line:
                        line = line.strip()
                        if line:
                            # flm 로그
                            logger.info(f"[flm] {line}")
            except Exception:
                pass

        threading.Thread(target=monitor_pipe, args=(self._process.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=monitor_pipe, args=(self._process.stderr, "stderr"), daemon=True).start()

# 전역 인스턴스
stream_asr_worker = StreamASRWorker()
