# Architecture Improvement Plan

## 1. Backend/Worker 아키텍처 (SOLID & Prompt Injection)

현재 Worker가 전처리, 후처리, 요약 로직까지 과도하게 많은 책임을 지고 있어, Backend 코드 의존성이 높고 변경에 취약합니다.
SOLID 원칙(SRP, DIP)에 기반하여 **"Prompt Injection" 패턴**을 도입, Worker를 순수한 실행(Execution) 도구로 전환하고 모든 제어 로직을 Backend로 중앙화합니다.

**핵심 설계 원칙:**
*   **SRP (Single Responsibility):** Worker는 오직 "실행"만 담당. 로직(프롬프트 구성, 후처리)은 Backend가 담당.
*   **DIP (Dependency Inversion):** Worker는 Backend 코드(`app.prompts` 등)에 의존하지 않고, Backend가 주입한 데이터(Prompt)에만 의존.
*   **Prompt Injection:** Backend가 완성된 프롬프트(`messages`)를 구성하여 Worker에 주입. Worker는 이를 그대로 LLM에 전달.

| 작업 | 설명 | 우선순위 |
|------|------|----------|
| **Worker 역할 축소** | **Logic 제거, Execution 전념** | **높음** |
| | *AS-IS: 전처리 + 추론 + 후처리(병합) + 요약(프롬프트 구성) <br> TO-BE: 전처리 + 추론 (Dumb Proxy). 후처리 및 요약 로직은 Backend로 이동* | |
| **Prompt Centralization** | **Prompt Injection 패턴 도입** | **높음** |
| | *Backend가 전체 프롬프트(`messages`)를 구성하여 Worker Queue에 주입. Worker는 단순 전달자 역할 수행. `app.prompts` 의존성 제거* | |
| **ASR Post-processing 이동** | Worker → Backend (`StreamConsumer`) | 높음 |
| | *ASR 결과의 병합/필터링 로직을 Backend의 StreamConsumer로 이동. Worker는 Raw 텍스트만 전송* | |
| **Worker Docker 경량화** | Backend 코드 의존성 완전 제거 | 중간 |
| | *로직 이동 완료 후, Worker에서 Backend 소스코드 import 제거 및 Docker 이미지 경량화* | |

## 1.5 DB Schema Normalization (New)

| 작업 | 설명 | 우선순위 |
|------|------|----------|
| **File/Content 분리** | 물리적 파일(File)과 비즈니스 엔티티(Content) 분리 | 높음 |
| | *File: S3 Key, Size, MimeType (불변) / Content: Title, Summary, Status (가변). 1:N 관계 지원 및 역할 분리* | |
| **JobHistory 테이블** | 작업 이력 및 상태 변경 로그 정규화 | 높음 |
| | *JSON 로그 대신 정규화된 테이블에 Job ID, Trace ID, Status, Stage, Duration 저장. 통계 쿼리 및 디버깅 용이성 확보, 상태 머신 이력 추적* | |

## 2. 통신 프로토콜 정리 (Optional / Phase 1.5)

현재 WebSocket이 콘텐츠 상태 알림과 실시간 ASR에 혼재되어 사용되고 있습니다. 15명 규모에서는 과도한 분리가 오버엔지니어링이므로, 프로토콜 분리는 선택 사항으로 미룹니다.

| 작업 | 설명 | 우선순위 |
|------|------|----------|
| **콘텐츠 상태 알림** | WebSocket → SSE 전환 | **낮음 (보류)** |
| | *현재 WebSocket으로 상태 알림을 보내는데, 단방향만 필요. SSE로 변경하여 자동 재연결 및 더 단순한 프로토콜 적용* | |
| **실시간 ASR** | WebSocket 유지 (오디오 스트림) | 유지 |
| | *오디오 바이너리 스트리밍에 WebSocket이 적합. 15명 규모에서 Backend가 직접 처리해도 무리 없음* | |
| **LLM 채팅** | SSE 유지 | 유지 |
| | *이미 SSE로 구현되어 있으며 텍스트 스트리밍에 적합. 유지* | |
| **파일 업로드** | REST API 유지 | 유지 |
| | *동기/비동기 명확한 구분 유지* | |

## 3. Observability 개선

현재 Jaeger로 trace는 수집되지만, 로그는 각 컨테이너에 흩어져 있어 디버깅이 어렵습니다. 또한 span이 불규칙하게 생성되어 noise가 많습니다. 상용 서비스 수준의 통합 observability를 구축합니다.

| 작업 | 설명 | 우선순위 |
|------|------|----------|
| **Loki + Promtail 도입** | 로그 중앙 집중화 | 높음 |
| | *로그가 Docker 컨테이너별로 흩어져 `docker logs`로 확인해야 함. Loki로 중앙 집중하고 trace_id로 로그 검색 가능하게 하여 디버깅 시간 단축* | |
| **Span 샘플링 조정** | 불규칙한 span 생성 문제 해결 | 높음 |
| | *자동 계측으로 HTTP GET, health check 등 불필요한 span이 과도하게 생성됨. 샘플링 10%로 조정하고 health check 등 제외하여 노이즈 감소* | |
| **Trace ID - 로그 연결** | Grafana에서 통합 검색 | 중간 |
| | *trace_id로 Jaeger와 로그를 연결하여 "PROCESSING 10분" 상황에서 원인을 5분 내에 찾을 수 있도록 통합 뷰 구축* | |
| **Tempo 검토** | Jaeger 대체 (Grafana 통합) | 중간 |
| | *Jaeger는 별도 UI, Tempo는 Grafana 내장 패널. S3 저장으로 비용 효율적. Loki 도입 후 통합 검토* | |
| **Health check 제외** | 불필요한 span 생성 방지 | 중간 |
| | *Kubernetes 환경의 health check, metrics 수집 등이 span을 생성하여 비용/노이즈 증가. 제외 설정* | |

## 4. 상태 관리

현재는 단순한 상태 필드만 있고, 유효한 상태 전이나 timeout 처리가 없어 STUCK 상태 추적이 어렵습니다. 엔터프라이즈급 상태 머신을 구현하여 운영 안정성을 확보합니다.

| 작업 | 설명 | 우선순위 |
|------|------|----------|
| **상태 머신 구현** | DB 기반 + 유효 전이 검증 | 높음 |
| | *현재 PENDING→COMPLETED 같은 무효 전이 가능. DB 기반으로 유효 전이만 허용하고 trace_id와 연결하여 "어디서 멈췄는지" 추적 가능하게 구현* | |
| **상태별 timeout** | STUCK 감지 및 복구 | 중간 |
| | *INFERENCE 상태가 10분 이상 지속 시 FAILED로 전이. 자동 복구 또는 알림* | |
| **재시도 로직** | FAILED → 재시도 → 포기 | 중간 |
| | *일시적 GPU/NPU 오류 시 최대 3회 재시도. 3회 초과 시 포기 및 사용자 알림* | |
| **사용자 알림** | SSE로 실시간 상태 전송 | 중간 |
| | *처리 진행률(20%, 60% 등)을 SSE로 실시간 전송하여 사용자 경험 향상* | |

## 5. 모니터링/알림

Prometheus와 Grafana가 있지만, 통합된 대시보드와 알림 체계가 미흡합니다. SRE 관점에서 가시성을 확보합니다.

| 작업 | 설명 | 우선순위 |
|------|------|----------|
| **Grafana 대시보드** | 통합 뷰 (trace + log + metric) | 중간 |
| | *현재 메트릭만 보임. Loki 도입 후 trace_id로 로그 + 메트릭 + 트레이스를 한 패널에서 조회 가능한 통합 대시보드 구성* | |
| **Alert 설정** | GPU 풀, Worker 오류 등 | 낮음 |
| | *GPU 사용률 > 90%, Worker 오류율 > 5% 등 임계값 초과 시 Slack/Email 알림* | |
| **SLI/SLO 정의** | 가용성, 응답 시간 목표 | 낮음 |
| | *처리 성공률 > 99%, p95 응답 시간 < 3초 등 서비스 수준 목표 정의 및 측정* | |

## 6. Inference Server 통합 (lemonade-server)

현재 FLM(NPU) + llama-server(GPU LLM) + whisper.cpp(GPU ASR)가 분리되어 관리 포인트가 많습니다. lemonade-server로 통합하여 단일 바이너리로 여러 모델을 서빙하고 운영 복잡도를 낮춥니다.

### 단계별 접근 방식
1. **도입 단계**: lemonade-server 병렬 구축 및 테스트
2. **검증 단계**: 기존 서비스와 비교 테스트 (성능, 안정성)
3. **통폐합 단계**: FLM + llama-server + whisper.cpp → lemonade-server 전환

| 작업 | 설명 | 우선순위 |
|------|------|----------|
| **lemonade-server 도입** | FLM/llama.cpp/whisper.cpp 통합 오픈소스 도입 | 중간 |
| | *현재 3개의 별도 서비스를 단일 오픈소스로 통합하여 관리 포인트 감소. Windows NPU/GPU 호환성 확인 필요* | |
| **병렬 테스트 환경 구축** | 기존 서비스와 lemonade-server 동시 운영 | 중간 |
| | *A/B 테스트 환경 구축. 기존 서비스 유지하며 lemonade-server로 일부 트래픽 분산하여 리스크 없이 검증* | |
| **성능/안정성 비교** | 처리량, 지연시간, 자원 사용률 비교 분석 | 중간 |
| | *동일한 요청에 대한 처리량(TPS), p95/p99 지연시간, GPU/NPU 메모리 사용률 비교. regression 방지* | |
| **호환성 검증** | 기존 API 인터페이스 유지 여부 확인 | 높음 |
| | *Litellm 연동, OpenAI API 형식 호환성 확인. 기존 클라이언트 코드 수정 없이 전환 가능해야 함* | |
| **단계적 마이그레이션** | GPU → NPU 순으로 전환 (롤백 계획 수립) | 중간 |
| | *리스크 관리를 위해 GPU 먼저 전환 후 NPU로 확장. 문제 발생 시 즉시 롤백 가능한 구조* | |
| **기존 서비스 통폐합** | FLM + llama-server + whisper.cpp 제거 | 낮음 (검증 후) |
| | *lemonade-server 검증 완료 후 기존 3개 서비스 제거 및 인프라 단순화. 롤백 대비 보관 기간 설정* | |

**참고**: https://github.com/lemonade-sdk/lemonade
- 지원 구성: FLM, llama.cpp, whisper.cpp 통합
- Windows NPU/GPU 호환성 확인 필요

---

## 실행 순서

### Phase 1: Worker 역할 축소 & Prompt Injection (2주)
1. **ASR Post-processing 이관**: Worker의 ASR 병합/필터링 로직을 Backend `StreamConsumer`로 이동.
2. **LLM Logic Centralization**: Backend에서 요약 프롬프트 구성 및 파싱 로직 구현 (Prompt Injection).
3. **Worker Code Cleanup**: Worker에서 Backend import 제거 및 Dumb Proxy화.

### Phase 1.5: 프로토콜 및 DB (선택/병행)
4. (Optional) 통신 프로토콜 정리 (WebSocket → SSE)
5. DB Schema Normalization (File/Content 분리, JobHistory)

### Phase 2: Observability (1주)
6. Loki + Promtail 도입
7. Span 샘플링 조정
8. Trace ID - 로그 연결

### Phase 3: 상태 관리 (1주)
9. 상태 머신 구현
10. State Watchdog (Stuck 감지)
11. Timeout 및 재시도 로직
12. Grafana 대시보드 구성

### Phase 4: 검증 (1주)
13. 통합 테스트
14. 부하 테스트 (15명 → 50명)
15. 문서화 완료

### Phase 5: Inference Server 통합 (병행, 4~6주)
16. lemonade-server 도입 및 병렬 구축
17. 기존 서비스와 성능/안정성 비교 테스트
18. 호환성 검증 및 API 인터페이스 확인
19. 단계적 마이그레이션 (GPU → NPU)
20. 기존 서비스 통폐합 (FLM + llama-server + whisper.cpp)
