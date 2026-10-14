# AI 통합 플랫폼

> 음성 인식 · 문서 처리 · AI 대화 에이전트를 하나의 플랫폼에서 — AMD ROCm GPU + Ryzen AI NPU(선택)

현재 서비스는 콘텐츠 처리와 AI 대화에 집중합니다. 심리검사 화면·API·실행 자원은 제거했으며, 기존 기록의 보존 범위와 이관 자료는 [정리 기록](docs/assessment-removal.md)에 남겼습니다.

---

## 시스템 아키텍처

```
Browser ── POST(stream=true) ──► Backend (FastAPI BFF)
   ▲                                  │ persist + Celery enqueue
   └──── SSE (Backend relay) ──────────┤
                                      ▼
                                    Valkey
                              Pub/Sub · Celery broker
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
| CI | GitHub Actions (빌드·회귀 테스트·배포된 서버의 품질 평가·자동 태깅) |

---

## 코드 구성

| 경로 | 역할 |
|------|------|
| [`frontend/`](frontend/) | React 화면·라우팅·API/SSE 클라이언트 |
| [`backend/app/`](backend/app/) | FastAPI API·인증·DB·LangGraph 에이전트·Agent Worker |
| [`backend/alembic/`](backend/alembic/) | DB 스키마 마이그레이션 |
| [`worker/`](worker/) | 음성·문서·요약을 처리하는 Celery Batch Worker |
| [`infra/ai-gateway/`](infra/ai-gateway/) | 추론 요청 라우팅·Provider 상태·동시성 제어 |
| [`infra/inference/`](infra/inference/) | LLM·ASR·임베딩·화자 분리 서버와 GPU 이미지 구성 |
| [`infra/`](infra/) | Nginx·검색·관측 도구 설정 |
| [`tests/`](tests/), [`backend/tests/`](backend/tests/), [`worker/tests/`](worker/tests/) | 영역별 회귀 테스트 |
| [`scripts/`](scripts/), [`.github/workflows/`](.github/workflows/) | 운영·품질 검증 스크립트와 CI 정의 |

## 개발 시작

UI 빌드·단위 테스트와 전체 AI 서비스 실행은 준비 범위가 다릅니다. CI와 같은 **Node.js 22, Python 3.12**를 기준으로 시작합니다. Backend의 지원 범위는 [의존성 정의](backend/pyproject.toml)의 Python `>=3.12,<3.14`입니다.

### Frontend

저장소 루트에서 실행합니다.

```powershell
cd frontend
npm ci
npm run dev
```

개발 서버는 `http://localhost:5173`입니다. [Vite 설정](frontend/vite.config.ts)은 `/api`를 `localhost:8000`으로, `/media`를 로컬 MinIO로 프록시합니다. 화면에서 로그인·콘텐츠 조회 등을 사용하려면 Backend와 데이터 서비스가 준비되어 있어야 합니다. Compose의 Frontend 포트 `3000`과는 구분합니다.

### Backend

저장소 루트에서 개발용 가상환경을 준비합니다. 아래는 Windows PowerShell 기준이며, 의존성 설치는 CI와 같은 방식입니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "./backend[dev]"
```

Backend는 저장소 루트의 `.env`를 읽습니다. [`.env.example`](.env.example)을 참고해 `.env`를 만들고, [설정 모델](backend/app/core/config.py)에 맞춰 개발용 PostgreSQL·Valkey·MinIO의 주소와 자격 증명을 구성합니다. `SIGNUP_ACCESS_CODE`는 필수이며 비워 두면 Backend가 시작되지 않습니다. AI 연결에는 `AI_GATEWAY_URL`·`AI_GATEWAY_API_KEY`를 설정하고, `SESSION_COOKIE_SECURE`는 로컬 HTTP에서 `false`, HTTPS 운영 환경에서 `true`로 지정합니다.

Gateway의 `/v1/chat/completions`(OCR·ASR 포함), `/v1/embeddings`, `/health/readiness`는 `Authorization: Bearer <AI_GATEWAY_API_KEY>`를 요구합니다. Gateway·Backend·Agent Worker·Batch Worker와 평가·벤치마크 클라이언트에 같은 비어 있지 않은 키를 설정한 뒤 함께 재시작합니다. Gateway 키가 없으면 해당 경로는 503, 호출자 키가 없거나 다르면 401을 반환합니다. Docker 생존 점검용 `/health`와 `/health/liveliness`는 인증 없이 사용할 수 있습니다. 자세한 이관 절차는 [라우팅 운영 계약](docs/routing-v2.md#5-readiness와-배포-불변식)을 참고합니다.

로컬 프로세스와 Docker 컨테이너에서 사용하는 서비스 주소가 다르므로 예제 값을 그대로 사용하지 않습니다. 파일 변환에는 FFmpeg, YouTube 다운로드에는 Deno 등 [Backend 이미지](backend/Dockerfile)의 실행 의존성도 필요합니다.

**개발용 DB와 저장소·큐 서비스가 준비된 뒤**, `.env`가 해당 개발 환경을 가리키는지 확인하고 실행합니다. 마이그레이션 명령은 지정한 DB의 스키마를 변경합니다.

```powershell
cd backend
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

API 서버만 실행해도 AI 작업이 처리되는 것은 아닙니다. 대화에는 Agent Worker, 콘텐츠 처리에는 Batch Worker가 필요하며, 각 작업이 사용하는 AI Gateway·추론 서버·검색 서비스도 연결되어야 합니다. 서비스별 실행 명령과 연결은 [`docker-compose.yml`](docker-compose.yml), 전체 흐름은 [아키텍처 문서](docs/architecture-v1.3.0.md)를 기준으로 확인합니다.

### 전체 서비스 실행 전 확인

현재 Compose는 **Windows/WSL의 AMD GPU 운영 환경**을 반영합니다. `/dev/dxg`, WSL GPU 라이브러리, 로컬 ROCm 보조 라이브러리 마운트가 있고, `ai-diarize:1.0.0`과 `langfuse-custom:latest`는 Compose 안에 빌드 정의가 없는 이미지입니다. 필요한 이미지·모델 접근 권한·호스트 파일을 먼저 준비해야 하므로, 새로 복제한 저장소에서 `docker compose up`만으로 전체 구성이 실행되는 설치 패키지는 아닙니다. NPU는 선택 사항입니다.

Backend 컨테이너는 `uv.lock`으로 런타임 의존성을 설치하고 시작 시 DB 마이그레이션을 실행합니다. 실제 구성은 [Compose](docker-compose.yml)와 각 서비스의 Dockerfile을 확인하며, [과거 설치 기록](docs/archived/README-legacy.md)은 현재 설치 절차가 아닌 참고 자료로 사용합니다.

AI Gateway는 기존 `OTEL_EXPORTER_OTLP_ENDPOINT`가 있을 때 추론 요청의 기본 trace를 전송합니다. 요청·응답 본문, 헤더, query, 예외 원문은 수집하지 않으며 경로·상태·검증한 라우팅 값·설정된 모델/Provider만 기록합니다. Endpoint가 없으면 꺼져 있고, Collector 장애가 추론을 막도록 연결하지 않습니다. 범위와 운영 제한은 [라우팅 계약](docs/routing-v2.md#8-gateway-기본-trace)을 참고합니다.

## 검증과 CI

Frontend 디렉터리에서 CI와 같은 검사 명령을 실행할 수 있습니다.

```powershell
npm run test:auth-session
npm run test:chat-stream
npm run build
```

Backend 개발 의존성을 설치한 가상환경에서는 저장소 루트에서 다음 에이전트 단위 테스트를 실행합니다. 이 테스트는 실제 LLM이나 라이브 서버를 호출하지 않습니다.

```powershell
python -m pytest tests/unit/agents/ -v --tb=short
```

추가 Backend·Worker·Gateway 검증 대상과 의존성은 [Release Readiness Gate](.github/workflows/release-readiness-gate.yml)에 정의되어 있습니다. 테스트 경로를 명시해 실행하며, 위 명령 하나가 전체 테스트를 대체하지는 않습니다.

현재 [워크플로 파일](.github/workflows/)은 7개이며 실행 조건은 서로 다릅니다.

| 실행 조건 | 워크플로 |
|-----------|----------|
| `develop`·`main`의 비초안 PR | Release Readiness Gate, Agent Behavior Gate, Langfuse Eval Gate |
| `main`의 비초안 PR | Quality Regression Gate 추가 실행 |
| `main` push | Quality Baseline Snapshot, Auto Tag |
| 정기 실행 | Quality Baseline Snapshot |
| 수동 실행 | 품질·E2E 검증 워크플로 (`workflow_dispatch`) |

Langfuse Eval Gate는 self-hosted runner에서 `E2E_BASE_URL`에 지정된 **이미 배포된 HTTPS 서버**를 검사합니다. 이 검사가 PR 코드를 해당 서버에 배포하는 것은 아닙니다. CI 통과는 실행된 검사의 성공을 뜻하며, README 자동 갱신이나 운영 배포 완료를 뜻하지 않습니다. 문서는 변경 내용과 대조해 별도로 리뷰합니다.

---

## 문서

- 아키텍처 상세 → [`docs/architecture-v1.3.0.md`](docs/architecture-v1.3.0.md)
- 심리검사 기능·자원 제거 및 원본 보관 위치 → [`docs/assessment-removal.md`](docs/assessment-removal.md)
- AI 채팅 요청·SSE 복구 흐름 → [`docs/workflow-ai-chat.md`](docs/workflow-ai-chat.md)
- LLM RoutingProfile 운영 계약 → [`docs/routing-v2.md`](docs/routing-v2.md)
- NPU/GPU/Codex capacity 실증 → [`docs/benchmarks/routing-npu-gpu-overflow.md`](docs/benchmarks/routing-npu-gpu-overflow.md)
- 과거 Gemma 4 12B 벤치마크 → [`docs/benchmarks/gemma4-12b-vllm-mtp.md`](docs/benchmarks/gemma4-12b-vllm-mtp.md)
- 과거 GPU/ROCm 설치 기록 → [`docs/archived/README-legacy.md`](docs/archived/README-legacy.md)
