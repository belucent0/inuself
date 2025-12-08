# .env 파일 구성 가이드

이 문서는 프로젝트의 `.env` 파일 구성 방법을 설명합니다.

## 기본 구조

프로젝트 루트에 `.env` 파일을 생성하고 아래 설정을 추가하세요.

## 시나리오별 구성

### 시나리오 1: llama.cpp 서버 사용 (Qwen3-VL 모델) - 권장

worker-llm이 subprocess로 llama.cpp 서버를 자동 실행하고 Qwen3-VL 모델을 사용하는 경우:

```env
# ============================================
# 데이터베이스 설정
# ============================================
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/asr

# ============================================
# Redis 설정
# ============================================
REDIS_URL=redis://localhost:6379/0

# ============================================
# 작업 큐 설정
# ============================================
TASK_QUEUE_TYPE=celery
NUM_ASR_WORKERS=1
NUM_LLM_WORKERS=1
SEQUENTIAL_PROCESSING=true

# ============================================
# LLM Provider 설정
# ============================================
LLM_PROVIDER=llamacpp_server

# ============================================
# LLM API 서버 설정 (공통)
# ============================================
LLM_BASE_URL=http://localhost:8080
LLM_MODEL_NAME=Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf

# ============================================
# 공통 LLM 설정
# ============================================
LLM_CONTEXT_LENGTH=8192
LLM_TEMPERATURE=0.4
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1024
LLM_N_THREADS=8

# ============================================
# llama.cpp 서버 설정 (PM2로 실행)
# ============================================
LLAMA_SERVER_PATH=C:\path\to\llama.cpp\build\bin\Release\llama-server.exe
LLAMA_SERVER_MODEL=C:\timblo\torch-test\models\Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf
LLAMA_SERVER_MMPROJ=C:\timblo\torch-test\models\mmproj-F32.gguf
LLAMA_SERVER_PORT=8080
LLAMA_SERVER_CTX_SIZE=15000  # 메모리 사용량 최적화 (기본값: 15000)
LLAMA_SERVER_THREADS=8
LLAMA_SERVER_GPU_LAYERS=99

# ============================================
# MinIO (S3) 설정
# ============================================
S3_ENDPOINT=http://localhost:9000
S3_REGION=us-east-1
S3_ACCESS_KEY=torchdev
S3_SECRET_KEY=torchdev-secret
S3_BUCKET=asr-media
S3_PREFIX=uploads
```

### 시나리오 2: llama.cpp 서버 사용 (텍스트 전용 모델)

텍스트 전용 Qwen 모델을 사용하는 경우 (mmproj 불필요):

```env
# ... (데이터베이스, Redis 등 동일) ...

LLM_PROVIDER=llamacpp_server
LLM_BASE_URL=http://localhost:8080
LLM_MODEL_NAME=Qwen2.5-32B-Instruct-Q4_K_M.gguf

LLM_CONTEXT_LENGTH=8192
LLM_TEMPERATURE=0.4
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1024

# llama.cpp 서버 설정
LLAMA_SERVER_PATH=C:\path\to\llama.cpp\build\bin\Release\llama-server.exe
LLAMA_SERVER_MODEL=C:\timblo\torch-test\models\Qwen2.5-32B-Instruct-Q4_K_M.gguf
# LLAMA_SERVER_MMPROJ는 설정하지 않음 (텍스트 전용 모델)
LLAMA_SERVER_PORT=8080
LLAMA_SERVER_CTX_SIZE=15000  # 메모리 사용량 최적화 (기본값: 15000)
LLAMA_SERVER_THREADS=8
LLAMA_SERVER_GPU_LAYERS=99
```

### 시나리오 3: LM Studio 사용

LM Studio 앱을 사용하는 경우:

```env
# ... (데이터베이스, Redis 등 동일) ...

LLM_PROVIDER=llamacpp_server
LLM_BASE_URL=http://localhost:1234
LLM_MODEL_NAME=gpt-oss-20b

# 공통 LLM 설정
LLM_CONTEXT_LENGTH=15016
LLM_TEMPERATURE=0.4
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1024

# LLAMA_SERVER_* 설정은 사용하지 않음 (LM Studio는 별도 앱으로 실행)
```

## 주요 설정 설명

### LLM Provider 선택

| Provider | 설명 | 사용 시기 |
|----------|------|-----------|
| `llamacpp_server` | llama.cpp 서버 사용 | Qwen3-VL 등 Vision 모델, PM2로 관리 (권장) |
| `lmstudio` | LM Studio 앱 사용 | LM Studio 앱으로 서버 실행 (deprecated) |

### LLM API 서버 설정

- **LLM_BASE_URL**: LLM API 서버 URL
  - llama.cpp 서버: `http://localhost:8080`
  - LM Studio: `http://localhost:1234`
  
- **LLM_MODEL_NAME**: 서버에서 로드한 모델 이름
  - 서버에서 실제로 로드한 모델 이름과 일치해야 함

### llama.cpp 서버 설정

- **LLAMA_SERVER_PATH**: llama-server.exe 경로 (필수)
  - worker-llm이 subprocess로 서버를 시작하려면 이 경로가 필요
  - 설정하지 않으면 worker-llm이 llama-server를 시작하지 않음

- **LLAMA_SERVER_MODEL**: 모델 파일 경로 (필수)
  - 절대 경로 권장

- **LLAMA_SERVER_MMPROJ**: mmproj 파일 경로 (Vision 모델만)
  - 텍스트 전용 모델 사용 시 설정하지 않음

- **LLAMA_SERVER_PORT**: 서버 포트 (기본값: 8080)
  - `LLM_BASE_URL`의 포트와 일치해야 함

## 최소 구성 예시

가장 간단한 구성:

```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/asr
REDIS_URL=redis://localhost:6379/0
TASK_QUEUE_TYPE=celery

LLM_PROVIDER=llamacpp_server
LLM_BASE_URL=http://localhost:8080
LLM_MODEL_NAME=Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf

LLAMA_SERVER_PATH=C:\path\to\llama.cpp\build\bin\Release\llama-server.exe
LLAMA_SERVER_MODEL=C:\timblo\torch-test\models\Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf
LLAMA_SERVER_MMPROJ=C:\timblo\torch-test\models\mmproj-F32.gguf
```

## 주의사항

1. **경로 설정**: Windows 경로는 백슬래시(`\`) 사용 또는 슬래시(`/`) 사용 가능
2. **포트 일치**: `LLAMA_SERVER_PORT`와 `LLM_BASE_URL`의 포트가 일치해야 함
3. **모델 이름**: `LLM_MODEL_NAME`은 서버에서 실제로 로드한 모델 이름과 일치해야 함
4. **mmproj 파일**: Vision 모델 사용 시에만 필요, 텍스트 전용 모델은 불필요

