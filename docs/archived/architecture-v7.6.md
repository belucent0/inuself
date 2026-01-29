# Architecture V7.6

> Redis -> Valkey 마이그레이션 + OpenTelemetry + 관측성 스택 + NPU Thinking Model

## 버전 히스토리

| 버전 | 변경 내용 |
|------|----------|
| V6.6 | Redis Stream 기반 메시징 (Docker Desktop 크래시 해결) |
| V7.0 | Provider Manager 통합 프로세스 관리 |
| V7.3 | Provider Manager 패키지 구조화 + Consumer Group 자동 복구 |
| V7.4 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager, audio_gateway 제거 |
| V7.5 | Redis -> Valkey 마이그레이션 (라이선스/성능 이슈 대응) |
| **V7.6** | **FLM Thinking Model 지원, Client AI 모드 UI 전면 개편** |

---

## 개괄 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Docker                                                                          │
│                                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ Frontend │──▶│ Backend  │──▶│  Valkey  │──▶│  Worker  │──▶│ LiteLLM  │     │
│  │  :3000   │   │  :8000   │   │  :6379   │   │ (Celery) │   │  :4000   │     │
│  └──────────┘   └────┬─────┘   └──────────┘   └────┬─────┘   └────┬─────┘     │
│        │             │              │              │              │            │
│        │             │              │              │              │            │
│  ┌─────┴─────────────┴──────────────┴──────────────┴──────────────┴─────┐      │
│  │                      관 측 성   스 택                                 │      │
│  │  ┌────────┐  ┌────────────┐  ┌──────────┐  ┌──────────┐             │      │
│  │  │ Jaeger │  │ Prometheus │  │ Grafana  │  │  Flower  │             │      │
│  │  │ :16686 │  │   :9090    │  │  :3002   │  │  :5555   │             │      │
│  │  └────────┘  └────────────┘  └──────────┘  └──────────┘             │      │
│  └─────────────────────────────────────────────────────────────────────┘      │
│                                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                                    │
│  │  Nginx   │   │  MinIO   │   │PostgreSQL│                                    │
│  │   :80    │   │  :9000   │   │  :5432   │                                    │
│  └──────────┘   └──────────┘   └──────────┘                                    │
│                                                                                 │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │
                                        │ Redis Stream (TCP)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Host (Windows)                                                                  │
│                                                                                 │
│            ┌────────────────────────────────────────┐                          │
│            │      Provider Manager (V7.4+)          │                          │
│            │  - Redis Stream Consumer               │                          │
│            │  - HTTP API (:9998)                    │                          │
│            │  - Idle Timeout Manager                │                          │
│            │  - OpenTelemetry trace 전파            │                          │
│            └───────────────┬────────────────────────┘                          │
│                            │                                                    │
│         ┌──────────────────┼──────────────────┐                                │
│         ▼                  ▼                  ▼                                │
│   ┌───────────┐     ┌───────────┐     ┌───────────┐                           │
│   │ GPU (LLM) │     │ GPU (ASR) │     │ NPU (FLM) │                           │
│   │ :8080-81  │     │ :8001-03  │     │ :11434-37 │                           │
│   └───────────┘     └───────────┘     └───────────┘                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 세부 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ Docker Compose Services                                                                  │
│                                                                                          │
│ ┌── 애플리케이션 ──────────────────────────────────────────────────────────────────────┐ │
│ │                                                                                      │ │
│ │  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │ │
│ │  │   nginx      │     │   frontend   │     │   backend    │     │   litellm    │   │ │
│ │  │   :80        │────▶│   :3000      │     │   :8000      │────▶│   :4000      │   │ │
│ │  │              │     │   Next.js    │     │   FastAPI    │     │   Proxy      │   │ │
│ │  │ Reverse Proxy│     │   React      │     │   +Watchdog  │     │   Router     │   │ │
│ │  └──────────────┘     └──────────────┘     └──────┬───────┘     └──────┬───────┘   │ │
│ │         │                     │                   │                    │           │ │
│ │         └─────────────────────┴───────────────────┴────────────────────┘           │ │
│ │                                       │                                            │ │
│ └───────────────────────────────────────│────────────────────────────────────────────┘ │
│                                         │                                              │
│ ┌── 데이터/메시징 ──────────────────────│────────────────────────────────────────────┐ │
│ │                                       ▼                                            │ │
│ │  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │ │
│ │  │   valkey     │◀───▶│   postgres   │     │   minio      │     │   worker     │   │ │
│ │  │   :6379      │     │   :5432      │     │   :9000      │     │ Celery       │   │ │
│ │  │   + Streams  │     │   DB         │     │   S3 Storage │     │ ASR/LLM/OCR  │   │ │
│ │  └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘   │ │
│ │                                                                                    │ │
│ └────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
│ ┌── 관측성 스택 ─────────────────────────────────────────────────────────────────────┐ │
│ │                                                                                      │ │
│ │  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐   │ │
│ │  │   jaeger     │     │  prometheus  │     │   grafana    │     │   flower     │   │ │
│ │  │   :16686     │     │  :9090       │     │   :3002      │     │   :5555      │   │ │
│ │  │   OTLP:4317  │     │  Metrics     │     │   Dashboard  │     │   Celery UI  │   │ │
│ │  │   분산 추적  │     │  수집        │     │   시각화     │     │   모니터링   │   │ │
│ │  └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘   │ │
│ │                                                                                      │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
└──────────────────────────────────────────┬───────────────────────────────────────────────┘
                                           │
                                           │ Redis Stream (stream:gpu:*)
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ Host (Windows) - PM2 Managed                                                             │
│                                                                                          │
│  ┌── Provider Manager ─────────────────────────────────────────────────────────────────┐ │
│  │                                                                                      │ │
│  │   ┌─────────────────────────────────────────────────────────────────────────────┐   │ │
│  │   │  provider-manager (main.py)           npu-exporter (메트릭 수집)            │   │ │
│  │   │  - Redis Stream Consumer              - :9183                               │   │ │
│  │   │  - HTTP API (:9998)                   - GPU Compute → NPU 라벨링            │   │ │
│  │   │  - StreamProcessor                                                          │   │ │
│  │   │  - IdleManager                                                              │   │ │
│  │   │  - JobTracker                                                               │   │ │
│  │   │  - LogRotator                                                               │   │ │
│  │   │  - OpenTelemetry traceparent 전파                                           │   │ │
│  │   └─────────────────────────────────────────────────────────────────────────────┘   │ │
│  │                                        │                                            │ │
│  └────────────────────────────────────────│────────────────────────────────────────────┘ │
│                                           │                                              │
│  ┌── GPU Providers (On-Demand) ───────────┼────────────────────────────────────────────┐ │
│  │                                        ▼                                            │ │
│  │  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐            │ │
│  │  │ gpu-llm            │  │ gpu-asr            │  │ gpu-ocr            │            │ │
│  │  │                    │  │                    │  │                    │            │ │
│  │  │ llama-server :8080 │  │ whisper-cpp  :8001 │  │ llama-ocr    :8081 │            │ │
│  │  │ (LLM 요약)         │  │ (Speed Mode)       │  │ (Vision)           │            │ │
│  │  │                    │  │                    │  │                    │            │ │
│  │  │                    │  │ insanely     :8002 │  │                    │            │ │
│  │  │                    │  │ (Accuracy Mode)    │  │                    │            │ │
│  │  │                    │  │                    │  │                    │            │ │
│  │  │                    │  │ diarization  :8003 │  │                    │            │ │
│  │  │                    │  │ (Speaker)          │  │                    │            │ │
│  │  └────────────────────┘  └────────────────────┘  └────────────────────┘            │ │
│  │                                                                                      │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
│  ┌── NPU Providers (Always-On / On-Demand) ─────────────────────────────────────────────┐ │
│  │                                                                                      │ │
│  │  ┌────────────────────────────────────────────────────────────────────────────────┐ │ │
│  │  │                         FLM Server Group                                       │ │ │
│  │  │                                                                                │ │ │
│  │  │   flm-asr  :11434          flm-llm  :11435          flm-ocr  :11436           │ │ │
│  │  │   (whisper-v3:turbo)       (lfm2:2.6b)              (qwen3vl-it:4b)            │ │ │
│  │  │   NPU ASR                  NPU LLM                  NPU OCR                    │ │ │
│  │  │                                                                                │ │ │
│  │  └────────────────────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                                      │ │
│  └──────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 서비스 목록

### Docker Compose 서비스

| 서비스 | 컨테이너명 | 포트 | 역할 | 비고 |
|--------|-----------|------|------|------|
| **nginx** | asr-nginx | 80, 8080 | Reverse Proxy | traceparent 헤더 전달 |
| **frontend** | asr-frontend | 3000 | Next.js 웹 UI | OpenTelemetry 브라우저 추적 |
| **backend** | asr-backend | 8000 | FastAPI 메인 API | StateWatchdog, Reconciler |
| **worker** | asr-worker-unified | - | Celery 워커 | ASR/LLM/OCR 통합 태스크 |
| **litellm** | asr-litellm-proxy | 4000 | LLM 라우팅 프록시 | GPU/NPU 동적 선택 |
| **valkey** | asr-valkey | 6379 | 캐시 + Celery Broker | Valkey 9 (Redis 호환), Streams 메시징 |
| **postgres** | asr-postgres | 5432 | 메인 DB | 콘텐츠/트랜스크립트 저장 |
| **minio** | asr-minio | 9000, 9001 | S3 호환 스토리지 | 미디어 파일 저장 |
| **jaeger** | asr-jaeger | 16686, 4317, 4318 | 분산 추적 | OTLP gRPC/HTTP |
| **prometheus** | asr-prometheus | 9090 | 메트릭 수집 | GPU/NPU 모니터링 |
| **grafana** | asr-grafana | 3002 | 대시보드 | 관측성 시각화 |
| **flower** | asr-flower | 5555 | Celery 모니터링 | 읽기 전용 모드 |

### Host Providers (PM2 관리)

| 프로바이더 | 포트 | 모델/역할 | 관리 방식 | 그룹 |
|-----------|------|----------|----------|------|
| **provider-manager** | 9998 | Redis Stream 브릿지 | PM2 항상 실행 | - |
| **npu-exporter** | 9183 | NPU 메트릭 수집 | PM2 항상 실행 | - |
| **llama-server** | 8080 | LLM 요약 (Qwen3-4B) | On-Demand | gpu-llm |
| **llama-ocr-server** | 8081 | OCR Vision (Qwen3-VL-8B) | On-Demand | gpu-llm |
| **whisper-server** | 8001 | ASR Speed Mode | On-Demand | gpu-asr |
| **insanely-fast** | 8002 | ASR Accuracy Mode | On-Demand | gpu-asr |
| **diarization** | 8003 | Speaker Diarization | On-Demand | gpu-asr |
| **flm-asr** | 11434 | NPU ASR (whisper-v3:turbo) | Always-On | flm |
| **flm-llm** | 11435 | NPU LLM (lfm2:2.6b) | Always-On | flm |
| **flm-ocr** | 11436 | NPU OCR (qwen3vl-it:4b) | Always-On | flm |
| **flm-llm-thinking** | 11437 | NPU LLM (lfm2.5-tk:1.2b) | On-Demand | flm |

---

## 분산 추적 (OpenTelemetry)

### 트레이스 흐름

```
Frontend (asr-frontend)
    │ traceparent 헤더
    ▼
Nginx → Backend (asr-backend)
            │ Celery task + traceparent
            ▼
        Worker (asr-worker)
            │ Redis Stream + traceparent
            ▼
        Provider Manager
            │ HTTP + traceparent
            ▼
        GPU/NPU Server
            │
            ▼
        Jaeger (수집)
```

### OTLP 엔드포인트

| 서비스 | 환경변수 | 엔드포인트 |
|--------|---------|-----------|
| backend | OTEL_EXPORTER_OTLP_ENDPOINT | http://jaeger:4317 |
| worker | OTEL_EXPORTER_OTLP_ENDPOINT | http://jaeger:4317 |
| litellm | OTEL_EXPORTER_OTLP_ENDPOINT | http://jaeger:4317 |
| provider-manager | OTEL_EXPORTER_OTLP_ENDPOINT | http://localhost:4317 |
| frontend | /otlp/v1/traces | Nginx → Jaeger:4318 |

---

## 관측성 및 복구 기능

### Backend

| 컴포넌트 | 파일 | 역할 |
|---------|------|------|
| **StateWatchdog** | state_watchdog.py | 타임아웃 기반 STUCK 상태 감지 |
| **StateReconciler** | state_reconciler.py | DB 상태 일관성 복구 |
| **WatchdogScheduler** | watchdog_scheduler.py | 주기적 상태 검증 |
| **AdminController** | admin_controller.py | 시스템 관리 API |

### Provider Manager

| 컴포넌트 | 파일 | 역할 |
|---------|------|------|
| **IdleManager** | idle_manager.py | On-Demand 프로바이더 Idle Timeout |
| **LogRotator** | log_rotator.py | 로그 파일 자동 로테이션 |
| **Telemetry** | telemetry.py | OpenTelemetry trace 전파 |
| **JobTracker** | job_tracker.py | 작업 진행 상태 추적 (Redis Hash) |

---

## Redis 인터페이스

### Streams

| Stream | 용도 |
|--------|------|
| `stream:gpu:requests` | GPU 작업 요청 (Worker/LiteLLM → Provider Manager) |
| `stream:gpu:responses` | GPU 작업 결과 (Provider Manager → Worker/LiteLLM) |
| `stream:provider:requests` | 제어 요청 (상태 조회, 재시작) |
| `stream:provider:responses` | 제어 응답 |
| `stream:provider:events` | 상태 변경 이벤트 |

### Keys

| Key | 용도 |
|-----|------|
| `providers:status` | 프로바이더 실시간 상태 |
| `providers:jobs` | 프로바이더별 활성 작업 수 |
| `job:{id}` | 개별 작업 상세 정보 |

---

## 로드맵

| 버전 | 상태 | 계획 |
|------|------|------|
| V7.4 | ✅ 완료 | OpenTelemetry 분산 추적, StateWatchdog, IdleManager, audio_gateway 제거 |
| V7.5 | ✅ 완료 | Redis -> Valkey 마이그레이션 (라이선스/성능 대응) |
| V7.6 | ✅ 완료 | **FLM Thinking Model 지원, Client UI 전면 개편** |
| V8.0 | 예정 | Temporal 워크플로우 엔진, 멀티 호스트 지원 |
