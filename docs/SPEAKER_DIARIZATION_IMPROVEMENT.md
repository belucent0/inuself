# 화자 분리 정확도 개선 방법

## 현재 구현 상태

현재 시스템은 `pyannote.audio`의 `speaker-diarization-3.1` 모델을 사용하여 화자 분리를 수행합니다. 기본 파라미터로 동작하며, 각 세그먼트별 임베딩 벡터를 추출하여 저장하고 있습니다.

### 겹치는 발화(Overlapping Speech) 처리

**구현됨 (2024-12)**: 겹치는 발화 구간을 별도 세그먼트로 분리하는 기능이 추가되었습니다.

예를 들어, 화자 A가 말하는 중간에 화자 B가 "네"라고 끼어드는 경우:
- **이전**: 화자 A의 세그먼트가 겹치는 구간을 포함하여 하나의 긴 세그먼트로 처리
- **현재**: 겹치는 구간을 분리하여 각 화자별로 별도 세그먼트 생성
  - 화자 A: 0.0 ~ 3.0초, 4.0 ~ 5.0초
  - 화자 B: 3.0 ~ 4.0초

이 기능은 `extract_speaker_segments()` 함수의 `split_overlaps=True` 옵션으로 활성화되며, `merge_segments_with_speakers()` 함수에서도 자동으로 사용됩니다.

## 개선 방법

### 1. 하이퍼파라미터 튜닝

#### 1.1 Clustering 파라미터 조정

`pyannote.audio`의 화자 분리 파이프라인은 클러스터링 알고리즘을 사용하여 화자를 구분합니다. 다음 파라미터를 조정할 수 있습니다:

```python
# 현재 코드 (backend/worker/diarization_utils.py)
diarization_pipeline = DiarizationPipeline.from_pretrained("pyannote/speaker-diarization-3.1")

# 개선: 클러스터링 파라미터 튜닝
# pyannote.audio는 내부적으로 AgglomerativeClustering을 사용하며,
# 다음과 같은 파라미터를 조정할 수 있습니다:
# - threshold: 클러스터링 임계값 (낮을수록 더 많은 화자로 분리)
# - min_cluster_size: 최소 클러스터 크기
# - linkage: 클러스터링 연결 방법 ('ward', 'complete', 'average')
```

**구현 방법:**
- `diarization_pipeline.clustering` 객체에 접근하여 파라미터 조정
- 또는 `diarization_pipeline.instantiate()` 메서드를 사용하여 하이퍼파라미터 설정

#### 1.2 Segmentation 파라미터 조정

화자 분리 전에 음성 활동 감지(VAD) 및 세그멘테이션을 수행합니다. 다음 파라미터를 조정할 수 있습니다:

```python
# segmentation.threshold: 음성/비음성 구분 임계값
# segmentation.min_duration_off: 최소 무음 구간 길이
# segmentation.min_duration_on: 최소 음성 구간 길이
```

**권장 값:**
- `threshold`: 0.3 ~ 0.7 (기본값: 0.5)
- `min_duration_off`: 0.0 ~ 1.0초 (짧은 간격 채우기)
- `min_duration_on`: 0.0 ~ 0.5초 (짧은 세그먼트 제거)

### 2. 화자 수 제약 조건 활용

화자 수를 알고 있는 경우, `num_speakers`, `min_speakers`, `max_speakers` 파라미터를 사용하여 정확도를 높일 수 있습니다:

```python
# 정확한 화자 수를 알고 있는 경우
result = diarization_pipeline(audio_data, num_speakers=4)

# 화자 수 범위를 알고 있는 경우
result = diarization_pipeline(audio_data, min_speakers=2, max_speakers=6)
```

**구현 위치:** `backend/worker/diarization_utils.py`의 `run_diarization` 함수

### 3. 임베딩 기반 후처리

현재 추출된 세그먼트 임베딩을 활용하여 화자 분리 결과를 개선할 수 있습니다:

#### 3.1 임베딩 유사도 기반 재할당

```python
def refine_speaker_assignment_with_embeddings(
    diarization_result: Annotation,
    segment_embeddings: list[dict],
    speaker_embeddings: dict[str, list[float]],
    similarity_threshold: float = 0.7,
) -> Annotation:
    """
    세그먼트 임베딩과 화자 임베딩 간의 코사인 유사도를 계산하여
    잘못 할당된 세그먼트를 재할당합니다.
    """
    from scipy.spatial.distance import cosine
    
    refined_result = Annotation()
    
    for seg_emb in segment_embeddings:
        segment_emb_vector = np.array(seg_emb['embedding'])
        best_speaker = seg_emb['speaker']
        best_similarity = -1
        
        # 각 화자 임베딩과 비교
        for speaker, speaker_emb_vector in speaker_embeddings.items():
            similarity = 1 - cosine(segment_emb_vector, np.array(speaker_emb_vector))
            if similarity > best_similarity:
                best_similarity = similarity
                best_speaker = speaker
        
        # 유사도가 임계값 이상인 경우에만 재할당
        if best_similarity >= similarity_threshold:
            refined_result[seg_emb['start']:seg_emb['end'], best_speaker] = True
    
    return refined_result
```

#### 3.2 짧은 세그먼트 병합

인접한 동일 화자 세그먼트를 병합하여 작은 오류를 수정:

```python
def merge_adjacent_segments(
    diarization_result: Annotation,
    max_gap_duration: float = 0.5,
) -> Annotation:
    """
    인접한 동일 화자 세그먼트를 병합합니다.
    max_gap_duration 이하의 간격은 같은 화자로 간주합니다.
    """
    merged_result = Annotation()
    
    # 화자별로 세그먼트 그룹화
    speaker_segments = {}
    for turn, _, speaker in diarization_result.itertracks(yield_label=True):
        if speaker not in speaker_segments:
            speaker_segments[speaker] = []
        speaker_segments[speaker].append((turn.start, turn.end))
    
    # 각 화자의 세그먼트를 시간순으로 정렬하고 병합
    for speaker, segments in speaker_segments.items():
        segments.sort(key=lambda x: x[0])
        merged_segments = []
        current_start, current_end = segments[0]
        
        for start, end in segments[1:]:
            if start - current_end <= max_gap_duration:
                # 간격이 작으면 병합
                current_end = end
            else:
                # 간격이 크면 새 세그먼트 시작
                merged_segments.append((current_start, current_end))
                current_start, current_end = start, end
        
        merged_segments.append((current_start, current_end))
        
        # 병합된 세그먼트를 결과에 추가
        for start, end in merged_segments:
            merged_result[start:end, speaker] = True
    
    return merged_result
```

### 4. 더 나은 임베딩 모델 사용

현재 `speaker-diarization-3.1`은 기본 임베딩 모델을 사용합니다. 더 정확한 임베딩 모델을 사용할 수 있습니다:

```python
# 더 정확한 임베딩 모델 사용
diarization_pipeline = DiarizationPipeline.from_pretrained(
    "pyannote/speaker-diarization-3.1",
    embedding="speechbrain/spkrec-ecapa-voxceleb@5c0be3875fda05e81f3c004ed8c7c06be308de1e"
)
```

**사용 가능한 임베딩 모델:**
- `speechbrain/spkrec-ecapa-voxceleb`: ECAPA-TDNN 기반, 높은 정확도
- `pyannote/embedding`: 기본 모델
- 커스텀 학습 모델

### 5. Resegmentation 파이프라인 사용

초기 화자 분리 결과를 개선하기 위해 `Resegmentation` 파이프라인을 사용할 수 있습니다:

```python
from pyannote.audio import Pipeline as ResegmentationPipeline

# 초기 화자 분리 수행
initial_diarization = diarization_pipeline(audio_data)

# Resegmentation으로 결과 개선
resegmentation_pipeline = ResegmentationPipeline.from_pretrained(
    "pyannote/segmentation-3.0"
)
refined_diarization = resegmentation_pipeline(
    audio_data,
    diarization=initial_diarization
)
```

### 6. 오디오 전처리

#### 6.1 노이즈 제거

화자 분리 전에 오디오에서 노이즈를 제거하면 정확도가 향상될 수 있습니다:

```python
import noisereduce as nr

# 노이즈 제거
reduced_noise = nr.reduce_noise(y=waveform, sr=sample_rate)
```

#### 6.2 정규화

오디오 레벨을 정규화하여 일관된 입력을 제공:

```python
# RMS 정규화
rms = np.sqrt(np.mean(waveform**2))
normalized_waveform = waveform / (rms + 1e-8) * 0.1
```

### 7. 앙상블 방법

여러 화자 분리 모델의 결과를 결합하여 정확도를 높일 수 있습니다:

```python
# 여러 모델로 화자 분리 수행
results = []
for model_name in ["pyannote/speaker-diarization-3.1", "pyannote/speaker-diarization-2.1"]:
    pipeline = DiarizationPipeline.from_pretrained(model_name)
    result = pipeline(audio_data)
    results.append(result)

# 결과 결합 (다수결 또는 임베딩 유사도 기반)
ensemble_result = combine_diarization_results(results)
```

## 구현 우선순위

1. **즉시 구현 가능 (높은 효과):**
   - 화자 수 제약 조건 추가 (`num_speakers`, `min_speakers`, `max_speakers`)
   - 임베딩 기반 후처리 (세그먼트 재할당)
   - 짧은 세그먼트 병합

2. **중기 구현 (중간 효과):**
   - Segmentation 파라미터 튜닝
   - 더 나은 임베딩 모델 사용
   - Resegmentation 파이프라인 적용

3. **장기 구현 (낮은 효과, 높은 비용):**
   - 앙상블 방법
   - 커스텀 모델 학습
   - 오디오 전처리 파이프라인 구축

## 참고 자료

- [pyannote.audio 공식 문서](https://github.com/pyannote/pyannote-audio)
- [Speaker Diarization 튜토리얼](https://github.com/pyannote/pyannote-audio/blob/develop/tutorials/pretrained/speaker_diarization.ipynb)
- [Clustering 파라미터 설명](https://github.com/pyannote/pyannote-audio/blob/develop/tutorials/pretrained/speaker_diarization.ipynb)

