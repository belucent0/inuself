
import asyncio
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Optional


import httpx
from ..core.logging import logger

# Whisper Server 설정
WHISPER_SERVER_PATH = r"C:\whisper-cpp\build\bin\Release\whisper-server.exe"

# 모델 경로 (우선순위: C:\whisper-cpp\models\ggml-base.bin)
# WHISPER_MODEL_PATH = "C:\whisper-cpp\models\ggml-base.bin"
# WHISPER_MODEL_PATH = r"C:\whisper-cpp\models\ggml-large-v3.bin"
WHISPER_MODEL_PATH = "C:\whisper-cpp\models\ggml-large-v3-turbo-q8_0.bin"

WHISPER_SERVER_PORT = 8001


class StreamASRWorker:
    """whisper-server.exe 프로세스를 관리하는 싱글톤 워커 클래스."""
    
    _instance: Optional["StreamASRWorker"] = None
    _process: Optional[subprocess.Popen] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StreamASRWorker, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance
    
    def __init__(self):
        if self.initialized:
            return
        self.initialized = True
        self.host = "127.0.0.1"
        self.port = WHISPER_SERVER_PORT
        
    def start(self):
        """whisper-server.exe 실행."""
        if self._process and self._process.poll() is None:
            logger.info("StreamASRWorker: Server already running.")
            return

        server_path = Path(WHISPER_SERVER_PATH)
        if not server_path.exists():
            logger.error(f"StreamASRWorker: Server binary not found at {server_path}")
            return

        model_path = Path(WHISPER_MODEL_PATH)
        if not model_path.exists():
            logger.error(f"StreamASRWorker: Model not found at {model_path}")
            return

        cmd = [
            str(server_path),
            "-m", str(model_path),
            "--port", str(self.port),
            "--host", self.host,
            "-l", "ko",  # 한국어 기본 설정
        ]
        
        logger.info(f"StreamASRWorker: Starting server... {' '.join(cmd)}")
        
        try:
            # Windows 프로세스 그룹 생성 (종료 시 자식까지 정리)
            creation_flags = 0
            if sys.platform == "win32":
                import subprocess as sp
                creation_flags = sp.CREATE_NEW_PROCESS_GROUP | sp.CREATE_NO_WINDOW

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creation_flags
            )
            
            # 비동기적으로 로그 모니터링 시작
            self._start_log_monitor()
            
            logger.info(f"StreamASRWorker: Server started with PID {self._process.pid}")
            
        except Exception as e:
            logger.error(f"StreamASRWorker: Failed to start server: {e}")
            self._process = None

    def stop(self):
        """서버 종료."""
        if self._process:
            logger.info("StreamASRWorker: Stopping server...")
            try:
                if sys.platform == "win32":
                    import ctypes
                    # 강제 종료 (Terminate)
                    ctypes.windll.kernel32.TerminateProcess(int(self._process._handle), 1)
                else:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
            except Exception as e:
                logger.error(f"StreamASRWorker: Error stopping server: {e}")
            finally:
                self._process = None
                logger.info("StreamASRWorker: Server stopped.")

    async def wait_for_ready(self, timeout: float = 60.0) -> bool:
        """서버가 준비될 때까지 대기 (Warm-up Inference 포함)."""
        if not self.is_running():
            return False
            
        start_time = time.time()
        url = f"{self.base_url}/inference"
        
        # 1초짜리 무음 WAV 생성 (Warm-up용)
        import io
        import wave
        
        dummy_wav = io.BytesIO()
        with wave.open(dummy_wav, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b'\x00' * 32000) # 1 sec of silence (16000 * 2 bytes)
        dummy_wav_bytes = dummy_wav.getvalue()
        
        async with httpx.AsyncClient() as client:
            while time.time() - start_time < timeout:
                try:
                    # 실제 인퍼런스 요청을 보내서 모델 로딩 여부 확인 (Warm-up)
                    files = {'file': ('warmup.wav', dummy_wav_bytes, 'audio/wav')}
                    data = {
                        "temperature": "0.0",
                        "response_format": "json",
                        "language": "ko" 
                    }
                    
                    resp = await client.post(url, files=files, data=data, timeout=5.0)
                    
                    if resp.status_code == 200:
                        logger.info("StreamASRWorker: Server is fully ready (Warm-up successful)!")
                        return True
                    else:
                        logger.debug(f"StreamASRWorker: Server responded {resp.status_code}, still loading...")
                        
                except Exception as e:
                    # 아직 연결 안됨
                    logger.debug(f"StreamASRWorker: Waiting for server... ({e})")
                    pass
                
                if not self.is_running():
                    return False
                    
                await asyncio.sleep(1.0)
                
        logger.error("StreamASRWorker: Timeout waiting for server ready")
        return False

    def is_running(self) -> bool:
        """서버 실행 여부 확인."""
        if self._process is None:
            return False
        if self._process.poll() is not None:
            # 프로세스가 종료된 상태
            self._process = None
            return False
        return True
        
    def _start_log_monitor(self):
        """서버 로그 모니터링 스레드 시작."""
        if not self._process:
            return
            
        def monitor_pipe(pipe, level):
            try:
                for line in iter(pipe.readline, ''):
                    if line:
                        line = line.strip()
                        if line:
                            if level == "stderr":
                                # whisper-server 로그는 stderr로 많이 나옴
                                logger.info(f"[WhisperServer] {line}")
                            else:
                                logger.debug(f"[WhisperServer] {line}")
            except Exception as e:
                logger.error(f"Error reading server output: {e}")

        # stdout, stderr 각각 모니터링 스레드 생성
        t1 = threading.Thread(target=monitor_pipe, args=(self._process.stdout, "stdout"), daemon=True)
        t2 = threading.Thread(target=monitor_pipe, args=(self._process.stderr, "stderr"), daemon=True)
        t1.start()
        t2.start()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

# 전역 인스턴스
stream_asr_worker = StreamASRWorker()
