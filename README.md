# AI 통합 플랫폼

> 음성 인식 · 문서 처리 · AI 대화 에이전트를 하나의 플랫폼에서 — AMD GPU/NPU 가속

---

## 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLIENT                                                             │
│  Browser (Vite + React + TypeScript)                                │
└────────────────────────┬────────────────────────────────────────────┘
                         │ HTTPS
═════════════════════════╪════════════════════════════════════════════
  Docker Environment     │
┌────────────────────────▼────────────────────────────────────────────┐
│  APPLICATION                                                        │
│  Backend (FastAPI) — Controllers → Services → Repositories          │
│  LangGraph AI Agent — 9 Nodes · 6 Tools · 5 Modes                   │
└──────────┬──────────────────────────────────┬───────────────────────┘
           │ Celery                            │ OpenAI SDK (httpx)
┌──────────▼──────────┐           ┌────────────▼──────────────────────┐
│  WORKER             │           │  AI GATEWAY                       │
│  Celery Workers     │──── HTTP ─▶  ai-gateway (FastAPI)             │
│  파일 전처리        │           │  routing · tier 매핑 · serverless │
│  (FFmpeg/PDF 변환)  │           └────┬───────────┬──────────┬───────┘
└─────────────────────┘                │ httpx     │ httpx    │ httpx
                                       ▼           ▼          ▼
┌─────────────────────────┐   ┌────────────────────────────────────────┐
│  DATA                   │   │  INFERENCE CONTAINERS (GPU/NPU)        │
│  PostgreSQL + pgvector  │   │  ai-llm        : vLLM (Gemma 4 E4B)    │
│  Valkey (Redis)         │   │  ai-asr        : Whisper-large-v3-turbo│
│  MinIO (S3) · SearXNG   │   │  ai-diarize    : pyannote community-1  │
└─────────────────────────┘   │  ai-ocr        : dots.ocr (llama.cpp)  │
                              │  ai-embedding  : EmbeddingGemma 308M   │
                              └────────────────────────────────────────┘
```

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| **AI 대화 에이전트** | LangGraph — 9 노드 · 6 도구 · 5 모드 (일반 / 딥서치 / 문서 / YouTube / WPI) |
| **실시간 음성 인식** | WebSocket 스트리밍 + NPU 가속 (FastFlowLM/Whisper), LLM 후처리 |
| **배치 음성 전사** | 음성·영상 파일 업로드 → whisper.cpp (GPU) 고정밀 전사 + 화자 분리, 상담 녹취록 처리 지원 |
| **화자 분리** | Pyannote.audio + AMD GPU (ROCm) 가속, 배치 고정밀 모드 |
| **문서 OCR & 요약** | PDF/이미지 → LLM Vision 처리, Celery 비동기 파이프라인 |
| **LLM 요약** | 전사 결과 · 문서 · 영상 콘텐츠를 LLM으로 구조화 요약 |
| **WPI 심리검사** | 5유형 × 5차원 성격 분석, AI 에이전트 활용, 검사 결과에 개인 맞춤형 마음읽기 해설 제공 |
| **YouTube 영상 처리** | 다운로드 → 전사 → 구조화 요약 |
| **웹 검색 통합** | SearXNG 기반 딥서치, 멀티턴 검색 재시도 |
| **AI 추론 게이트웨이** | ai-gateway (FastAPI + httpx) — 로컬 추론 컨테이너(vLLM · llama.cpp · transformers) 직결 호출, tier 기반 모델 매핑, serverless 폴백(Codex/RunPod) |
| **옵저버빌리티** | Grafana + Prometheus + Loki + Tempo + Langfuse |

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| Frontend | Vite + React 19 + TypeScript + Tailwind CSS + shadcn/ui |
| Backend | FastAPI + SQLAlchemy + Celery |
| AI Agent | LangGraph + LangChain |
| Database | PostgreSQL + pgvector · Valkey (Redis) · MinIO |
| Inference | ai-gateway (FastAPI) · vLLM · llama.cpp · transformers · pyannote |
| Infra | Docker Compose · Nginx |
| Observability | Grafana + Prometheus + Loki + Tempo + Langfuse |
| CI/CD | GitHub Actions (품질 게이트 4종 + 자동 태깅) |

---

## 문서

- 아키텍처 상세 → [`docs/architecture-v1.2.0.md`](docs/architecture-v1.2.0.md)
- GPU/ROCm 설치 가이드 → [`docs/archived/README-legacy.md`](docs/archived/README-legacy.md)

---

## 라이선스

MIT
