# Architecture V7.3

> Redis Stream 기반 메시징 아키텍처 + Provider Manager 패키지 구조화

## 개요

Docker Desktop 크래시 문제 해결을 위해 `host.docker.internal` HTTP 통신을 Redis Stream 메시징으로 대체한 아키텍처.

## 핵심 변경사항

| 버전 | 변경 내용 |
|------|----------|
| V6.6 | Redis Stream 기반 메시징 도입 (Docker Desktop 크래시 해결) |
| V7.0 | Provider Manager로 통합 프로세스 관리 |
| V7.3 | Provider Manager 패키지 구조화 + Consumer Group 자동 복구 |

---

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Docker                                                                       │
│                                                                              │
│  ┌──────────┐     ┌──────────┐     ┌───────────┐     ┌──────────┐          │
│  │ Frontend │────▶│ Backend  │────▶│   Redis   │────▶│  Worker  │          │
│  │ Next.js  │     │ FastAPI  │     │  (Celery) │     │ (Celery) │          │
│  │  :3000   │     │  :8000   │     │  :6379    │     │          │          │
│  └──────────┘     └──────────┘     └───────────┘     └────┬─────┘          │
│                                                           │                 │
│                   ┌──────────┐           ┌────────────────┘                 │
│                   │ LiteLLM  │           │                                  │
│                   │  :4000   │           │                                  │
│                   └────┬─────┘           │                                  │
│                        │                 │                                  │
│                        └────────┬────────┘                                  │
│                                 │                                           │
│                                 ▼                                           │
│                        ┌────────────────┐                                   │
│                        │  Redis Stream  │                                   │
│                        │                │                                   │
│                        │ stream:gpu:requests                                │
│                        │ stream:gpu:responses                               │
│                        └───────┬────────┘                                   │
│                                │                                            │
└────────────────────────────────│────────────────────────────────────────────┘
                                 │
                                 │ TCP (안정적, Docker Desktop 크래시 방지)
                                 │
┌────────────────────────────────│────────────────────────────────────────────┐
│ Host (Windows)                 ▼                                            │
│                    ┌─────────────────────────────────────┐                  │
│                    │       Provider Manager (V7.3)       │                  │
│                    │                                     │                  │
│                    │  - Redis Stream Consumer            │                  │
│                    │  - HTTP API (:9998)                 │                  │
│                    │  - 프로세스 수명주기 관리            │                  │
│                    │  - 자동 복구 (Health Check)         │                  │
│                    │  - 좀비 프로세스 방지               │                  │
│                    └──────────────┬──────────────────────┘                  │
│                                   │                                         │
│                        localhost  │                                         │
│         ┌─────────────────────────┼─────────────────────────┐              │
│         │                         │                         │              │
│         ▼                         ▼                         ▼              │
│  ┌─────────────┐          ┌─────────────┐          ┌─────────────┐        │
│  │  GPU (LLM)  │          │  GPU (ASR)  │          │  NPU (FLM)  │        │
│  │             │          │             │          │             │        │
│  │ llama-server│          │ whisper.cpp │          │ flm-asr     │        │
│  │   :8080     │          │   :8001     │          │   :11434    │        │
│  │             │          │             │          │             │        │
│  │ llama-ocr   │          │ insanely    │          │ flm-llm     │        │
│  │   :8081     │          │   :8002     │          │   :11435    │        │
│  │             │          │             │          │             │        │
│  │             │          │ diarization │          │ flm-ocr     │        │
│  │             │          │   :8003     │          │   :11436    │        │
│  └─────────────┘          └─────────────┘          └─────────────┘        │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 통신 흐름

### ASR 요청 (Worker → GPU)

```
1. Worker (Docker)
   │
   │  Redis Stream XADD
   │  stream:gpu:requests
   │  {task_type: "asr", audio_data: base64, ...}
   │
   ▼
2. Provider Manager (Host)
   │
   │  Redis Stream XREADGROUP (Consumer Group)
   │  작업 디스패치
   │
   ▼
3. GPU Server (localhost:8001)
   │
   │  HTTP POST /v1/audio/transcriptions
   │
   ▼
4. Provider Manager
   │
   │  Redis Stream XADD
   │  stream:gpu:responses
   │  {request_id: "...", result: {...}}
   │
   ▼
5. Worker (Docker)
   │
   │  Redis Stream XREAD (응답 대기)
   │
   ▼
6. 처리 완료
```

### LLM 요청 (LiteLLM → GPU)

```
1. LiteLLM (Docker)
   │
   │  Redis Stream XADD
   │  stream:gpu:requests
   │  {task_type: "llm", messages: [...], ...}
   │
   ▼
2. Provider Manager (Host)
   │
   │  localhost HTTP → llama-server:8080
   │
   ▼
3. 응답 → Redis Stream → LiteLLM
```

---

## Provider Manager (V7.3)

### 패키지 구조

```
infra/provider_manager/
├── main.py                 # 진입점 (FastAPI + Stream 처리)
├── core/
│   ├── config.py           # 설정 (Redis, 포트, 타임아웃)
│   └── manager.py          # ProviderManager (프로세스 관리)
├── api/
│   └── routes/
│       ├── providers.py    # /providers/* 엔드포인트
│       ├── groups.py       # /groups/* 엔드포인트
│       ├── health.py       # /health, /metrics
│       └── jobs.py         # /jobs/* 엔드포인트
├── services/
│   ├── stream_processor.py # Redis Stream 처리
│   ├── job_tracker.py      # 작업 추적 (Redis Hash)
│   └── provider_service.py # 비즈니스 로직
├── models/
│   └── schemas.py          # Pydantic 모델
└── clients/
    └── control_client.py   # 외부에서 호출용 클라이언트
```

### 주요 기능

| 기능 | 설명 |
|------|------|
| 프로세스 관리 | 그룹별 시작/종료, PID 추적 |
| 좀비 방지 | PID 파일 + 포트 스캔 + Redis 상태 기반 3중 정리 |
| 자동 복구 | Health Check 실패 시 재시작 (최대 3회 + 쿨다운) |
| Consumer Group 복구 | NOGROUP 에러 시 자동 재생성 |
| 상태 공유 | Redis Hash, Event Stream, Prometheus |

### 프로바이더 그룹

| 그룹 | 프로바이더 | 포트 | 역할 |
|------|-----------|------|------|
| flm (NPU) | flm-asr | 11434 | NPU ASR (Whisper V3 Turbo) |
| | flm-llm | 11435 | NPU LLM (LFM2) |
| | flm-ocr | 11436 | NPU OCR (Qwen3-VL) |
| gpu-llm | llama-server | 8080 | GPU LLM (llama.cpp) |
| | llama-ocr-server | 8081 | GPU OCR (llama.cpp) |
| gpu-asr | whisper-server | 8001 | GPU ASR Speed Mode |
| | insanely-fast-server | 8002 | GPU ASR Accuracy Mode |
| | diarization-server | 8003 | Speaker Diarization |

---

## Redis 인터페이스

### Streams

| Stream | 용도 |
|--------|------|
| `stream:gpu:requests` | GPU 작업 요청 (Worker/LiteLLM → Provider Manager) |
| `stream:gpu:responses` | GPU 작업 결과 (Provider Manager → Worker/LiteLLM) |
| `stream:provider:requests` | 제어 요청 (상태 조회, 재시작 등) |
| `stream:provider:responses` | 제어 응답 |
| `stream:provider:events` | 상태 변경 이벤트 |

### Keys (Redis Hash)

| Key | 용도 |
|-----|------|
| `providers:status` | 프로바이더 실시간 상태 (status, pid, ram, error) |
| `providers:jobs` | 프로바이더별 활성 작업 수 |
| `job:{id}` | 개별 작업 상세 정보 |

---

## Windows 호환성

### 핵심 수정사항

1. **stdin=DEVNULL**: `CREATE_NO_WINDOW` 플래그와 함께 사용 시 stdin 블록 방지
   ```python
   proc = subprocess.Popen(
       cmd,
       stdin=subprocess.DEVNULL,  # 중요!
       stdout=log_handle,
       stderr=subprocess.STDOUT,
       creationflags=subprocess.CREATE_NO_WINDOW
   )
   ```

2. **pythonw.exe**: 콘솔 창 없이 Python 스크립트 실행

3. **PID 파일**: 프로세스 추적 및 재시작 시 정리

---

## 실행 방법

### PM2 (권장)

```bash
# ecosystem.config.js에서 provider-manager 실행
pm2 start ecosystem.config.js
pm2 logs provider-manager
```

### 직접 실행

```bash
cd infra/provider_manager
python main.py              # API + Stream 처리
python main.py --api-only   # API 서버만
python main.py --stream-only  # Stream 처리만
```

---

## 알려진 문제

### 1. 작업 실패 시 상태 불일치
- **증상**: Worker 작업 실패 시 Backend DB 상태가 업데이트되지 않음
- **원인**: Provider Manager → Worker → Backend 에러 전파 불완전
- **해결 예정**: Backend StateWatchdog 도입 (타임아웃 기반)

### 2. Health Check 타임아웃 오탐
- **증상**: 대형 모델 로딩 시 health check 실패 판정
- **원인**: 고정 타임아웃 (120초)
- **해결 예정**: estimated_ram 기반 동적 타임아웃

---

## 로드맵

| 버전 | 계획 |
|------|------|
| V7.4 | Backend StateWatchdog, Flower 모니터링 |
| V7.5 | OpenTelemetry 분산 트레이싱, 동적 타임아웃 |
| V8.0 | Temporal 워크플로우 엔진, 멀티 호스트 지원 |
