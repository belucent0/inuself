# ASR 백엔드 마이그레이션 + 벤치마크 — Vulkan → transformers → whisper.cpp HIP → vLLM Whisper

> 역사적 비교 기록입니다. 현재 운영은 `ai-asr-vllm`(Whisper Large-v3-Turbo)과
> `ai-diarize`(pyannote community-1)입니다.

> 한 문장 요약: GPU 가속을 잃지 않으면서 컨테이너 기반 운영을 가능하게 하기 위한 ASR 백엔드 마이그레이션 흐름. WSL2 컨테이너에서 Vulkan GPU 추론 불가 → 컨테이너용 transformers ROCm은 너무 느림 → whisper.cpp HIP F16으로 속도 회복 → 환각 잔존 문제를 vLLM Whisper 통합으로 해소.

## 0. 배경 (Context)

### 환경
| 항목 | 값 |
|------|-----|
| OS | WSL2 (Windows 11 호스트) |
| GPU runtime | AMD ROCm 7.2.1 + `/dev/dxg` (WSL DirectX bridge) |
| GPU | gfx1150 (Radeon 890M, Strix Point iGPU) |
| VRAM 운영 한도 | **32 GB** (시스템 공유) |
| 현재 컨테이너 | docker compose 기반 (`ai-llm` / `ai-asr-vllm` / `ai-diarize` / `ai-embedding`) |
| ASR 모델 | Whisper Large-v3-Turbo (전 구간 동일, 추론 엔진만 교체) |

### 사용 시나리오
사용자가 dev.inuself.me에 오디오/영상 업로드 → worker(Celery)가 ai-gateway 통해 ASR 호출 → 화자분리(diarize) + LLM 요약 자동 처리.

요구 기준: RTF < 1.0, 환각/누락 최소, JSON 결과 구조화, **컨테이너 기반 운영**.

### 컨테이너 기반 운영이 필요한 이유
다른 추론 컴포넌트(LLM, OCR, 화자분리, embedding)와 동일 인프라 패턴으로 묶어 ai-gateway · worker · 모니터링을 일관되게 관리하기 위함. 호스트 직접 설치 방식은 OS 의존성/버전 관리/배포 자동화 측면에서 확장성 떨어짐.

---

## 1. 이전 운영 — whisper.cpp Vulkan (호스트 바이너리, 컨테이너 없음)

가장 오래 사용한 백엔드. `whisper.cpp`를 `-DGGML_VULKAN=ON`으로 빌드해 호스트에서 직접 실행. AMD GPU Vulkan 드라이버 사용해 GPU 가속.

**작동했던 점**:
- 속도/품질 production 수준
- AMD GPU 가속

**한계 (전환 동기)**:
- **WSL2 docker 컨테이너 안에서 Vulkan은 llvmpipe (CPU 소프트웨어 렌더링)만 작동** — AMD GPU 접근 불가
- WSL2 Vulkan ICD는 `radeon_icd.json` 있지만 enumerate 실패 (Native RADV는 `/dev/dri/*` 필요, WSL2는 `/dev/dxg`만 제공)
- Microsoft Dozen driver(D3D12→Vulkan bridge)는 별도 셋업 필요 + 검증 안 됨

→ 다른 서비스들과 동일하게 컨테이너 기반으로 운영하기 위해 GPU 가속을 유지할 수 있는 대안 필요.

---

## 2. 컨테이너 1차 시도 — transformers + PyTorch ROCm

base 이미지의 transformers Whisper pipeline 사용. SDPA / AOTriton Flash attention 시도.
선택 이유: 컨테이너에 가장 손쉽게 도입 가능 (이미지에 PyTorch + transformers 이미 포함).

### 문제 발견 (2026-05-08 ~ 11)

**실제 사례 — dev 컨텐츠 `019e0341-...` (영어 20분 영상)**:
- 업로드 후 **4일째 "인식중" 상태 hang**, 결과 안 나옴
- 측정: RTF 1.4 (실시간보다 느림) — Vulkan 시절 속도에 한참 못 미침
- `compression_ratio_threshold` 필터가 chunk 결과 다수 거부 → 텍스트 누락
- AOTriton Flash 켜면 환각 폭발 (`vanilla vanilla...` ×100 반복)
- 끄면 RTF는 회복 but 누락 그대로

→ 컨테이너 기반은 충족했지만 **속도가 production 사용 불가 수준**. Vulkan 시절 속도를 컨테이너 안에서도 회복할 방법 필요.

### 후보 평가

| 후보 | 결과 |
|------|------|
| Vulkan 재시도 (Dozen ICD 셋업) | ❌ WSL2 Vulkan은 llvmpipe(CPU)만 작동 — gfx1150 직접 access 안 됨. Dozen은 D3D12 위 에뮬레이션이라 ROCm 대비 빠를 보장 없음 |
| CTranslate2 ROCm (faster-whisper) | ❌ 빌드 실패 — davidguttman 패치(ROCm 6.x)가 ROCm 7.2의 thrust/hipblas API 변경과 호환 안 됨 |
| **whisper.cpp HIP** (ROCm native) | ✅ gfx1150 정상 작동, RTF 0.35로 Vulkan 시절 속도 회복 |

---

## 3. 컨테이너 2차 시도 — whisper.cpp HIP F16 (PR #143, 2026-05-12)

`-DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1150` cmake 빌드. 별도 컨테이너 `ai-asr` + FastAPI adapter(`/transcribe` ↔ whisper-server `/inference` 변환).

**선택 이유**: Vulkan 시절과 동일한 whisper.cpp 인프라(검증된 코드) + ROCm HIP 백엔드로 GPU 가속 회복 + 컨테이너 운영 가능. 세 요구사항 동시 충족.

### Quantization 선택 — F16 채택

| Quantization | RTF (tariff 14:45) | 메모리 | 환각 |
|--------------|--------------------|--------|------|
| Q5_0 | 0.414 | 0.75 GB | 가벼움 |
| Q8_0 | 0.352 | 1.1 GB | 심함 (`흥미를 응원해야...` ×11) |
| **F16** | **0.351** | **1.85 GB** | 최소 |

**왜 F16이 가장 빠른가**: gfx1150은 F16 native hw 가속이라 큰 모델이 오히려 빠름. Q4/Q8의 dequantization overhead가 GPU에서 손해. **= 양자화가 항상 빠르다는 통념의 반례**.

### 운영 후 발견된 잔여 문제 (한국어 tariff 측정)

- 끝부분 환각: "다음 영상에서 만나요" ×2 (chunk boundary)
- 본문 인식은 정상

→ production 가능 수준이지만 finetune 여지 있음.

---

## 4. 컨테이너 3차 PoC — vLLM Whisper 통합 검증 (이번 PR)

### 동기
PR #145에서 vLLM이 Qwen3-VL을 통합하면서 **vLLM이 chat/요약/OCR을 한 컨테이너에서 처리** 중. 만약 ASR도 vLLM에 들어가면 **인프라 단일화** + whisper.cpp 컨테이너 폐기 가능.

- vLLM 0.20.1+ 가 Whisper 정식 지원 (`WhisperForConditionalGeneration`)
- `/v1/audio/transcriptions` OpenAI 호환 endpoint
- `pip install librosa soundfile av` 추가 의존성만 필요

다만 vLLM batched 인프라가 gfx1150 ROCm Whisper(eager mode fallback)에서 효과 있을지 미검증 → PoC 진행.

---

### tariff 14:45 한국어 측정

| 항목 | whisper.cpp HIP F16 (이전) | vLLM Whisper BF16 |
|------|----------------------------|---------------------|
| 처리 시간 | 5:10 | 5:41 |
| RTF | 0.351 | 0.385 |
| 메모리 | 1.85 GB | 5.5 GB |
| 추출 글자 수 | 미측정 | 8,250 chars |
| 끝부분 환각 | "다음 영상에서 만나요" ×2 | 없음, "감사합니다." 자연 종료 ✅ |
| 인프라 | 별도 컨테이너 (whisper-server) | vLLM 단일 인프라 |

### Quality 비교 (vLLM 우위)

vLLM Whisper 결과 (앞/뒤):
- **시작**: "남주자 깐깐 남 인 뉴욕 시작하겠습니다..." — 한국어 자연스러움
- **본문**: 트럼프/관세/미국 경제 컨텐츠 정확
- **끝**: "트럼프 대통령을 응원해야 될 것인가 저주해야 될 것인가 굉장히 복잡한 순간입니다. **감사합니다.**" — 자연 종료

→ whisper.cpp의 환각 문제 해결.

### sample.wav (33s 한국어, 짧은 오디오) 보조 측정

| 백엔드 | 처리 시간 | RTF |
|--------|----------|-----|
| whisper.cpp adapter (server-resident) | 7.31s | **0.218** |
| vLLM Whisper | 13.32s | 0.397 |

→ 짧은 오디오는 whisper.cpp 1.8× 우위 (server-resident 모드 vs vLLM cold engine setup overhead).
→ 긴 오디오에서 격차 거의 사라짐.

### 종합 평가

| 측면 | 결과 |
|------|------|
| 속도 | 거의 동일 (vLLM 10% 느림, gfx1150 eager mode 한계) |
| 메모리 | whisper.cpp 우위 (-3.65 GB) |
| 정확도/환각 | vLLM 우위 (환각 없음) |
| 인프라 단순화 | vLLM 우위 (단일 vLLM 운영) |
| 구현 복잡성 | vLLM 더 단순 (`vllm serve` 한 줄) |

### gfx1150 + vLLM Whisper 한계
- `WARNING: CUDA graph is not supported for whisper on ROCm yet, fallback to eager mode`
- eager mode 영향으로 graph 최적화 없음 — gfx1150에서 vLLM Whisper가 약간 느린 주 원인
- vLLM CUDA graph가 ROCm Whisper로 확장되면 추가 속도 ↑ 기대

---

## 5. 결정 — vLLM Whisper 채택

### 옵션
- **A. vLLM Whisper 채택** — ai-asr를 vLLM Whisper로 교체. quality + 인프라 단순화. 메모리 +3.65 GB
- B. whisper.cpp 유지 — 메모리 효율, 약간 빠름. 환각 단점 감수
- C. 하이브리드 — text/요약/OCR은 vLLM Qwen3-VL, ASR은 whisper.cpp. 통합 가치 < 메모리 가치 손해

### 채택: A

**근거**:
1. **환각 해결** — production quality 핵심 가치 (whisper.cpp 마지막 잔여 문제)
2. **인프라 단일화** — chat/요약/OCR/ASR을 vLLM 한 인프라로 → 컨테이너 5개 → 4개, Dockerfile 1개 폐기 가능
3. **구현 단순화** — Dockerfile + adapter.py + entrypoint.sh + librocdxg.so + librocprofiler-sdk-stub.so 5개 파일 폐기. `vllm serve openai/whisper-large-v3-turbo` 한 줄로 대체
4. **속도 trade-off 작음** (RTF 0.35 → 0.39, 사용자 체감 X)
5. **메모리 여유** — +3.65 GB는 VRAM 32 GB 한도 내 (안정 22 GB → 25.7 GB, 마진 6 GB)

---

## 6. Production Migration 완료 (2026-05-14, `feat/vllm-whisper-production`)

PoC PR #146 이후 production 경로 전환. 핵심 변경: **worker 코드 무수정** — ai-gateway가 어댑터 책임.

- [x] `ai-asr-vllm` `profile=poc` 제거 (기본 활성)
- [x] whisper.cpp `ai-asr` 컨테이너는 legacy profile로 격리
- [x] ai-gateway `ASR_BASE_URL` → `http://ai-asr-vllm:8000`
- [x] ai-gateway `_handle_asr`: `/transcribe` → `/v1/audio/transcriptions` (OpenAI 호환) + verbose_json → worker 호환 포맷 변환
- [x] **language 옵셔널 분기 추가**: 빈 값/`auto`/`none` 수신 시 vLLM에 전달하지 않고 자동 감지 위임 → 영한 혼합/영어 단일 콘텐츠 환각 해결
- [x] ASR 런타임 의존성(`soundfile`, `av`)을 공용 `ai-llm-gemma4:0.26.0` 이미지에 포함

### 6.1 production 전환 후 검증 (2026-05-14)

ai-gateway 통한 end-to-end 흐름 검증.

| 파일 | 크기 | 길이 | RTF | 언어 | 환각 | 비고 |
|------|------|------|-----|------|------|------|
| test_10s.wav | 0.3 MB | 10s | 0.808 | ko 강제 | 없음 | 초기 검증 |
| sample.wav | 1 MB | 34s | 0.485 | ko 강제 | 없음 | 한국어 단편 |
| audio_for_whisper_tariff.wav (ko 강제) | 27 MB | 15min | 0.448 | ko 강제 | 없음 | 긴 한국어 |
| audio_for_whisper_tariff.wav (auto) | 27 MB | 15min | **0.375** | auto→ko | 없음 | **자동 감지가 더 빠름** |
| bigtech.mp4 (en 강제) | 16 MB | 6min | 0.463 | en **강제** | **있음** | "It's not a goal" 반복 환각 |
| bigtech.mp4 (auto) | 16 MB | 6min | 0.402 | auto→ko | 없음 | **자동 감지로 환각 해결** |
| I lost my 200000 job.mp4 (auto) | 76 MB | 20min | **0.340** | auto→en | 없음 | **이전 4일 hangs 컨텐츠 해결** |

핵심 발견: **vLLM Whisper는 mp4 binary 직접 수신 가능** (내부 ffmpeg 처리). ai-gateway 변환 불필요.

### 6.2 language 정책

| 시나리오 | 권장 language 인자 |
|---|---|
| 한국어 단일 | `ko` 또는 `auto` (둘 다 OK, auto가 약간 빠름) |
| 영어 단일 | `auto` (또는 `en`) |
| 영한 혼합 (한국어 위주) | **`auto`** — ko 강제 동급 결과 + 영어 단일 대응력 보너스 |
| 알 수 없음 | **`auto`** |

→ 운영 기본값을 `auto`로 가는 것이 합리적. ai-gateway는 이미 옵셔널 처리 — worker가 `auto`/빈값 송신 시 자동 감지 위임.

---

## 7. 동시성 측정 — vLLM continuous batching 효과 (2026-05-14)

### 7.1 동기

기존 whisper.cpp는 worker GPU lock으로 ASR 단일성을 강제 운영 중(`worker:gpu:active`). vLLM Whisper는 `--max-num-seqs 4`로 continuous batching 지원 → **동시 처리 시 GPU idle gap 활용해 throughput 향상 가능** 가설.

### 7.2 측정 — sample.wav (34s 한국어)

| N | total_time | avg_latency | throughput | vs N=1 | 이론 효율 |
|---|---|---|---|---|---|
| 1 | 18.89s | 18.89s | 0.053 req/s | 1.00× | - |
| **2** | **27.17s** | 27.05s | 0.074 req/s | **1.39×** | **70%** |
| 4 | 45.57s | 45.20s | 0.088 req/s | 1.66× | 42% |

### 7.3 측정 — audio_for_whisper_tariff.wav (15min 한국어, 본격 워크로드)

| N | total_time | avg_latency | throughput | vs N=1 | 이론 효율 |
|---|---|---|---|---|---|
| 1 | 338.5s | 338.5s | 0.00295 req/s | 1.00× | - |
| **2** | **455.22s** | 429.25s | 0.00439 req/s | **1.49×** | **75%** |

**관찰**: 긴 파일이 동시성 효율 더 좋음 (75% > 70%) — decode idle gap이 길수록 batching 효과↑.

품질: text_len 미세 차이(~1~4%)는 batched inference의 BF16 비결정성. segments 개수 거의 동일 → 누락/환각 아님.

### 7.4 4건 처리 풀세트 매트릭스 — whisper.cpp vs vLLM

동일 audio (tariff.wav, 15min 한국어) 4건 처리. ai-asr / ai-asr-vllm 컨테이너 동시 메모리 점유 상태에서 측정 (fair 비교).

| 백엔드 | N=1 (큐) | N=2 | N=3 | N=4 | 평균 |
|---|---|---|---|---|---|
| **whisper.cpp HIP** | 767.76s | 771.49s | 760.20s | 797.38s | **774s** |
| vLLM Whisper | ~1354s (추정 4×338.5) | ~910s (추정 2×455.22) | 1097.86s | **815.26s** | 1044s |

### 7.5 per-request 패턴 분석

#### whisper.cpp HIP — 서버 inference slot 1개 (직렬 처리)

N=3 예: req#0=199s, req#1=378s, req#2=567s, req#3=560s
```
t=0:    req#0,1,2 동시 호출 (req#1,2는 서버 큐 대기)
t=199:  req#0 끝 → req#1 처리 시작, req#3 진입
t=378:  req#1 끝 → req#2 처리 시작
t=567:  req#2 끝 → req#3 처리 시작
t=760:  req#3 끝
```

→ **동시성 N과 무관하게 ~770s** (서버 슬롯 1개 직렬 처리). 클라이언트가 동시 호출해도 whisper-server 내부에서 큐잉.

#### vLLM Whisper — continuous batching (병렬 처리)

N=3 비효율 패턴: req#1 먼저 끝(613s) → req#3 단독 처리(484s) → 마지막에 단일 처리 비효율

N=4 최적 패턴: 4건 모두 한 batch로 동시 처리 → 815s

### 7.6 발견 요약 (재정립)

1. **whisper.cpp HIP가 모든 동시성 케이스에서 vLLM보다 빠름** (770s vs 815s 최저, 6% 차이)
2. **단건 RTF**: whisper.cpp **0.213** vs vLLM **0.375** — whisper.cpp 단건 inference 1.8× 빠름 (gfx1150 native F16 hw 가속 + warm server-resident)
3. **vLLM의 동시 batching 능력으로도 단건 손해를 못 메움** (단일 노드 4건 환경)
4. **vLLM N=3은 비효율 패턴** — 4건 + 3 동시 = 끝에 1건 단독 처리로 N=4보다 느림

### 7.7 vLLM 채택 정당화 재정립

| 측면 | 우위 백엔드 | 비고 |
|---|---|---|
| 속도 (4건 워크로드) | **whisper.cpp HIP** | 평균 774s vs vLLM N=4 815s |
| 환각 해결 | **vLLM** | whisper.cpp는 5.7% 더 긴 텍스트(환각 추정) |
| 자동 언어 감지 | **vLLM** | bigtech/lostjob mp4 검증 |
| mp4 직접 처리 | **vLLM** | 내부 ffmpeg |
| 인프라 단순화 | **vLLM** | ai-llm과 동일 vLLM 스택 |
| 메모리 효율 | **whisper.cpp** | 1.85GB vs 5GB |

→ vLLM 채택 근거는 **속도 아님**. 인프라/품질/유연성. 속도가 최우선이면 whisper.cpp HIP 유지가 합리적.

### 7.8 동시 처리 시나리오별 비교 — 자원/사용자 관점

7.4 매트릭스의 절대 시간 비교는 "**1 컨테이너 vs 1 컨테이너**" 한정. 자원/사용자 관점으로 보면 vLLM의 동시 처리 메커니즘이 의미를 가진다.

#### 7.8.1 핵심 차이 — 1 인스턴스로 N건 동시 처리 가능

| 4건 동시 처리 시 자원 | whisper.cpp 매칭 | vLLM 1 인스턴스 |
|---|---|---|
| VRAM | **4 컨테이너 × 1.85GB = 7.4GB** | **5GB** |
| 컨테이너 수 | 4개 | 1개 |
| 라우팅 로직 | load balancer 필요 (nginx upstream 등) | 불필요 |
| 운영 관리 | 4× 컴플렉시티 | 단일 |

whisper.cpp는 서버 inference slot 1개라 다중 사용자 동시 처리 시 **N 인스턴스 띄워야 함**. 본 환경(iGPU 32GB UMA)에서 ai-llm 16GB와 함께 운영 시 자원 빠듯.

#### 7.8.2 시나리오 1 — 단일 사용자, 단발 요청

| 메트릭 | whisper.cpp HIP | vLLM Whisper |
|---|---|---|
| Latency | **192s** | 338s |

→ **whisper.cpp 압승**. 1.8× 빠른 응답.

#### 7.8.3 시나리오 2 — 4명 동시 요청, **단일 인스턴스만** 운영

| 메트릭 | whisper.cpp N=1 (직렬) | vLLM N=4 (batched) |
|---|---|---|
| 1번째 결과 | 192s | 815s |
| 4번째 결과 (max) | **768s** | 815s |
| 평균 latency | 480s | 815s |

→ **whisper.cpp 우위 (latency)**. 단, 첫 응답은 whisper.cpp가 빠르지만 max 응답은 비슷.

#### 7.8.4 시나리오 3 — 4명 동시 요청, **인스턴스 확장 허용**

| 메트릭 | whisper.cpp **4 인스턴스** | vLLM **1 인스턴스** |
|---|---|---|
| 모두 결과 | **192s (병렬)** | 815s |
| VRAM 점유 | 7.4GB | **5GB** |
| 컨테이너 수 | **4개** | 1개 |
| ai-llm(16GB) 동시 운영 가능성 | 빠듯 (16+7.4+1.5=24.9GB) | **여유 (16+5+1.5=22.5GB)** |

→ **latency는 whisper.cpp 4 인스턴스 압승**, **메모리/운영 단순성은 vLLM 우위**.

#### 7.8.5 시나리오 4 — 큐 누적된 peak 시간

큐 1000건 누적 시 단일 인스턴스 처리:
- whisper.cpp 1 인스턴스: 1000 × 192s = **53시간**
- vLLM 1 인스턴스 N=4: 1000/4 × 815s = **56시간**

→ 단일 인스턴스로는 둘 다 한계. **다중 인스턴스가 진짜 throughput 해결책**. 그러나 vLLM은 1 인스턴스에서 4 동시 처리 가능 → **적은 인스턴스로 같은 throughput 달성**.

#### 7.8.6 vLLM 동시 처리의 실질 장점 (정리)

| # | 장점 | 본 프로젝트 적용도 |
|---|---|---|
| 1 | **1 인스턴스로 N=4 동시 처리** | iGPU 32GB UMA에서 ai-llm 동시 운영 필수 → **메모리 절약 큰 가치** |
| 2 | **인프라 단일화** (라우터 불필요) | docker compose 1 서비스로 끝 |
| 3 | **확장성 곡선** | whisper.cpp는 flat, vLLM은 우상향. 다중 사용자 전망 시 의미 ↑ |
| 4 | **OpenAI API 표준** | 추후 다른 ASR 백엔드 교체 시 ai-gateway 코드 손대지 않음 |
| 5 | **모델/vLLM 버전 업그레이드 자동화** | vLLM 0.21+에서 CUDA graph ROCm 확장 시 자동 가속 |

#### 7.8.7 vLLM 동시 처리의 한계

| # | 한계 | 본 프로젝트 영향 |
|---|---|---|
| 1 | **단건 inference 절대 속도 느림** (RTF 0.375 vs 0.213) | 단일 사용자 latency 손해 |
| 2 | **동시 batching 효율 75%** | LLM(95%+ 효율)보단 낮음 — Whisper는 max_seq_len 448로 짧아서 |
| 3 | **N=3 같은 비균등 batch 비효율** | 4건 + N=3 = 끝에 1건 단독 처리, N=4보다 느림 |

#### 7.8.8 본 프로젝트 환경 종합 평가

iGPU 32GB UMA + ai-llm 동시 운영 + 사용자 수~수십 명 환경:

| 평가 항목 | 평가 |
|---|---|
| 단일 사용자 단발 호출 | whisper.cpp 우위 (latency 1.8× 빠름) |
| **다중 동시 사용자 처리 (자원 제한)** | **vLLM 우위** (1 인스턴스로 N=4, whisper.cpp는 4 인스턴스 필요) |
| 환각/품질 | vLLM 우위 |
| 인프라 단순화 | vLLM 우위 |
| 미래 확장성 | vLLM 우위 (OpenAI 표준, vLLM 버전 업, multi-modal 통합) |

→ **vLLM 채택은 "현재 단일 사용자 latency 손해 6%를 미래 확장성 + 자원 효율 + 품질에 투자"하는 의사결정.**

### 7.9 Triton 도입 효과 재검토 (실측 기반)

| 옵션 | 단일 노드 ASR throughput |
|---|---|
| whisper.cpp HIP (현 legacy) | **770s/4건 (최저)** |
| vLLM Whisper N=1 (현 운영) | 1354s/4건 |
| vLLM Whisper N=4 (lock 완화 시) | 815s/4건 |
| Triton + vLLM backend | **vLLM과 동급** (dynamic batching 동일 메커니즘) |

→ **단일 노드 ASR throughput 향상에 Triton 도입 정당화 불가**. Triton의 가치는 다른 곳 (다중 모델 통합 서빙, ensemble 파이프라인, 모델 라이프사이클 관리, 포트폴리오).

### 7.10 운영 정책 권고 (재정립)

#### worker GPU lock 정책

| 옵션 | 효과 | ROI |
|---|---|---|
| **현 N=1 lock 유지** | 안정 | **권장** — lock 완화 ROI 작음 |
| N=2 semaphore (vLLM) | +49% throughput | 그러나 whisper.cpp 큐 처리(770s) 못 이김 |
| lock 제거 | 이론상 최대 throughput | OOM 위험 + 효과 마진적 |

**권장: 현 N=1 lock 유지.** vLLM의 continuous batching 메커니즘은 존재하지만 단일 노드 단일 사용자 환경에선 실익 작음. Triton/Ray Serve 검토 시점에 다시 평가.

#### 대안 시나리오

| 운영 우선순위 | 권장 백엔드 |
|---|---|
| **속도 critical, 환각 감수** | whisper.cpp HIP 유지 (legacy → primary 회복) |
| **품질/인프라 우선** ← 현재 선택 | vLLM Whisper (현 운영) |
| 다중 사용자 (수십+ 동시) 전망 | vLLM Whisper + worker lock 완화 (continuous batching 실익) |

---

## 8. 참고

- **모델**: 전 구간 Whisper Large-v3-Turbo 유지. 추론 엔진만 교체.
- **vLLM 환경 변수**: `VLLM_MAX_AUDIO_CLIP_FILESIZE_MB` 기본 25 MB (큰 파일은 명시 필요 — tariff 28 MB라 200으로 설정).
- **vLLM 첫 콜드 스타트**: HF 다운로드 ~60s + KV cache + warmup ~130s = 약 4분.
- **모델 캐시**: `hf-cache-fast` named volume (현행 `ai-*` 추론 서비스가 공유).
- **CT2 ROCm 재시도 가치**: 낮음. 현재 whisper.cpp/vLLM 모두 production 수준이고, vLLM이 ASR도 통합하면 별도 추론 엔진 추가 필요 없음 (`docs/benchmarks/`에 별도 조사 결과 기록).

### 관련 PR
- 이전 (호스트 운영): whisper.cpp Vulkan (별도 PR 추적 없음 — 컨테이너 마이그레이션 이전 시기)
- PR #143 (머지): transformers ROCm → whisper.cpp HIP F16 (컨테이너 기반에서 속도 회복)
- PR #145 (머지): vLLM Qwen3-4B-Instruct → Qwen3-VL-4B-Instruct (chat/요약/OCR 통합)
- PR #146 (머지): vLLM Whisper PoC + 마이그레이션 history 문서
- PR #147 (머지): docs(ocr) NPU(FLM) 시절 history 보충
- **이번 PR (`feat/vllm-whisper-production`)**: vLLM Whisper production migration + 자동 언어 감지 + 동시성 측정
- 후속 PR (예정): worker GPU lock N=2 semaphore (ASR throughput 향상)
