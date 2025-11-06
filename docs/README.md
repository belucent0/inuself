# 문서 (Documentation)

연구, 테스트, 결과 분석 문서들을 모아놓은 폴더입니다.

## 문서 목록

### 연구 문서 (Research)

- **GPU_OPTIMIZATION_RESEARCH.md**: GPU 사용률 향상 방법 연구 결과
  - Windows 하드웨어 가속 GPU 스케줄링
  - PyTorch 최적화 설정
  - ROCm 성능 튜닝 방법

### 분석 문서 (Analysis)

- **BOTTLENECK_ANALYSIS.md**: 병목 지점 분석 결과
  - 프로파일링 실행 정보
  - CPU/GPU 사용률 분석
  - 병목 지점 식별 및 개선 방안

- **performance_analysis.md**: 실행 시나리오별 성능 분석
  - 14.75분 파일 테스트 결과
  - Cold start vs Warm start 성능 비교
  - 반복 실행 시 성능 변화 분석

### 테스트 결과 (Test Results)

- **PARALLEL_PROCESSING_RESULTS.md**: 병렬 처리 테스트 결과
  - 세그먼트 분할 처리 결과
  - 순차 처리 vs 병렬 처리 비교
  - 성능 향상 분석

- **transcription_comparison.md**: Whisper 모델 전사 결과 비교
  - large-v3, large-v3-turbo, large-v2 모델 성능 비교
  - 세그먼트별 전사 비교
  - 정확도 및 속도 분석

## 문서 구조

```
docs/
├── README.md                        # 이 파일
├── GPU_OPTIMIZATION_RESEARCH.md     # GPU 최적화 연구
├── BOTTLENECK_ANALYSIS.md           # 병목 지점 분석
├── performance_analysis.md          # 성능 분석
├── PARALLEL_PROCESSING_RESULTS.md   # 병렬 처리 결과
└── transcription_comparison.md      # 전사 결과 비교
```

