# llama.cpp 메모리 사용량 설명

## 왜 시스템 RAM을 많이 사용하나요?

llama.cpp는 **KV 캐시(Key-Value Cache)**를 위해 상당한 메모리를 사용합니다.

### 1. KV 캐시란?

KV 캐시는 Transformer 모델이 이전 토큰들의 attention 정보를 저장하는 메모리입니다. 컨텍스트 윈도우가 클수록 더 많은 메모리가 필요합니다.

**메모리 사용량 계산:**
```
KV 캐시 메모리 ≈ (컨텍스트 길이) × (모델 차원) × (레이어 수) × (데이터 타입 크기) × 2
```

예를 들어:
- 컨텍스트 길이: 15000 토큰
- 모델 차원: 4096 (Qwen3-VL-30B 기준)
- 레이어 수: 30
- 데이터 타입: float16 (2 bytes)

대략적인 KV 캐시 메모리:
```
15000 × 4096 × 30 × 2 bytes × 2 (K + V) ≈ 7.2 GB
```

### 2. AMD 통합 메모리 아키텍처 (UMA)

AMD의 통합 메모리 아키텍처에서는:
- **시스템 RAM이 VRAM으로도 사용됩니다**
- GPU가 시스템 RAM의 일부를 공유 GPU 메모리로 사용
- 따라서 시스템 RAM 사용량이 증가합니다

현재 시스템 상태:
- 전용 GPU 메모리: 47.3 / 48.0GB (거의 가득 참)
- 공유 GPU 메모리: 17.7 / 23.8GB (시스템 RAM에서 할당)
- 시스템 메모리: 46.4 / 47.6GB (97% 사용)

### 3. 메모리 사용 구성

llama.cpp가 사용하는 메모리:

1. **모델 가중치**: ~18GB (Qwen3-VL-30B Q4_K_M)
2. **KV 캐시**: ~7-10GB (컨텍스트 길이에 비례)
3. **임시 버퍼**: ~2-5GB (추론 중 사용)
4. **mmproj (Vision 모델)**: ~2GB

**총 메모리**: 약 30-35GB

### 4. 메모리 최적화 방법

#### 컨텍스트 윈도우 줄이기

```env
# .env 파일
LLM_CONTEXT_LENGTH=15000  # 15016 → 15000으로 감소
LLAMA_SERVER_CTX_SIZE=15000  # llama.cpp 서버도 동일하게 설정
```

**효과**: KV 캐시 메모리 약 1-2GB 절약

#### 더 작은 컨텍스트 윈도우 사용

```env
LLM_CONTEXT_LENGTH=8192  # 더 작은 컨텍스트
LLAMA_SERVER_CTX_SIZE=8192
```

**효과**: KV 캐시 메모리 약 3-4GB 절약

#### 텍스트 전용 모델 사용

Qwen3-VL 대신 Qwen2.5-32B-Instruct 사용:
- mmproj 파일 불필요 (2GB 절약)
- Vision 기능 제거로 메모리 효율 향상

### 5. 메모리 모니터링

```bash
# GPU 메모리 확인
nvidia-smi  # NVIDIA GPU
# 또는 Windows 작업 관리자 → 성능 → GPU

# 시스템 메모리 확인
# Windows 작업 관리자 → 성능 → 메모리
```

### 6. 권장 설정

메모리가 부족한 경우:

```env
# 최소 메모리 사용
LLM_CONTEXT_LENGTH=8192
LLAMA_SERVER_CTX_SIZE=8192
LLAMA_SERVER_GPU_LAYERS=99  # 모든 레이어 GPU 사용 (VRAM 우선)
```

메모리가 충분한 경우:

```env
# 더 긴 컨텍스트 지원
LLM_CONTEXT_LENGTH=15000
LLAMA_SERVER_CTX_SIZE=15000
LLAMA_SERVER_GPU_LAYERS=99
```

## 참고

- KV 캐시는 컨텍스트 길이에 선형적으로 비례합니다
- AMD UMA 시스템에서는 시스템 RAM이 VRAM으로도 사용됩니다
- 컨텍스트를 줄이면 메모리 사용량이 크게 감소합니다





