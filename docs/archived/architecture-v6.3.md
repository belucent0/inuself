# Architecture V6.3: Servy Migration

## Overview

V6.3은 프로세스 관리 시스템을 PM2에서 Servy(Windows Service)로 마이그레이션합니다.

- **GPU 서버**: Always-On (상시 실행)
- **NPU/FLM 서버**: On-Demand (HTTP Agent 통해 제어)

---

## 변경 이유

### PM2의 문제점

| 문제 | 설명 | GitHub Issue |
|------|------|--------------|
| 좀비 프로세스 | Windows에서 네이티브 프로세스 종료 실패 | [#5237](https://github.com/Unitech/pm2/issues/5237) |
| 중복 프로세스 | start 명령 중복 시 락 메커니즘 없음 | [#4311](https://github.com/Unitech/pm2/issues/4311) |
| Reload 좀비 | reload 중 다시 reload 시 좀비 발생 | [#2951](https://github.com/Unitech/pm2/issues/2951) |
| Windows 호환성 | Windows Server 2022/2025 서비스 생성 문제 | 문서화됨 |

### Servy의 장점

| 항목 | PM2 | Servy |
|------|-----|-------|
| Windows 네이티브 | ❌ Node.js 기반 | ✅ .NET 네이티브 |
| 좀비 프로세스 | ⚠️ 발생 가능 | ✅ 거의 없음 |
| Health Check | ⚠️ 기본적 | ✅ 내장 + 자동 복구 |
| GUI 모니터링 | ❌ 없음 | ✅ 실시간 |
| 설정 형식 | JS | JSON (Git 친화적) |
| 유지보수 | ⚠️ Windows 이슈 | ✅ 활발한 개발 |

### 통신 방식 변경: Redis → HTTP

| 항목 | Redis Pub/Sub (V6) | HTTP (V6.3) |
|------|-------------------|-------------|
| 통신 패턴 | Fire-and-forget | Request-Response |
| 응답 확인 | ❌ 불가 | ✅ 즉시 확인 |
| 디버깅 | ⚠️ 어려움 | ✅ curl로 테스트 |
| 의존성 | Redis 필수 | 없음 |
| 에러 처리 | ⚠️ 복잡 | ✅ HTTP 상태 코드 |

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                        ARCHITECTURE V6.3: SERVY MIGRATION                        │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│  DOCKER NETWORK                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│   │  Frontend   │    │   Backend   │    │   Worker    │    │   LiteLLM   │     │
│   │  (Next.js)  │    │  (FastAPI)  │    │  (Celery)   │    │  (Gateway)  │     │
│   │   :3000     │    │   :8000     │    │             │    │   :4000     │     │
│   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘     │
│          │                  │                  │                  │            │
│          └──────────────────┴──────────────────┴──────────────────┘            │
│                                       │                                         │
│   ┌───────────────────────────────────┴───────────────────────────────────┐    │
│   │                         REDIS :6379                                   │    │
│   │                    (Celery Queue + Result Stream)                     │    │
│   │                    ❌ provider.control 채널 제거                       │    │
│   └───────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                        │
│   │  PostgreSQL │    │    MinIO    │    │ Prometheus  │                        │
│   │   :5432     │    │   :9000     │    │   :9090     │                        │
│   └─────────────┘    └─────────────┘    └─────────────┘                        │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
          │
          │ host.docker.internal
          ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  WINDOWS 11 HOST                                                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │                    SERVY (Windows Service Manager)                      │  │
│   │                    ✅ 네이티브 Windows 서비스                            │  │
│   │                    ✅ Health Check + 자동 복구                           │  │
│   │                    ✅ JSON 설정 (Git 버전관리)                           │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│          │                                                                      │
│          │ manages                                                              │
│          ▼                                                                      │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │  HOST AGENT (Always-On) :9999                                           │  │
│   │  ├─ 50줄 Python FastAPI                                                 │  │
│   │  ├─ LiteLLM → HTTP → Servy CLI 호출                                     │  │
│   │  └─ NPU/FLM 온디맨드 제어                                               │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│          │                                                                      │
│          │ controls (on-demand)                                                 │
│          ▼                                                                      │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │                     GPU SERVERS (Always-On)                             │  │
│   │  ┌───────────────┬───────────────┬───────────────┬───────────────┐     │  │
│   │  │ whisper-cpp   │ insanely-fast │  diarization  │ llama-server  │     │  │
│   │  │    :8001      │    :8002      │    :8003      │    :8080      │     │  │
│   │  │  (ASR Speed)  │ (ASR Accuracy)│  (Speaker ID) │  (LLM Chat)   │     │  │
│   │  │    ⚡ GPU      │    ⚡ GPU      │    ⚡ GPU      │    ⚡ GPU      │     │  │
│   │  │  [ALWAYS-ON]  │  [ALWAYS-ON]  │  [ALWAYS-ON]  │  [ALWAYS-ON]  │     │  │
│   │  └───────────────┴───────────────┴───────────────┴───────────────┘     │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │                     NPU SERVERS (On-Demand)                             │  │
│   │  ┌───────────────┬───────────────┬───────────────┐                     │  │
│   │  │  flm-asr      │   flm-llm     │   flm-ocr     │                     │  │
│   │  │   :11434      │   :11435      │   :11436      │                     │  │
│   │  │  (NPU ASR)    │  (NPU Chat)   │ (NPU Vision)  │                     │  │
│   │  │    🔷 NPU      │    🔷 NPU      │    🔷 NPU      │                     │  │
│   │  │  [ON-DEMAND]  │  [ON-DEMAND]  │  [ON-DEMAND]  │                     │  │
│   │  └───────────────┴───────────────┴───────────────┘                     │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │  ❌ REMOVED                                                             │  │
│   │  ├─ infra/provider_manager/main.py (900줄 → 삭제)                      │  │
│   │  ├─ ecosystem.config.js (PM2 설정 → 삭제)                              │  │
│   │  ├─ Redis provider.control 채널                                        │  │
│   │  └─ active_count Redis 키                                              │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Request Flow

### GPU (Always-On) - 직접 요청

```
┌──────────┐         ┌──────────┐         ┌──────────┐
│  Client  │         │ LiteLLM  │         │   GPU    │
│ Request  │         │ Gateway  │         │ Servers  │
└────┬─────┘         └────┬─────┘         └────┬─────┘
     │                    │                    │
     │  1. API Request    │                    │
     │ ─────────────────► │                    │
     │                    │                    │
     │                    │  2. Direct HTTP    │
     │                    │ ─────────────────► │
     │                    │  host:8001/8002    │
     │                    │                    │
     │                    │  3. Response       │
     │                    │ ◄───────────────── │
     │                    │                    │
     │  4. Return         │                    │
     │ ◄───────────────── │                    │
     ▼                    ▼                    ▼
```

### NPU (On-Demand) - HTTP Agent 경유

```
┌──────────┐         ┌──────────┐         ┌──────────┐         ┌──────────┐
│  Client  │         │ LiteLLM  │         │   Host   │         │   NPU    │
│ Request  │         │ Gateway  │         │  Agent   │         │ Servers  │
└────┬─────┘         └────┬─────┘         └────┬─────┘         └────┬─────┘
     │                    │                    │                    │
     │  1. API Request    │                    │                    │
     │ ─────────────────► │                    │                    │
     │                    │                    │                    │
     │                    │  2. Start Service  │                    │
     │                    │ ─────────────────► │                    │
     │                    │  host:9999/start   │                    │
     │                    │                    │                    │
     │                    │                    │  3. servy start    │
     │                    │                    │ ─────────────────► │
     │                    │                    │                    │
     │                    │  4. {"started"}    │                    │
     │                    │ ◄───────────────── │                    │
     │                    │                    │                    │
     │                    │  5. Inference      │                    │
     │                    │ ────────────────────────────────────► │
     │                    │  host:11434        │                    │
     │                    │                    │                    │
     │                    │  6. Response       │                    │
     │                    │ ◄──────────────────────────────────── │
     │                    │                    │                    │
     │  7. Return         │                    │                    │
     │ ◄───────────────── │                    │                    │
     ▼                    ▼                    ▼                    ▼
```

---

## Directory Structure

```
torch-test/
├── docs/
│   └── architecture-v6.3.md          # 이 문서
│
├── infra/
│   ├── servy/                         # NEW: Servy 설정
│   │   ├── host-agent.json            # HTTP Agent 서비스
│   │   ├── whisper-server.json        # GPU ASR (Always-On)
│   │   ├── insanely-fast-server.json  # GPU ASR Accuracy (Always-On)
│   │   ├── diarization-server.json    # GPU Diarization (Always-On)
│   │   ├── llama-server.json          # GPU LLM (Always-On)
│   │   ├── flm-asr-server.json        # NPU ASR (On-Demand)
│   │   ├── flm-llm-server.json        # NPU LLM (On-Demand)
│   │   ├── flm-ocr-server.json        # NPU OCR (On-Demand)
│   │   └── install-all.ps1            # 일괄 설치 스크립트
│   │
│   ├── host_agent/                    # NEW: HTTP Agent
│   │   ├── main.py                    # FastAPI 서버 (~50줄)
│   │   └── requirements.txt
│   │
│   ├── litellm/
│   │   ├── custom_handler.py          # MODIFIED: HTTP로 변경
│   │   └── ...
│   │
│   └── provider_manager/              # DEPRECATED: 삭제 예정
│       └── main.py
│
├── ecosystem.config.js                # DEPRECATED: 삭제 예정
└── ...
```

---

## Service Configuration

### Always-On (GPU) 예시

```json
// infra/servy/whisper-server.json
{
  "Name": "whisper-server",
  "DisplayName": "Whisper CPP Server",
  "Description": "GPU ASR Server (Speed Mode)",
  "ExecutablePath": "C:\\whisper-cpp\\build\\bin\\Release\\whisper-server.exe",
  "Parameters": "--model C:\\whisper-cpp\\models\\ggml-large-v3-turbo.bin --port 8001",
  "StartupDirectory": "C:\\whisper-cpp",
  "StartupType": "Automatic",
  "EnableHealthCheck": true,
  "HeartbeatInterval": 30,
  "MaxFailedHealthChecks": 3,
  "RecoveryAction": "RestartService",
  "MaxRestartAttempts": 5,
  "StandardOutputPath": "C:\\timblo\\torch-test\\logs\\whisper-stdout.log",
  "StandardErrorPath": "C:\\timblo\\torch-test\\logs\\whisper-stderr.log",
  "EnableSizeRotation": true,
  "RotationSize": 10485760
}
```

### On-Demand (NPU) 예시

```json
// infra/servy/flm-llm-server.json
{
  "Name": "flm-llm-server",
  "DisplayName": "FLM LLM Server",
  "Description": "NPU LLM Server (On-Demand)",
  "ExecutablePath": "C:\\path\\to\\flm.exe",
  "Parameters": "serve --llm --port 11435",
  "StartupType": "Manual",
  "EnableHealthCheck": true,
  "HeartbeatInterval": 30,
  "MaxFailedHealthChecks": 3,
  "RecoveryAction": "RestartService"
}
```

---

## Host Agent API

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/start/{service}` | 서비스 시작 |
| POST | `/stop/{service}` | 서비스 중지 |
| GET | `/status/{service}` | 서비스 상태 |
| GET | `/health` | Agent 상태 |

### Example Usage

```bash
# 서비스 시작
curl -X POST http://localhost:9999/start/flm-llm-server
# Response: {"status": "started", "service": "flm-llm-server"}

# 서비스 중지
curl -X POST http://localhost:9999/stop/flm-llm-server
# Response: {"status": "stopped", "service": "flm-llm-server"}

# 상태 확인
curl http://localhost:9999/status/flm-llm-server
# Response: {"status": "Running", "service": "flm-llm-server"}
```

---

## Migration Checklist

### Phase 1: 준비
- [ ] Servy 설치 (winget install servy)
- [ ] docs/architecture-v6.3.md 작성
- [ ] infra/servy/ 디렉토리 생성

### Phase 2: 서비스 설정
- [ ] GPU 서버 JSON 설정 작성 (Always-On)
- [ ] NPU 서버 JSON 설정 작성 (On-Demand)
- [ ] Host Agent JSON 설정 작성
- [ ] install-all.ps1 스크립트 작성

### Phase 3: Host Agent 구현
- [ ] infra/host_agent/main.py 작성
- [ ] Servy 서비스로 등록

### Phase 4: LiteLLM 수정
- [ ] custom_handler.py에서 Redis pub/sub 제거
- [ ] HTTP Agent 호출로 변경 (NPU만)
- [ ] GPU는 직접 호출 유지

### Phase 5: 정리
- [ ] PM2 서비스 중지 및 삭제
- [ ] ecosystem.config.js 삭제
- [ ] infra/provider_manager/ 삭제
- [ ] 테스트 및 검증

---

## Rollback Plan

문제 발생 시:

```powershell
# 1. Servy 서비스 중지
servy stop --all --quiet

# 2. PM2 복원
pm2 start ecosystem.config.js

# 3. Git 롤백
git checkout main -- infra/litellm/custom_handler.py
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| V6.3 | 2026-01-16 | PM2 → Servy 마이그레이션, HTTP Agent 도입 |
| V6.0 | 이전 | PM2 + Provider Manager 기반 |
