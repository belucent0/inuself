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

module.exports = {
  apps: [
    {
      name: 'celery-worker',
      cwd: 'C:\\timblo\\torch-test\\backend',
      script: 'C:\\Users\\jg\\.local\\bin\\poetry.exe',
      args: ['run', 'python', 'run_celery_worker.py'],
      env: {
        // 작업 큐 설정
        TASK_QUEUE_TYPE: envVars.TASK_QUEUE_TYPE || 'celery',
        NUM_ASR_WORKERS: envVars.NUM_ASR_WORKERS || '1',
        NUM_LLM_WORKERS: envVars.NUM_LLM_WORKERS || '1',
        // 순차 처리 설정 (기본값: true)
        // true: ASR과 LLM 작업을 순차 처리 (concurrency=1) - GPU 메모리 안정적
        // false: ASR과 LLM 작업을 동시 처리 (concurrency=2) - 더 빠르지만 GPU 메모리 부족 가능
        SEQUENTIAL_PROCESSING: envVars.SEQUENTIAL_PROCESSING || 'true',
        
        // 공통 LLM 설정 (.env에서 읽어옴)
        LLM_PROVIDER: envVars.LLM_PROVIDER || 'lmstudio',
        LLM_SYSTEM_PROMPT: envVars.LLM_SYSTEM_PROMPT || '',
        LLM_CONTEXT_LENGTH: envVars.LLM_CONTEXT_LENGTH || '15016',
        LLM_TEMPERATURE: envVars.LLM_TEMPERATURE || '0.4',
        LLM_TOP_P: envVars.LLM_TOP_P || '0.9',
        LLM_MAX_TOKENS: envVars.LLM_MAX_TOKENS || '1024',
        LLM_N_THREADS: envVars.LLM_N_THREADS || '8',
        
        // LM Studio 전용 설정 (.env에서 읽어옴)
        LMSTUDIO_BASE_URL: envVars.LMSTUDIO_BASE_URL || 'http://localhost:1234',
        LMSTUDIO_MODEL_NAME: envVars.LMSTUDIO_MODEL_NAME || 'gpt-oss-20b',
        
        // llama_cpp 전용 설정 (.env에서 읽어옴)
        LLM_MODEL_PATH: envVars.LLM_MODEL_PATH || '',
        LLM_N_GPU_LAYERS: envVars.LLM_N_GPU_LAYERS || '-1',  // -1: 모든 레이어 GPU, 0: CPU만, 양수: 하이브리드
      },
      autorestart: true,
      max_restarts: 10,
      min_uptime: '10s',
      restart_delay: 4000,
      watch: false,
      error_file: 'C:\\timblo\\torch-test\\logs\\celery-error.log',
      out_file: 'C:\\timblo\\torch-test\\logs\\celery-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    }
  ]
};

