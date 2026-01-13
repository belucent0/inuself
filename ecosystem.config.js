
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

// ROCm 환경 경로 (Audio Gateway용)
const ROCM_VENV_PATH = 'C:\\timblo\\torch-test\\rocm_env';
const ROCM_PYTHON_PATH = `${ROCM_VENV_PATH}\\Scripts\\python.exe`;

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

      // Architecture V4: 모든 AI 요청은 LiteLLM/Audio Gateway를 통해 라우팅
      // LiteLLM 설정 (V4 표준)
      LLM_PROVIDER: 'litellm',
      LITELLM_BASE_URL: 'http://127.0.0.1:4000',
      LITELLM_API_KEY: envVars.LITELLM_API_KEY || 'sk-litellm-master',
      LITELLM_MODEL: envVars.LITELLM_MODEL || 'qwen3-4b',

      // Audio Gateway 설정 (ASR/Diarization API)
      AUDIO_GATEWAY_URL: 'http://127.0.0.1:8001',

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
  // Provider Manager (Host Process Manager)
  // ========================================
  {
    name: 'provider-manager',
    cwd: 'C:\\timblo\\torch-test',
    script: 'infra/provider_manager/main.py',
    interpreter: BACKEND_PYTHONW_PATH,
    args: [],
    env: {
      PYTHONUNBUFFERED: '1',
      REDIS_URL: envVars.REDIS_URL || 'redis://localhost:6379/0',

      // FLM 설정 (Manager가 사용)
      FLM_LLM_MODEL: envVars.FLM_LLM_MODEL || 'qwen3-it:4b',
      FLM_PORT: '11434',

      // Llama 서버 설정 (Manager가 사용)
      LLM_SERVER_PATH: envVars.LLM_SERVER_PATH || 'llama-server',
      LLM_SERVER_MODEL: envVars.LLM_SERVER_MODEL || 'models/Qwen3-4B-Instruct-2507-Q4_K_S.gguf',
      LLM_SERVER_PORT: envVars.LLM_SERVER_PORT || '8080',
      LLM_CONTEXT_LENGTH: envVars.LLM_CONTEXT_LENGTH || '15000',
      LLM_N_GPU_LAYERS: envVars.LLM_N_GPU_LAYERS || '99',
      LLM_N_THREADS: envVars.LLM_N_THREADS || '8',
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 4000,
    watch: false,
    error_file: 'C:\\timblo\\torch-test\\logs\\provider-manager-error.log',
    out_file: 'C:\\timblo\\torch-test\\logs\\provider-manager-out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,
    windowsHide: true,
  },
  // ========================================
  // Audio Gateway (GPU Whisper + Diarization)
  // Architecture V4: GPU 기반 ASR 및 화자분리 서비스
  // ROCm 환경에서 실행 (rocm_env 가상환경)
  // ========================================
  {
    name: 'audio-gateway',
    cwd: 'C:\\timblo\\torch-test',
    script: ROCM_PYTHON_PATH,
    args: ['-m', 'uvicorn', 'audio_gateway.main:app', '--host', '0.0.0.0', '--port', '8001'],
    env: {
      PYTHONUNBUFFERED: '1',
      // Audio Gateway 설정
      AUDIO_GATEWAY_HOST: '0.0.0.0',
      AUDIO_GATEWAY_PORT: '8001',
      // Whisper 모델 설정 (정확도 모드: v3 사용)
      WHISPER_MODEL: 'openai/whisper-large-v3',
      WHISPER_DEVICE: '0',
      // Diarization 모델 설정
      DIARIZATION_MODEL: 'pyannote/speaker-diarization-community-1',
      CLUSTERING_THRESHOLD: '0.45',
      // HuggingFace 토큰 (pyannote 모델 접근용)
      HF_TOKEN: envVars.HF_TOKEN || '',
      // 임시 디렉토리
      TEMP_DIR: 'C:\\timblo\\torch-test\\data\\temp\\audio_gateway',
      // ROCm/CUDA 설정
      CUDA_VISIBLE_DEVICES: '0',
      HIP_VISIBLE_DEVICES: '0',
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '30s',  // 모델 로딩 시간 고려
    restart_delay: 10000,  // 재시작 전 10초 대기 (GPU 메모리 해제 시간)
    watch: false,
    error_file: 'C:\\timblo\\torch-test\\logs\\audio-gateway-error.log',
    out_file: 'C:\\timblo\\torch-test\\logs\\audio-gateway-out.log',
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,
    windowsHide: true,
  }
];

module.exports = {
  apps: apps
};
