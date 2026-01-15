/**
 * PM2 Configuration - Architecture V6.2
 * Provider Manager + GPU/NPU Servers
 *
 * VRAM 관리 전략:
 * - diarization-server, insanely-fast-server: 상시 실행, 모델만 on-demand 로드/언로드
 * - whisper-server: 상시 실행 (요청마다 whisper-cli.exe 호출, 상시 VRAM 사용 없음)
 * - llama-server, llama-ocr-server: 프로세스 자체 on-demand (GPU)
 *
 * GPU 서버 포트 분리:
 * - llama-server: LLM 요약 (Qwen3-4B) - Port 8080
 * - llama-ocr-server: OCR Vision (Qwen3-VL-8B) - Port 8081
 *
 * FLM NPU 서버 (3개 분리 운영):
 * - flm-asr-server: ASR 스트리밍 (whisper-v3:turbo) - Port 11434
 * - flm-llm-server: LLM 요약 (qwen3-it:4b) - Port 11435
 * - flm-ocr-server: OCR Vision (qwen3vl-it:4b) - Port 11436
 */
const fs = require('fs');
const path = require('path');

// .env 파일 로드
const envPath = path.join(__dirname, '.env');
let envVars = {};

if (fs.existsSync(envPath)) {
  const envContent = fs.readFileSync(envPath, 'utf-8');
  envContent.split('\n').forEach(line => {
    line = line.trim();
    if (line && !line.startsWith('#')) {
      const [key, ...valueParts] = line.split('=');
      if (key && valueParts.length > 0) {
        const value = valueParts.join('=').trim();
        envVars[key.trim()] = value.replace(/^["']|["']$/g, '');
      }
    }
  });
}

// ==========================================
// 경로 설정 (외부 경로는 .env에서)
// ==========================================
const BACKEND_VENV_PATH = envVars.BACKEND_VENV_PATH || 'C:\\Users\\jg\\AppData\\Local\\pypoetry\\Cache\\virtualenvs\\torch-asr-backend-D1eM01ne-py3.12';
const BACKEND_PYTHONW_PATH = `${BACKEND_VENV_PATH}\\Scripts\\pythonw.exe`;

const ROCM_ENV_PATH = envVars.ROCM_ENV_PATH || 'rocm_env';
const ROCM_PYTHONW = `${ROCM_ENV_PATH}/Scripts/pythonw.exe`;

const WHISPER_CPP_PATH = envVars.WHISPER_CPP_PATH || 'whisper-server.exe';
const WHISPER_CPP_MODEL = envVars.WHISPER_CPP_MODEL || 'models/ggml-large-v3-turbo.bin';
const LLM_SERVER_PATH = envVars.LLM_SERVER_PATH || 'llama-server';

// LLM 설정
const LLM_SERVER_PORT = envVars.LLM_SERVER_PORT || '8080';
const LLM_CONTEXT_LENGTH = envVars.LLM_CONTEXT_LENGTH || '15000';
const LLM_N_GPU_LAYERS = envVars.LLM_N_GPU_LAYERS || '99';
const LLM_N_THREADS = envVars.LLM_N_THREADS || '8';
const LLM_MODEL = envVars.LLM_MODEL || 'models/Qwen3-4B-Instruct-2507-Q4_K_S.gguf';

// OCR Vision 설정 (GPU) - llama-ocr-server
const OCR_SERVER_PORT = envVars.OCR_SERVER_PORT || '8081';
const OCR_SERVER_MODEL = envVars.OCR_SERVER_MODEL || 'models/Qwen3-VL-8B-Instruct/Qwen3-VL-8B-Instruct-Q8_0.gguf';
const OCR_SERVER_MMPROJ = envVars.OCR_SERVER_MMPROJ || 'models/Qwen3-VL-8B-Instruct/mmproj-F32.gguf';
const OCR_SERVER_THREADS = envVars.OCR_SERVER_THREADS || '4';
const OCR_SERVER_GPU_LAYERS = envVars.OCR_SERVER_GPU_LAYERS || '75';
const OCR_CONTEXT_LENGTH = envVars.OCR_CONTEXT_LENGTH || '10000';

// FLM 설정 (NPU 서버) - 3개 분리 운영
const FLM_LLM_MODEL = envVars.FLM_LLM_MODEL || 'qwen3-it:4b';   // LLM 요약
const FLM_OCR_MODEL = envVars.FLM_OCR_MODEL || 'qwen3vl-it:4b'; // OCR Vision
// ASR은 --asr 1 플래그로 whisper-v3:turbo 단독 실행

module.exports = {
  apps: [
    // ========================================
    // Provider Manager (항상 실행)
    // Redis ↔ PM2 브릿지, idle timeout 관리
    // ========================================
    {
      name: 'provider-manager',
      script: 'infra/provider_manager/main.py',
      interpreter: BACKEND_PYTHONW_PATH,
      cwd: __dirname,
      env: {
        PYTHONUNBUFFERED: '1',
        REDIS_URL: envVars.REDIS_URL || 'redis://localhost:6379/0',
      },
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
      restart_delay: 4000,
      watch: false,
      windowsHide: true,
    },

    // ========================================
    // GPU/NPU Servers
    // Model Unload Servers: 상시 실행, 모델만 언로드
    // Process On-Demand: Provider Manager가 pm2 start/stop으로 제어
    // ========================================

    // whisper.cpp (GPU Speed ASR) - Port 8001
    // 상시 실행 (요청마다 whisper-cli.exe 서브프로세스 실행, 상시 VRAM 사용 없음)
    {
      name: 'whisper-server',
      script: ROCM_PYTHONW,
      args: 'scripts/whisper_cpp_server.py',
      cwd: __dirname,
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
      watch: false,
      kill_timeout: 5000,
      windowsHide: true,
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONUTF8: '1',
        WHISPER_CLI_PATH: WHISPER_CPP_PATH.replace('whisper-server.exe', 'whisper-cli.exe'),
        WHISPER_MODELS_DIR: path.dirname(WHISPER_CPP_MODEL),
      }
    },

    // llama.cpp (LLM Summarization) - Port 8080
    // 단일 모델 모드 사용 (멀티 모델 모드는 라우터 연결 문제 있음)
    {
      name: 'llama-server',
      script: LLM_SERVER_PATH,
      args: `-m ${LLM_MODEL} --port ${LLM_SERVER_PORT} --ctx-size ${LLM_CONTEXT_LENGTH} --n-gpu-layers ${LLM_N_GPU_LAYERS} --threads ${LLM_N_THREADS} --host 0.0.0.0`,
      cwd: __dirname,
      interpreter: 'none',
      autorestart: false,
      watch: false,
      kill_timeout: 5000,
      windowsHide: true,
    },

    // llama.cpp OCR Vision (GPU Accuracy OCR) - Port 8081
    // Qwen3-VL-8B Vision 모델 (mmproj 필요)
    {
      name: 'llama-ocr-server',
      script: LLM_SERVER_PATH,
      args: `-m ${OCR_SERVER_MODEL} --mmproj ${OCR_SERVER_MMPROJ} --port ${OCR_SERVER_PORT} --ctx-size ${OCR_CONTEXT_LENGTH} --n-gpu-layers ${OCR_SERVER_GPU_LAYERS} --threads ${OCR_SERVER_THREADS} --host 0.0.0.0`,
      cwd: __dirname,
      interpreter: 'none',
      autorestart: false,  // Provider Manager가 on-demand 제어
      watch: false,
      kill_timeout: 10000,
      windowsHide: true,
    },

    // Diarization (Speaker Diarization) - Port 8003
    // 상시 실행, 모델만 on-demand 로드/언로드 (VRAM 관리)
    {
      name: 'diarization-server',
      script: ROCM_PYTHONW,
      args: 'scripts/diarization_server.py',
      cwd: __dirname,
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
      watch: false,
      kill_timeout: 10000,
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONUTF8: '1'
      }
    },

    // Insanely Fast Whisper (GPU Accuracy ASR) - Port 8002
    // 상시 실행, 모델만 on-demand 로드/언로드 (VRAM 관리)
    {
      name: 'insanely-fast-server',
      script: ROCM_PYTHONW,
      args: 'scripts/insanely_fast_server.py',
      cwd: __dirname,
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
      watch: false,
      kill_timeout: 10000,
      windowsHide: true,
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONUTF8: '1'
      }
    },

    // FLM ASR (NPU) - Port 11434
    // whisper-v3:turbo 단독 (--asr 1, 기본 모델 없음)
    {
      name: 'flm-asr-server',
      script: 'C:\\Program Files\\flm\\flm.exe',
      args: `serve --asr 1 --port 11434 --pmode performance`,
      cwd: __dirname,
      interpreter: 'none',
      autorestart: false,  // Provider Manager가 on-demand 제어
      watch: false,
      kill_timeout: 10000,
      windowsHide: true,
    },

    // FLM LLM (NPU) - Port 11435
    // qwen3-it:4b (텍스트 LLM)
    {
      name: 'flm-llm-server',
      script: 'C:\\Program Files\\flm\\flm.exe',
      args: `serve ${FLM_LLM_MODEL} --port 11435 --pmode performance`,
      cwd: __dirname,
      interpreter: 'none',
      autorestart: false,  // Provider Manager가 on-demand 제어
      watch: false,
      kill_timeout: 10000,
      windowsHide: true,
    },

    // FLM OCR Vision (NPU) - Port 11436
    // qwen3vl-it:4b (Vision 모델)
    {
      name: 'flm-ocr-server',
      script: 'C:\\Program Files\\flm\\flm.exe',
      args: `serve ${FLM_OCR_MODEL} --port 11436 --pmode performance`,
      cwd: __dirname,
      interpreter: 'none',
      autorestart: false,  // Provider Manager가 on-demand 제어
      watch: false,
      kill_timeout: 10000,
      windowsHide: true,
    }
  ]
};
