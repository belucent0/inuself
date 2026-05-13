# ASR 백엔드 마이그레이션 + 벤치마크 — Vulkan → transformers → whisper.cpp HIP → vLLM Whisper

> 한 문장 요약: GPU 가속을 잃지 않으면서 컨테이너 기반 운영을 가능하게 하기 위한 ASR 백엔드 마이그레이션 흐름. WSL2 컨테이너에서 Vulkan GPU 추론 불가 → 컨테이너용 transformers ROCm은 너무 느림 → whisper.cpp HIP F16으로 속도 회복 → 환각 잔존 문제를 vLLM Whisper 통합으로 해소.

## 0. 배경 (Context)

### 환경
| 항목 | 값 |
|------|-----|
| OS | WSL2 (Windows 11 호스트) |
| GPU runtime | AMD ROCm 7.2.1 + `/dev/dxg` (WSL DirectX bridge) |
| GPU | gfx1150 (Radeon 890M, Strix Point iGPU) |
| VRAM 운영 한도 | **32 GB** (시스템 공유) |
| 컨테이너 | docker compose 기반 (`ai-llm` / `ai-asr` / `ai-ocr` / `ai-diarize` / `ai-embedding`) |
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

## 6. 후속 작업 (별도 PR)

이번 PoC PR은 **벤치마크 + `docker-compose`에 `ai-asr-vllm` profile=poc 추가**만. Production migration은 별도 PR:

- [ ] `vllm[audio]` 의존성을 `ai-llm` 이미지에 baked-in (현재 PoC는 `pip install librosa soundfile av` 런타임 설치 — 컨테이너 recreate 시 사라짐)
- [ ] ai-gateway `ASR_BASE_URL`을 `http://ai-asr-vllm:8000`으로 전환
- [ ] worker가 `/v1/audio/transcriptions` 호출하도록 변경 (현재 `/transcribe` adapter API)
- [ ] whisper.cpp HIP 컨테이너(`ai-asr`)를 `profiles: ["legacy"]`로 보존 → 일정 기간 후 폐기

---

## 7. 참고

- **모델**: 전 구간 Whisper Large-v3-Turbo 유지. 추론 엔진만 교체.
- **vLLM 환경 변수**: `VLLM_MAX_AUDIO_CLIP_FILESIZE_MB` 기본 25 MB (큰 파일은 명시 필요 — tariff 28 MB라 200으로 설정).
- **vLLM 첫 콜드 스타트**: HF 다운로드 ~60s + KV cache + warmup ~130s = 약 4분.
- **모델 캐시**: `hf-cache-fast` named volume (vLLM/ai-ocr와 공유).
- **CT2 ROCm 재시도 가치**: 낮음. 현재 whisper.cpp/vLLM 모두 production 수준이고, vLLM이 ASR도 통합하면 별도 추론 엔진 추가 필요 없음 (`docs/benchmarks/`에 별도 조사 결과 기록).

### 관련 PR
- 이전 (호스트 운영): whisper.cpp Vulkan (별도 PR 추적 없음 — 컨테이너 마이그레이션 이전 시기)
- PR #143 (머지): transformers ROCm → whisper.cpp HIP F16 (컨테이너 기반에서 속도 회복)
- PR #145 (머지): vLLM Qwen3-4B-Instruct → Qwen3-VL-4B-Instruct (chat/요약/OCR 통합)
- 이번 PR #146 (PoC): vLLM Whisper 추가 검증 + 마이그레이션 history 문서
- 후속 PR (예정): vLLM Whisper production migration
