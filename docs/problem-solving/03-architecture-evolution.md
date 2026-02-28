# AI 서비스 아키텍처 진화: 분산된 직접 연결에서 단일 관문 구조로

> **프로젝트**: LLM 기반 문서요약 + AI 채팅 서비스
> **기간**: 2025년 ~ 2026년 (Legacy → v1.1.0)
> **역할**: 전체 아키텍처 설계 및 구현 (단독)

---

## 1. Situation — 어떤 상황이었나

**모든 컴포넌트가 단일 온프레미스 머신 위에서 동작한다.** GPU와 NPU를 탑재한 서버 한 대가 전부다. 비용 절감과 데이터 보안을 위해 외부 API 없이 자체 모델을 직접 운영하는 구조였다.

초기 서비스의 컴포넌트는 크게 두 레이어로 나뉘었다:
- **Docker Compose 레이어**: Backend API + Redis + MinIO (인프라 컨테이너만)
- **Host 레이어 (PM2)**: Worker OCR/ASR/LLM + GPU 모델 서버 (하드웨어 직접 접근)

초기 서비스는 **배치 처리 중심**이었다. 사용자가 파일을 업로드하면 큐에 쌓이고, 기능별 워커가 꺼내서 처리하는 구조였다:

| 기능 | 모델 | 처리 방식 |
|------|------|---------|
| 문서 요약 | Llama-Server (GPU, Host) | Worker LLM → 직접 호출 |
| ASR (음성 인식) | Whisper, Pyannote | Worker ASR → 모델 직접 로드 |
| OCR | Llama-Server (GPU, Host) | Worker OCR → 직접 호출 |

이 구조는 배치 처리에는 잘 맞았다. 하지만 **실시간 AI 채팅 추가**와 **NPU 채널 도입**의 두 가지 확장 계획이 동시에 생기면서 한계가 드러났다.

---

## 2. Problem — 구체적으로 뭐가 문제였나

### Before 아키텍처 (Legacy — 배치 전용, GPU만)

```
[User Browser]
     │ HTTP
     ▼
╔══════════════════════════════════╗
║  Docker Compose                  ║
║  ┌────────────────────────────┐  ║
║  │  Backend                   │  ║
║  │  (업로드 API · 결과 조회)   │  ║
║  └──────────┬─────────────────┘  ║
║             │ Batch Job           ║
║  ┌──────────▼─────────────────┐  ║
║  │       Redis Queue          │  ║
║  └──────────┬─────────────────┘  ║
╚═════════════╪════════════════════╝
              │ (Queue 폴링)
╔═════════════╪══════════════════════════════════════╗
║  Host (PM2) │                                      ║
║     ┌───────▼────┐ ┌──────────┐ ┌────────────┐    ║
║     │Worker OCR  │ │Worker ASR│ │Worker LLM  │    ║
║     └─────┬──────┘ └────┬─────┘ └─────┬──────┘    ║
║           │ localhost    │ 직접 로드    │ localhost  ║
║           ▼              ▼             ▼           ║
║     ┌───────────┐ ┌──────────┐ ┌───────────┐      ║
║     │Llama-Server│ │ Whisper  │ │Llama-Server│     ║
║     │  (GPU)     │ │ Pyannote │ │  (GPU)     │     ║
║     └───────────┘ │  (GPU)   │ └───────────┘      ║
║                    └──────────┘                    ║
╚════════════════════════════════════════════════════╝

  ※ Worker와 모델 서버가 같은 Host → 컨테이너 경계 문제 없음
  ※ GPU 1대. 실시간 채팅 없음. PM2로 워커 프로세스 관리.
```

### 전환의 계기: NPU 도입 + 채팅 기능 계획

두 가지를 동시에 추가하려고 설계를 검토했다.

1. **NPU 채널 도입**: GPU 부하 분산 목적. FLM(NPU)을 추가하면 특정 작업에서 GPU 없이도 추론이 가능하다.
2. **실시간 AI 채팅**: 사용자가 NPU 기반 LLM과 직접 대화하는 기능.

이 두 가지를 기존 구조에 넣으려 하자 문제가 바로 드러났다.

**NPU를 추가하면:** PM2로 관리되는 Worker OCR, Worker ASR, Worker LLM 각각에 NPU 연결 코드를 심어야 한다. "GPU를 쓸지 NPU를 쓸지" 판단 로직도 워커마다 따로 구현해야 한다.

**채팅을 추가하면:** Backend(Docker 컨테이너)가 LLM을 직접 호출해야 한다. 그런데 모델 서버는 Host(PM2)에 있다. 컨테이너에서 Host 프로세스로 직접 연결하는 경계 문제가 생기고, GPU/NPU 상태를 확인하는 로직도 Backend에 따로 구현해야 했다.

**기존 구조에서는 Provider가 늘거나 호출 주체가 늘 때마다 N곳을 동시에 수정해야 했다.**

### 세 가지 구조적 문제

**① 연결 관리 파편화 — Provider 추가 시 N곳 수정**

워커마다 모델 서버 URL을 각자 갖고 있었다. NPU를 추가하면 이 설정이 3곳에 동시에 추가돼야 했다.

```
# Worker OCR 환경변수 (PM2 ecosystem.config.js)
LLAMA_SERVER_URL=http://localhost:8080/...

# Worker ASR 환경변수
WHISPER_MODEL=large-v3

# Worker LLM 환경변수
LLM_PROVIDER=llamacpp
LLAMA_SERVER_URL=http://localhost:8080/...
```

**② 라우팅 로직 분산 — "GPU vs NPU" 결정이 코드 안에**

NPU가 생기면 "이 요청에 GPU를 쓸지 NPU를 쓸지"를 각 워커가 스스로 판단해야 했다. 워커마다 라우팅 기준이 달라지고, 부하 분산 로직도 각자 구현해야 하는 상황이었다.

**③ 자원 충돌 — GPU OOM 위험**

Worker ASR는 Whisper · Pyannote를 Host에서 직접 로드했다. GPU 한 대를 여러 PM2 프로세스가 공유하는데, 어떤 프로세스가 GPU를 쓰고 있는지 다른 프로세스는 알 방법이 없었다. 동시 실행 시 OOM이 발생할 수 있었고, 채팅 기능까지 추가되면 이 위험은 더 커질 것이 자명했다.

---

## 3. Attempted Solution — 처음에 어떻게 시도했나

NPU·채팅 추가 전에 자원 충돌 문제부터 Redis 키로 임시 처리했다.

```python
# 워커 측: GPU 사용 시작
redis.set("gpu:asr:active", "1", ex=600)

# 다른 워커 측: GPU 접근 전 확인
if redis.exists("gpu:asr:active"):
    raise ServiceUnavailable()
```

동작하긴 했다. 그러나 이건 증상 치료였다. 진짜 문제는 그대로였다:

- NPU를 추가하면 "GPU vs NPU 선택" 로직을 **모든 워커 코드에 심어야** 한다
- 채팅 기능을 추가하면 Backend에도 **동일한 패턴을 또 구현**해야 한다
- Provider가 하나 더 생기면 이 패턴을 **또 반복**해야 한다

패치가 쌓일수록 구조는 더 복잡해진다는 것을 확인했다.

---

## 4. Final Solution — 최종적으로 어떻게 해결했나

### 설계 원칙: "모든 AI 요청이 하나의 관문을 통과한다"

근본 해결책은 **LiteLLM Proxy를 단일 AI 게이트웨이로 도입**하는 것이었다. Backend도, 모든 워커도 Host의 모델 서버를 직접 알지 않는다. `LITELLM_BASE_URL` 하나만 알면 된다.

그리고 **Provider Manager를 Host 프로세스로 도입**하고, 컨테이너와의 통신은 Valkey Stream으로 연결했다. 추론 서버가 현재는 동일 머신 위에 있지만, 전용 GPU 서버로 분리되는 시점을 고려한 구조다. Valkey Stream 기반이면 컨테이너 코드 변경 없이 네트워크 건너편의 추론 서버를 그대로 붙일 수 있다.

### After 아키텍처 (현재)

```
  [User Browser]
       │ HTTP
       ▼
╔═════════════════════════════════════════════════════╗
║  Docker Compose                                     ║
║                                                     ║
║  ┌──────────────────────────────────────────────┐  ║
║  │  Backend  (업로드 API + 실시간 채팅 — 신규)   │  ║
║  └──────────┬────────────────┬──────────────────┘  ║
║             │ Realtime Chat   │ Batch Job            ║
║             │                 ▼                      ║
║             │    ┌────────────────────────┐          ║
║             │    │     Redis Queue        │          ║
║             │    └────────────┬───────────┘          ║
║             │                 │                      ║
║             │    ┌────────────▼────────────┐         ║
║             │    │Unified Worker(CPU bound)│         ║
║             │    │  오디오 변환 (ffmpeg)    │         ║
║             │    │  파일 → 이미지 변환      │         ║
║             │    │  환각 필터링 · 화자 병합 │         ║
║             │    └────────────┬────────────┘         ║
║             └─────────────────┘                       ║
║                          │ (OpenAI 호환 엔드포인트)   ║
║             ┌────────────▼────────────┐              ║
║             │     LiteLLM Proxy       │              ║
║             │  · 티어 기반 라우팅      │              ║
║             │  · Fallback / 재시도    │              ║
║             └────────────┬────────────┘              ║
║                          │ Valkey Stream XADD        ║
║             ┌────────────▼────────────┐              ║
║             │         Valkey          │              ║
║             └────────────┬────────────┘              ║
╚══════════════════════════╪══════════════════════════╝
            컨테이너 → Host │ XREADGROUP
╔══════════════════════════╪══════════════════════════╗
║  Host Process            │                         ║
║             ┌────────────▼────────────┐            ║
║             │    Provider Manager     │            ║
║             │  ├─ StreamProcessor     │            ║
║             │  ├─ ProviderManager     │            ║
║             │  ├─ IdleManager (자동 언로드)         ║
║             │  └─ Semaphore (동시성 제어)           ║
║             └──────┬─────────────┬───┘             ║
║                    │             │                 ║
║           ┌────────▼────┐  ┌─────▼──────────────┐  ║
║           │  NPU (new)  │  │  GPU (existing)    │  ║
║           │  FLM        │  │  llama-server      │  ║
║           └─────────────┘  │  lemonade-server   │  ║
║                            │  whisper-cpp       │  ║
║                            │  insanely-fast     │  ║
║                            │  pyannote          │  ║
║                            └────────────────────┘  ║
╚════════════════════════════════════════════════════╝

  ※ Valkey Stream = 현재는 컨테이너-Host IPC, 추론 서버 분리 시 코드 변경 없이 확장
  ※ NPU 추가 = LiteLLM config 1줄
```

### Before vs After 핵심 비교

```
[Before]                              [After]
────────────────────────────────────────────────────────────
PM2 워커 → 모델 서버 직접             컨테이너 → LiteLLM
(같은 Host, 워커별 URL 개별 보유)                │
채팅 추가 시: 컨테이너-Host           Valkey Stream (브릿지)
경계 문제 발생                                   │
                                       Host: Provider Manager
                                       (lifecycle · 동시성)
                                                 │
                                          NPU  /  GPU

워커 수:     3개 (기능별, 일부 모델 직접 로드)  워커 수:   1개 (CPU 전처리 전담)
환경변수:    PM2 워커별 모델 서버 URL 개별 보유  환경변수:  LITELLM_BASE_URL 1개
라우팅 결정: 워커 코드 안에                    라우팅 결정: LiteLLM config
Provider 추가: PM2 워커 N곳 코드 수정          Provider 추가: config 1줄
Lifecycle:   없음 (상시 상주)                  Lifecycle:  자동 언로드
모니터링:    없음                              모니터링:  Prometheus + Grafana
```

### 버전별 핵심 변경 흐름

| 버전 | 핵심 변경 | 해결한 문제 |
|------|-----------|------------|
| Legacy | 배치 전용 · PM2 워커별 직접 연결 | (출발점) |
| **V4** | **LiteLLM 단일 관문 · 통합 워커 · NPU 추가** | **연결 파편화, 라우팅 분산** |
| V6.2 | SETNX 기반 자원 잠금 강화 | 컨테이너 간 GPU 충돌 |
| **V7.0** | **Provider Manager (Host) · Valkey Stream 도입** | **Lifecycle 관리 부재, 컨테이너-Host 경계 정리** |
| V8.0 | LangGraph 에이전트 전환 | 단순 LLM 호출의 한계 |
| V1.0.0 | Worker-Backend 책임 완전 분리 | 결합도 누적 |

---

## 5. Result — 결과와 임팩트

| 항목 | Legacy | V4 이후 |
|------|--------|---------|
| 워커 프로세스 수 | 3개 (PM2 · 기능별 · 일부 모델 직접 로드) | 1개 (CPU 전처리 전담) |
| Provider 추가 비용 | PM2 워커 N곳 코드 수정 | config 1줄 |
| 라우팅 로직 위치 | 워커 코드 내 분산 | LiteLLM config 집중 |
| 실시간 채팅 추가 | 컨테이너-Host 경계 문제 발생 | Backend에서 동일 엔드포인트 호출 |
| 프로세스 관리 | PM2 수동 관리 (좀비 프로세스, 메모리 누수) | Valkey Stream + 자동 Lifecycle |
| 모니터링 | 없음 | Prometheus + Grafana |

---

## 6. Lessons Learned — 이 경험에서 배운 것

**"분산된 직접 연결은 기능 추가마다 복잡도를 곱한다"**

Legacy 구조의 문제는 기능이 추가될 때마다 표면으로 올라왔다. 워커가 하나 생길 때마다 모델 서버 연결 코드가 그 안에 복제됐고, Provider가 하나 바뀌면 모든 워커 코드를 수정해야 했다. NPU 추가라는 단 하나의 결정이 전체 구조를 재검토하게 만들었다.

세 가지를 배웠다:

1. **단일 관문 패턴의 복리 효과**: LiteLLM 도입 이후 "새 Provider 추가"는 config 한 줄이 됐다. 아키텍처 투자는 초기엔 비용이지만, 이후 모든 변경에서 이자가 붙어 돌아온다.

2. **경계를 명확히 하면 책임이 명확해진다**: 컨테이너와 Host 사이의 경계를 Valkey Stream으로 정의한 순간, "하드웨어를 아는 것은 Host 프로세스뿐"이라는 원칙이 자연스럽게 따라왔다. 경계가 흐릿할 때 책임도 흐릿해진다. 단일 머신 IPC 용도로는 Valkey Stream이 다소 무거운 선택이지만, 추론 서버를 전용 하드웨어로 분리할 때 컨테이너 코드를 건드리지 않아도 된다는 점에서 그 무게를 감수했다.

3. **확장 계획이 아키텍처를 검증한다**: "NPU를 추가하면 어떻게 되는가?"라는 질문 하나로 기존 구조의 한계가 드러났다. 현재 기능이 잘 돌아간다는 것과, 다음 기능을 저렴하게 추가할 수 있다는 것은 다른 문제다.

---

## 7. 기술 스택

- **LiteLLM Proxy**: 통합 AI 게이트웨이, 모델명 기반 라우팅 + Fallback
- **Valkey (Redis 호환)**: 배치 작업 큐 + 컨테이너-Host 간 통신 브릿지 (Stream)
- **Provider Manager**: Host 프로세스, GPU/NPU 하드웨어 lifecycle 전담 (asyncio)
- **LangGraph**: AI 에이전트 그래프 오케스트레이션
- **Prometheus + Grafana**: 중앙 모니터링
- **Docker Compose**: 애플리케이션 컨테이너 구성

---

*관련 문서: `docs/archived/architecture_comparison.md`, `docs/archived/architecture_v4.md`, `docs/architecture-v1.1.0.md`*
