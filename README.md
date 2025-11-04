# PyAnnote Speaker Diarization with ROCm on Windows

Speaker diarization using PyAnnote with AMD GPU (ROCm) acceleration on Windows.

## 📋 Table of Contents

- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Features](#features)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)

## 🖥️ System Requirements

- **OS**: Windows 10/11 (64-bit)
- **GPU**: AMD Radeon GPU (ROCm supported)
- **Python**: 3.12
- **Driver**: AMD Radeon driver 25.20.01.14 or higher (for ROCm 6.4.4)

## 📦 Installation

### 1. Create Python Virtual Environment

```bash
python -m venv rocm_env
rocm_env\Scripts\activate
```

### 2. Install PyTorch and Dependencies

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

### 4. Hugging Face Model Access Setup (Required)

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

### 5. pyannote.audio Compatibility Patch

**⚠️ Required**: The PyTorch version string (`2.8.0a0+gitfc14c65`) provided by ROCm 6.4.4 is not in SemVer format, causing errors in `pyannote.audio`'s version check.

**Problem**: The original code performs `".".join(mine.split(".")[:3])`, but when splitting `2.8.0a0+gitfc14c65` by `.`, it becomes `['2', '8', '0a0+gitfc14c65']`, resulting in `2.8.0a0+gitfc14c65` remaining unchanged. The `semver` library cannot parse `a0` (alpha version) and `+gitfc14c65` (build metadata), causing `ValueError: 2.8.0a0+gitfc14c65 is not valid SemVer string` error.

The following patch **must** be applied:

#### 5.1. Modify `pyannote/audio/utils/version.py`

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

## 🚀 Usage

### Basic Usage

1. Place your audio file in the project folder
2. Set the audio file path in `test_pyannote.py`:

```python
audio_file = "your_audio_file.wav"
```

3. Run the script:

```bash
source rocm_env/Scripts/activate
python test_pyannote.py
```

### Results

- **Console Output**: Real-time progress and speaker diarization results
- **Log Files**: Timestamped log files saved in `logs/` folder
  - `.log`: Text format log
  - `.json`: Structured JSON format log

## ✨ Features

- ✅ AMD GPU (ROCm) acceleration support
- ✅ Automatic logging (text + JSON format)
- ✅ Detailed statistics (processing time, speed, number of speakers, etc.)
- ✅ MIOpen error handling and automatic workarounds
- ✅ Windows console encoding fixes
- ✅ Real-time GPU monitoring during processing

## ⚡ Performance Optimization

### Test Environment

**Hardware:**
- GPU: AMD Radeon(TM) 890M Graphics (gfx1150)
- GPU Memory: 48GB (shared)
- ROCm Version: 6.4.4
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

### Current Performance (FP32 - Accurate)

**Short audio (33 seconds)** - `sample.wav`:
- Processing time: ~10 seconds
- Processing speed: 3.2x real-time
- GPU usage: 3% average
- Memory: 2.01GB peak

**Long audio (14.75 minutes)** - `audio_for_whisper_tariff.wav`:
- Processing time: 243 seconds (4m 3s)
- Processing speed: 3.64x real-time
- GPU usage: 3% average  
- Memory: 2.01GB peak
- **All 6 speakers correctly identified** ✅

### 🔬 Other Attempted Optimizations (No Effect)

During development, we tested many optimization strategies. Here's what **didn't work** in our ROCm 6.4.4 + Windows environment:

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

### 🔍 Why Most Optimizations Failed

The root cause is **`torch.backends.cudnn.enabled = False`**, which is required to prevent MIOpen errors:

```python
# Required to prevent miopenStatusInternalError
torch.backends.cudnn.enabled = False
```

**We also tried MIOpen workarounds** (reference: [PyTorch Issue #150168](https://github.com/pytorch/pytorch/issues/150168)):
```python
os.environ['MIOPEN_FIND_MODE'] = 'FAST'
os.environ['MIOPEN_DISABLE_CACHE'] = '1'
os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'
torch.backends.cudnn.enabled = True
```

But still got: `SQLite prepare error: no such column: mode` → This is a fundamental MIOpen bug in ROCm 6.4.4 + Windows + gfx1150.

With `cudnn.enabled = False`, PyTorch is forced to:
- Use slower fallback implementations (e.g., `_slow_conv2d_forward`)
- Increase CPU-GPU data transfer overhead
- Disable MIOpen-specific optimizations

**Why Mixed Precision failed:** Even though it optimizes at a different level (FP16), the accuracy degradation (6→2 speakers) makes it unusable.

### 💡 Recommendations

1. **Prioritize accuracy over speed** - FP32 ensures correct speaker identification
2. **Upgrade to ROCm 7.1+** on Linux for better gfx1150 support (may enable cuDNN/MIOpen)
3. **3.64x real-time speed is already practical** - 15 minutes of audio processed in ~4 minutes
4. **Consider batch processing** for multiple files to amortize model loading overhead

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

### 5. GPU Not Detected

**Check**:
- Verify AMD Radeon driver is up to date
- Verify PyTorch is installed with ROCm version:

```python
import torch
print(torch.cuda.is_available())  # Should be True
print(torch.cuda.get_device_name(0))  # Should print GPU name
```

## 📁 Project Structure

```
torch-test/
├── test_pyannote.py          # Main speaker diarization script
├── diarization_logger.py     # Logging module
├── README.md                 # This file
├── .gitignore               # Git ignore file list
└── logs/                    # Log file storage folder (auto-created)
```

## 📝 References

- [AMD ROCm Official Documentation](https://rocm.docs.amd.com/)
- [PyAnnote Audio Official Documentation](https://github.com/pyannote/pyannote-audio)
- [PyTorch ROCm Installation Guide](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installryz/windows/install-pytorch.html)

## ⚠️ Important Notes

- **Performance**: The `torch.backends.cudnn.enabled = False` setting may cause some operations (especially `instance_norm`) to fall back to CPU. This is a necessary setting to prevent MIOpen errors.
- **ROCm Version**: ROCm 6.4.4 is a preview version on Windows. Stability issues may occur.
- **GPU Usage**: Most operations run on GPU, but some operations fall back to CPU. You may see high CPU usage in Task Manager.
- **Compatibility Patches**: Compatibility patches may need to be reapplied after package updates.

## 📄 License

This project is for educational and research purposes.
