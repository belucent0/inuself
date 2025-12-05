# llama.cpp 서버 설정 가이드

이 가이드는 llama.cpp 서버를 별도로 실행하여 LLM 요약 기능을 사용하는 방법을 설명합니다.

## 1. llama.cpp 서버 설치

### Windows에서 빌드

```bash
# Git Bash 또는 PowerShell에서
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
mkdir build
cd build

# CMake로 빌드 (Vulkan 지원)
cmake .. -DGGML_VULKAN=ON
cmake --build . --config Release
```

빌드된 실행 파일은 `build/bin/Release/` 디렉토리에 있습니다.

### 또는 미리 빌드된 바이너리 사용

[llama.cpp Releases](https://github.com/ggerganov/llama.cpp/releases)에서 Windows 바이너리를 다운로드할 수 있습니다.

## 2. Qwen3-VL 모델 준비

Qwen3-VL은 Vision-Language 모델이므로 **mmproj 파일**이 필요합니다.

### 모델 및 mmproj 파일 다운로드

```bash
# Hugging Face CLI 사용
huggingface-cli download unsloth/Qwen3-VL-30B-A3B-Instruct-GGUF \
  --local-dir models/qwen3-vl-30b-a3b-instruct-gguf \
  --include "*Q4_K_M.gguf" "mmproj-F16.gguf"
```

또는 수동으로 다운로드:
- 모델 파일: `Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf`
- mmproj 파일: `mmproj-F16.gguf`

## 3. llama.cpp 서버 실행

### 텍스트 전용 모델 (권장)

텍스트 요약에는 Vision 기능이 필요 없으므로, 텍스트 전용 Qwen 모델 사용을 권장합니다:

```bash
# Qwen2.5-32B-Instruct 예시
./llama-server \
  --model models/Qwen2.5-32B-Instruct-Q4_K_M.gguf \
  --n-gpu-layers 99 \
  --host 0.0.0.0 \
  --port 8080 \
  --ctx-size 8192 \
  --threads 8
```

### Qwen3-VL 모델 (Vision 기능 필요 시)

```bash
./llama-server \
  --model models/Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf \
  --mmproj models/mmproj-F32.gguf \
  --n-gpu-layers 99 \
  --host 0.0.0.0 \
  --port 8080 \
  --ctx-size 8192 \
  --threads 8 \
  --jinja
```

**참고**: `mmproj-F32.gguf`는 F32 정밀도 파일입니다. F16 버전(`mmproj-F16.gguf`)도 사용 가능하며 더 작은 용량입니다.

**주의**: Qwen3-VL은 텍스트 요약에는 과도한 기능입니다. 텍스트 전용 모델을 사용하는 것이 더 효율적입니다.

## 4. 환경 변수 설정

프로젝트 루트의 `.env` 파일에 추가:

### worker-llm이 subprocess로 llama.cpp 서버 실행 시 (권장)

```env
# llama.cpp 서버 실행 파일 경로 (필수)
LLAMA_SERVER_PATH=C:\path\to\llama.cpp\build\bin\Release\llama-server.exe

# Qwen3-VL 모델 경로
LLAMA_SERVER_MODEL=C:\timblo\torch-test\models\Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf
LLAMA_SERVER_MMPROJ=C:\timblo\torch-test\models\mmproj-F32.gguf

# 서버 설정 (선택사항, 기본값 사용 가능)
LLAMA_SERVER_PORT=8080
LLAMA_SERVER_CTX_SIZE=15000  # 메모리 사용량 최적화
LLAMA_SERVER_THREADS=8
LLAMA_SERVER_GPU_LAYERS=99

# Celery 워커가 llama.cpp 서버를 사용하도록 설정
LLM_PROVIDER=llamacpp_server
LLM_BASE_URL=http://localhost:8080
LLM_MODEL_NAME=Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf

# 공통 LLM 설정
LLM_CONTEXT_LENGTH=15000  # 메모리 사용량 최적화
LLM_TEMPERATURE=0.4
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1024
```

### 텍스트 전용 모델 사용 시

```env
# llama.cpp 서버 실행 파일 경로
LLAMA_SERVER_PATH=C:\path\to\llama.cpp\build\bin\Release\llama-server.exe

# 텍스트 전용 모델 경로 (mmproj 불필요)
LLAMA_SERVER_MODEL=C:\timblo\torch-test\models\Qwen2.5-32B-Instruct-Q4_K_M.gguf

# Celery 워커 설정
LLM_PROVIDER=llamacpp_server
LLM_BASE_URL=http://localhost:8080
LLM_MODEL_NAME=Qwen2.5-32B-Instruct-Q4_K_M.gguf

# 공통 LLM 설정
LLM_CONTEXT_LENGTH=15000  # 메모리 사용량 최적화
LLM_TEMPERATURE=0.4
LLM_TOP_P=0.9
LLM_MAX_TOKENS=1024
```

**참고**: 
- `LLAMA_SERVER_PATH`가 설정되지 않으면 worker-llm이 llama-server를 시작하지 않습니다
- 텍스트 전용 모델 사용 시 `LLAMA_SERVER_MMPROJ`는 설정하지 않아도 됩니다
- worker-llm이 요청을 받을 때마다 llama-server를 subprocess로 시작하고 종료합니다

## 5. 서버 시작 스크립트 (Windows)

### 텍스트 전용 모델용 스크립트

`start_llamacpp_server.bat` 파일 생성:

```batch
@echo off
cd C:\path\to\llama.cpp\build\bin\Release
.\llama-server.exe ^
  --model C:\timblo\torch-test\models\Qwen2.5-32B-Instruct-Q4_K_M.gguf ^
  --n-gpu-layers 99 ^
  --host 0.0.0.0 ^
  --port 8080 ^
  --ctx-size 8192 ^
  --threads 8
pause
```

**참고**: 현재 시스템은 `worker-llm`이 요청을 받을 때마다 llama-server를 subprocess로 자동 시작/종료하므로, 수동으로 서버를 실행할 필요가 없습니다. 위 스크립트는 테스트 목적으로만 사용할 수 있습니다.

## 6. 장단점 비교

### llama.cpp 서버 사용
**장점:**
- 최신 llama.cpp 기능 지원 (Qwen3-VL 등)
- 별도 프로세스로 실행되어 메모리 격리
- 여러 워커에서 동일한 서버 공유 가능

**단점:**
- 별도 프로세스 관리 필요
- 네트워크 오버헤드 (로컬이면 미미함)
- 서버 시작/중지 관리 필요

### llama-cpp-python 직접 사용
**장점:**
- Python 프로세스 내에서 직접 실행
- 네트워크 오버헤드 없음
- 간단한 설정

**단점:**
- 최신 모델 지원이 늦을 수 있음
- Qwen3-VL 같은 Vision 모델 지원 제한적

## 7. 추천 설정

**텍스트 요약 용도라면:**
- 텍스트 전용 Qwen 모델 사용 (Qwen2.5-32B-Instruct 등)
- llama.cpp 서버 또는 llama-cpp-python 모두 사용 가능

**Vision 기능이 필요하다면:**
- llama.cpp 서버 사용 (mmproj 파일 필요)
- 또는 다른 Vision 모델 전용 라이브러리 고려

