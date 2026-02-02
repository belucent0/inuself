# Observability Stack v1.0

## 개요

분산 시스템의 로그 수집, 분산 추적, 메트릭 모니터링을 위한 옵저빌리티 스택.

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌──────────────────┐
│  Frontend   │───▶│   Backend   │───▶│   Worker    │───▶│ Provider Manager │
│  (Next.js)  │    │  (FastAPI)  │    │  (Celery)   │    │     (PM2)        │
└─────────────┘    └─────────────┘    └─────────────┘    └──────────────────┘
       │                  │                  │                    │
       │                  ▼                  ▼                    ▼
       │           trace_id=abc123    trace_id=abc123     trace_id=abc123
       │                  │                  │                    │
       │                  └────────┬─────────┴────────────────────┘
       │                           ▼
       │                    ┌─────────────┐
       │                    │  Promtail   │ ◀── Docker SD + PM2 logs
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
- Docker 컨테이너 로그 (자동 발견)
- PM2 로그 (Provider Manager)

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

---

## trace_id 연계 로깅

### 동작 원리

1. **Frontend**: 요청 시 `traceparent` 헤더 생성
2. **Backend**: OpenTelemetry로 trace context 수신, 로그에 trace_id 자동 포함
3. **Worker**: Celery 태스크 헤더에서 traceparent 추출, 로그에 trace_id 포함
4. **Provider Manager**: Redis Stream 메시지에서 traceparent 추출, 로그에 trace_id 포함

### 로그 포맷

```
# 요청 처리 (trace_id 포함)
2026-02-02 19:15:50 [INFO] trace_id=5a8ec4ab92b438994db128f42e3c2838 | StreamProcessor | Transcription completed

# 백그라운드 작업 (trace_id=0)
2026-02-02 19:31:13 [INFO] trace_id=0 | ProviderManager | Starting all provider processes...
```

### 서비스별 구현

| 서비스 | 파일 | 방식 |
|--------|------|------|
| Backend | `backend/app/core/logging.py` | Loguru patcher |
| Worker | `worker/logging_config.py` | Loguru patcher |
| Provider Manager | `infra/provider_manager/main.py` | logging.Formatter |

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

# 백그라운드 작업만 (trace_id=0)
{container="provider-manager"} |= "trace_id=0 |"

# 실제 요청만 (백그라운드 제외)
{container="provider-manager"} |~ "trace_id=[a-f0-9]{32}"
```

### 3. 분산 추적 (Jaeger)

```
URL: http://localhost:16686
```

1. Service 선택: `asr-backend`, `asr-worker`, `provider-manager`
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

---

## 설정 파일

| 파일 | 설명 |
|------|------|
| `infra/loki/config.yaml` | Loki 설정 (S3 저장소) |
| `infra/promtail/config.yaml` | Promtail 설정 (Docker SD, PM2) |
| `infra/tempo/config.yaml` | Tempo 설정 |
| `infra/grafana/provisioning/datasources/datasource.yml` | Grafana 데이터소스 |
| `infra/grafana/dashboards/docker-logs.json` | 로그 대시보드 |

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

### Provider Manager 로그가 수집 안 됨

1. `docker-compose.yml`에서 promtail 볼륨 마운트 확인:
   ```yaml
   volumes:
     - ./logs:/host-logs:ro
   ```

2. PM2 로그 경로 확인:
   ```bash
   pm2 show provider-manager | grep log
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
