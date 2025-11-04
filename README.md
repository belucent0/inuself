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
