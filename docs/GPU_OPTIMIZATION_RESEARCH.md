# GPU 사용률 향상 방법 연구 결과

## 온라인 검색 결과 종합

### 1. Windows 하드웨어 가속 GPU 스케줄링 ⭐ (새로운 방법)

**설명:**
- Windows 10/11에서 제공하는 하드웨어 가속 GPU 스케줄링 기능
- GPU 작업 스케줄링을 하드웨어 레벨에서 처리하여 성능 향상

**활성화 방법:**
1. 설정 → 디스플레이 → 그래픽 설정
2. "하드웨어 가속 GPU 스케줄링" 켜기
3. 시스템 재부팅

**예상 효과:**
- GPU 작업 스케줄링 효율 향상
- 특히 여러 프로세스가 GPU를 사용할 때 효과적

**현재 상태:** 미적용 (테스트 필요)

---

### 2. 전력 관리 설정 최적화

**설정 방법:**
- 제어판 → 전원 옵션 → "고성능" 모드 선택
- GPU 전력 제한 해제 (가능한 경우)

**예상 효과:**
- GPU가 최대 클럭 속도로 동작
- Thermal throttling 감소

**현재 상태:** 확인 필요

---

### 3. 데이터 로딩 및 전처리 최적화

**현재 코드 분석:**
- `torchaudio.load()`로 오디오 로드
- `non_blocking=True`로 비동기 전송 (이미 적용됨)

**개선 가능한 부분:**
```python
# 현재: 단순 로드
audio_data, sample_rate = torchaudio.load(audio_file)

# 개선 옵션 1: pin_memory 사용 (CPU-GPU 전송 최적화)
# 단, 이미 GPU로 전송했으므로 효과 제한적

# 개선 옵션 2: 오디오 전처리 파이프라인 최적화
# pyannote 내부에서 처리하므로 직접 제어 어려움
```

**예상 효과:** 제한적 (이미 최적화됨)

---

### 4. 연산 그래프 최적화 (JIT 컴파일)

**PyTorch 옵션:**
1. `torch.jit.script()` - 스크립트 모드
2. `torch.jit.trace()` - 트레이스 모드
3. `torch.compile()` - PyTorch 2.0+ (Triton 필요, 현재 환경에서 실패)

**pyannote 적용 가능성:**
- pyannote 파이프라인은 동적 구조로 인해 JIT 컴파일 어려움
- 모델 자체는 이미 최적화되어 있을 가능성 높음

**테스트 가능한 방법:**
```python
# 모델을 JIT로 컴파일 시도 (실패 가능성 높음)
# pipeline._segmentation.model = torch.jit.script(pipeline._segmentation.model)
```

**예상 효과:** 낮음 (동적 구조로 인한 제약)

---

### 5. GPU 메모리 관리 최적화

**현재 코드:**
- `torch.cuda.empty_cache()` 사용 중
- `torch.inference_mode()` 사용 중

**추가 최적화:**
```python
# 메모리 할당 전략 변경
torch.cuda.set_per_process_memory_fraction(0.95)  # GPU 메모리 95% 사용

# 메모리 풀링 최적화
torch.cuda.memory.set_allocator_settings('expandable_segments:True')
```

**예상 효과:** 제한적 (현재 메모리 사용량이 낮음, 2GB/전체 용량)

---

### 6. 프로파일링 및 병목 지점 식별

**도구:**
- PyTorch Profiler (`torch.profiler`)
- AMD ROCm Profiler (rocprof)

**활용 방법:**
```python
from torch.profiler import profile, record_function, ProfilerActivity

with profile(
    activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
    record_shapes=True,
    profile_memory=True,
) as prof:
    with record_function("diarization"):
        diarization = pipeline(audio_data)

print(prof.key_averages().table(sort_by="cuda_time_total"))
```

**예상 효과:**
- 병목 지점 정확히 파악
- 최적화 방향 제시

**현재 상태:** 미적용 (테스트 필요)

---

### 7. 멀티 스트림 처리

**개념:**
- 여러 CUDA 스트림을 사용하여 작업 병렬화
- 데이터 전송과 연산을 동시에 수행

**적용 가능성:**
- pyannote는 내부적으로 처리하므로 직접 제어 어려움
- 단, 오디오 청크를 여러 스트림으로 처리하는 것은 가능할 수 있음

**예상 효과:** 중간 (구현 복잡도 대비 효과)

---

### 8. 오디오 청크 크기 조정

**현재 상황:**
- pyannote가 내부적으로 오디오를 청크로 나누어 처리
- 청크 크기는 모델/파이프라인 설정에 따라 결정

**개선 가능성:**
- pyannote 파이프라인 설정에서 청크 크기 조정 가능 여부 확인 필요
- 더 큰 청크 = 더 높은 GPU 사용률 (메모리 제약 내에서)

**테스트 방법:**
```python
# pyannote 파이프라인 설정 확인
print(pipeline._segmentation.inference)
print(pipeline._embedding.inference)

# 가능한 경우 청크 크기 조정
# pipeline._segmentation.inference.chunk_duration = 10.0  # 예시
```

**예상 효과:** 중간 (pyannote 내부 구조에 따라 다름)

---

## 우선순위별 적용 계획

### 즉시 테스트 가능 (높은 우선순위)

1. **Windows 하드웨어 가속 GPU 스케줄링 활성화** ⭐
   - 설정 변경만으로 가능
   - 시스템 재부팅 필요
   - 예상 효과: 중간-높음

2. **전력 관리 설정 확인**
   - 고성능 모드 확인
   - 예상 효과: 중간

3. **PyTorch Profiler로 병목 지점 식별**
   - 코드 수정 필요
   - 예상 효과: 높음 (병목 파악)

### 추가 검토 필요 (중간 우선순위)

4. **오디오 청크 크기 조정**
   - pyannote 내부 구조 확인 필요
   - 예상 효과: 중간

5. **멀티 스트림 처리**
   - 구현 복잡도 높음
   - 예상 효과: 중간

### 낮은 우선순위

6. **JIT 컴파일**
   - pyannote 동적 구조로 인해 어려움
   - 예상 효과: 낮음

7. **메모리 관리 최적화**
   - 현재 메모리 사용량이 낮아 효과 제한적
   - 예상 효과: 낮음

---

## 결론

**가장 효과적일 것으로 예상되는 방법:**
1. Windows 하드웨어 가속 GPU 스케줄링 활성화
2. PyTorch Profiler로 정확한 병목 지점 파악
3. 전력 관리 설정 최적화

**pyannote 특성상 제한적인 방법:**
- 배치 크기 조정 (이미 테스트 완료, 효과 미미)
- JIT 컴파일 (동적 구조로 인해 어려움)
- 멀티 스트림 (내부 처리로 인해 직접 제어 어려움)

**다음 단계:**
1. Windows 하드웨어 가속 GPU 스케줄링 활성화 테스트
2. PyTorch Profiler 추가하여 병목 지점 정확히 파악
3. 프로파일링 결과를 바탕으로 추가 최적화 방향 결정

