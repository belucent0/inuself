# Whisper ASR with ROCm

OpenAI Whisper를 사용한 음성 인식 (ASR) 테스트 및 사용 가이드

## 📁 폴더 구조

```
src/asr/
├── models/          # 모델 파일 (ggml 형식은 whisper.cpp용, 사용 안 함)
├── test_asr.py     # ASR 테스트 스크립트
└── README.md       # 이 파일
```

## 🚀 사용법

### 기본 사용 (base 모델)

```bash
python src/asr/test_asr.py base media/wav/sample.wav
```

### 정확도가 중요할 때 (large-v3 모델)

```bash
python src/asr/test_asr.py large-v3 media/wav/sample.wav
```

### 사용 가능한 모델

- `tiny`, `tiny.en` - 가장 작고 빠름
- `base`, `base.en` - 기본 모델 (간단한 테스트용) ⭐
- `small`, `small.en` - 중간 크기
- `medium`, `medium.en` - 중간-대형
- `large-v1`, `large-v2`, `large-v3` - 가장 정확함 (정확도 중요할 때) ⭐
- `large-v3-turbo`, `turbo` - 빠른 large 모델

## 📝 참고사항

- **whisper.cpp 및 Vulkan 가속**: 이 프로젝트는 `whisper.cpp`를 사용하며 **Vulkan 기반 GPU 가속**을 지원합니다.
  - `whisper-cli.exe`가 Vulkan 지원으로 빌드되어 있어야 합니다 (메인 README.md의 Installation 섹션 참조)
  - Vulkan 가속으로 빠른 처리 속도를 제공합니다 (예: 14분 오디오를 약 5분에 처리)
- **GGML 모델 파일 다운로드**: `models/` 폴더에 GGML 형식 모델 파일 (`.bin`)이 필요합니다.
  - 우선순위 1: `src/asr/models/` 폴더에서 모델 검색
  - 우선순위 2: `C:/whisper-cpp/models/` 폴더에서 모델 검색
  - 모델 다운로드: [Hugging Face - whisper.cpp models](https://huggingface.co/ggerganov/whisper.cpp/tree/main)
  - 지원 모델: `ggml-base.bin`, `ggml-large-v2.bin`, `ggml-large-v3.bin`, `ggml-large-v3-turbo.bin` 등
- **모델 자동 다운로드 없음**: GGML 모델 파일은 수동으로 다운로드해야 합니다.

## 🔗 관련 문서

- `WHISPERX_ROCM_STATUS.md` - WhisperX와 ROCm 호환성 분석

