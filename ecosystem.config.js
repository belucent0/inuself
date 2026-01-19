/**
 * PM2 Configuration - Architecture V7.3
 * Provider Manager가 모든 프로바이더 관리
 *
 * V7.3 변경사항:
 * - PM2는 provider-manager만 관리
 * - 모든 GPU/NPU 서버는 provider-manager가 직접 관리
 * - ProviderManager 모듈 분리 (provider_manager.py)
 * - HTTP API 지원 (provider_api.py)
 *
 * Provider Manager가 관리하는 서버들:
 * GPU 서버 (on-demand):
 * - whisper-server: Port 8001 (speed mode ASR)
 * - insanely-fast-server: Port 8002 (accuracy mode ASR)
 * - diarization-server: Port 8003 (speaker diarization)
 * - llama-server: Port 8080 (LLM 요약)
 * - llama-ocr-server: Port 8081 (OCR Vision)
 *
 * NPU 서버 (on-demand):
 * - FLM ASR: Port 11434 (whisper-v3:turbo)
 * - FLM LLM: Port 11435 (lfm2:2.6b)
 * - FLM OCR: Port 11436 (qwen3vl-it:4b)
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
const ROCM_PYTHON = `${ROCM_ENV_PATH}/Scripts/python.exe`;  // provider-manager용

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

// V7.3: Provider Manager가 모든 GPU/NPU 서버를 관리
// - GPU 서버: whisper-server, insanely-fast, diarization, llama-server, llama-ocr-server
// - NPU 서버: FLM ASR, FLM LLM, FLM OCR (각각 독립 포트)

module.exports = {
  apps: [
    // ========================================
    // Provider Manager (V7.3) - 항상 실행
    // Redis Stream GPU task processor + 모든 프로바이더 관리
    // Docker ↔ Host GPU/NPU 브릿지
    // ========================================
    {
      name: 'provider-manager',
      script: 'infra/provider_manager/main.py',
      interpreter: ROCM_PYTHONW,  // pythonw.exe 사용 (stdin=DEVNULL로 자식 프로세스 문제 해결됨)
      cwd: __dirname,
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONUTF8: '1',
        REDIS_URL: envVars.REDIS_URL || 'redis://localhost:6379/0',
        DIARIZATION_URL: 'http://localhost:8003',
        WHISPER_CPP_URL: 'http://localhost:8001',
        INSANELY_FAST_URL: 'http://localhost:8002',
        LLAMA_SERVER_URL: 'http://localhost:8080',
        LLAMA_OCR_SERVER_URL: 'http://localhost:8081',
        FLM_ASR_URL: 'http://localhost:11434',
        FLM_LLM_URL: 'http://localhost:11435',
        FLM_OCR_URL: 'http://localhost:11436',
        // OpenTelemetry 분산 추적
        OTEL_EXPORTER_OTLP_ENDPOINT: 'http://localhost:4317',
        OTEL_SERVICE_NAME: 'provider-manager',
        OTEL_TRACES_EXPORTER: 'otlp',
      },
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
      restart_delay: 4000,
      watch: false,
      windowsHide: true,

      // ========================================
      // PM2 로그 로테이션 설정
      // ========================================
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      error_file: 'logs/pm2-provider-manager-error.log',
      out_file: 'logs/pm2-provider-manager-out.log',
      merge_logs: true,
      // 참고: 파일 크기 기반 로테이션은 pm2-logrotate 모듈 필요
      // pm2 install pm2-logrotate
      // pm2 set pm2-logrotate:max_size 50M
      // pm2 set pm2-logrotate:retain 3
    },

    // ========================================
    // NPU Exporter - GPU Compute 엔진을 NPU로 변환/라벨링
    // Prometheus가 NPU 메트릭으로 스크래핑할 수 있도록 함
    // ========================================
    {
      name: 'npu-exporter',
      script: 'infra/npu-exporter/npu_exporter.py',
      interpreter: ROCM_PYTHON,
      cwd: __dirname,
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONUTF8: '1',
        NPU_EXPORTER_PORT: '9183',
        NPU_SCRAPE_INTERVAL: '5',
      },
      autorestart: true,
      max_restarts: 10,
      min_uptime: '5s',
      restart_delay: 2000,
      watch: false,
      windowsHide: true,

      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      error_file: 'logs/pm2-npu-exporter-error.log',
      out_file: 'logs/pm2-npu-exporter-out.log',
      merge_logs: true,
    },
  ]
};
