
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
const VENV_PATH = 'C:\\Users\\jg\\AppData\\Local\\pypoetry\\Cache\\virtualenvs\\torch-asr-backend-D1eM01ne-py3.12';
const PYTHONW_PATH = `${VENV_PATH}\\Scripts\\pythonw.exe`;
// pythonw.exe를 사용하여 콘솔 창이 나타나지 않도록 함
// celery.exe는 콘솔 애플리케이션이므로 CMD 창이 나타남
const CELERY_PATH = PYTHONW_PATH;

// llama-server 관련 설정은 PM2에서 관리하지 않음
// worker-llm이 .env 파일에서 직접 읽어서 subprocess로 llama-server를 시작함
// LLAMA_SERVER_* 환경변수는 .env 파일에 설정하고, worker-llm의 Settings 클래스가 읽음

const apps = [
  {
    name: 'worker-asr',
    cwd: 'C:\\timblo\\torch-test\\backend',
    script: CELERY_PATH,
    args: [
      '-m', 'celery',
      '-A', 'app.worker.celery_app',
      'worker',
      '--pool=solo',
      '--loglevel=info',
      '--concurrency=1',
      '--max-tasks-per-child=100',
      '--queues=asr',
      '--hostname=worker-asr@%h'
    ],
    env: {
      // Python 출력 버퍼링 비활성화 (실시간 로그 출력)
      PYTHONUNBUFFERED: '1',
      // 워커 타입 설정
      WORKER_TYPE: 'asr',
      // 순차 처리 설정 (기본값: true)
      // true: ASR과 LLM 작업을 순차 처리 (concurrency=1) - GPU 메모리 안정적
      // false: ASR과 LLM 작업을 동시 처리 (concurrency=2) - 더 빠르지만 GPU 메모리 부족 가능
      SEQUENTIAL_PROCESSING: envVars.SEQUENTIAL_PROCESSING || 'true',
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 4000,
    watch: false,
    error_file: 'C:\\timblo\\torch-test\\logs\\worker-asr-error.log',
    out_file: 'C:\\timblo\\torch-test\\logs\\worker-asr-out.log',
    // PM2 타임스탬프는 유지하되, Python logging의 타임스탬프는 제거하여 중복 방지
    // PM2의 타임스탬프 형식: YYYY-MM-DD HH:mm:ss Z
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,
    // Windows에서 콘솔 창 숨기기
    windowsHide: true,
  },
  {
    name: 'worker-llm',
    cwd: 'C:\\timblo\\torch-test\\backend',
    script: CELERY_PATH,
    args: [
      '-m', 'celery',
      '-A', 'app.worker.celery_app',
      'worker',
      '--pool=solo',
      '--loglevel=info',
      '--concurrency=1',
      '--max-tasks-per-child=100',
      '--queues=llm',  // LLM 요약만 처리
      '--hostname=worker-llm@%h'
    ],
    env: {
      // Python 출력 버퍼링 비활성화 (실시간 로그 출력)
      PYTHONUNBUFFERED: '1',
      // 워커 타입 설정
      WORKER_TYPE: 'llm',
      // 순차 처리 설정 (기본값: true)
      SEQUENTIAL_PROCESSING: envVars.SEQUENTIAL_PROCESSING || 'true',
      
      // LLM 설정 (.env에서 읽어옴)
      LLM_PROVIDER: envVars.LLM_PROVIDER || 'llamacpp_server',
      LLM_SYSTEM_PROMPT: envVars.LLM_SYSTEM_PROMPT || '',
      LLM_CONTEXT_LENGTH: envVars.LLM_CONTEXT_LENGTH || '15000',  // 메모리 사용량 최적화
      LLM_TEMPERATURE: envVars.LLM_TEMPERATURE || '0.4',
      LLM_TOP_P: envVars.LLM_TOP_P || '0.9',
      LLM_MAX_TOKENS: envVars.LLM_MAX_TOKENS || '1024',
      LLM_N_THREADS: envVars.LLM_N_THREADS || '8',
      
      // LLM API 서버 설정 (모든 OpenAI 호환 API, provider와 무관)
      LLM_BASE_URL: envVars.LLM_BASE_URL || 'http://localhost:8080',
      LLM_MODEL_NAME: envVars.LLM_MODEL_NAME || 'Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf',
      
      // llama_cpp 전용 설정 (.env에서 읽어옴, llama-cpp-python 직접 사용 시)
      LLM_MODEL_PATH: envVars.LLM_MODEL_PATH || '',
      LLM_N_GPU_LAYERS: envVars.LLM_N_GPU_LAYERS || '-1',  // -1: 모든 레이어 GPU, 0: CPU만, 양수: 하이브리드
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 4000,
    watch: false,
    error_file: 'C:\\timblo\\torch-test\\logs\\worker-llm-error.log',
    out_file: 'C:\\timblo\\torch-test\\logs\\worker-llm-out.log',
    // PM2 타임스탬프는 유지하되, Python logging의 타임스탬프는 제거하여 중복 방지
    // PM2의 타임스탬프 형식: YYYY-MM-DD HH:mm:ss Z
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,
    // Windows에서 콘솔 창 숨기기
    windowsHide: true,
  },
  {
    name: 'worker-ocr',
    cwd: 'C:\\timblo\\torch-test\\backend',
    script: CELERY_PATH,
    args: [
      '-m', 'celery',
      '-A', 'app.worker.celery_app',
      'worker',
      '--pool=solo',
      '--loglevel=info',
      '--concurrency=1',
      '--max-tasks-per-child=100',
      '--queues=ocr',  // OCR 처리만 (기본 모드: Qwen3-VL API, Docling 모드: Docling)
      '--hostname=worker-ocr@%h'
    ],
    env: {
      // Python 출력 버퍼링 비활성화 (실시간 로그 출력)
      PYTHONUNBUFFERED: '1',
      // 워커 타입 설정
      WORKER_TYPE: 'ocr',
      // 순차 처리 설정 (기본값: true)
      SEQUENTIAL_PROCESSING: envVars.SEQUENTIAL_PROCESSING || 'true',
      
      // LLM API 서버 설정 (기본 모드에서 Qwen3-VL API 사용)
      LLM_BASE_URL: envVars.LLM_BASE_URL || 'http://localhost:8080',
      LLM_MODEL_NAME: envVars.LLM_MODEL_NAME || 'Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf',
    },
    autorestart: true,
    max_restarts: 10,
    min_uptime: '10s',
    restart_delay: 4000,
    watch: false,
    error_file: 'C:\\timblo\\torch-test\\logs\\worker-ocr-error.log',
    out_file: 'C:\\timblo\\torch-test\\logs\\worker-ocr-out.log',
    // PM2 타임스탬프는 유지하되, Python logging의 타임스탬프는 제거하여 중복 방지
    // PM2의 타임스탬프 형식: YYYY-MM-DD HH:mm:ss Z
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    merge_logs: false,
    // Windows에서 콘솔 창 숨기기
    windowsHide: true,
  }
];

// llama-server는 PM2에서 관리하지 않음
// worker-llm이 요청을 받을 때마다 subprocess로 llama-server를 시작하고 종료함
// (llamacpp_server_client.py의 _llama_server_process 컨텍스트 매니저 참조)
// 따라서 PM2 설정에서 제외하여 메모리 낭비를 방지함

module.exports = {
  apps: apps
};
