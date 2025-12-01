# Windows PM2 워커 실행 가이드

이 가이드는 Windows 환경에서 PM2를 사용하여 Celery 워커를 실행하는 방법을 설명합니다.

## 1. PM2 설치 (처음 한 번만)

```bash
# Git Bash 또는 PowerShell에서
npm install -g pm2
npm install -g pm2-windows-startup
```

## 2. llama.cpp 설정

### 2.1 llama-cpp-python 설치 (Vulkan 지원)

```bash
cd backend
poetry shell  # Poetry 가상환경 활성화

# Vulkan 지원 빌드로 설치
CMAKE_ARGS='-DGGML_VULKAN=ON' pip install llama-cpp-python --no-cache-dir
```

> ⚠️ **참고**: Vulkan SDK가 설치되어 있어야 합니다.

### 2.2 모델 파일 준비

모델 파일을 프로젝트 루트의 `models/` 디렉토리에 배치:
```
torch-test/
└── models/
    └── gpt-oss-20b-Q4_K_S.gguf
```

### 2.3 .env 파일 설정

프로젝트 루트의 `.env` 파일에 추가:

```env
# LLM Provider 설정
LLM_PROVIDER=llama_cpp
LLM_MODEL_PATH=models/gpt-oss-20b-Q4_K_S.gguf

# LLM 설정 (선택사항)
LLM_CONTEXT_LENGTH=4096
LLM_TEMPERATURE=0.4
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1024
LLM_N_THREADS=8

# 작업 큐 설정
TASK_QUEUE_TYPE=celery
NUM_ASR_WORKERS=1
NUM_LLM_WORKERS=1
SEQUENTIAL_PROCESSING=true
```

## 3. PM2로 워커 시작

### 방법 1: 직접 PM2 명령어 사용

```bash
# 프로젝트 루트에서
pm2 start ecosystem.config.js

# 상태 확인
pm2 status

# 로그 확인
pm2 logs celery-worker

# 실시간 로그 확인
pm2 logs celery-worker --lines 100
```

### 방법 2: service_manager.py 사용 (권장)

```bash
# 프로젝트 루트에서
python service_manager.py
```

메뉴에서:
- `7. PM2 워커 관리` 선택
- `1. PM2 워커 시작` 선택

### 방법 3: 배치 파일 사용

```bash
# 모든 서비스 시작 (Docker Compose + PM2)
start.bat
```

## 4. PM2 워커 관리 명령어

```bash
# 워커 재시작 (환경 변수 업데이트 포함)
pm2 restart celery-worker --update-env

# 워커 중지
pm2 stop celery-worker

# 워커 삭제
pm2 delete celery-worker

# 모든 PM2 프로세스 중지
pm2 stop all

# PM2 모니터링 대시보드
pm2 monit

# 워커 상태 상세 확인
pm2 describe celery-worker
```

## 5. Windows 부팅 시 자동 시작 설정

```bash
# 현재 PM2 프로세스 목록 저장
pm2 save

# Windows 부팅 시 자동 시작 설정
pm2-startup install

# 또는 수동으로 (PowerShell 관리자 권한)
pm2 startup
# 출력된 명령어를 복사해서 실행
```

## 6. 로그 확인

### PM2 로그 위치

- 표준 출력: `C:\timblo\torch-test\logs\celery-out.log`
- 에러 로그: `C:\timblo\torch-test\logs\celery-error.log`

### 로그 확인 명령어

```bash
# 최근 50줄 로그
pm2 logs celery-worker --lines 50

# 실시간 로그 (Ctrl+C로 종료)
pm2 logs celery-worker

# 로그 파일 직접 확인
type C:\timblo\torch-test\logs\celery-out.log
type C:\timblo\torch-test\logs\celery-error.log
```

## 7. llama.cpp 사용 확인

워커가 정상적으로 시작되면 로그에서 다음 메시지를 확인할 수 있습니다:

```
[LLM Worker] 헬스체크 시작: LLM provider=llama_cpp
[LLM Worker] 모델 파일 확인 중: C:\timblo\torch-test\models\gpt-oss-20b-Q4_K_S.gguf
[LLM Worker] [OK] 모델 파일 확인 완료 (크기: XXXX.XX MB)
[LLM Worker] llama.cpp 모델 로드 및 테스트 쿼리 실행 중...
[LLM Worker] LLM 모델 로드 시작: ...
[LLM Worker] Vulkan 가속 사용 (n_gpu_layers=-1, CPU 폴백 금지)
[LLM Worker] LLM 모델 로드 완료
[LLM Worker] [OK] 헬스체크 성공: 모델이 정상 응답함
```

## 8. LM Studio ↔ llama.cpp 전환

`.env` 파일의 `LLM_PROVIDER`와 provider별 설정만 변경하고 워커를 재시작하면 됩니다:

```env
# 공통 LLM 설정 (모든 provider에서 사용)
LLM_SYSTEM_PROMPT=당신은 회의록을 요약하는 전문가입니다...
LLM_CONTEXT_LENGTH=15016
LLM_TEMPERATURE=0.4
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1024
LLM_N_THREADS=8

# LM Studio 사용
LLM_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234
LMSTUDIO_MODEL_NAME=gpt-oss-20b

# llama.cpp 사용
LLM_PROVIDER=llama_cpp
LLM_MODEL_PATH=models/gpt-oss-20b-Q4_K_S.gguf
LLM_N_GPU_LAYERS=-1
```

워커 재시작:
```bash
pm2 restart celery-worker --update-env
```

## 9. 문제 해결

### 워커가 시작되지 않는 경우

1. 로그 확인:
   ```bash
   pm2 logs celery-worker --lines 100
   ```

2. Poetry 경로 확인:
   - `ecosystem.config.js`의 `script` 경로가 올바른지 확인
   - 기본값: `C:\Users\jg\.local\bin\poetry.exe`
   - 실제 Poetry 경로로 수정 필요

3. 모델 파일 경로 확인:
   - `.env`의 `LLM_MODEL_PATH`가 올바른지 확인
   - 절대 경로 사용 권장

### 워커가 계속 재시작되는 경우

1. 로그에서 에러 메시지 확인
2. 모델 파일이 존재하는지 확인
3. llama-cpp-python이 제대로 설치되었는지 확인

### 환경 변수가 적용되지 않는 경우

```bash
# --update-env 옵션으로 재시작
pm2 restart celery-worker --update-env

# 또는 삭제 후 재시작
pm2 delete celery-worker
pm2 start ecosystem.config.js
```

## 10. 전체 시스템 시작 순서

1. **Docker Compose 서비스 시작**
   ```bash
   docker compose up -d
   ```

2. **PM2 워커 시작**
   ```bash
   pm2 start ecosystem.config.js
   ```

3. **상태 확인**
   ```bash
   docker compose ps
   pm2 status
   ```

## 참고

- `ecosystem.config.js`: PM2 설정 파일
- `service_manager.py`: GUI 기반 서비스 관리 도구
- `start.bat`, `stop.bat`, `restart.bat`: 배치 파일로 간편하게 관리



