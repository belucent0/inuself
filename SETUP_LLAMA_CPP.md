# llama.cpp 설정 가이드

이 가이드는 Windows 환경에서 llama.cpp를 사용하여 LLM 요약 기능을 활성화하는 방법을 설명합니다.

## 1. llama-cpp-python 설치 (Vulkan 지원)

llama.cpp는 Vulkan 가속을 지원하는 빌드가 필요합니다.

### 방법 A: Poetry를 통한 설치 (권장)

```bash
cd backend
poetry install --extras gpu_worker
```

그 다음 Vulkan 지원으로 재빌드:

```bash
# Poetry 가상환경에서
poetry run pip uninstall llama-cpp-python -y
CMAKE_ARGS='-DGGML_VULKAN=ON' poetry run pip install llama-cpp-python --no-cache-dir
```

### 방법 B: 직접 pip로 설치

```bash
cd backend
poetry shell  # Poetry 가상환경 활성화

# 기존 설치 제거 (있는 경우)
pip uninstall llama-cpp-python -y

# Vulkan 지원 빌드로 설치
CMAKE_ARGS='-DGGML_VULKAN=ON' pip install llama-cpp-python --no-cache-dir
```

> ⚠️ **참고**: 
> - Vulkan SDK가 설치되어 있어야 합니다. [Vulkan SDK 다운로드](https://vulkan.lunarg.com/sdk/home)
> - 빌드에 시간이 걸릴 수 있습니다 (10-30분)
> - CMake와 C++ 컴파일러가 필요합니다

## 2. 모델 파일 준비

1. GGUF 형식 모델 파일을 다운로드합니다
   - 예: `gpt-oss-20b-Q4_K_S.gguf`
   - Hugging Face나 다른 모델 저장소에서 다운로드 가능

2. 모델 파일을 프로젝트 루트의 `models/` 디렉토리에 배치합니다:
   ```
   torch-test/
   ├── models/
   │   └── gpt-oss-20b-Q4_K_S.gguf
   ├── backend/
   └── ...
   ```

   또는 절대 경로를 사용할 수 있습니다.

## 3. .env 파일 설정

프로젝트 루트의 `.env` 파일에 다음 설정을 추가합니다:

```env
# LLM Provider를 llama_cpp로 설정
LLM_PROVIDER=llama_cpp

# 모델 파일 경로 (프로젝트 루트 기준 상대 경로 또는 절대 경로)
LLM_MODEL_PATH=models/gpt-oss-20b-Q4_K_S.gguf

# 공통 LLM 설정 (선택사항, 기본값 사용 가능)
LLM_SYSTEM_PROMPT=당신은 회의록을 요약하는 전문가입니다...
LLM_CONTEXT_LENGTH=4096
LLM_TEMPERATURE=0.4
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1024
LLM_N_THREADS=8

# llama_cpp 전용 설정
LLM_N_GPU_LAYERS=-1
```

### 설정 설명

#### 공통 LLM 설정 (모든 provider에서 사용)
- `LLM_SYSTEM_PROMPT`: 시스템 프롬프트 (선택사항)
- `LLM_CONTEXT_LENGTH`: 컨텍스트 윈도우 크기 (토큰 수)
- `LLM_TEMPERATURE`: 생성 온도 (0.0 ~ 1.0, 낮을수록 일관성 높음)
- `LLM_TOP_P`: Top-p 샘플링 (0.0 ~ 1.0)
- `LLM_MAX_TOKENS`: 최대 생성 토큰 수
- `LLM_N_THREADS`: CPU 스레드 수

#### llama_cpp 전용 설정
- `LLM_PROVIDER`: `llama_cpp`로 설정
- `LLM_MODEL_PATH`: 모델 파일 경로 (상대 또는 절대 경로)
- `LLM_N_GPU_LAYERS`: GPU 레이어 수 (-1: 모든 레이어 GPU, 0: CPU만, 양수: 지정된 레이어 수만큼 GPU)

## 4. 워커 실행 (PM2 사용)

Windows에서는 PM2를 사용하여 Celery 워커를 실행합니다. PM2는 워커를 백그라운드에서 실행하고 자동 재시작을 관리합니다.

### PM2 설치 (처음 한 번만)

```bash
# Git Bash 또는 PowerShell에서
npm install -g pm2
npm install -g pm2-windows-startup
```

### PM2로 워커 시작

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

### PM2 워커 관리 명령어

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
```

### Windows 부팅 시 자동 시작 설정

```bash
# 현재 PM2 프로세스 목록 저장
pm2 save

# Windows 부팅 시 자동 시작 설정
pm2-startup install

# 또는 수동으로 (PowerShell 관리자 권한)
pm2 startup
# 출력된 명령어를 복사해서 실행
```

### service_manager.py 사용 (권장)

GUI 기반 서비스 관리 도구를 사용할 수 있습니다:

```bash
# 프로젝트 루트에서
python service_manager.py
```

메뉴에서:
- `7. PM2 워커 관리` 선택
- 워커 시작/중지/재시작/로그 확인 가능

### 배치 파일 사용

프로젝트 루트에 있는 배치 파일을 사용할 수 있습니다:

```bash
# 모든 서비스 시작 (Docker Compose + PM2)
start.bat

# 모든 서비스 중지
stop.bat

# 모든 서비스 재시작
restart.bat
```

### 개발 환경 (수동 실행)

개발 중에는 수동으로 실행할 수도 있습니다:

```bash
cd backend
poetry run python -m app.worker.run_celery_worker
```

> ⚠️ **참고**: PM2를 사용하면 워커가 백그라운드에서 실행되며, 자동 재시작과 로그 관리가 자동으로 처리됩니다.

## 5. 확인

워커가 시작되면 다음과 같은 로그를 확인할 수 있습니다:

```
[LLM Worker] 헬스체크 시작: LLM provider=llama_cpp
[LLM Worker] 모델 파일 확인 중: /path/to/models/gpt-oss-20b-Q4_K_S.gguf
[LLM Worker] [OK] 모델 파일 확인 완료 (크기: XXXX.XX MB)
[LLM Worker] llama.cpp 모델 로드 및 테스트 쿼리 실행 중...
[LLM Worker] LLM 모델 로드 시작: /path/to/models/gpt-oss-20b-Q4_K_S.gguf
[LLM Worker] Vulkan 가속 사용 (n_gpu_layers=-1, CPU 폴백 금지)
[LLM Worker] LLM 모델 로드 완료
[LLM Worker] [OK] 헬스체크 성공: 모델이 정상 응답함
```

## 문제 해결

### 모델 파일을 찾을 수 없는 경우

```
[LLM Worker] [ERROR] 헬스체크 실패: 모델 파일이 존재하지 않습니다
```

**해결 방법:**
- `LLM_MODEL_PATH`가 올바른지 확인
- 모델 파일이 실제로 존재하는지 확인
- 절대 경로를 사용해보세요

### Vulkan 지원 빌드가 아닌 경우

```
LLM 모델 로드 실패: ...
```

**해결 방법:**
1. Vulkan SDK가 설치되어 있는지 확인
2. llama-cpp-python을 Vulkan 지원으로 재빌드:
   ```bash
   CMAKE_ARGS='-DGGML_VULKAN=ON' pip install --force-reinstall llama-cpp-python --no-cache-dir
   ```

### 모델 형식이 호환되지 않는 경우

**해결 방법:**
- GGUF 형식 모델을 사용하는지 확인
- llama-cpp-python 버전과 모델 형식이 호환되는지 확인

## LM Studio와 llama.cpp 전환

환경 변수 `LLM_PROVIDER`와 provider별 설정만 변경하면 됩니다:

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

워커를 재시작하면 새로운 provider가 적용됩니다.

