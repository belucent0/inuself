# Observability Stack v1.0

## 개요

분산 시스템의 로그 수집, 분산 추적, 메트릭 모니터링을 위한 옵저빌리티 스택.

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌──────────────────┐
│  Frontend   │───▶│   Backend   │───▶│ AI Gateway  │───▶│ Inference        │
│  (Vite/React)│   │  (FastAPI)  │    │  (FastAPI)  │    │ Containers (ai-*)│
└─────────────┘    └─────────────┘    └─────────────┘    └──────────────────┘
       │                  │                  │                    │
       │                  ▼                  ▼                    ▼
       │           trace_id=abc123    trace_id=abc123     trace_id=abc123
       │                  │                  │                    │
       │                  │           Worker (Celery) ────────────┤
       │                  │                  │                    │
       │                  └────────┬─────────┴────────────────────┘
       │                           ▼
       │                    ┌─────────────┐
       │                    │  Promtail   │ ◀── Docker SD (모든 컨테이너 로그)
       │                    └──────┬──────┘
       │                           ▼
       │                    ┌─────────────┐
       │                    │    Loki     │ ◀── 로그 저장 (MinIO S3)
       │                    └──────┬──────┘
       │                           │
       │    ┌──────────────────────┼──────────────────────┐
       │    │                      ▼                      │
       │    │               ┌─────────────┐               │
       └────┼──────────────▶│   Grafana   │◀──────────────┤
            │               └─────────────┘               │
            │                      ▲                      │
            │                      │                      │
     ┌──────┴──────┐        ┌──────┴──────┐        ┌──────┴──────┐
     │ Prometheus  │        │    Tempo    │        │   Jaeger    │
     │  (Metrics)  │        │  (Traces)   │        │  (Traces)   │
     └─────────────┘        └─────────────┘        └─────────────┘
```

---

## 구성 요소

### 1. 로그 수집 (Loki + Promtail)

| 컴포넌트 | 역할 | 포트 |
|----------|------|------|
| **Loki** | 로그 저장 및 쿼리 | 3100 |
| **Promtail** | 로그 수집 에이전트 | 9080 |

**Promtail 수집 대상:**
- Docker 컨테이너 로그 자동 발견 (Docker Service Discovery)
  - 애플리케이션: `asr-backend`, `asr-frontend`, `asr-worker-unified`, `asr-nginx`, `asr-cli-proxy-api`
  - AI Gateway / 추론: `ai-gateway`, `ai-llm`, `ai-asr`, `ai-diarize`, `ai-ocr`, `ai-embedding`
  - 데이터: `asr-postgres`, `asr-valkey`, `asr-minio`, `asr-searxng`
  - 옵저빌리티: `asr-prometheus`, `asr-grafana`, `asr-loki`, `asr-promtail`, `asr-tempo`, `asr-flower`, `asr-langfuse`

### 2. 분산 추적 (Jaeger + Tempo)

| 컴포넌트 | 역할 | 포트 |
|----------|------|------|
| **Jaeger** | 분산 추적 UI/백엔드 | 16686 (UI), 4317/4318 (OTLP) |
| **Tempo** | 분산 추적 저장소 | 3200 |

### 3. 메트릭 (Prometheus)

| 컴포넌트 | 역할 | 포트 |
|----------|------|------|
| **Prometheus** | 메트릭 수집/저장 | 9090 |

### 4. 시각화 (Grafana)

| 컴포넌트 | 역할 | 포트 |
|----------|------|------|
| **Grafana** | 대시보드/시각화 | 3002 |

### 5. 데이터소스

| 데이터소스 | 역할 | 연결 대상 |
|------------|------|-----------|
| **Prometheus** | 메트릭 쿼리 | prometheus:9090 |
| **Loki** | 로그 쿼리 | loki:3100 |
| **Tempo** | 트레이스 쿼리 | tempo:3200 |
| **PostgreSQL** | 콘텐츠 상태 쿼리 | postgres:5432 |

---

## trace_id 연계 로깅

### 동작 원리

1. **Frontend**: 요청 시 `traceparent` 헤더 생성
2. **Backend**: OpenTelemetry로 trace context 수신, 로그에 trace_id 자동 포함
3. **Worker**: Celery 태스크 헤더에서 traceparent 추출, 로그에 trace_id 포함
4. **AI Gateway**: 들어온 traceparent 헤더를 그대로 추론 컨테이너 호출(`httpx`)에 전파, 로그에 trace_id 포함

### 로그 포맷

```
# 요청 처리 (trace_id 포함)
2026-02-02 19:15:50 [INFO] trace_id=5a8ec4ab92b438994db128f42e3c2838 | StreamProcessor | Transcription completed

# 백그라운드 작업 (trace_id=00_00)
2026-02-02 19:31:13 [INFO] trace_id=00_00 | ProviderManager | Starting all provider processes...
```

> **Note**: 백그라운드 작업은 `trace_id=00_00`으로 표시됩니다. 시각적으로 구분되어 Loki 검색이 용이합니다.

### 서비스별 구현

| 서비스 | 파일 | 방식 |
|--------|------|------|
| Backend | `backend/app/core/logging.py` | Loguru patcher |
| Worker | `worker/logging_config.py` | Loguru patcher |
| AI Gateway | `infra/ai-gateway/main.py` (`middleware/telemetry.py`) | OpenTelemetry + Loguru |
| 추론 컨테이너 | `infra/inference/{llm,asr,diarize,ocr,embedding}/server.py` | 컨테이너 stdout (Promtail 수집) |

---

## 사용 방법

### 1. Grafana 접속

```
URL: http://localhost:3002
또는: http://asr.timblo.io/grafana (nginx 프록시)
```

### 2. 로그 조회 (Loki)

**Grafana > Explore > Loki 선택**

```logql
# 모든 Backend 로그
{container="asr-backend"}

# 에러만 필터링
{container="asr-backend"} |= "ERROR"

# 특정 trace_id로 전체 서비스 로그 추적
{trace_id="5a8ec4ab92b438994db128f42e3c2838"}

# 백그라운드 작업만 (trace_id=00_00)
{trace_id="00_00"}

# 실제 요청만 (백그라운드 제외)
{container="asr-backend"} |~ "trace_id=[a-f0-9]{32}"
```

### 3. 분산 추적 (Jaeger)

```
URL: http://localhost:16686
```

1. Service 선택: `asr-backend`, `asr-worker-unified`, `ai-gateway`, `ai-llm`, `ai-asr`, `ai-diarize`, `ai-ocr`, `ai-embedding`
2. Find Traces 클릭
3. 특정 trace 클릭 → 전체 span 타임라인 확인

### 4. 로그 → Trace 연결

Grafana Loki에서 로그 조회 시:
1. `trace_id=abc123...` 가 포함된 로그 확인
2. trace_id 클릭 → Tempo/Jaeger에서 전체 trace 확인

---

## 대시보드

### Docker Logs Dashboard

**위치**: Grafana > Dashboards > Docker Logs

| 패널 | 설명 |
|------|------|
| Log Volume by Container | 컨테이너별 로그 볼륨 시각화 |
| Backend Logs | asr-backend 로그 |
| Worker Logs | asr-worker-unified 로그 |
| Errors & Exceptions | 전체 에러/예외 로그 |
| All Logs (Filtered) | 검색 필터링 가능한 전체 로그 |

### 변수

| 변수 | 설명 |
|------|------|
| `$container` | 컨테이너 필터 (다중 선택) |
| `$search` | 텍스트 검색 |

### Content Pipeline Dashboard

**위치**: Grafana > Dashboards > Content Pipeline
**URL**: `/grafana/d/content-pipeline/content-pipeline`

콘텐츠 처리 파이프라인(ASR/OCR/LLM 요약) 상태를 실시간으로 모니터링합니다.

| 패널 | 설명 |
|------|------|
| 파이프라인 현황 | ASR/OCR/LLM 요약별 대기/처리중/완료/실패 카운트 테이블 |
| 전체 통계 | 전체 파일 수, 완료 수, 완료율 |
| 시간대별 처리량 | 24시간 ASR/OCR/요약 처리량 스택 바 차트 |
| 타입별 분포 | AUDIO/DOCUMENT/PORTRAY 비율 파이 차트 |
| 최근 실패 작업 | 실패한 작업 목록 (파일명, 상태, 경과시간) |
| Stuck 작업 | 30분 이상 처리 중인 작업 목록 |

**파이프라인별 상태 매핑:**

| 파이프라인 | ContentType | 처리 상태 | 완료 상태 | 실패 상태 |
|-----------|-------------|----------|----------|----------|
| ASR | AUDIO | QUEUED, PULLING, PROCESSING | SUMMARY_QUEUED+ | ASR_FAILED |
| OCR | DOCUMENT | QUEUED, OCR_PROCESSING | SUMMARY_QUEUED+ | OCR_FAILED |
| LLM 요약 | ALL | SUMMARY_QUEUED, SUMMARIZING | COMPLETED | SUMMARY_FAILED |

---

## 로그 필터링

### 헬스체크/메트릭 로그 제외

노이즈 감소를 위해 헬스체크 및 메트릭 로그를 필터링합니다.

**Backend (`backend/app/main.py`):**
- `/health`, `/metrics`, `/ready`, `/livez`, `/readyz` 경로
- 성공 응답(200, 204)만 제외, **에러는 로깅**

**Promtail (`infra/promtail/config.yaml`):**
```yaml
pipeline_stages:
  - drop:
      expression: '(GET|POST) /health'
  - drop:
      expression: '(GET|POST) /metrics'
```

### 필터링 원칙

| 응답 코드 | 로깅 여부 | 이유 |
|-----------|----------|------|
| 200, 204 | ❌ 제외 | 정상 동작, 노이즈 |
| 4xx, 5xx | ✅ 로깅 | 문제 감지 필요 |

---

## 보안

### Grafana 관리자 비밀번호

Grafana admin 비밀번호는 환경변수로 관리합니다.

**docker-compose.yml:**
```yaml
grafana:
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=${GF_SECURITY_ADMIN_PASSWORD:-admin}
```

**프로덕션 설정:**
```bash
# .env 파일
GF_SECURITY_ADMIN_PASSWORD=your-secure-password
```

### Nginx 접근 제어

외부 사용자의 Grafana 관리 페이지 접근 차단:

```nginx
# 차단 경로
location ~ ^/grafana/(login|admin|org|profile|datasources) {
    return 403;
}
```

---

## 설정 파일

| 파일 | 설명 |
|------|------|
| `infra/loki/config.yaml` | Loki 설정 (S3 저장소) |
| `infra/promtail/config.yaml` | Promtail 설정 (Docker Service Discovery) |
| `infra/tempo/config.yaml` | Tempo 설정 |
| `infra/grafana/provisioning/datasources/datasource.yml` | Grafana 데이터소스 (Prometheus, Loki, Tempo, PostgreSQL) |
| `infra/grafana/dashboards/docker-logs.json` | 로그 대시보드 |
| `infra/grafana/dashboards/content-pipeline.json` | 콘텐츠 파이프라인 대시보드 |

---

## 문제 해결

### Loki에서 로그가 안 보임

```bash
# Promtail 상태 확인
docker logs asr-promtail --tail 20

# Loki 라벨 확인
curl -s "http://localhost:3100/loki/api/v1/labels"

# 컨테이너 목록 확인
curl -s "http://localhost:3100/loki/api/v1/label/container/values"
```

### trace_id가 항상 0으로 나옴

1. OpenTelemetry가 초기화되었는지 확인
2. 요청이 Frontend에서 시작되었는지 확인 (traceparent 헤더)
3. 백그라운드 작업은 trace_id=0이 정상

### Grafana Explore 접근 불가

`infra/grafana/grafana.ini`에서 확인:
```ini
[users]
viewers_can_edit = true
```

### 추론 컨테이너(`ai-*`) 로그가 수집 안 됨

1. Promtail이 Docker socket에 접근 가능한지 확인:
   ```yaml
   # infra/promtail/config.yaml — docker_sd_configs 활성화
   ```

2. 컨테이너 정상 기동 확인:
   ```bash
   docker logs ai-llm --tail 20
   docker ps --filter name=ai-
   ```

3. Loki에서 컨테이너 라벨 검색:
   ```bash
   curl -s "http://localhost:3100/loki/api/v1/label/container/values" | jq '.data[] | select(startswith("ai-"))'
   ```

---

## 성능 고려사항

### Loki 카디널리티

- `trace_id` 라벨은 high cardinality
- 대량 로그 시 Loki 성능에 영향 가능
- 필요시 `trace_id` 라벨 대신 로그 본문에서만 추출

### 로그 보존 기간

Loki 설정 (`infra/loki/config.yaml`):
```yaml
compactor:
  retention_enabled: true
  retention_delete_delay: 2h
  delete_request_store: s3
```

---

## 버전 히스토리

| 버전 | 날짜 | 변경사항 |
|------|------|----------|
| v1.0 | 2026-02-02 | 초기 버전 - Loki, Promtail, Tempo, trace_id 연계 로깅 |
| v1.1 | 2026-02-02 | LiteLLM trace_id 추가, 00_00 형식, 헬스체크 필터링, Grafana 보안 |
| v1.2 | 2026-02-03 | Content Pipeline 대시보드 추가 - PostgreSQL 데이터소스, ASR/OCR/LLM 요약 파이프라인 모니터링 |
| v1.3 | 2026-05-04 | Provider Manager(PM2) · LiteLLM Proxy · NPU(FLM) 폐기 반영. ai-gateway + 추론 컨테이너(`ai-*`) 직결 구조로 다이어그램·서비스 목록·trace 흐름 갱신 |
