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
 * - llama-server: Port 8082 (LLM 요약)
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
const ROCM_ENV_PATH = envVars.ROCM_ENV_PATH || 'rocm_env';
const ROCM_PYTHONW = `${ROCM_ENV_PATH}/Scripts/pythonw.exe`;
const ROCM_PYTHON = `${ROCM_ENV_PATH}/Scripts/python.exe`;  // npu-exporter용 (현재 주석처리됨)

// V7.3: Provider Manager가 모든 GPU/NPU 서버를 관리하므로 
// 개별 서버 설정 변수들은 여기서 사용되지 않음 (Provider Manager가 .env에서 직접 로드)

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
      interpreter: ROCM_PYTHONW,  // pythonw.exe 사용 (콘솔 창 없음)
      cwd: __dirname,
      env: {
        PYTHONUNBUFFERED: '1',
        PYTHONUTF8: '1',
        REDIS_URL: envVars.REDIS_URL || 'redis://localhost:6379/0',
        DIARIZATION_URL: 'http://localhost:8003',
        WHISPER_CPP_URL: 'http://localhost:8001',
        INSANELY_FAST_URL: 'http://localhost:8002',
        LLAMA_SERVER_URL: 'http://localhost:8082',
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
      max_restarts: 3,          // 최대 3번만 재시작 (crash loop 방지)
      min_uptime: '30s',        // 30초 이상 실행되어야 안정으로 판단
      restart_delay: 10000,     // 재시작 전 대기 (10초)
      kill_timeout: 15000,      // 종료 대기 (15초) - graceful shutdown 시간 확보
      treekill: true,           // 모든 자식 프로세스도 종료 (Windows)
      wait_ready: false,
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
  ]
};
