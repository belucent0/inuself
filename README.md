# AI 통합 플랫폼

> 음성 인식 · 문서 처리 · AI 대화 에이전트를 하나의 플랫폼에서 — AMD ROCm GPU + Ryzen AI NPU(선택)

---

## 시스템 아키텍처

```
Browser ── POST(stream=true) ──► Backend (FastAPI BFF)
   ▲                                  │ persist + Celery enqueue
   │ SSE                              ▼
   └──────── Pub/Sub relay ─────── Valkey
                                      │
                         ┌────────────┴────────────┐
                         ▼                         ▼
                Agent Worker Pool            Batch Worker
                (LangGraph)                  (ASR/OCR/요약)
                         │                         │
                         └──────────┬──────────────┘
                                    ▼
                              AI Gateway
                       profile routing / fallback
                           ┌────────┴────────┐
                           ▼                 ▼
                 FastFlowLM (NPU, 선택)   ai-* (ROCm GPU)

PostgreSQL: 대화 상태·5초 partial snapshot·최종 응답
Valkey: Celery broker·실시간 Pub/Sub·락/캐시 (응답 토큰 영속 저장소 아님)
```

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| **AI 대화 에이전트** | 전용 Celery Agent Worker에서 LangGraph 실행. Backend는 요청 접수·DB 영속화·SSE relay 담당 |
| **배치 음성 전사** | 음성·영상 파일 업로드 → ai-asr-vllm (Whisper-large-v3-turbo, ROCm GPU) 고정밀 전사 + ai-diarize 화자 분리, 상담 녹취록 처리 지원 |
| **화자 분리** | Pyannote.audio + AMD GPU (ROCm) 가속, 배치 고정밀 모드 |
| **문서 OCR & 요약** | PDF/이미지 → LLM Vision 처리, Celery 비동기 파이프라인 |
| **LLM 요약** | 전사 결과 · 문서 · 영상 콘텐츠를 LLM으로 구조화 요약 |
| **WPI 심리검사** | 5유형 × 5차원 성격 분석, AI 에이전트 활용, 검사 결과에 개인 맞춤형 마음읽기 해설 제공 |
| **YouTube 영상 처리** | 다운로드 → 전사 → 구조화 요약 |
| **웹 검색 통합** | SearXNG 기반 딥서치, 멀티턴 검색 재시도 |
| **AI 추론 게이트웨이** | ai-gateway (FastAPI + OpenAI SDK) — `RoutingProfile` 능력 필터, Provider health/circuit, NPU 1 · GPU 4 · Codex 2 동시성 제어, 조건부 serverless 폴백 |
| **옵저버빌리티** | Grafana + Prometheus + Loki + Tempo + Langfuse |

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| Frontend | Vite + React 19 + TypeScript + Tailwind CSS + shadcn/ui |
| Backend | FastAPI + SQLAlchemy |
| AI Agent | LangGraph + LangChain |
| Async Worker | Celery (Agent Worker + Batch Worker) |
| Database | PostgreSQL + pgvector · Valkey (Redis) · MinIO |
| Inference | ai-gateway (FastAPI) · FastFlowLM · vLLM · llama.cpp · transformers · pyannote |
| Infra | Docker Compose · Nginx |
| Observability | Grafana + Prometheus + Loki + Tempo + Langfuse |
| CI/CD | GitHub Actions (품질 게이트 4종 + 자동 태깅) |

---

## 문서

- 아키텍처 상세 → [`docs/architecture-v1.2.0.md`](docs/architecture-v1.2.0.md)
- AI 채팅 요청·SSE 복구 흐름 → [`docs/workflow-ai-chat.md`](docs/workflow-ai-chat.md)
- LLM RoutingProfile 운영 계약 → [`docs/routing-v2.md`](docs/routing-v2.md)
- NPU/GPU/Codex capacity 실증 → [`docs/benchmarks/routing-npu-gpu-overflow.md`](docs/benchmarks/routing-npu-gpu-overflow.md)
- 과거 Gemma 4 12B 벤치마크 → [`docs/benchmarks/gemma4-12b-vllm-mtp.md`](docs/benchmarks/gemma4-12b-vllm-mtp.md)
- GPU/ROCm 설치 가이드 → [`docs/archived/README-legacy.md`](docs/archived/README-legacy.md)

---

## 라이선스

MIT
