module.exports = {
  apps: [
    {
      name: 'celery-worker',
      cwd: 'C:\\timblo\\torch-test\\backend',
      script: 'C:\\Users\\jg\\.local\\bin\\poetry.exe',
      args: ['run', 'python', 'run_celery_worker.py'],
      env: {
        TASK_QUEUE_TYPE: 'celery',
        NUM_ASR_WORKERS: '1',
        NUM_LLM_WORKERS: '1',
        // 순차 처리 설정 (기본값: true)
        // true: ASR과 LLM 작업을 순차 처리 (concurrency=1) - GPU 메모리 안정적
        // false: ASR과 LLM 작업을 동시 처리 (concurrency=2) - 더 빠르지만 GPU 메모리 부족 가능
        SEQUENTIAL_PROCESSING: 'true',
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

