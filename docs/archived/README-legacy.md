# ASR/LLM/OCR Processing Platform with AMD GPU/NPU Acceleration

음성 인식(ASR), LLM 요약, OCR 처리를 지원하는 통합 플랫폼입니다. AMD GPU (ROCm) 및 NPU (Ryzen AI) 가속을 지원합니다.

**주요 기능:**
- 실시간 ASR 스트리밍 (WebSocket, FLM/Whisper NPU 가속)
- 화자 분리 (PyAnnote, ROCm GPU 가속)
- LLM 요약 및 교정 (FLM/llama.cpp)
- PDF/이미지 OCR (LLM Vision)

## 📋 Table of Contents

- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Usage](#usage)
- [LLM 요약 파이프라인](#-llm-요약-파이프라인)
- [Features](#features)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)

## 🖥️ System Requirements

- **OS**: Windows 10/11 (64-bit)
- **GPU**: AMD Radeon GPU (ROCm supported, tested on gfx1150)
- **Python**: 3.12
- **Driver**: AMD Radeon driver 25.20.01.14 or higher
- **PyTorch Version**: 2.9.0+rocm7.10.0a20251105 (recommended) or 2.8.0+rocm6.4.4 (legacy)

## 📦 Installation

### 1. Create Python Virtual Environment

```bash
python -m venv rocm_env
rocm_env\Scripts\activate
```

### 2. Install PyTorch and Dependencies

#### Option 1: Install PyTorch 2.9.0 (ROCm 7.10.0a20251105) - Recommended ⭐

This version has been tested and verified to work correctly with GPU acceleration:

```bash
python -m pip install --no-cache-dir --no-deps --target "C:/timblo/torch-test/rocm_env/Lib/site-packages" "https://rocm.nightlies.amd.com/v2-staging/gfx1150/torch-2.9.0%2Brocm7.10.0a20251105-cp312-cp312-win_amd64.whl"
python -m pip install --no-cache-dir --no-deps --target "C:/timblo/torch-test/rocm_env/Lib/site-packages" "https://rocm.nightlies.amd.com/v2-staging/gfx1150/torchaudio-2.9.0%2Brocm7.10.0a20251105-cp312-cp312-win_amd64.whl"
python -m pip install --no-cache-dir --no-deps --target "C:/timblo/torch-test/rocm_env/Lib/site-packages" "https://rocm.nightlies.amd.com/v2-staging/gfx1150/torchvision-0.24.0%2Brocm7.10.0a20251105-cp312-cp312-win_amd64.whl"
```

**Note**: Replace `C:/timblo/torch-test/rocm_env` with your actual virtual environment path.

**Alternative (if using standard pip install)**:
```bash
pip install --no-cache-dir --no-deps "https://rocm.nightlies.amd.com/v2-staging/gfx1150/torch-2.9.0%2Brocm7.10.0a20251105-cp312-cp312-win_amd64.whl"
pip install --no-cache-dir --no-deps "https://rocm.nightlies.amd.com/v2-staging/gfx1150/torchaudio-2.9.0%2Brocm7.10.0a20251105-cp312-cp312-win_amd64.whl"
pip install --no-cache-dir --no-deps "https://rocm.nightlies.amd.com/v2-staging/gfx1150/torchvision-0.24.0%2Brocm7.10.0a20251105-cp312-cp312-win_amd64.whl"
```

**Installed Versions:**
- torch: `2.9.0+rocm7.10.0a20251105`
- torchaudio: `2.9.0+rocm7.10.0a20251105`
- torchvision: `0.24.0+rocm7.10.0a20251105`

### Backend 워커와 ROCm 연동

FastAPI에서 실행되는 백엔드 워커는 아래 순서로 ROCm 전용 파이썬 패키지를 자동으로 탐색해 GPU 가속을 사용합니다.

1. `ROCM_SITE_PACKAGES` 환경변수에 명시된 경로
2. `ROCM_ENV_PATH` 환경변수(루트) 아래의 `Lib/site-packages` (Windows) 또는 `lib/python3.x/site-packages`
3. 프로젝트 루트의 `rocm_env/Lib/site-packages`

따라서 위의 `rocm_env` 가상환경에 ROCm 빌드의 `torch`, `torchaudio`, `torchvision`을 설치해 두면 워커가 자동으로 GPU를 사용합니다. 다른 위치를 사용한다면 환경변수를 설정한 뒤 FastAPI 서버를 재시작하면 됩니다.

#### Option 2: Install PyTorch 2.8.0 (ROCm 6.4.4) - Legacy

Install PyTorch for ROCm 6.4.4 according to AMD's official documentation:

```bash
pip install --no-cache-dir https://repo.radeon.com/rocm/windows/rocm-rel-6.4.4/torch-2.8.0a0%2Bgitfc14c65-cp312-cp312-win_amd64.whl
pip install --no-cache-dir https://repo.radeon.com/rocm/windows/rocm-rel-6.4.4/torchaudio-2.6.0a0%2B1a8f621-cp312-cp312-win_amd64.whl
pip install --no-cache-dir https://repo.radeon.com/rocm/windows/rocm-rel-6.4.4/torchvision-0.24.0a0%2Bc85f008-cp312-cp312-win_amd64.whl
```

### 3. Install Other Required Packages

```bash
pip install pyannote.audio librosa
```

### 4. Install whisper.cpp and Download Models (Required for ASR)

**⚠️ Required for ASR functionality**: This project uses `whisper.cpp` with Vulkan acceleration for fast ASR processing.

#### 4.1. Build whisper.cpp

1. **Clone whisper.cpp repository**:
   ```bash
   git clone https://github.com/ggerganov/whisper.cpp.git
   cd whisper.cpp
   ```

2. **Build with Vulkan support** (Windows):
   - Install CMake and Visual Studio (or Build Tools)
   - Configure with Vulkan:
     ```bash
     mkdir build
     cd build
     cmake .. -DWHISPER_VULKAN=ON
     cmake --build . --config Release
     ```
   - The executable will be at: `build/bin/Release/whisper-cli.exe`

3. **Set the path** (or place `whisper-cli.exe` at `C:/whisper-cpp/build/bin/Release/whisper-cli.exe`)

#### 4.2. Download GGML Model Files

Download the GGML model files (`.bin` format) for whisper.cpp:

**Option 1: Download to project folder** (Recommended):
- Place model files in `src/asr/models/` folder
- Supported models: `ggml-base.bin`, `ggml-large-v2.bin`, `ggml-large-v3.bin`, `ggml-large-v3-turbo.bin`, etc.

**Option 2: Download to external folder**:
- Place model files in `C:/whisper-cpp/models/` folder
- The script will automatically fall back to this location if models are not found in the project folder

**Model download links**:
- [Hugging Face - whisper.cpp models](https://huggingface.co/ggerganov/whisper.cpp/tree/main)
- Or use the conversion script in whisper.cpp repository to convert PyTorch models to GGML format

**Note**: The project uses **Vulkan-based GPU acceleration** for whisper.cpp, which provides significant speed improvements (e.g., 14 minutes of audio processed in ~5 minutes).

### 5. Hugging Face Model Access Setup (Required)

This project uses Hugging Face's `pyannote/speaker-diarization-3.1` model. The following steps are **required** to use the model:

1. **Create Hugging Face Account**
   - Create an account at [Hugging Face](https://huggingface.co/) (free)

2. **Accept Model Terms**
   - Visit [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) model page
   - Click "Agree and access repository" to accept the terms
   - Also accept terms for related models:
     - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
     - [pyannote/embedding](https://huggingface.co/pyannote/embedding)

3. **Create Hugging Face Token and Login**
   - Create a token at [Hugging Face Settings > Tokens](https://huggingface.co/settings/tokens) (Read permission)
   - Login in the virtual environment:

```bash
huggingface-cli login
```

The token will be automatically saved and used for subsequent runs.

### 6. pyannote.audio Compatibility Patch

**⚠️ Required**: The PyTorch version string (`2.8.0a0+gitfc14c65`) provided by ROCm 6.4.4 is not in SemVer format, causing errors in `pyannote.audio`'s version check.

**Problem**: The original code performs `".".join(mine.split(".")[:3])`, but when splitting `2.8.0a0+gitfc14c65` by `.`, it becomes `['2', '8', '0a0+gitfc14c65']`, resulting in `2.8.0a0+gitfc14c65` remaining unchanged. The `semver` library cannot parse `a0` (alpha version) and `+gitfc14c65` (build metadata), causing `ValueError: 2.8.0a0+gitfc14c65 is not valid SemVer string` error.

The following patch **must** be applied:

#### 6.1. Modify `pyannote/audio/utils/version.py`

File location: `rocm_env/Lib/site-packages/pyannote/audio/utils/version.py`

Add version string normalization logic inside the `check_version` function (around lines 28-60):

```python
def check_version(library: Text, theirs: Text, mine: Text, what: Text = "Pipeline"):
    theirs = ".".join(theirs.split(".")[:3])
    mine = ".".join(mine.split(".")[:3])
    
    # Normalize non-SemVer version strings (e.g., 2.8.0a0+gitfc14c65 -> 2.8.0)
    def normalize_version(version_str):
        # Remove part after '+' (e.g., +gitfc14c65)
        if '+' in version_str:
            version_str = version_str.split('+')[0]
        # Split by dots
        parts = version_str.split('.')
        # Extract only digits from each part
        cleaned_parts = []
        for part in parts[:3]:  # Maximum 3 parts
            if part.isdigit():
                cleaned_parts.append(part)
            elif part and part[0].isdigit():
                # Extract only digits if part starts with digit (e.g., 0a0 -> 0)
                import re
                digits = re.match(r'^(\d+)', part)
                if digits:
                    cleaned_parts.append(digits.group(1))
        # Ensure minimum 3-part format (pad with '0' if needed)
        while len(cleaned_parts) < 3:
            cleaned_parts.append('0')
        return '.'.join(cleaned_parts[:3])

    theirs = normalize_version(theirs)
    mine = normalize_version(mine)
    # ... rest of the code
```

**Important**: This patch is **required**. Without it, you will get `ValueError: 2.8.0a0+gitfc14c65 is not valid SemVer string` error when loading the model.

## 🧠 LLM 요약 파이프라인

ASR/화자분리 결과는 이제 LLM으로 2차 요약됩니다. 전체 파이프라인은 `QUEUED → PROCESSING → SUMMARIZING → COMPLETED`(또는 `FAILED`/`SUMMARY_FAILED`)로 진행되며, 결과 Markdown은 `summary_md` 컬럼과 `/contents/{id}` API에서 확인할 수 있습니다.

### 1. ASR/LLM Provider 선택

용도에 따라 두 가지 ASR 모드를 지원합니다:

| 모드 | Provider | 가속 | 용도 |
|------|----------|------|------|
| **실시간 스트리밍** | FLM (Whisper) | NPU | WebSocket 실시간 전사 |
| **정확도 모드** | whisper.cpp | Vulkan GPU | 파일 업로드 배치 처리 |

#### FLM (FastFlowLM) - NPU 가속 ⭐

FLM은 AMD Ryzen AI NPU를 활용하는 고성능 추론 서버입니다. **실시간 ASR 스트리밍**과 **LLM 처리**에 사용됩니다.

**설치 및 실행:**
1. [FastFlowLM 릴리즈](https://github.com/FastFlowLM/FastFlowLM/releases)에서 `flm-setup.exe` 다운로드
2. 설치 후 모델 다운로드 (Whisper-V3-Turbo, Qwen3 등)
3. PM2로 서버 관리:
   ```bash
   pm2 start ecosystem.config.js --only flm-server
   pm2 logs flm-server
   ```

**`.env` 설정:**
```env
FLM_BASE_URL=http://127.0.0.1:11434
FLM_LLM_MODEL=qwen3-it:4b
```

**지원 엔드포인트:**
- `/v1/audio/transcriptions` - Whisper ASR (실시간 스트리밍용)
- `/v1/chat/completions` - LLM Chat (요약, 교정용)

#### whisper.cpp - Vulkan GPU 가속

whisper.cpp는 Vulkan GPU 가속을 사용하여 **높은 정확도의 배치 ASR 처리**에 사용됩니다.

**설치:** Installation 섹션 4 참조 (whisper.cpp 빌드 및 모델 다운로드)

**사용 시나리오:**
- 파일 업로드 후 배치 처리 (정확도 우선)
- 긴 오디오 파일의 고품질 전사

#### llama.cpp 서버 - GPU/CPU 가속 (대안)

llama.cpp 서버는 Vulkan GPU 또는 CPU로 LLM을 실행합니다. FLM 대안으로 사용할 수 있습니다.

**설치 및 실행:**
```bash
# llama.cpp 빌드 (Vulkan 지원)
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp && mkdir build && cd build
cmake .. -DLLAMA_VULKAN=ON
cmake --build . --config Release

# 서버 실행
./bin/llama-server -m /path/to/model.gguf --port 8080
```

**`.env` 설정:**
```env
LLM_BASE_URL=http://localhost:8080
LLM_MODEL_NAME=Qwen3-4B-Instruct-Q4_K_M.gguf
```

### 2. 환경 변수 / 설정

`backend/app/core/config.py`에 다음 설정이 추가되었습니다. 필요 시 `.env`에서 오버라이드하면 됩니다.

#### FLM 서버 설정 (권장)

| 설정 | 기본값 | 설명 |
| --- | --- | --- |
| `FLM_BASE_URL` | `http://127.0.0.1:11434` | FLM 서버 엔드포인트 |
| `FLM_LLM_MODEL` | `qwen3-it:4b` | FLM LLM 모델명 |

#### llama.cpp 서버 설정

| 설정 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://localhost:8080` | llama.cpp 서버 엔드포인트 |
| `LLM_MODEL_NAME` | `Qwen3-4B-Instruct-Q4_K_M.gguf` | 모델 파일명 |

#### 공통 LLM 설정

| 설정 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_TEMPERATURE` | `0.4` | 생성 온도 (0.0 ~ 1.0) |
| `LLM_MAX_TOKENS` | `1024` | 최대 토큰 수 |

### 3. 워커 실행

LLM/ASR/OCR 처리는 Celery 큐를 통해 비동기 실행됩니다.

```bash
# PM2로 모든 워커 시작 (권장)
pm2 start ecosystem.config.js

# 또는 개별 Celery 워커 실행
celery -A worker.celery_app worker --pool=solo --queues=asr --hostname=worker-asr@%h
celery -A worker.celery_app worker --pool=solo --queues=llm --hostname=worker-llm@%h
celery -A worker.celery_app worker --pool=solo --queues=ocr --hostname=worker-ocr@%h
```

실패한 작업은 `FAILED` 또는 `SUMMARY_FAILED` 상태로 표시되며, 콘텐츠를 다시 큐에 넣으면 재시도할 수 있습니다.

## 🔌 FastAPI API & Next.js 콘솔

### 인프라 (docker-compose)

```
docker compose up -d redis redis-insight minio
docker compose up minio-bootstrap
```

- `redis`: 큐 및 워커용 (포트 6379). `.env`의 `REDIS_URL=redis://127.0.0.1:6379/0`.
- `minio`: S3 호환 객체 스토리지. 기본 엔드포인트 `http://127.0.0.1:9000`, 콘솔 `http://127.0.0.1:9001`.
- `minio-bootstrap`: `minio/mc`를 사용해 `asr-media` 버킷을 자동으로 생성/검증하는 일회성 서비스입니다. 이미 존재하면 그대로 종료됩니다.

**설정:**
- `.env`의 `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_ENDPOINT`를 MinIO 설정(기본값 torchdev/torchdev-secret, `http://127.0.0.1:9000`)과 일치시켜 주세요.

### 백엔드 (FastAPI)

```
cd backend
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
```

- `.env`에 `POSTGRES_DSN`, `REDIS_URL`, `UPLOAD_DIR`, `WHISPER_MODEL_DEFAULT`,
  `S3_ENDPOINT`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_PREFIX` 등을 설정하세요.
- 업로드 API: `POST /api/contents/upload`
- 목록/상세 API: `GET /api/contents`, `GET /api/contents/{id}`

### 워커 & Redis 큐

#### 아키텍처 개요 (Backend-Worker 완전 분리)

이 프로젝트는 **Backend API 서버**와 **GPU Worker**가 완전히 분리된 아키텍처를 사용합니다:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  FastAPI API    │────▶│  Redis/Celery   │────▶│  GPU Workers    │
│  (backend/)     │     │  (Task Queue)   │     │  (worker/)      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                       │
        │                       ▼                       │
        │              ┌─────────────────┐              │
        │              │  Redis Stream   │◀─────────────┘
        │              │  (Result Queue) │   결과 발행
        │              └─────────────────┘
        │                       │
        ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│   PostgreSQL    │◀────│ StreamConsumer  │
│   (Results DB)  │     │  (Backend)      │
└─────────────────┘     └─────────────────┘
```

**핵심 원칙: 워커는 GPU 작업만 수행, DB 접근 없음**

| 컴포넌트 | 역할 | DB 접근 |
|----------|------|---------|
| **Backend API** | 비즈니스 로직, 전처리, DB 저장 | ✅ |
| **GPU Worker** | ASR/LLM/OCR GPU 연산만 | ❌ |
| **StreamConsumer** | Redis Stream 구독 → DB 저장 | ✅ |

- **완전한 분리**: 워커는 DB 의존성 없이 순수 GPU 작업만 수행
- **Redis Stream**: 워커 결과를 백엔드로 전달하는 경량 메시지 큐 (Kafka 대안)
- **전처리 분리**: PDF→이미지 변환 등 CPU 작업은 백엔드에서 수행
- **확장성**: 워커를 여러 대 실행하여 수평 확장 가능

#### 워커 실행 방법

**방법 1: PM2 사용 (권장)**

```bash
# PM2로 모든 워커 시작
pm2 start ecosystem.config.js

# 개별 워커 시작
pm2 start ecosystem.config.js --only worker-asr
pm2 start ecosystem.config.js --only worker-llm
pm2 start ecosystem.config.js --only worker-ocr

# 상태 확인
pm2 status
pm2 logs worker-asr
```

**방법 2: 직접 Celery 명령 실행**

```bash
# 프로젝트 루트에서 실행 (worker 패키지 접근)
cd C:\timblo\torch-test

# ASR 워커
celery -A worker.celery_app worker --pool=solo --queues=asr --hostname=worker-asr@%h

# LLM 워커
celery -A worker.celery_app worker --pool=solo --queues=llm --hostname=worker-llm@%h

# OCR 워커
celery -A worker.celery_app worker --pool=solo --queues=ocr --hostname=worker-ocr@%h
```

#### 워커 큐 구성

| 워커 | 큐 | 입력 | 출력 | 설명 |
|------|-----|------|------|------|
| `worker-asr` | `asr` | `file_id`, `s3_key` | S3 결과 + Redis Stream | ASR + 화자분리 (GPU) |
| `worker-llm` | `llm` | `file_id`, `text_to_summarize` | Redis Stream | LLM 요약 (GPU/NPU) |
| `worker-ocr` | `ocr` | `file_id`, `image_s3_keys` | Redis Stream | OCR Vision (GPU/NPU) |

**워커 처리 흐름:**

```
1. 백엔드: 작업 큐잉 (Celery task)
   - ASR: enqueue_asr_job(file_id, s3_key)
   - LLM: enqueue_llm_job(file_id, text_to_summarize)  ← 텍스트 직접 전달
   - OCR: enqueue_ocr_job(file_id, image_s3_keys)      ← 이미지 S3 키 전달

2. 워커: GPU 작업만 수행 (DB 접근 없음)
   - S3에서 파일 다운로드 → GPU 처리 → 결과 S3 저장
   - Redis Stream으로 결과 발행

3. 백엔드 StreamConsumer: Redis Stream 구독
   - 결과 수신 → PostgreSQL 저장
   - OCR: 임시 이미지 S3 삭제
```

**OCR 전처리 분리:**
- 이전: 워커가 PDF → 이미지 변환 + OCR 전체 처리
- 현재: 백엔드(CPU)에서 PDF → 이미지 변환 → S3 임시 저장 → 워커는 이미지 OCR만

### Next.js 클라이언트

```
cd client
npm install
npm run dev
```

- 기본 API 엔드포인트는 `NEXT_PUBLIC_API_BASE_URL` (기본값 `http://localhost:8000/api`)
- `/contents` 페이지에서 목록과 업로드, `/contents/[id]`에서 상세/로그를 확인할 수 있습니다.

### 테스트

```
cd backend
poetry run pytest
```

`backend/tests/test_health.py` 에 FastAPI 헬스 체크 테스트가 포함되어 있습니다.

### 7. PyTorch Compatibility Fixes (Required for PyTorch 2.9.0)

**⚠️ Required for PyTorch 2.9.0**: The following fixes must be applied after installing PyTorch 2.9.0 to ensure proper GPU initialization and compatibility.

#### 7.1. Remove `hipsparselt` from ROCm initialization

File location: `rocm_env/Lib/site-packages/torch/_rocm_init.py`

The `hipsparselt` library is not available in the ROCm SDK, causing initialization errors. Remove it from the preload list:

```python
# Before:
preload_shortnames=['amd_comgr', 'amdhip64', 'hiprtc', 'hipblas', 'hipfft', 'hiprand', 'hipsparse', 'hipsparselt', 'hipsolver', 'hipblaslt', 'miopen', 'rocm-openblas'],

# After:
preload_shortnames=['amd_comgr', 'amdhip64', 'hiprtc', 'hipblas', 'hipfft', 'hiprand', 'hipsparse', 'hipsolver', 'hipblaslt', 'miopen', 'rocm-openblas'],
```

Also update the version check to match your installed version:
```python
check_version='7.10.0a20251105'  # Match your PyTorch ROCm version
```

#### 7.2. Exclude `caffe2_nvrtc.dll` from loading

File location: `rocm_env/Lib/site-packages/torch/__init__.py`

The `caffe2_nvrtc.dll` may have dependency issues on ROCm. Exclude it from DLL loading:

Find the `_load_dll_libraries` function (around line 252) and modify:

```python
dlls = glob.glob(os.path.join(th_dll_path, "*.dll"))
# Exclude caffe2_nvrtc.dll as it may have dependency issues on ROCm
dlls = [dll for dll in dlls if os.path.basename(dll) != "caffe2_nvrtc.dll"]
path_patched = False
```

#### 7.3. Handle torchvision meta registration errors

File location: `rocm_env/Lib/site-packages/torchvision/__init__.py`

Modify the import statement (around line 10):

```python
# Before:
from torchvision import _meta_registrations, datasets, io, models, ops, transforms, utils  # usort:skip

# After:
try:
    from torchvision import _meta_registrations  # usort:skip
except RuntimeError:
    # Ignore meta registration errors for compatibility
    pass
from torchvision import datasets, io, models, ops, transforms, utils  # usort:skip
```

#### 7.4. Update ROCm SDK version

File location: `rocm_env/Lib/site-packages/rocm_sdk/_dist_info.py`

Update the version to match your PyTorch installation (around line 257):

```python
__version__ = '7.10.0a20251105'  # Match your PyTorch ROCm version
```

**Note**: These fixes are required for PyTorch 2.9.0+rocm7.10.0a20251105. Without them, you may encounter:
- `ModuleNotFoundError: Unknown rocm library 'hipsparselt'`
- `OSError: Error loading "caffe2_nvrtc.dll"`
- `RuntimeError: operator torchvision::nms does not exist`

## 🚀 Usage

### Media File Conversion

Before running speaker diarization, you may need to convert media files to WAV format:

1. Place your media files (`.mp3`, `.mp4`, `.m4a`, `.flac`, etc.) in the `media/upload/` folder
2. Run the media converter:

```bash
python src/utils/media_converter.py
```

The script will:
- Automatically detect all media files in `media/upload/` folder
- Convert them to WAV format (16kHz, mono)
- Save converted files to `media/wav/` folder
- Display conversion progress and statistics

**Supported formats**: `.mp3`, `.mp4`, `.m4a`, `.flac`, `.wav`, `.ogg`, `.wma`, `.aac`, `.mkv`, `.avi`, `.mov`, `.webm`

**Requirements**: ffmpeg must be installed and available in PATH. Install from [ffmpeg.org](https://ffmpeg.org/download.html) or use:
- Windows: `winget install ffmpeg` or `choco install ffmpeg`

### Speaker Diarization

1. Place your audio file in the `media/wav/` folder (or use converted files from `media/upload/`)
2. Run the script:

```bash
source rocm_env/Scripts/activate
python src/diarization/test_pyannote.py
```

Or use the test script:

```bash
bash run_test.sh
```

### ASR (Automatic Speech Recognition)

**Note**: ASR uses `whisper.cpp` with **Vulkan GPU acceleration** for fast processing. Make sure you have:
- `whisper-cli.exe` built with Vulkan support (see Installation section 4.1)
- GGML model files (`.bin`) downloaded (see Installation section 4.2)

Run ASR with Whisper:

```bash
python src/asr/test_asr.py base media/wav/sample.wav
```

For higher accuracy:

```bash
python src/asr/test_asr.py large-v3 media/wav/sample.wav
```

### ASR + Speaker Diarization (Integrated)

**Note**: This uses `whisper.cpp` with **Vulkan GPU acceleration** for ASR. Make sure whisper.cpp is installed (see Installation section 4).

Run both ASR and speaker diarization together:

```bash
python src/diarization/test_asr_with_diarization.py base media/wav/sample.wav
```

The script will:
- Run ASR using `whisper-cli.exe` with Vulkan acceleration
- Run speaker diarization using pyannote.audio
- Process both tasks in parallel for optimal performance

### Results

- **Console Output**: Real-time progress and results
- **Log Files**: Timestamped log files saved in respective `logs/` folders
  - ASR logs: `src/asr/logs/`
  - Diarization logs: `src/diarization/logs/`
  - `.log`: Text format log
  - `.json`: Structured JSON format log

## ✨ Features

### 핵심 기능
- ✅ **실시간 ASR 스트리밍** - WebSocket 기반, 5초 단위 실시간 전사 (FLM/NPU)
- ✅ **정확도 모드 ASR** - whisper.cpp Vulkan GPU 가속, 배치 처리
- ✅ **NPU 가속 LLM** (FLM/Qwen3) - 요약, 문법 교정, 언어 필터링
- ✅ **GPU 가속 화자분리** (PyAnnote/ROCm) - AMD GPU 활용
- ✅ **PDF/이미지 OCR** (LLM Vision) - 문서 텍스트 추출

### 아키텍처
- ✅ **Backend-Worker 완전 분리** - 워커는 GPU 작업만, DB 접근 없음
- ✅ **Redis Stream 기반 결과 전달** - 경량 메시지 큐
- ✅ **Celery 분산 처리** - ASR/LLM/OCR 워커 분리

### 기타
- ✅ AMD GPU (ROCm) acceleration for speaker diarization
- ✅ Automatic logging (text + JSON format)
- ✅ MIOpen error handling and automatic workarounds
- ✅ Media file conversion tool (MP3, MP4, etc. → WAV)
- ✅ Parallel processing (ASR and diarization run simultaneously)

## 🗺️ 로드맵

### ✅ 완료된 항목

| 항목 | 설명 | 완료일 |
|------|------|--------|
| ASR 실시간 스트리밍 | WebSocket 기반 5초 단위 실시간 전사 | 2026-01 |
| ASR NPU 가속 | FLM/Whisper를 통한 NPU 가속 지원 | 2026-01 |
| LLM NPU 가속 | FLM/Qwen3를 통한 NPU 가속 지원 | 2026-01 |
| ASR 전사 정확도 LLM 후처리 | 실시간 스트리밍에서 문법 교정, 언어 필터링 | 2026-01 |
| Backend-Worker 완전 분리 | 워커 DB 의존성 제거, Redis Stream 결과 전달 | 2026-01 |
| PDF OCR 전처리 분리 | 백엔드에서 PDF→이미지 변환, 워커는 OCR만 | 2026-01 |

### 현재 개발 중인 항목

| 항목 | 설명 |
|------|------|
| ASR 청킹 오버랩 후처리 | ASR 처리 시 청킹된 결과의 오버랩 부분을 후처리하여 정확도 향상 |
| .pdf 및 .docx 문서 파일의 요약 및 뷰어 기능 | 문서 파일 업로드 및 LLM 기반 요약, 뷰어 기능 제공 |
| 저품질 음성 파일의 ASR시 환각 제거 혹은 후처리 | 낮은 품질의 음성 파일 처리 시 발생하는 환각 현상 제거 및 후처리 |
| 화자분리 pyannote.audio 버전 업데이트 (community-1) | pyannote.audio를 최신 community 버전으로 업데이트 |

### 개발 고려 중인 항목

| 항목 | 설명 |
|------|------|
| 음성 프로필을 통한 화자 인식 | 화자의 음성 프로필을 학습하여 화자 자동 인식 기능 제공 |
| 실시간 스트리밍 화자분리 | 실시간 ASR에 화자분리 기능 통합 |

### 장기 도전 과제

| 항목 | 설명 |
|------|------|
| Linux 기반 GPU 가속 ASR 워커 지원 | Linux 환경에서 GPU 가속을 활용한 ASR 워커 지원 |
| Whisper 모델 경량화 | Whisper 모델의 크기 및 연산량 최적화를 통한 경량화 |

## ⚡ Performance Optimization

### Test Environment

**Hardware:**
- GPU: AMD Radeon(TM) 890M Graphics (gfx1150)
- GPU Memory: 48GB (shared)
- ROCm Version: 7.10.0a20251105 (PyTorch 2.9.0) or 6.4.4 (PyTorch 2.8.0)
- OS: Windows 10/11

**Test Audio Files:**

| File | Duration | Sample Rate | File Size | Use Case |
|------|----------|-------------|-----------|----------|
| `sample.wav` | 33.54 seconds | 16 kHz | ~1 MB | Short audio test |
| `audio_for_whisper_tariff.wav` | 885.47 seconds (14m 45s) | 16 kHz | ~27 MB | Long audio test |

**Model:**
- pyannote/speaker-diarization-3.1

### Optimization Results

We tested various optimization strategies individually to identify what actually improves performance. Here are the results with the **14.75-minute audio file**:

| Test | Optimization Method | Time | Speed | Speakers | Segments | Speed Gain | Accuracy |
|------|-------------------|------|-------|----------|----------|-----------|----------|
| **Baseline** | No optimization (FP32) | 243.06s | 3.64x | **6** | **56** | - | ✅ **Accurate** |
| **Test 1** | Mixed Precision (AMP) only | 98.24s | 9.01x | **2** | 42 | 2.47x faster | ❌ **Failed** (-4 speakers) |
| **Test 2** | `inference_mode()` only | 242.56s | 3.65x | **6** | 56 | 0.2% | ✅ Accurate |
| **Test 3** | `matmul_precision('medium')` only | 242.59s | 3.65x | **6** | 56 | 0.2% | ✅ Accurate |
| **All Combined** | All 3 optimizations | 97.74s | 9.06x | **2** | 42 | 2.49x faster | ❌ **Failed** (AMP issue) |

### MIOpen Mode Test Results

We tested different `MIOPEN_FIND_MODE` settings to find the optimal balance between speed and accuracy. All tests used the **14.75-minute audio file** (`audio_for_whisper_tariff.wav`):

| Mode | FIND_MODE | Cache | Benchmark | Time | Speed | Speakers | Segments | Status | Notes |
|------|-----------|-------|-----------|------|-------|----------|----------|--------|-------|
| **Mode 8** ⭐ | **FAST** | Disabled | Enabled | **94.82s** | **9.34x** | **6** | **56** | ✅ **Success** | **Fastest & Recommended** |
| Mode 6 | NORMAL | Disabled | Enabled | 145.86s | 6.07x | 6 | 56 | ✅ Success | Previous fastest |
| Mode 5 | NORMAL | Enabled | Enabled | 154.36s | 5.74x | 6 | 56 | ✅ Success | Cache enabled |
| Mode 7 | IMMEDIATE | Disabled | Disabled | - | - | - | - | ❌ Failed | `miopenStatusUnknownError` |
| Mode 1 | FAST | Enabled | - | - | - | - | - | ❌ Failed | SQLite error (without rocRAND headers) |
| Mode 0 | - (Disabled) | - | - | ~243s | 3.64x | 6 | 56 | ✅ Success | Baseline (MIOpen disabled) |

**Key Findings:**
- ✅ **Mode 8 (FAST)** is the **fastest and most stable** configuration
  - 35% faster than Mode 6 (94.82s vs 145.86s)
  - 54% speed improvement (9.34x vs 6.07x real-time)
  - All 6 speakers correctly detected
  - No errors or stability issues
- ✅ **All successful modes maintain 100% accuracy** (6 speakers, 56 segments)
- ❌ **IMMEDIATE mode fails** due to MIOpen compilation errors
- ❌ **Cache enabled modes are slower** - cache overhead exceeds benefits in this workload
- ✅ **rocRAND header paths are required** for FAST mode to work (prevents SQLite errors)

**Recommended Configuration:**
```python
MIOPEN_MODE = 8  # FAST mode - fastest verified configuration
os.environ['MIOPEN_FIND_MODE'] = 'FAST'
os.environ['MIOPEN_DISABLE_CACHE'] = '1'
os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'
torch.backends.cudnn.benchmark = True
```

### 🎯 Key Findings

1. **Mixed Precision (AMP) has CRITICAL accuracy issues** ⚠️
   - Speed improvement: 243s → 98s (2.5x faster) ✅
   - **Accuracy degradation: 6 speakers → 2 speakers detected** ❌
   - **4 speakers completely missed** - unacceptable for production use
   - Root cause: FP16 precision loss corrupts speaker embedding vectors
   - Different speakers incorrectly merged into same identity
   - **NOT RECOMMENDED for speaker diarization tasks**

2. **`inference_mode()` and `matmul_precision('medium')` are safe but ineffective**
   - Speed: < 0.2% improvement (< 1 second difference)
   - Accuracy: ✅ No degradation - still detects all 6 speakers correctly
   - Conclusion: No benefit, not worth the added complexity

3. **Final Decision: Use standard FP32 with no aggressive optimizations**
   - Accuracy is paramount for speaker diarization
   - 3.64x real-time speed is already practical (15min audio in 4min)
   - Reliable results > faster but incorrect results

### ⚠️ Why Mixed Precision (AMP) Was Rejected

Although Mixed Precision (FP16) showed impressive speed improvements, **it severely degrades accuracy**:

**Example: 14.75-minute audio file**

| Metric | FP32 (Baseline) | FP16 (AMP) | Result |
|--------|----------------|-----------|--------|
| Processing Time | 243s | 98s (2.5x faster) | ✅ Much faster |
| **Speakers Detected** | **6 speakers** | **2 speakers** | ❌ **Critical failure** |
| Segments | 56 segments | 42 segments | ❌ Less detailed |
| GPU Usage | 3% | 25% | ✅ Better utilization |

**Root Cause**: FP16's reduced precision corrupts speaker embedding vectors, causing the model to incorrectly merge different speakers into the same identity.

**Decision**: **Accuracy is more important than speed for speaker diarization.** We keep FP32 precision to ensure reliable results.

### Current Performance (Mode 8 - FAST Mode) ⭐

**Tested with PyTorch 2.9.0+rocm7.10.0a20251105:**

**Short audio (33 seconds)** - `sample.wav`:
- Processing time: ~9.62 seconds
- Processing speed: 3.49x real-time
- GPU usage: 3% average
- Memory: 2.12GB peak (reserved), 0.56GB allocated
- **GPU: AMD Radeon(TM) 890M Graphics** ✅

**Long audio (14.75 minutes)** - `audio_for_whisper_tariff.wav`:
- Processing time: **94.82 seconds** (1m 35s) ⚡
- Processing speed: **9.34x real-time** ⚡
- GPU usage: 3% average  
- Memory: 2.01GB peak
- **All 6 speakers correctly identified** ✅
- **Mode 8 (FAST) configuration** - fastest verified setting

### 🔬 Other Attempted Optimizations (No Effect)

During development, we tested many optimization strategies. Here's what **didn't work** in our ROCm 7.10.0a20251105 / 6.4.4 + Windows environment:

| Strategy | Speed Result | Accuracy Impact | Reason |
|----------|-------------|-----------------|--------|
| **Mixed Precision (AMP)** | ✅ 2.5x faster | ❌ **Critical failure** (6→2 speakers) | FP16 corrupts speaker embeddings |
| **inference_mode()** | ❌ No effect | ✅ No impact | Already disabled gradients, minimal overhead |
| **matmul_precision('medium')** | ❌ No effect | ✅ No impact | Not a bottleneck in this workload |
| **cuDNN/MIOpen optimization** | ❌ Failed | N/A (crashed) | `miopenStatusInternalError` - had to disable |
| **MIOPEN_FIND_MODE=FAST** | ❌ Failed | N/A (crashed) | Still crashes with SQLite database errors (ROCm 6.4.4 bug) |
| **cuDNN benchmark mode** | ❌ No effect | N/A | Requires cuDNN enabled (not available) |
| **TF32 acceleration** | ❌ No effect | Not tested | `matmul.allow_tf32=True` didn't help |
| **torch.compile** | ❌ Failed | N/A (crashed) | `ModuleNotFoundError: No module named 'triton'` |
| **Batch size increase** | ❌ No effect | Not tested | 32 → 256, no gain (other bottlenecks) |
| **pin_memory + non_blocking** | ❌ No effect | Not tested | Data transfer not the bottleneck |
| **CUDA streams** | ❌ No effect | Not tested | Already maximized by PyTorch |
| **GPU memory fraction** | ❌ No effect | Not tested | Memory not the bottleneck (only 2GB used) |

### 🔍 MIOpen Optimization Success

**The key breakthrough:** Setting up rocRAND header paths enabled FAST mode to work properly.

**Previous issue:** Without rocRAND headers, FAST mode failed with SQLite errors:
```
SQLite prepare error: no such column: mode
```

**Solution:** Configure HIP include paths for rocRAND headers:
```python
# Set HIP include paths to point to rocRAND headers
os.environ['HIP_INCLUDE_PATH'] = '...'
os.environ['ROCM_PATH'] = '...'
os.environ['CPLUS_INCLUDE_PATH'] = '...'
os.environ['C_INCLUDE_PATH'] = '...'
os.environ['HIPCC_COMPILE_FLAGS_APPEND'] = '-I...'
```

**With rocRAND headers configured:**
- ✅ FAST mode works perfectly (Mode 8: 9.34x speed)
- ✅ NORMAL mode also works (Mode 6: 6.07x speed)
- ✅ No SQLite errors
- ✅ All modes maintain 100% accuracy

**Why Mixed Precision failed:** Even though it optimizes at a different level (FP16), the accuracy degradation (6→2 speakers) makes it unusable.

### 💡 Recommendations

1. **Use Mode 8 (FAST mode)** ⭐ - Fastest verified configuration (9.34x speed, 100% accuracy)
2. **Prioritize accuracy over speed** - FP32 ensures correct speaker identification
3. **9.34x real-time speed is excellent** - 15 minutes of audio processed in ~1.5 minutes
4. **Consider batch processing** for multiple files to amortize model loading overhead
5. **Ensure rocRAND headers are configured** - Required for FAST mode to work properly

## 🔧 Troubleshooting

### 1. `RuntimeError: miopenStatusInternalError` ⚠️ **Main Issue**

**Cause**: MIOpen fails when compiling `instance_norm` operations due to SQLite database errors or missing `rocrand` header files.

**Symptoms**: 
- `MIOpen Error: SQLite prepare error: Internal error while accessing SQLite database: no such column: mode`
- `RuntimeError: miopenStatusInternalError` occurs

**Solution**: 
- `torch.backends.cudnn.enabled = False` setting (already included in code) - **This is the key solution**
- Automatic MIOpen cache deletion (already included in code)

**Note**: This setting causes some operations (`instance_norm`, etc.) to fall back to CPU, but completely prevents MIOpen errors.

### 2. `RuntimeError: miopenStatusUnknownError`

**Cause**: MIOpen fails when compiling LSTM dropout due to missing `rocrand` header files.

**Symptoms**: 
- `fatal error: 'rocrand/rocrand_xorwow.h' file not found`
- `RuntimeError: miopenStatusUnknownError` occurs

**Solution**: Same solution as `miopenStatusInternalError` (`torch.backends.cudnn.enabled = False`) - already included in code

### 3. `ValueError: 2.8.0a0+gitfc14c65 is not valid SemVer string`

**Cause**: The PyTorch version string (`2.8.0a0+gitfc14c65`) provided by ROCm 6.4.4 is not in SemVer format, causing `pyannote.audio`'s `check_version` function to fail parsing.

**Symptoms**: `ValueError: 2.8.0a0+gitfc14c65 is not valid SemVer string` occurs when loading the model.

**Solution**: Add version normalization function to `pyannote/audio/utils/version.py` (see Installation section 5.1) - **Must be applied**

### 4. `UnicodeEncodeError: 'cp949' codec can't encode character`

**Cause**: Windows console encoding issue

**Solution**: Automatically handled in code (`sys.stdout.reconfigure(encoding='utf-8')`)

### 5. `ModuleNotFoundError: Unknown rocm library 'hipsparselt'`

**Cause**: PyTorch 2.9.0 tries to load `hipsparselt` library which is not available in the ROCm SDK.

**Symptoms**: 
- `ModuleNotFoundError: Unknown rocm library 'hipsparselt'` occurs during PyTorch initialization

**Solution**: Remove `hipsparselt` from the preload list in `torch/_rocm_init.py` (see Installation section 7.1)

### 6. `OSError: Error loading "caffe2_nvrtc.dll"`

**Cause**: The `caffe2_nvrtc.dll` has dependency issues on ROCm and may fail to load.

**Symptoms**: 
- `OSError: [WinError 126] 지정된 모듈을 찾을 수 없습니다. Error loading "caffe2_nvrtc.dll"`

**Solution**: Exclude `caffe2_nvrtc.dll` from DLL loading in `torch/__init__.py` (see Installation section 7.2)

### 7. `RuntimeError: operator torchvision::nms does not exist`

**Cause**: torchvision meta registration fails due to version compatibility issues.

**Symptoms**: 
- `RuntimeError: operator torchvision::nms does not exist` occurs when importing torchvision

**Solution**: Handle the error gracefully in `torchvision/__init__.py` (see Installation section 7.3)

### 8. `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc1`

**Cause**: GPU initialization may encounter encoding issues when reading system information on Windows.

**Symptoms**: 
- `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc1 in position 57: invalid start byte` occurs during GPU initialization

**Solution**: The code already handles this gracefully by catching the error and using a fallback GPU name. If this occurs, GPU functionality should still work correctly.

### 9. GPU Not Detected

**Check**:
- Verify AMD Radeon driver is up to date
- Verify PyTorch is installed with ROCm version:

```python
import torch
print(torch.cuda.is_available())  # Should be True
print(torch.cuda.get_device_name(0))  # Should print GPU name
```

**Note**: If `get_device_name()` fails with `UnicodeDecodeError`, GPU functionality should still work. The error is handled in the code.

## 📁 Project Structure

```
torch-test/
├── worker/                   # GPU 워커 (독립 패키지, DB 접근 없음) ⭐
│   ├── celery_app.py         # Celery 앱 설정
│   ├── config.py             # 환경변수 기반 독립 설정
│   ├── logging_config.py     # loguru 기반 로깅
│   ├── tasks/                # Celery 태스크 정의
│   │   ├── asr_task.py       # ASR 처리 태스크
│   │   ├── llm_task.py       # LLM 요약 태스크
│   │   └── ocr_task.py       # OCR 처리 태스크
│   ├── processors/           # GPU 처리 로직 ⭐ NEW
│   │   ├── asr_processor.py  # ASR 처리 (S3 저장 → Stream 발행)
│   │   ├── llm_processor.py  # LLM 요약 (text_to_summarize 입력)
│   │   └── ocr_processor.py  # OCR 처리 (image_s3_keys 입력)
│   ├── utils/                # 워커 전용 유틸리티 ⭐ NEW
│   │   ├── storage.py        # S3 다운로드/업로드 (백엔드 독립)
│   │   ├── result_publisher.py # Redis Stream 발행
│   │   └── postprocess.py    # ASR 후처리 함수
│   ├── pipelines/            # 파이프라인 코드
│   │   ├── asr/              # ASR 파이프라인 (whisper, diarization)
│   │   ├── llm/              # LLM 파이프라인 (summarizer)
│   │   └── ocr/              # OCR 파이프라인
│   └── pyproject.toml        # 독립 의존성 관리
├── backend/                  # FastAPI 백엔드 (비즈니스 로직, DB 접근)
│   ├── app/
│   │   ├── controllers/      # API 컨트롤러
│   │   │   ├── websocket_controller.py  # 실시간 ASR WebSocket ⭐
│   │   │   └── websocket_helper.py      # LLM 후처리 함수
│   │   ├── core/             # 핵심 설정
│   │   │   ├── config.py     # 환경변수 설정
│   │   │   ├── storage.py    # S3 클라이언트 (download_json, delete_files_by_prefix 추가)
│   │   │   └── redis.py      # Redis 클라이언트
│   │   ├── services/         # 비즈니스 로직 ⭐
│   │   │   ├── ocr_service.py     # OcrPreprocessor (PDF→이미지 전처리만)
│   │   │   └── stream_consumer.py # Redis Stream 구독 → DB 저장 ⭐ NEW
│   │   ├── utils/
│   │   │   └── task_queue_adapter.py # 워커 큐잉 인터페이스 ⭐
│   │   ├── db/               # 데이터베이스 모델 및 세션
│   │   ├── repositories/     # 데이터 접근 계층
│   │   ├── schemas/          # Pydantic 스키마
│   │   └── main.py           # FastAPI 앱 진입점
│   ├── alembic/              # 데이터베이스 마이그레이션
│   └── pyproject.toml        # Poetry 의존성 관리
├── client/                   # Next.js 클라이언트
│   ├── app/                  # Next.js 앱 라우터
│   ├── components/           # React 컴포넌트
│   │   ├── StreamingASRModal.tsx  # 실시간 ASR 모달 ⭐
│   │   ├── ContentDetail.tsx
│   │   ├── ContentList.tsx
│   │   └── UploadForm.tsx
│   └── package.json
├── src/                      # 독립 실행 가능한 테스트 모듈
│   ├── asr/                  # ASR 테스트
│   ├── diarization/          # 화자분리 테스트
│   └── utils/                # 유틸리티 (media_converter 등)
├── docs/                     # 연구/테스트/결과 문서
├── infra/                    # 인프라 설정 (nginx, redis)
├── docker-compose.yml        # Docker Compose 설정
└── ecosystem.config.js       # PM2 설정 (FLM 서버 포함)
```

### 실시간 ASR 스트리밍 아키텍처

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Client         │────▶│  Backend        │────▶│  FLM Server     │
│  (Next.js)      │ WS  │  (FastAPI)      │HTTP │  (PM2 관리)     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                       │
        │ 1. 5초 오디오 청크     │ 2. WebM→WAV 변환      │ 3. Whisper 전사
        │    (바이너리 전송)     │    FLM 전사 요청      │    NPU 가속
        │                       │                       │
        │◀──────────────────────│◀──────────────────────│
        │ 5. "commit" 메시지     │ 4. 전사 결과 반환      │
        │    (즉시 표시)         │                       │
        │                       │                       │
        │◀──────────────────────│ 6. LLM 후처리 (백그라운드)
        │ 7. "correction" 메시지 │    - 언어 필터링 (한/영 외 → "음성 인식 불가")
        │    (교정 텍스트)       │    - 문법 교정, 구두점 추가
```

**WebSocket 메시지 프로토콜:**

| 방향 | 타입 | 설명 |
|------|------|------|
| Client → Server | 바이너리 | WebM 오디오 데이터 |
| Client → Server | `audio_chunk` | 전사 트리거 (chunk_id, is_last) |
| Client → Server | `finish` | 녹음 종료 |
| Server → Client | `ready` | 서버 준비 완료 |
| Server → Client | `commit` | 전사 결과 (즉시 표시) |
| Server → Client | `correction` | LLM 교정 결과 (업데이트) |

## 📝 References

- [AMD ROCm Official Documentation](https://rocm.docs.amd.com/)
- [PyAnnote Audio Official Documentation](https://github.com/pyannote/pyannote-audio)
- [PyTorch ROCm Installation Guide](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installryz/windows/install-pytorch.html)

## ⚠️ Important Notes

- **Performance**: The `torch.backends.cudnn.enabled = False` setting may cause some operations (especially `instance_norm`) to fall back to CPU. This is a necessary setting to prevent MIOpen errors.
- **ROCm Version**: 
  - **PyTorch 2.9.0+rocm7.10.0a20251105** (recommended): Tested and verified to work correctly with GPU acceleration
  - **PyTorch 2.8.0+rocm6.4.4** (legacy): Preview version on Windows, stability issues may occur
- **GPU Usage**: Most operations run on GPU, but some operations fall back to CPU. You may see high CPU usage in Task Manager.
- **Compatibility Patches**: Compatibility patches (sections 6 and 7) must be applied after installing PyTorch 2.9.0. These patches may need to be reapplied after package updates.
- **Installation Method**: Use `--no-deps` flag when installing PyTorch packages to avoid dependency conflicts. Install other dependencies separately.

## 📄 License

This project is for educational and research purposes.
