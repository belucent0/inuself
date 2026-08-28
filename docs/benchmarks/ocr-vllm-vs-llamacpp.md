# OCR 백엔드 마이그레이션 + 벤치마크 — FLM(NPU) → dots.ocr → Qwen3-VL(llama.cpp) → vLLM Qwen3-VL

> 역사적 비교 기록입니다. 현재 운영 OCR은 별도 컨테이너 없이
> `ai-gateway → ai-llm(Gemma 4 26B A4B vision)` 경로를 사용합니다.

> 한 문장 요약: NPU(FLM)가 WSL에서 동작 안 해 GPU 컨테이너 백엔드로 마이그레이션 — dots.ocr는 chat prompt와 호환 안 됨 → Qwen3-VL 4B로 복구 → vLLM 통합으로 chat/요약/OCR 단일 모델로 단순화.

## 0. 배경 (Context)

### 환경
| 항목 | 값 |
|------|-----|
| OS | WSL2 (Windows 11 호스트) |
| GPU runtime | AMD ROCm 7.2.1 + `/dev/dxg` |
| GPU | gfx1150 (Radeon 890M, Strix Point iGPU) |
| VRAM 운영 한도 | **32 GB** |
| 컨테이너 | docker compose 기반 |

### 사용 시나리오
사용자가 이미지/PDF 업로드 → worker → ai-gateway → OCR 컨테이너 → HTML 형식 텍스트 추출. 모드 두 가지: `document` (텍스트 추출) / `portray` (이미지 묘사).

worker prompt 형태 (chat-style):
```
[System] 당신은 전문 OCR 시스템입니다. 응답은 순수 HTML 태그만 사용하세요. <h1>, <p>, <table>...
[User]   이미지 + "이 이미지의 모든 텍스트를 위에서 아래로 추출... HTML 헤더 태그 사용..."
```

---

## 1. 이전 운영 — FLM (NPU 백엔드, WSL 마이그레이션 이전)

가장 오래 운영한 OCR 백엔드는 **FLM(NPU)** 이었음. document/portray 모드 분기, HTML 태그 요구 같은 현재 worker prompt 구조도 NPU 시절부터 확립.

**한계 (전환 동기)**: NPU는 **WSL 환경에서 작동 안 함**. WSL2 + docker 컨테이너로 마이그레이션하면서 NPU 백엔드 사용 불가 → GPU 백엔드 검토 시작.

> worker config의 `ocr_provider: "flm"` default 값 및 docker-compose의 "FLM 종료로 silent fail" 코멘트는 이 시점 흔적.

**PoC만 진행하고 폐기한 후보**: PaddleOCR (실제 운영 X).

→ NPU 시절의 worker prompt(chat-style + HTML 태그)를 그대로 가져갈 수 있는 GPU 백엔드를 찾는 것이 컨테이너 마이그레이션의 출발점.

---

## 2. 컨테이너 1차 시도 — dots.ocr

`ggml-org/dots.ocr-GGUF` Q8 (**1.7B params OCR 전용 모델**, llama.cpp HIP build).

**선택 이유**: OCR 전용으로 학습되어 가볍고 빠를 것으로 기대 (Q8 양자화 + 작은 모델 + 한국어 학습 데이터 포함).

**메모리**: 3.9 GB (model 1.7 + mmproj 1.6 + KV/compute 0.6)

### 문제 발견 (2026-05-12, dev 컨텐츠 `019e1c58-...`)

> NPU 시절부터 확립된 worker prompt(chat-style + HTML 태그 요구)는 일반 VLM 가정. dots.ocr는 OCR 전용 학습이라 이 prompt를 따라가지 못함.

사용자가 이미지 OCR 요청 → silent fail:

```
ai-ocr 로그:
  prompt eval time = 7990.86 ms / 1573 tokens  ← 이미지+prompt 정상 처리
  eval time = 64.15 ms / 2 tokens              ← 생성 즉시 EOS

worker 로그:
  [OCR] [5/5] OCR completed: 0 chars extracted
  Status: 완료
```

→ 시스템상 "완료"인데 결과 빈 문자열. UI에선 "OCR 결과" 영역 비어있음. 사용자는 처리 실패한 줄 모름.

### 원인 분석

dots.ocr는 **OCR 전용 학습** 모델 — 입력: 이미지 → 출력: 이미지 안 텍스트.
worker의 chat-style prompt(system message + HTML 태그 요구)를 따라가지 못함 → 첫 토큰부터 EOS → 빈 응답.

**즉 dots.ocr 자체가 망가진 게 아니라, worker prompt와 dots.ocr 모델 가정이 어긋남.**

### 후보 평가

| 후보 | 평가 |
|------|------|
| worker prompt를 dots.ocr 호환 형식(단순 OCR 지시)으로 변경 | worker 코드 변경 필요. portray 같은 다른 케이스 불가 |
| **Qwen3-VL 4B 모델 교체** | 일반 VLM, chat instructions + HTML 응답 정상. 메모리 +3.5 GB |
| Qwen3-VL 8B | quality 잠재 ↑, BF16 17 GB는 한도 빡빡. AWQ INT4는 gfx1150에서 BF16보다 느림 (자체 측정 1.5 tok/s) |

---

## 3. 컨테이너 2차 시도 — `ai-ocr-qwen` 별도 컨테이너 (PR #144, 2026-05-12)

새 컨테이너 추가: `ai-ocr-qwen` (포트 18081, 기존 ai-ocr와 별도)

- 모델: `unsloth/Qwen3-VL-4B-Instruct-GGUF` Q4_K_M (2.5 GB) + `mmproj-F16.gguf` (836 MB)
- 인프라: ai-ocr 이미지 재사용 (llama.cpp HIP build), env로 모델 override
- ai-gateway `OCR_BASE_URL`을 `http://ai-ocr-qwen:8080`로 변경
- dots.ocr 컨테이너는 `profiles: ["legacy"]`로 보존 (단순 OCR 케이스 도입 시 재활용 후보)

### 측정 (screenshot.jpg, 한국어 텍스트 이미지)

| 백엔드 | 처리 시간 | 추출 글자 수 | 메모리 | 환각 |
|--------|----------|--------------|--------|------|
| dots.ocr (이전) | 13초 | **0 chars** | 3.9 GB | (silent fail) |
| **ai-ocr-qwen (Qwen3-VL 4B Q4)** | **45초** | **473 chars** | **5 GB** | 없음 |

→ 즉시 production 가능 수준.

### 단점 발견 — vision encoder 중복

- ai-ocr (dots.ocr) mmproj: 1.6 GB
- ai-ocr-qwen (Qwen3-VL) mmproj: 836 MB
- **다른 architecture라 공유 불가 → vision encoder만 약 2.4 GB 중복 점유**

또 chat/요약은 별도 vLLM(Qwen3-4B-Instruct), OCR은 별도 llama.cpp(Qwen3-VL 4B Q4) — **인프라 분리, Qwen3 모델이 두 컨테이너에 중복 메모리 점유**.

---

## 4. 컨테이너 3차 마이그레이션 — vLLM Qwen3-VL 통합 (PR #145, 2026-05-13)

### 동기
- vLLM 0.20.1+가 Qwen3-VL 정식 지원 (`Qwen3VLForConditionalGeneration`)
- chat/요약 vLLM이 이미 Qwen3-4B-Instruct 사용 중
- → **Qwen3-VL-4B로 교체하면 chat + 요약 + OCR이 한 모델·한 컨테이너로 통합**
- vision encoder 중복 해결 + 컨테이너 단순화

### 변경
- 이전: vLLM `Qwen3-4B-Instruct-2507` (text only, 14 GB)
- 신규: vLLM `Qwen3-VL-4B-Instruct` (text + vision, 14 GB — model 8.5 + KV 5.84)
- ai-gateway `OCR_BASE_URL`: `http://ai-ocr-qwen:8080` → `http://ai-llm:8000`
- ai-ocr-qwen 컨테이너 제거
- ai-ocr (dots.ocr)는 `profiles: ["legacy"]` 유지

### 필수 옵션 (vLLM 단)
```yaml
vllm serve Qwen/Qwen3-VL-4B-Instruct
  --dtype bfloat16
  --gpu-memory-utilization 0.30
  --max-num-seqs 3
  --limit-mm-per-prompt '{"image":1,"video":0}'                       # ← 없으면 OOM (아래 설명)
  --mm-processor-kwargs '{"min_pixels":50176,"max_pixels":1003520}'   # 224×224 ~ 1280×784
  --max-model-len 16384
```

**`--limit-mm-per-prompt` 가 필수인 이유**: vLLM의 vision encoder profiling은 worst-case 메모리를 추정하기 위해 **256 GB 할당을 시도**함 (Qwen3-VL이 임의 해상도 video까지 받을 수 있다고 가정). 옵션 없으면 startup 시 즉시 OOM. image=1, video=0 + max_pixels로 제한해야 합리적인 KV memory에 fit.

### 측정

| 항목 | screenshot.jpg | screenshot_claud.png |
|------|----------------|----------------------|
| 처리 시간 | 73초 | 4분 23초 |
| 추출 글자 수 | 350 chars | **3,814 chars** |
| Page split | — | 2회 호출 |
| 환각 | 없음 | 없음 |

### 종합 비교 (전체 마이그레이션)

| 항목 | dots.ocr (이전) | ai-ocr-qwen (PR #144 임시) | vLLM Qwen3-VL (PR #145 당시) |
|------|------------------|-----------------------------|------------------------|
| 모델 | dots.ocr Q8 (1.7B) | Qwen3-VL 4B Q4_K_M | **Qwen3-VL 4B BF16** |
| chat instructions | ❌ 미지원 | ✅ | ✅ |
| 환각 | (응답 자체 없음) | 없음 | 없음 |
| 컨테이너 | ai-ocr | ai-ocr + ai-ocr-qwen | **ai-llm 통합** |
| vision encoder 중복 | (단일) | 2.4 GB 중복 | 단일 |
| 인프라 통합도 | 분리 | 더 분리 | **단일 vLLM (chat+요약+OCR)** |

### 전체 시스템 메모리 변화

| 컴포넌트 | dots.ocr 시대 | ai-ocr-qwen 추가 시 | vLLM 통합 (PR #145 당시) |
|---------|---------------|----------------------|-------------------|
| vLLM (text/chat) | 14 GB | 14 GB | 14 GB (Qwen3-VL로 교체) |
| ai-ocr (dots.ocr) | 3.9 GB | 3.9 GB | (stop, legacy) |
| ai-ocr-qwen | — | 5 GB | (제거) |
| **합계 (안정)** | ~18 GB | ~23 GB | **~14 GB (9 GB 절약)** |

---

## 5. 당시 결정 — vLLM Qwen3-VL 4B BF16 채택

### 채택 이유
1. **chat/요약/OCR 단일 모델·단일 인프라** — 운영/모니터링/로깅 단순화
2. **vision encoder 중복 제거** — 메모리 9 GB 절약
3. **환각 없음, instruction following 정상** — production quality
4. **vLLM batched inference + paged attention** — 동시 요청 처리 효율

### 미채택 옵션
- **dots.ocr 호환 prompt로 worker 변경**: portray 같은 다른 케이스 불가능. 확장성 떨어짐
- **Qwen3-VL 8B**: gfx1150에서 AWQ INT4가 BF16보다 느림 (자체 측정 1.5 tok/s), BF16 17 GB는 한도 빡빡. **4B가 sweet spot**

---

## 6. 후속 전환 완료

- **2026-08**: OCR을 Gemma 4 26B A4B vision으로 전환하고 Qwen3-VL·dots.ocr·`ai-ocr*` 로컬 구성을 제거했다.

---

## 7. 참고

- **vLLM의 vision encoder profiling**: worst-case 256 GB 시도 → `--limit-mm-per-prompt` 필수 (없으면 OOM)
- **`max_pixels=1003520`** (1280×784) = Qwen-VL series 표준 max. 더 큰 이미지는 자동 분할
- **한 페이지 이미지**도 page split으로 2회 vLLM 호출 (encoder profile + decode)
- **gfx1150은 BF16 native** — Qwen3-VL HF publish dtype과 일치 (변환 손실 없음)
- **모델 캐시**: `hf-cache-fast` named volume

### 관련 PR
- PR #144 (머지): dots.ocr → ai-ocr-qwen(Qwen3-VL 4B Q4) 라우팅 변경
- PR #145 (머지): vLLM Qwen3-4B-Instruct → Qwen3-VL-4B-Instruct (chat/요약/OCR 통합) + ai-ocr-qwen 폐기
