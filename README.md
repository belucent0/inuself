# PyAnnote Speaker Diarization with ROCm on Windows

Speaker diarization using PyAnnote with AMD GPU (ROCm) acceleration on Windows.

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

### 1. LLM Provider 선택

두 가지 LLM provider를 지원합니다:

#### 옵션 A: LM Studio

LM Studio는 Windows에서 실행되는 LLM 서버입니다.

**설정:**
1. LM Studio 앱을 다운로드하고 설치합니다
2. 모델을 로드하고 Local Server를 시작합니다 (포트 1234)
3. `.env` 파일 설정:
   ```env
   LLM_PROVIDER=lmstudio
   LMSTUDIO_BASE_URL=http://localhost:1234
   LMSTUDIO_MODEL_NAME=gpt-oss-20b
   ```

#### 옵션 B: llama-cpp-python 직접 사용

로컬에서 llama-cpp-python을 직접 사용합니다 (Vulkan 지원 빌드 필요).

**설정:**
1. 모델 파일 준비: `models/gpt-oss-20b-Q4_K_S.gguf`
2. `.env` 파일 설정:
   ```env
   LLM_PROVIDER=llama_cpp
   LLM_MODEL_PATH=models/gpt-oss-20b-Q4_K_S.gguf
   ```

### 2. 환경 변수 / 설정

`backend/app/core/config.py`에 다음 설정이 추가되었습니다. 필요 시 `.env`에서 오버라이드하면 됩니다.

| 설정 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_PROVIDER` | `lmstudio` | LLM provider: `lmstudio` 또는 `llama_cpp` |
| `LMSTUDIO_BASE_URL` | `http://localhost:1234` | LM Studio API 엔드포인트 |
| `LMSTUDIO_MODEL_NAME` | `gpt-oss-20b` | LM Studio에서 사용할 모델 이름 |
| `LLM_MODEL_PATH` | `models/gpt-oss-20b-Q4_K_S.gguf` | llama_cpp 사용 시 모델 파일 경로 |
| `LLM_CONTEXT_LENGTH` | `4096` | Context window |
| `LLM_TEMPERATURE` | `0.4` | 생성 온도 |
| `LLM_TOP_P` | `0.9` | Top-p nucleus 샘플링 |
| `LLM_MAX_TOKENS` | `1024` | Markdown 응답 최대 토큰 |
| `LLM_N_THREADS` | `8` | CPU 스레드 수 (llama_cpp만 사용) |

> ⚠️ **llama_cpp 직접 사용 시**: Vulkan 가속만 지원합니다. CPU 폴백은 제공되지 않으므로, 모델 로딩 실패 시 바로 `SUMMARY_FAILED` 상태로 기록됩니다.

### 3. 요약 워커 실행

LLM 요약은 RQ 큐(`llm_tasks`)를 통해 비동기 실행됩니다. 개발 스크립트(`run_dev.sh`)는 ASR/LLM 워커를 모두 자동으로 띄우지만, 수동으로 실행하려면 아래 명령을 사용하세요.

```bash
# ASR + 화자분리 워커
cd backend && poetry run python -m app.worker.run_worker

# LLM 요약 워커
cd backend && poetry run python -m app.worker.run_llm_worker
```

실패한 요약 작업은 `SUMMARY_FAILED` 상태로 표시되며, 콘텐츠를 다시 큐에 넣으면 재시도할 수 있습니다. 요약 로그는 `llm_log` 테이블에 저장되어 클라이언트 UI에서 확인 가능합니다.

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

1. Redis 서버 실행
2. 워커 시작:

```
cd backend
poetry run python -m app.worker.run_worker
```

워커는 Redis 큐(`asr_tasks`)를 소비하며 업로드된 파일에 대해 ASR+화자분리를 실행하고 PostgreSQL에 결과/로그를 기록합니다.

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

- ✅ AMD GPU (ROCm) acceleration support for speaker diarization
- ✅ **Vulkan GPU acceleration for ASR** (via whisper.cpp) - Fast processing (e.g., 14min audio in ~5min)
- ✅ Automatic logging (text + JSON format)
- ✅ Detailed statistics (processing time, speed, number of speakers, etc.)
- ✅ MIOpen error handling and automatic workarounds
- ✅ Windows console encoding fixes
- ✅ Real-time GPU monitoring during processing
- ✅ Media file conversion tool (MP3, MP4, etc. → WAV)
- ✅ Batch processing support for multiple media files
- ✅ Parallel processing (ASR and diarization run simultaneously)

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
├── src/
│   ├── asr/                  # ASR (음성 인식) 모듈
│   │   ├── models/          # 모델 파일들
│   │   ├── logs/            # ASR 로그 파일들
│   │   └── test_asr.py      # ASR 테스트 스크립트
│   ├── diarization/         # 화자분리 모듈
│   │   ├── test_pyannote.py
│   │   ├── test_asr_with_diarization.py
│   │   └── diarization_logger.py
│   └── utils/               # 유틸리티 모듈
│       └── media_converter.py  # 미디어 파일 변환 도구
├── docs/                     # 연구/테스트/결과 문서
│   ├── GPU_OPTIMIZATION_RESEARCH.md
│   ├── BOTTLENECK_ANALYSIS.md
│   ├── performance_analysis.md
│   ├── PARALLEL_PROCESSING_RESULTS.md
│   └── transcription_comparison.md
├── media/                    # 미디어 파일 폴더
│   ├── upload/              # 입력 미디어 파일 (원본)
│   └── wav/                 # 변환된 WAV 파일
├── README.md                 # 이 파일
└── .gitignore               # Git ignore 파일 목록
```

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
