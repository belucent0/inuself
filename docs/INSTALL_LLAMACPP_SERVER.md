# llama.cpp 서버 설치 가이드 (Windows)

llama.cpp 서버를 Windows에 설치하는 방법을 설명합니다.

## 방법 1: 미리 빌드된 바이너리 사용 (권장)

가장 간단한 방법입니다.

### 1. 다운로드

1. [llama.cpp Releases](https://github.com/ggerganov/llama.cpp/releases) 페이지 방문
2. 최신 릴리즈에서 Windows 바이너리 다운로드
   - 예: `llama-bXXXX-windows-amd64.zip` 또는 `llama-bXXXX-windows-x64.zip`
3. 압축 해제

### 2. 설치 위치

다운로드한 파일을 적절한 위치에 압축 해제:

```
C:\llama.cpp\
  ├── llama-server.exe
  ├── llama-cli.exe
  └── ...
```

또는 프로젝트 내부에 설치:

```
C:\timblo\torch-test\
  ├── llama.cpp\
  │   ├── llama-server.exe
  │   └── ...
  └── ...
```

### 3. .env 파일 설정

```env
LLAMA_SERVER_PATH=C:\llama.cpp\llama-server.exe
# 또는 프로젝트 내부에 설치한 경우
# LLAMA_SERVER_PATH=C:\timblo\torch-test\llama.cpp\llama-server.exe
```

## 방법 2: 소스에서 빌드

### 전제 조건

- Git
- CMake (3.15 이상)
- Visual Studio 2019 이상 또는 MinGW-w64
- Vulkan SDK (GPU 가속 사용 시)

### 빌드 단계

```bash
# 1. 소스 코드 클론
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp

# 2. 빌드 디렉토리 생성
mkdir build
cd build

# 3. CMake 설정 (Vulkan 지원)
cmake .. -DGGML_VULKAN=ON

# 4. 빌드
cmake --build . --config Release
```

빌드된 실행 파일 위치:
```
llama.cpp\build\bin\Release\llama-server.exe
```

### .env 파일 설정

```env
LLAMA_SERVER_PATH=C:\path\to\llama.cpp\build\bin\Release\llama-server.exe
```

## 방법 3: winget 사용 (Windows 11/10)

```bash
winget install llama.cpp
```

설치 경로 확인:
```bash
where llama-server.exe
```

## 설치 확인

설치가 완료되면 다음 명령어로 확인:

```bash
# 경로 확인
where llama-server.exe

# 또는 직접 실행
C:\path\to\llama-server.exe --help
```

## .env 파일 예시

설치 완료 후 `.env` 파일에 추가:

```env
# llama.cpp 서버 경로 (실제 설치 경로로 변경)
LLAMA_SERVER_PATH=C:\llama.cpp\llama-server.exe

# 모델 경로
LLAMA_SERVER_MODEL=C:\timblo\torch-test\models\Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf
LLAMA_SERVER_MMPROJ=C:\timblo\torch-test\models\mmproj-F32.gguf

# 서버 설정
LLAMA_SERVER_PORT=8080
LLAMA_SERVER_CTX_SIZE=8192
LLAMA_SERVER_THREADS=8
LLAMA_SERVER_GPU_LAYERS=99

# Celery 워커 설정
LLM_PROVIDER=llamacpp_server
LLM_BASE_URL=http://localhost:8080
LLM_MODEL_NAME=Qwen3-VL-30B-A3B-Instruct-Q4_K_M.gguf
```

## 문제 해결

### "Script not found" 에러

worker-llm이 `llama-server.exe`를 찾을 수 없는 경우:

1. `.env` 파일에 `LLAMA_SERVER_PATH`가 올바르게 설정되었는지 확인
2. 경로에 공백이나 특수문자가 있으면 따옴표로 감싸기
3. 절대 경로 사용 권장

### Vulkan 지원 확인

```bash
llama-server.exe --help
```

출력에 `--n-gpu-layers` 옵션이 있으면 Vulkan 지원이 활성화된 것입니다.





