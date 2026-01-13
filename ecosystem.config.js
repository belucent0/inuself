
// .env 파일 로드
const fs = require('fs');
const path = require('path');

// 프로젝트 루트의 .env 파일 읽기
const envPath = path.join(__dirname, '.env');
let envVars = {};

if (fs.existsSync(envPath)) {
  const envContent = fs.readFileSync(envPath, 'utf-8');
  envContent.split('\n').forEach(line => {
    line = line.trim();
    // 주석과 빈 줄 건너뛰기
    if (line && !line.startsWith('#')) {
      const [key, ...valueParts] = line.split('=');
      if (key && valueParts.length > 0) {
        const value = valueParts.join('=').trim();
        // 따옴표 제거
        envVars[key.trim()] = value.replace(/^["']|["']$/g, '');
      }
    }
  });
}

// Poetry 가상환경 경로 (하드코딩)
// 가상환경 경로가 변경되면 이 경로를 수정하세요
// 경로 확인: cd backend && poetry env info --path
const BACKEND_VENV_PATH = 'C:\\Users\\jg\\AppData\\Local\\pypoetry\\Cache\\virtualenvs\\torch-asr-backend-D1eM01ne-py3.12';
const BACKEND_PYTHONW_PATH = `${BACKEND_VENV_PATH}\\Scripts\\pythonw.exe`;

// Worker 가상환경 경로 (독립 가상환경)
// 경로 확인: cd worker && poetry env info --path
const WORKER_VENV_PATH = 'C:\\Users\\jg\\AppData\\Local\\pypoetry\\Cache\\virtualenvs\\torch-asr-worker-NVZIb8FW-py3.12';
const WORKER_PYTHONW_PATH = `${WORKER_VENV_PATH}\\Scripts\\pythonw.exe`;

// pythonw.exe를 사용하여 콘솔 창이 나타나지 않도록 함
const CELERY_PATH = WORKER_PYTHONW_PATH;

const apps = [
  // ========================================
  // 통합 워커 (V5): ASR, LLM, OCR 모든 작업 처리
  // ========================================
  {
    name: 'worker-unified',
    cwd: 'C:\\timblo\\torch-test',
    script: CELERY_PATH,
    args: [
      '-m', 'celery',
      '-A', 'worker.celery_app',
      'worker',
      '--pool=solo',
      '--loglevel=info',
      '--concurrency=1',
      '--max-tasks-per-child=100',
      '--queues=asr,llm,ocr',  // 모든 큐 처리
      '--hostname=worker-unified@%h'
    ],
    env: {
      PYTHONUNBUFFERED: '1',
      WORKER_TYPE: 'unified',
      SEQUENTIAL_PROCESSING: envVars.SEQUENTIAL_PROCESSING || 'true',

      // LiteLLM 설정 (V4 표준)
      LLM_PROVIDER: 'litellm',
      LITELLM_BASE_URL: envVars.LITELLM_BASE_URL || 'http://localhost:4000',
      LITELLM_API_KEY: envVars.LITELLM_API_KEY || 'sk-litellm-master',
      LITELLM_MODEL: envVars.LITELLM_MODEL || 'qwen3-4b',

      // LLM 공통 설정
      LLM_SYSTEM_PROMPT: envVars.LLM_SYSTEM_PROMPT || '',
      LLM_CONTEXT_LENGTH: envVars.LLM_CONTEXT_LENGTH || '15000',
      LLM_TEMPERATURE: envVars.LLM_TEMPERATURE || '0.4',
      LLM_MAX_TOKENS: envVars.LLM_MAX_TOKENS || '1024',

      // OCR 설정 (On-Demand llama-server용)
      OCR_SERVER_MODEL: 'models/Qwen3-VL-8B-Instruct/Qwen3-VL-8B-Instruct-Q8_0.gguf',
      OCR_SERVER_MMPROJ: 'models/Qwen3-VL-8B-Instruct/mmproj-F32.gguf',
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 4000,
    watch: false,
    error_file: 'C:\\timblo\\torch-test\\logs\\worker-unified-error.log',
    out_file: 'C:\\timblo\\torch-test\\logs\\worker-unified-out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,
    windowsHide: true,
  },
  // ========================================
  // FLM 서버 (NPU)
  // ========================================
  {
    name: 'flm-server',
    cwd: 'C:\\timblo\\torch-test',
    script: 'flm',
    args: ['serve', envVars.FLM_LLM_MODEL || 'qwen3-it:4b', '--asr', '1', '--port', '11434', '--ctx-len', '4096'],
    env: {
      FLM_LLM_MODEL: envVars.FLM_LLM_MODEL || 'qwen3-it:4b',
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 10000,
    kill_timeout: 15000,
    instance: 1,
    stop_exit_codes: [0, 1],
    listen_timeout: 10000,
    watch: false,
    error_file: 'C:\\timblo\\torch-test\\logs\\flm-server-error.log',
    out_file: 'C:\\timblo\\torch-test\\logs\\flm-server-out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,
    windowsHide: true,
  },
  // ========================================
  // Llama 서버 (GPU - 채팅용)
  // ========================================
  {
    name: 'llama-server',
    cwd: 'C:\\timblo\\torch-test',
    script: envVars.LLM_SERVER_PATH || 'llama-server',
    args: [
      '-m', envVars.LLM_SERVER_MODEL || 'models/Qwen3-4B-Instruct-2507-Q4_K_S.gguf',
      '--port', envVars.LLM_SERVER_PORT || '8080',
      '--ctx-size', envVars.LLM_CONTEXT_LENGTH || '15000',
      '--n-gpu-layers', envVars.LLM_N_GPU_LAYERS || '99',
      '--threads', envVars.LLM_N_THREADS || '8',
    ],
    env: {
      CUDA_VISIBLE_DEVICES: '0',
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 10000,
    kill_timeout: 15000,
    instance: 1,
    stop_exit_codes: [0, 1],
    listen_timeout: 10000,
    watch: false,
    error_file: 'C:\\timblo\\torch-test\\logs\\llama-server-error.log',
    out_file: 'C:\\timblo\\torch-test\\logs\\llama-server-out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,
    windowsHide: true,
  }
];

module.exports = {
  apps: apps
};
