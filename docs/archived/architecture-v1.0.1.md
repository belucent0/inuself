# Architecture v1.0.1

> v1.0.0 기준 아키텍처를 유지하면서, LLM 티어 라우팅 레이어 책임 분리 및 lemonade-server GPU 라우팅을 반영한 문서

## v1.0.1 업데이트 요약 (2026-02-21)

### 핵심 변경

1. **lemonade-server 프로바이더 추가**: 요약 전용 GPU 서버 — `tier-summarize` 라우팅
2. **LLMTier 상수 도입**: 원시 문자열 → 열거형 상수, 레이어 책임 분리 강화
3. **model_copy 안티패턴 제거**: 5곳에서 `settings.model_copy(update={...})` → `model=` 파라미터 직접 전달
4. **CI 품질 게이트 안정화**: Langfuse eval fixture 기반 필터링, false positive 제거

### v1.0.0 → v1.0.1 변경 흐름

| 버전 | 핵심 변경 |
|------|----------|
| **v1.0.0** | Worker-Backend 책임 분리 강화, 상태머신 중심 전이, 콘텐츠 E2E 플로우 정식화 |
| **v1.0.1** | LLM 티어 라우팅 레이어 분리, lemonade-server GPU 요약 라우팅, CI eval 안정화 |

---

## 버전 히스토리

| 버전 | 변경 내용 |
|------|----------|
| V7.5 | Redis → Valkey 마이그레이션 |
| V8.0 | LangGraph 기반 AI Agent 시스템 |
| V8.2 | Langfuse LLM Observability |
| V8.4 | 검색 재시도 메커니즘 (LangGraph 루프) |
| V8.5 | SSE 실시간 진행률 + Frontend Vite 마이그레이션 |
| v1.0.0 | 콘텐츠 처리 토폴로지/상태머신/컴포넌트 책임 재정렬 |
| **v1.0.1** | **LLM 티어 라우팅 레이어 분리 + lemonade-server GPU 요약 라우팅** |

---

## LLM 티어 라우팅 아키텍처 (v1.0.1 신규)

### 설계 원칙

```
Backend (무엇이 필요한가)  →  LiteLLM Proxy (어떻게 라우팅)  →  Provider Manager (어디서 실행)
        tier 상수만                  tier→model 매핑               model→server 매핑
```

- **Backend**: `LLMTier` 상수만 결정 (`tier-simple`, `tier-thinking`, `tier-summarize`)
- **LiteLLM**: tier → 실제 모델명 매핑 (`litellm_config.yaml`)
- **Provider Manager**: 모델명 → 서버 엔드포인트 매핑

### LLMTier 상수

```python
# backend/app/core/llm_tier.py
class LLMTier(str, Enum):
    SIMPLE    = "tier-simple"     # 간단한 작업 (인사, 짧은 질문)
    THINKING  = "tier-thinking"   # 복잡한 분석 + CoT 추론
    SUMMARIZE = "tier-summarize"  # 요약 전용 (lemonade-server GPU)
```

`str` 상속으로 `LLMTier.SIMPLE == "tier-simple"` 투명 호환 보장.

### 티어 선택 흐름

```text
사용자 요청
  │
  ▼
TierRouter.select_tier(query, mode, context_size)
  │
  ├─ mode == "reasoning"     → tier-thinking
  ├─ context_size > 3000     → tier-thinking
  ├─ 복잡도 키워드 포함       → tier-thinking
  └─ 기본                    → tier-simple

요약 파이프라인 (별도)
  └─ litellm_model_summarize → tier-summarize (기본값)
```

### 관련 파일

- `backend/app/core/llm_tier.py` — LLMTier 열거형 + TIER_DISPLAY_MAP
- `backend/app/agents/tools/model_router.py` — TierRouter
- `backend/app/controllers/search_controller.py` — get_litellm_model()
- `backend/app/agents/nodes/intent_parser.py` — fallback tier 결정
- `infra/litellm/litellm_config.yaml` — tier → model 매핑
- `infra/shared/tier_config.py` — model → provider 매핑

---

## Docker ↔ Windows Host 토폴로지 (v1.0.1 업데이트)

```text
┌──────────────────────────────────────── Windows Host ───────────────────────────────────────────┐
│                                                                                                 │
│  ┌────────────────────┐                                                                         │
│  │  Provider Manager  │  (process lifecycle / health check / auto recovery / status share)     │
│  └─────────┬──────────┘                                                                         │
│            │ localhost HTTP                                                                      │
│            ├────────────▶ flm-asr (NPU, :11434)                                                 │
│            ├────────────▶ flm-llm (NPU, :11435)          ← tier-simple 기본                     │
│            ├────────────▶ flm-ocr (NPU, :11436)                                                 │
│            ├────────────▶ lemonade-server (GPU, :11437)  ← tier-summarize [v1.0.1 신규]        │
│            ├────────────▶ llama-server (GPU, :8080)       ← tier-thinking                      │
│            ├────────────▶ llama-ocr-server (GPU, :8081)                                         │
│            ├────────────▶ whisper-server (GPU, :8001)                                           │
│            ├────────────▶ insanely-fast-server (GPU, :8002)                                     │
│            └────────────▶ diarization-server (GPU, :8003)                                       │
│                                                                                                 │
│  Status/Event 공유: providers:status, stream:provider:events                                   │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 요약 파이프라인 (v1.0.1 업데이트)

### 2-Phase 요약 흐름

```text
StreamConsumer → LlmSummaryService
                      │
                      ├─ Phase 1: 메타데이터 추출
                      │     model = tier-simple (litellm_model 기본값)
                      │     → title / keywords / toc
                      │
                      └─ Phase 2: 요약 생성
                            model = tier-summarize  ← [v1.0.1] lemonade-server GPU
                            → core_summary / detailed_sections
```

### 변경 전/후

| 항목 | v1.0.0 | v1.0.1 |
|------|--------|--------|
| Phase 2 모델 | `tier-simple` (기본값 오타) | `tier-summarize` (lemonade GPU) |
| 모델 전달 방식 | `settings.model_copy(update={...})` | `model=` 파라미터 직접 전달 |
| 환경변수 기본값 | `LITELLM_MODEL_SUMMARIZE=tier-simple` | `LITELLM_MODEL_SUMMARIZE=tier-summarize` |

### 관련 파일

- `backend/app/services/llm_summary_service.py`
- `backend/app/services/phase_executor.py`
- `backend/app/services/section_executor.py`
- `backend/app/services/section_nodes.py`
- `backend/app/core/config.py` — `litellm_model_summarize` 기본값

---

## CI 품질 게이트 (v1.0.1 안정화)

### Langfuse Eval Gate

```text
PR 오픈
  └─ langfuse-eval-gate.yml
       ├─ setup_eval_dataset.py --reset  ← fixture 기준 동기화
       └─ run_eval.py
            ├─ fixture JSON에서 valid case_id 추출
            ├─ Langfuse 항목 중 valid case_id만 필터 (구 항목 무시)
            └─ 멀티턴 대화 실행 → PASS/FAIL
```

### 검증 전략

| 검증 유형 | 방법 | 비고 |
|---|---|---|
| 맥락 유지 | `context_check.must_reference_any` | 긍정 검증 (부정 키워드 지양) |
| 라우팅 모드 | `mode.expected` | simple / search / reasoning |
| 응답 품질 | `content_contains_any` | 정답 키워드 포함 여부 |

### 관련 파일

- `.github/workflows/langfuse-eval-gate.yml`
- `scripts/run_eval.py`
- `scripts/setup_eval_dataset.py`
- `tests/e2e/fixtures/chat_multiturn_cases.json`

---

## 개괄 아키텍처 (v1.0.0과 동일)

### Docker Compose 전체 서비스 토폴로지

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Docker                                                                                                      │
│                                                                                                             │
│  Client                                                                                                     │
│   │                                                                                                         │
│   ▼                                                                                                         │
│ ┌──────────┐      ┌──────────┐                                                                              │
│ │  nginx   │─────▶│ frontend │                                                                              │
│ └────┬─────┘      └──────────┘                                                                              │
│      │  /api,/ws,/events,/media,/grafana,/flower,/langfuse                                                 │
│      ▼                                                                                                      │
│ ┌──────────┐      ┌──────────┐      ┌──────────┐      ┌──────────┐                                        │
│ │ backend  │◀───▶│  valkey  │◀───▶│  worker  │─────▶│ litellm  │                                        │
│ │ FastAPI  │      │ streams/  │      │ celery   │      │ router   │                                        │
│ │ agents   │      │ pubsub    │      │ asr/ocr  │      │          │                                        │
│ └──┬───┬───┘      └──────────┘      └──┬───┬───┘      └──────────┘                                        │
│    │   │                                │   │                                                               │
│    │   └──────────────▶┌──────────┐     │   └──────────────▶┌──────────┐                                   │
│    │                   │ postgres │     │                   │  minio   │                                   │
│    └──────────────────▶│ +pgvector│     └──────────────────▶│   s3     │                                   │
│                        └──────────┘                         └──────────┘                                   │
│                                                                                                             │
│  Search                                                                                                     │
│   backend/agents ───────────────────────────────────────────────────────────────────────────────▶ searxng   │
│                                                                                                             │
│  Observability                                                                                              │
│   backend/worker ─▶ tempo(OTLP)                                                                            │
│   docker logs   ─▶ promtail ─▶ loki                                                                        │
│   metrics       ─▶ prometheus ─▶ grafana                                                                   │
│   llm traces    ─▶ langfuse                                                                                │
│   celery state  ─▶ flower                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 기술 스택 (v1.0.1)

### Backend

| 구성요소 | 기술 | 현재 포인트 |
|----------|------|-------------|
| Web Framework | FastAPI | API + SSE/WebSocket 엔드포인트 |
| Agent Framework | LangGraph | 검색 재시도 루프 포함 |
| LLM Gateway | LiteLLM | 티어 기반 모델 라우팅 |
| Tier 상수 | LLMTier(str, Enum) | SIMPLE / THINKING / SUMMARIZE |
| Queue/Event | Valkey + Celery + Streams/PubSub | 비동기 처리/이벤트 전달 |
| DB | PostgreSQL (+ pgvector) | 콘텐츠/채팅 영속화 |
| Object Storage | MinIO | 원본/결과 파일 저장 |
| State Machine | ContentStateMachine | 상태 전이 검증 |
| Observability | OTel/Tempo + Langfuse + Prometheus/Grafana | 이중 관측성 |

---

## 주요 파일 맵 (v1.0.1 추가분)

### 신규/변경 파일

- `backend/app/core/llm_tier.py` — LLMTier 열거형 (신규)
- `backend/app/agents/tools/model_router.py` — LLMTier 상수 적용
- `backend/app/agents/nodes/intent_parser.py` — LLMTier.SIMPLE fallback
- `backend/app/controllers/chat_controller.py` — LLMTier.SIMPLE default
- `backend/app/controllers/websocket_helper.py` — LLMTier.SIMPLE default
- `backend/app/controllers/search_controller.py` — 하드코딩 모델명 → LLMTier 상수
- `backend/app/services/llm_summary_service.py` — model_copy 제거
- `backend/app/services/phase_executor.py` — model_copy 제거
- `backend/app/services/section_executor.py` — model_copy 제거
- `backend/app/services/section_nodes.py` — model_copy 제거 (2곳)
- `backend/app/core/config.py` — litellm_model_summarize 기본값 수정
- `infra/litellm/litellm_config.yaml` — tier-summarize 라우팅 규칙 추가
- `infra/provider_manager/core/manager.py` — lemonade-server 프로바이더 추가

---

## 참고/아카이브

- `docs/archived/architecture-v1.0.0.md`
- `docs/search-retry-architecture.md`
- `docs/v8.4-search-retry-implementation.md`
- `infra/provider_manager/README.md`
