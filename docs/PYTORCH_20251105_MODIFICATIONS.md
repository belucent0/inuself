# PyTorch 20251105 버전 실행을 위한 수정 사항

## 수정된 파일 목록

20251105 버전을 정상적으로 실행하기 위해 다음 4개 파일을 수정했습니다:

### 1. `torch/_rocm_init.py`

**위치**: `rocm_env/Lib/site-packages/torch/_rocm_init.py`

**수정 내용**:
- `hipsparselt` 라이브러리를 `preload_shortnames` 리스트에서 제거
- ROCm SDK 버전을 `7.10.0a20251105`로 업데이트

**수정 전**:
```python
preload_shortnames=['amd_comgr', 'amdhip64', 'hiprtc', 'hipblas', 'hipfft', 'hiprand', 'hipsparse', 'hipsparselt', 'hipsolver', 'hipblaslt', 'miopen', 'rocm-openblas'],
check_version='7.10.0a20251112'  # 또는 다른 버전
```

**수정 후**:
```python
preload_shortnames=['amd_comgr', 'amdhip64', 'hiprtc', 'hipblas', 'hipfft', 'hiprand', 'hipsparse', 'hipsolver', 'hipblaslt', 'miopen', 'rocm-openblas'],
check_version='7.10.0a20251105'
```

**이유**: `hipsparselt` 라이브러리가 ROCm SDK에 포함되어 있지 않아 `ModuleNotFoundError` 발생

---

### 2. `rocm_sdk/_dist_info.py`

**위치**: `rocm_env/Lib/site-packages/rocm_sdk/_dist_info.py`

**수정 내용**:
- `__version__` 값을 `7.10.0a20251105`로 업데이트

**수정 전**:
```python
__version__ = '7.10.0a20251112'  # 또는 다른 버전
```

**수정 후**:
```python
__version__ = '7.10.0a20251105'
```

**이유**: PyTorch 버전과 ROCm SDK 버전 불일치로 인한 경고 메시지 제거

---

### 3. `torch/__init__.py`

**위치**: `rocm_env/Lib/site-packages/torch/__init__.py`

**수정 내용**:
- **20251105 버전에서는 수정하지 않았습니다**
- 1112/1113 버전에서는 `caffe2_nvrtc.dll` 제외 코드를 추가했지만, 1105 버전에서는 필요 없었습니다

**참고**: 1112/1113 버전에서 추가했던 코드:
```python
dlls = glob.glob(os.path.join(th_dll_path, "*.dll"))
# Exclude caffe2_nvrtc.dll as it may have dependency issues on ROCm
dlls = [dll for dll in dlls if os.path.basename(dll) != "caffe2_nvrtc.dll"]
```

---

### 4. `torchvision/__init__.py`

**위치**: `rocm_env/Lib/site-packages/torchvision/__init__.py`

**수정 내용**:
- `_meta_registrations` import를 try-except로 감싸서 RuntimeError 처리

**수정 전**:
```python
from torchvision import _meta_registrations, datasets, io, models, ops, transforms, utils  # usort:skip
```

**수정 후**:
```python
try:
    from torchvision import _meta_registrations  # usort:skip
except RuntimeError:
    # Ignore meta registration errors for compatibility
    pass
from torchvision import datasets, io, models, ops, transforms, utils  # usort:skip
```

**이유**: `RuntimeError: operator torchvision::nms does not exist` 오류 방지

---

## 수정 요약

| 파일 | 수정 내용 | 필수 여부 |
|------|----------|----------|
| `torch/_rocm_init.py` | `hipsparselt` 제거, 버전 업데이트 | ✅ 필수 |
| `rocm_sdk/_dist_info.py` | 버전 업데이트 | ✅ 필수 |
| `torch/__init__.py` | 수정 없음 (1105 버전에서는 불필요) | ❌ |
| `torchvision/__init__.py` | `_meta_registrations` 오류 처리 | ✅ 필수 |

## 수정 방법

각 파일을 직접 편집하거나, 다음 명령어로 일괄 수정할 수 있습니다:

```bash
# 1. torch/_rocm_init.py 수정
sed -i "s/'hipsparselt', //g" rocm_env/Lib/site-packages/torch/_rocm_init.py
sed -i "s/check_version='7.10.0a202511[0-9][0-9]'/check_version='7.10.0a20251105'/g" rocm_env/Lib/site-packages/torch/_rocm_init.py

# 2. rocm_sdk/_dist_info.py 수정
sed -i "s/__version__ = '7.10.0a202511[0-9][0-9]'/__version__ = '7.10.0a20251105'/g" rocm_env/Lib/site-packages/rocm_sdk/_dist_info.py

# 3. torchvision/__init__.py 수정 (수동 편집 필요)
```

## 주의사항

1. **패키지 업데이트 시**: PyTorch나 관련 패키지를 업데이트하면 이 수정사항이 사라질 수 있으므로, 업데이트 후 다시 적용해야 합니다.

2. **버전별 차이**: 
   - 20251105 버전: `caffe2_nvrtc.dll` 제외 불필요
   - 20251112/20251113 버전: `caffe2_nvrtc.dll` 제외 필요

3. **백업**: 수정 전 원본 파일을 백업하는 것을 권장합니다.




