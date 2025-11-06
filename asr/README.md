# Whisper ASR with ROCm

OpenAI Whisper를 사용한 음성 인식 (ASR) 테스트 및 사용 가이드

## 📁 폴더 구조

```
asr/
├── models/          # 모델 파일 (ggml 형식은 whisper.cpp용, 사용 안 함)
├── test_asr.py     # ASR 테스트 스크립트
└── README.md       # 이 파일
```

## 🚀 사용법

### 기본 사용 (base 모델)

```bash
python asr/test_asr.py base wavs/sample.wav
```

### 정확도가 중요할 때 (large-v3 모델)

```bash
python asr/test_asr.py large-v3 wavs/sample.wav
```

### 사용 가능한 모델

- `tiny`, `tiny.en` - 가장 작고 빠름
- `base`, `base.en` - 기본 모델 (간단한 테스트용) ⭐
- `small`, `small.en` - 중간 크기
- `medium`, `medium.en` - 중간-대형
- `large-v1`, `large-v2`, `large-v3` - 가장 정확함 (정확도 중요할 때) ⭐
- `large-v3-turbo`, `turbo` - 빠른 large 모델

## 📝 참고사항

- **모델 저장 위치**: 모델은 `asr/models/` 폴더에 저장됩니다 (프로젝트 내 관리).
  - 기본 위치 (`~/.cache/whisper/`) 대신 프로젝트별로 관리하여 이식성과 버전 관리가 용이합니다.
- **ggml 모델 파일**: `models/` 폴더의 ggml 파일들은 whisper.cpp 전용입니다. OpenAI Whisper는 PyTorch 모델(`.pt`)을 사용합니다.
- **모델 자동 다운로드**: 첫 실행 시 모델이 자동으로 `asr/models/`에 다운로드됩니다.
- **GPU 가속**: ROCm 환경에서 자동으로 GPU를 사용합니다.

## 🔗 관련 문서

- `WHISPERX_ROCM_STATUS.md` - WhisperX와 ROCm 호환성 분석

