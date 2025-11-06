# 화자분리 (Speaker Diarization) 모듈

화자분리 관련 스크립트 및 실행 파일을 관리하는 폴더입니다.

## 파일 구조

- `test_pyannote.py`: pyannote.audio를 사용한 화자분리 테스트 스크립트
- `test_asr_with_diarization.py`: ASR(전사) + 화자분리 통합 스크립트
- `diarization_logger.py`: 화자분리 로깅 모듈

## 사용법

### ASR + 화자분리 통합 실행

```bash
python src/diarization/test_asr_with_diarization.py [모델명] [오디오파일]
```

예시:
```bash
python src/diarization/test_asr_with_diarization.py large-v3-turbo wavs/sample.wav
```

### 화자분리만 실행

```bash
python src/diarization/test_pyannote.py [오디오파일]
```

## 출력 파일

- 로그 파일: `src/diarization/logs/` 폴더에 저장
- JSON 결과: `src/diarization/logs/` 폴더에 저장

## 의존성

- `pyannote.audio`: 화자분리 파이프라인
- `whisper-cli.exe`: ASR 엔진 (whisper.cpp 기반, **Vulkan GPU 가속 지원**)
  - **필수**: `whisper.cpp`를 Vulkan 지원으로 빌드해야 함 (메인 README.md의 Installation 섹션 4.1 참조)
  - **필수**: GGML 모델 파일 (`.bin`) 다운로드 필요 (메인 README.md의 Installation 섹션 4.2 참조)
  - Vulkan 가속으로 빠른 처리 속도 제공 (예: 14분 오디오를 약 5분에 처리)
- `torch`: PyTorch (ROCm 지원)

