# NPU(FLM) 전사 타임스탬프 이슈 해결

## 문제 상황

### 증상
NPU 모드(FLM 서버)로 전사 시 세그먼트가 길게 나옴:
```
SPEAKER_01 [15.17s - 264.50s]  <- 약 4분짜리 단일 세그먼트
```

### 원인
FLM 서버는 OpenAI API 호환이지만 `verbose_json` 응답에서 **세그먼트(타임스탬프)를 반환하지 않음**:

```json
// OpenAI API 기대값
{
  "text": "...",
  "segments": [{"start": 0.0, "end": 3.32, "text": "..."}, ...]
}

// FLM 서버 실제 응답
{
  "model": "whisper-v3",
  "text": "전체 텍스트..."
  // segments 없음!
}
```

### whisper.cpp와의 차이

| ASR 엔진 | 세그먼트 생성 | 타임스탬프 |
|---------|-------------|----------|
| whisper.cpp | 자체 VAD 기반 자동 생성 | O |
| FLM (NPU) | 미지원 | X |

---

## 해결 과정

### 1차 시도: 순차 처리 (화자분리 기반 재전사)

```
[병렬]
├─ 화자분리 (GPU) ─────────────────────┐
└─ FLM 전체 전사 (NPU) ───────────────┤ <- 세그먼트 없음, 버려짐
                                       ↓
[순차 - 재전사]
화자분리 세그먼트 기반으로 FLM 재호출
├─ 15s-45s 구간 추출 → FLM 전사
├─ 45s-75s 구간 추출 → FLM 전사
└─ ...
```

**문제점:**
- NPU 2회 호출 (전체 전사 + 재전사)
- 첫 번째 전사 결과가 버려짐
- 비효율적

### 2차 시도: 순차 처리 (화자분리 먼저)

```
[순차]
화자분리 (GPU) → 완료 대기 → 세그먼트 기반 FLM 전사 (NPU)
```

**문제점:**
- 병렬 처리 불가
- GPU/NPU 동시 활용 못함

### 최종 해결: VAD 기반 병렬 처리

```
[병렬]
├─ 화자분리 (GPU) ─────────────────────────────────┐
│   └─ 화자별 시간 구간 + 화자 라벨                  │
│                                                   │
└─ VAD 기반 FLM 전사 (NPU) ────────────────────────┤
    ├─ VAD로 음성 구간 감지                         │
    ├─ 30초 이하 청크로 분할                        │
    ├─ 각 청크별 FLM 전사 (offset 기록)             │
    └─ 세그먼트: [{start, end, text}, ...]          ↓
                                               [병합]
                                         화자 정보 + 텍스트
```

---

## 아키텍처

### 전체 처리 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│                         pipeline.py                              │
│                  _run_case4_parallel_full_asr()                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   accuracy_mode == "speed" (FLM/NPU)                            │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │              ThreadPoolExecutor(max_workers=2)           │   │
│   ├─────────────────────────────────────────────────────────┤   │
│   │                                                          │   │
│   │  Thread 1: run_diarization()          [GPU - pyannote]  │   │
│   │  ├─ 화자분리 모델 로드                                   │   │
│   │  ├─ 임베딩 추출                                          │   │
│   │  └─ 클러스터링 → 화자 라벨                               │   │
│   │                                                          │   │
│   │  Thread 2: run_flm_asr_parallel_with_vad()  [NPU - FLM] │   │
│   │  ├─ VAD로 음성 구간 감지                                 │   │
│   │  ├─ 30초 이하 청크로 분할                                │   │
│   │  ├─ 각 청크별 FLM 전사                                   │   │
│   │  └─ offset 기반 세그먼트 생성                            │   │
│   │                                                          │   │
│   └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│                    merge_segments_with_speakers()                │
│                     (ASR 세그먼트 + 화자 정보)                   │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│   accuracy_mode == "accuracy" (whisper.cpp/GPU)                 │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │              ThreadPoolExecutor(max_workers=2)           │   │
│   ├─────────────────────────────────────────────────────────┤   │
│   │  Thread 1: run_diarization()          [GPU - pyannote]  │   │
│   │  Thread 2: run_asr_transcription()    [GPU - whisper]   │   │
│   │             └─ 자체 VAD로 세그먼트 생성                  │   │
│   └─────────────────────────────────────────────────────────┘   │
│                              ↓                                   │
│                    merge_segments_with_speakers()                │
└─────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│                        processor.py                              │
│                   process_transcription_job()                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   [공통 후처리 - 모든 ASR 엔진에 적용]                           │
│                                                                  │
│   1. split_long_segments(max_duration=30.0)                     │
│      └─ 30초 초과 세그먼트 분할 (안전장치)                       │
│                                                                  │
│   2. merge_consecutive_speaker_segments(max_duration=30.0)      │
│      └─ 같은 화자의 연속 세그먼트 병합 (30초 이하)               │
│                                                                  │
│   3. rebuild_transcription_text()                               │
│      └─ 세그먼트 텍스트로 전체 텍스트 재구성                     │
│                                                                  │
│   4. rebuild_speaker_stats()                                    │
│      └─ 화자별 통계 재계산                                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                               ↓
                         [DB 저장]
```

### VAD 기반 전사 상세 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│              run_flm_asr_parallel_with_vad()                     │
│                      (flm_asr.py)                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   1. 오디오 로드                                                 │
│      waveform, sample_rate = librosa.load(audio_path, sr=16000) │
│                                                                  │
│   2. VAD로 음성 구간 감지                                        │
│      speech_segments = get_speech_timestamps_energy(waveform)   │
│      예: [                                                       │
│        {"start": 0.0, "end": 15.5},                             │
│        {"start": 18.2, "end": 45.8},                            │
│        {"start": 48.0, "end": 120.5},  <- 72.5초, 분할 필요     │
│        ...                                                       │
│      ]                                                           │
│                                                                  │
│   3. 구간 병합 및 분할                                           │
│      processed = merge_speech_segments(speech_segments,          │
│                                        max_duration=30.0)        │
│      예: [                                                       │
│        {"start": 0.0, "end": 15.5},                             │
│        {"start": 18.2, "end": 45.8},                            │
│        {"start": 48.0, "end": 78.0},   <- 분할됨                │
│        {"start": 78.0, "end": 108.0},  <- 분할됨                │
│        {"start": 108.0, "end": 120.5}, <- 분할됨                │
│        ...                                                       │
│      ]                                                           │
│                                                                  │
│   4. 청크별 FLM 전사                                             │
│      for chunk in processed:                                     │
│        ├─ 오디오 청크 추출 (chunk.start ~ chunk.end)            │
│        ├─ FLM 서버로 전사 요청                                  │
│        └─ 세그먼트 생성: {                                       │
│             "start": chunk.start,  <- offset                    │
│             "end": chunk.end,                                    │
│             "text": flm_response.text                            │
│           }                                                      │
│                                                                  │
│   5. 결과 반환                                                   │
│      {                                                           │
│        "text": "전체 텍스트...",                                 │
│        "segments": [...],  <- 타임스탬프가 있는 세그먼트        │
│        "language": "ko"                                          │
│      }                                                           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 구현된 함수

### flm_asr.py

| 함수 | 설명 |
|-----|-----|
| `get_speech_timestamps_energy()` | 에너지 기반 VAD - RMS 에너지로 음성 구간 감지 |
| `merge_speech_segments()` | 인접 구간 병합 + 30초 초과 구간 분할 |
| `run_flm_asr_parallel_with_vad()` | VAD 기반 병렬 전사 메인 함수 |

### transcription_postprocess.py

| 함수 | 설명 |
|-----|-----|
| `split_long_segments()` | 긴 세그먼트 분할 (공통 후처리, 안전장치) |
| `merge_consecutive_speaker_segments()` | 같은 화자 연속 세그먼트 병합 |

---

## 성능 비교

### 처리 시간 (예: 6분 오디오)

| 방식 | 화자분리 | ASR | 총 시간 |
|-----|---------|-----|--------|
| 순차 (이전) | 30s | 40s | 70s |
| 병렬 (현재) | 30s | 40s | **40s** (max) |

### 리소스 활용

| 방식 | GPU | NPU | 동시 활용 |
|-----|-----|-----|----------|
| 순차 (이전) | ✓ → 대기 → ✗ | ✗ → 대기 → ✓ | ❌ |
| 병렬 (현재) | ✓ | ✓ | ✅ |

---

## 확장성

### 새로운 ASR 엔진 추가 시

1. **세그먼트를 반환하는 엔진** (예: insanely-fast-whisper)
   - whisper.cpp와 동일하게 병렬 처리
   - 공통 후처리에서 분할/병합 자동 적용

2. **세그먼트를 반환하지 않는 엔진**
   - FLM과 동일하게 VAD 기반 병렬 처리
   - `run_flm_asr_parallel_with_vad()` 참고

### 공통 후처리 파이프라인

```python
# processor.py - 모든 ASR 엔진에 적용
original_segments = result.transcription.get("segments", [])

# Step 1: 긴 세그먼트 분할 (30초 초과)
split_segments = split_long_segments(original_segments, max_duration=30.0)

# Step 2: 같은 화자 연속 세그먼트 병합 (30초 이하)
processed_segments = merge_consecutive_speaker_segments(split_segments, max_duration=30.0)
```

---

## 관련 파일

```
backend/
├── worker/
│   ├── pipeline.py                  # 메인 파이프라인 (병렬 처리 로직)
│   ├── flm_asr.py                   # FLM 전사 + VAD 함수
│   ├── whisper_utils.py             # whisper.cpp 전사
│   └── diarization_utils.py         # 화자분리
│
└── app/
    ├── worker/
    │   └── processor.py             # 공통 후처리 호출
    │
    └── services/
        └── transcription_postprocess.py  # 분할/병합 함수
```
