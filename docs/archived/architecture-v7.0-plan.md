# Architecture V7.0 계획

## 개요

Docker Desktop 크래시 문제 해결 및 아키텍처 단순화를 위한 재구성 계획.

## 현재 문제점 (V6.5)

### 1. Docker Desktop 크래시
- **원인**: Docker 컨테이너에서 `host.docker.internal`로 HTTP 요청 시 크래시
- **영향**: LiteLLM → Host GPU 서버 통신 불안정
- **임시 조치**: Health Check 비활성화 (`HEALTH_CHECK_ENABLED=false`)

### 2. 병렬 처리 비활성화
- **배경**: Docker Desktop 크래시 원인을 GPU 메모리 충돌로 오진
- **결과**: ASR + Diarization 순차 처리로 변경 (불필요한 성능 저하)
- **실제 원인**: `host.docker.internal` HTTP 통신 문제

### 3. 프로세스 관리 복잡성
- PM2: Windows에서 좀비 프로세스, 메모리 누수 발생
- 다중 llama-server: 관리 포인트 증가

---

## V7.0 목표

| 목표 | 설명 |
|------|------|
| Docker Desktop 크래시 방지 | `host.docker.internal` HTTP 제거 |
| Docker 최대화 | Frontend, Backend, LiteLLM, Redis, DB 모두 Docker |
| Host 최소화 | GPU/NPU 서버 + Stream Worker만 Host |
| 병렬 처리 복원 | ASR + Diarization 동시 실행 |
| 프로세스 관리 단순화 | 단일 llama-server + 동적 모델 로드 |
| 좀비 프로세스 방지 | 복합 전략 (Job Object + PID 파일 + atexit) |

---

## 새로운 아키텍처

### 전체 구조도

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
│                          ┌────────────────────────────────┘                 │
│                          │                                                  │
│                          ▼                                                  │
│                   ┌──────────┐                                              │
│                   │ LiteLLM  │                                              │
│                   │  :4000   │                                              │
│                   │          │                                              │
│                   │ - Prometheus 기반 라우팅                                 │
│                   │ - GPU/NPU 선택                                          │
│                   │ - Redis Stream 통신                                     │
│                   └────┬─────┘                                              │
│                        │                                                    │
│                        ▼                                                    │
│                 ┌─────────────┐        ┌─────────────┐                     │
│                 │    Redis    │        │ Prometheus  │                     │
│                 │   Stream    │        │   :9090     │                     │
│                 │             │◀───────│ GPU/NPU 메트릭                     │
│                 └──────┬──────┘        └─────────────┘                     │
│                        │                                                    │
└────────────────────────│────────────────────────────────────────────────────┘
                         │
                         │ TCP (안정적)
                         │
┌────────────────────────│────────────────────────────────────────────────────┐
│ Host                   ▼                                                    │
│                 ┌─────────────────────────────────────────────────────┐    │
│                 │              Stream Worker (Python)                  │    │
│                 │                                                      │    │
│                 │  - Redis Stream 구독 (XREAD)                         │    │
│                 │  - GPU/NPU 서버 호출 (localhost)                      │    │
│                 │  - FLM 프로세스 관리 (복합 전략)                       │    │
│                 │  - 응답 발행 (XADD)                                   │    │
│                 └───────────────────────┬─────────────────────────────┘    │
│                                         │                                   │
│                          localhost      │                                   │
│                 ┌───────────────────────┼───────────────────────┐          │
│                 │                       │                       │          │
│                 ▼                       ▼                       ▼          │
│  ┌──────────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │ GPU Servers (Servy)  │  │ pyannote (Servy) │  │  FLM (온디맨드)       │  │
│  │                      │  │     :8003        │  │                      │  │
│  │ llama-server :8080   │  │  화자분리 전용    │  │  ASR  :11434         │  │
│  │ (동적 모델 로드)      │  │                  │  │  LLM  :11435         │  │
│  │ - qwen3-4b (요약)    │  └──────────────────┘  │  OCR  :11436         │  │
│  │ - qwen3-vl-8b (OCR)  │                        │                      │  │
│  │                      │                        │  Stream Worker가      │  │
│  │ whisper.cpp :8001    │                        │  프로세스 관리        │  │
│  │ (ASR 전용)           │                        │                      │  │
│  └──────────────────────┘                        └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 요청 흐름

#### 1. 배치 ASR (음성 전사 + 화자분리)

```
Client ─▶ Backend ─▶ Redis Queue ─▶ Worker
                                      │
                                      ▼
                               ┌─────────────────────────────────────┐
                               │ Worker (asyncio.gather 병렬)        │
                               │                                     │
                               │   ┌─────────────┐ ┌─────────────┐  │
                               │   │ ASR 요청    │ │ Diarize요청 │  │
                               │   └──────┬──────┘ └──────┬──────┘  │
                               │          │               │         │
                               │          └───────┬───────┘         │
                               │                  ▼                 │
                               │            LiteLLM :4000           │
                               └──────────────────│─────────────────┘
                                                  │
                                           Redis Stream
                                                  │
                                                  ▼
                               ┌─────────────────────────────────────┐
                               │ Stream Worker (Host)                │
                               │                                     │
                               │   whisper.cpp ◀─── ASR 처리         │
                               │   pyannote    ◀─── 화자분리 처리     │
                               │                                     │
                               │         결과 병합 (Worker에서)       │
                               └─────────────────────────────────────┘
```

#### 2. 실시간 LLM 채팅 (스트리밍)

```
Client ◀─SSE─▶ Backend ◀─────▶ LiteLLM
                                  │
                                  ▼
                            Redis Stream
                                  │
                 ┌────────────────┼────────────────┐
                 ▼                                 ▼
          Stream Worker                      Stream Worker
                 │                                 │
          llama-server (GPU)              FLM (NPU)
          qwen3-4b                        qwen3-4b
                 │                                 │
                 └────────────────┬────────────────┘
                                  │
                          토큰 스트리밍
                          Redis Stream 응답
                                  │
                                  ▼
                          Backend ─▶ Client (SSE)
```

#### 3. 실시간 ASR (WebSocket)

```
Client ◀─WebSocket─▶ Backend
                        │
                   음성 청크 전송
                        │
                        ▼
                   Redis Stream
                        │
                        ▼
                  Stream Worker
                        │
               ┌────────┴────────┐
               ▼                 ▼
        whisper.cpp (GPU)   FLM ASR (NPU)
               │                 │
               └────────┬────────┘
                        │
                   전사 결과
                   Redis Stream
                        │
                        ▼
                  Backend ─▶ Client
```

---

## 컴포넌트 상세

### Docker 컴포넌트

| 컴포넌트 | 포트 | 역할 |
|---------|------|------|
| Frontend | 3000 | Next.js, React UI |
| Backend | 8000 | FastAPI, API/WebSocket |
| Worker | - | Celery, 배치 작업 오케스트레이션 |
| LiteLLM | 4000 | API Gateway, Prometheus 라우팅 |
| Redis | 6379 | Celery Broker + Stream |
| PostgreSQL | 5432 | 메타데이터 저장 |
| MinIO | 9000 | 파일 스토리지 |
| Prometheus | 9090 | GPU/NPU 메트릭 수집 |

### Host 컴포넌트

| 컴포넌트 | 포트 | 관리 방식 | 역할 |
|---------|------|----------|------|
| Stream Worker | - | Servy | Redis ↔ GPU 통신 |
| llama-server | 8080 | Servy | LLM 추론 (동적 모델) |
| whisper.cpp | 8001 | Servy | ASR (GPU) |
| pyannote | 8003 | Servy | 화자분리 |
| FLM ASR | 11434 | Stream Worker | ASR (NPU, 온디맨드) |
| FLM LLM | 11435 | Stream Worker | LLM (NPU, 온디맨드) |
| FLM OCR | 11436 | Stream Worker | OCR (NPU, 온디맨드) |

---

## GPU/NPU 라우팅 전략

### Prometheus 기반 동적 라우팅

```python
# LiteLLM custom_handler.py 의사코드

def select_provider(task_type: str, accuracy_mode: str):
    # 1. accuracy_mode 우선
    if accuracy_mode == "accuracy":
        return "gpu"  # 큰 모델
    elif accuracy_mode == "speed":
        return "npu"  # 빠른 응답

    # 2. 자원 사용률 기반 (Prometheus 쿼리)
    gpu_util = query_prometheus("gpu_utilization")
    npu_util = query_prometheus("npu_utilization")

    if gpu_util < 70:
        return "gpu"
    elif npu_util < 70:
        return "npu"
    else:
        return "gpu"  # 기본값
```

### 모델 매핑

| 기능 | GPU 모델 | NPU 모델 | 비고 |
|------|---------|---------|------|
| LLM 요약 | qwen3-4b | qwen3-4b | 동일 모델, 디바이스만 다름 |
| OCR | qwen3-vl-8b | qwen3-vl-4b | 정확도 vs 속도 |
| ASR | whisper-large-v3 | whisper-turbo | 정확도 vs 속도 |

---

## FLM 프로세스 관리 (복합 전략)

### 3중 보호

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Windows Job Object                                           │
│    - 부모(Stream Worker) 죽으면 자식(FLM)도 자동 종료            │
│    - OS 레벨 보장                                                │
├─────────────────────────────────────────────────────────────────┤
│ 2. PID 파일                                                     │
│    - 프로세스 시작 시 PID 기록                                   │
│    - Stream Worker 재시작 시 고아 프로세스 정리                   │
├─────────────────────────────────────────────────────────────────┤
│ 3. atexit 핸들러                                                │
│    - 정상 종료 시 자식 프로세스 정리                              │
│    - Graceful shutdown (CTRL_BREAK_EVENT)                       │
└─────────────────────────────────────────────────────────────────┘
```

### FLM Manager 클래스

```python
class FLMManager:
    """FLM 프로세스 관리자 - 좀비 방지 복합 전략"""

    def __init__(self):
        self.job = self._create_job_object()  # 1. Job Object
        self._cleanup_orphans()                # 2. 시작 시 고아 정리
        atexit.register(self.cleanup)          # 3. 종료 시 정리

    def start(self, model_type: str):
        """모델 시작 (asr/llm/ocr)"""
        # 이전 모델 종료
        self.stop()

        # 프로세스 시작
        proc = subprocess.Popen(["flm", "serve", f"-{model_type[0]}"], ...)

        # Job Object에 할당
        self._assign_to_job(proc)

        # PID 기록
        self._save_pid(proc.pid)

        return proc

    def stop(self):
        """Graceful shutdown"""
        # CTRL_BREAK_EVENT → 대기 → 강제 종료
```

---

## llama-server 동적 모델 관리

### 단일 서버 + 모델 스왑

```
기존 (V6.5):
  llama-server :8080 (qwen3-4b)     ← 프로세스 1
  llama-ocr-server :8081 (qwen3-vl) ← 프로세스 2

V7.0:
  llama-server :8080                ← 프로세스 1개만
    ├─ 요약 요청 → qwen3-4b 로드
    └─ OCR 요청 → qwen3-vl-8b 로드
```

### 모델 로드/언로드 API

```bash
# 현재 모델 확인
GET /v1/models

# 모델 변경 (서버 재시작 방식)
Stream Worker가 Servy API로 llama-server 재시작
servy restart --name llama-server --args "--model qwen3-vl-8b.gguf"
```

---

## 구현 순서

### Phase 1: 기반 구축
1. [ ] Stream Worker 기본 구조 (Redis Stream 통신)
2. [ ] FLM Manager 복합 전략 구현
3. [ ] PID 파일 관리 로직

### Phase 2: LiteLLM 연동
4. [ ] LiteLLM custom_handler Redis Stream 통신
5. [ ] gpu_stream_client 완성
6. [ ] Prometheus 라우팅 로직 검증

### Phase 3: 파이프라인 복원
7. [ ] ASR + Diarization 병렬 처리 복원
8. [ ] 실시간 LLM 스트리밍 (Redis Stream)
9. [ ] 실시간 ASR (Redis Stream)

### Phase 4: 테스트 및 안정화
10. [ ] 통합 테스트
11. [ ] Docker Desktop 크래시 테스트
12. [ ] 좀비 프로세스 테스트
13. [ ] 문서화

---

## 예상 효과

| 항목 | V6.5 | V7.0 |
|------|------|------|
| Docker Desktop 크래시 | 발생 | 해결 |
| ASR+Diarization 처리 시간 | 순차 (A+B) | 병렬 (max(A,B)) |
| Host 프로세스 수 | 6-8개 | 4-5개 |
| 좀비 프로세스 | 발생 가능 | 3중 보호 |
| VRAM 효율 | 모델별 상주 | 온디맨드 로드 |

---

## 포트폴리오 하이라이트

1. **Redis Stream 기반 Docker ↔ Host 통신**
   - Docker Desktop 크래시 회피
   - 실시간 스트리밍 지원

2. **Prometheus 기반 GPU/NPU 동적 라우팅**
   - 자원 사용률 모니터링
   - 동일 모델의 다른 디바이스 실행 비교

3. **FLM 프로세스 관리 복합 전략**
   - Windows Job Object
   - 좀비/고아 프로세스 방지

4. **LiteLLM Gateway 패턴**
   - OpenAI 호환 API
   - 멀티 프로바이더 추상화
