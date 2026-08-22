# AI 채팅 워크플로우

> 현행 기준: Backend BFF + Celery Agent Worker + Valkey Pub/Sub + PostgreSQL 복구

## 전체 흐름

```text
Browser
  │ POST /api/threads 또는 /api/threads/{thread_id}/messages
  │ { query, mode, stream: true }
  ▼
Backend (FastAPI)
  ├─ 사용자 메시지와 queued AI 메시지를 PostgreSQL에 commit
  ├─ Celery `agent` 큐에 message_id enqueue
  └─ `events:agent:{message_id}` subscribe 후 accepted SSE 전송
  ▼
Agent Worker
  ├─ PostgreSQL에서 요청과 대화 history 로드
  ├─ LangGraph 실행
  ├─ 상태·사고 단계·출처·토큰을 Valkey Pub/Sub으로 발행
  ├─ 5초마다 누적 partial_content를 PostgreSQL에 snapshot
  └─ 최종 content/metadata와 completed 또는 failed 상태 저장
  ▼
Backend Pub/Sub relay ── SSE ──► Browser
```

Backend는 LangGraph를 요청 프로세스에서 직접 실행하지 않습니다. 클라이언트 연결이 끊겨도 Agent Worker 작업은 계속되며 PostgreSQL이 source of truth입니다.

## API 계약

| 요청 | `stream: false` | `stream: true` |
|------|-----------------|----------------|
| `POST /api/threads` | `{thread_id, message_id, user_message_id}` JSON | 같은 ID를 `accepted`로 먼저 보내고 inline SSE 계속 |
| `POST /api/threads/{thread_id}/messages` | queued JSON | `accepted`부터 시작하는 inline SSE |
| `POST /api/threads/{thread_id}/regenerate` | 해당 없음 | `accepted`부터 시작하는 SSE |
| `GET /api/threads/{thread_id}/messages/{message_id}/stream` | 해당 없음 | 기존 작업 재접속/복구 SSE |

`accepted` 데이터:

```json
{
  "thread_id": "...",
  "message_id": "...",
  "user_message_id": "..."
}
```

주요 이벤트는 `accepted`, `status`, `thinking_step`, `query_analysis`, `search_queries`, `sources`, `token`, `content`, `partial_restore`, `done`, `error`입니다.

## 저장과 실시간 전달

| 데이터 | 저장 위치 | 주기/수명 |
|--------|-----------|-----------|
| 작업 요청 | Valkey Celery broker | Worker가 ack할 때까지 |
| 실시간 token/status | Valkey Pub/Sub | 휘발성, 구독 중인 Backend에만 전달 |
| 메시지 상태 | PostgreSQL `ai_message` | 상태 전환마다 |
| 생성 중 본문 | PostgreSQL `partial_content` | 최대 5초 간격 snapshot |
| 최종 본문/metadata | PostgreSQL | 완료 시 영속화 |

토큰을 Redis Stream이나 Kafka에 누적하지 않습니다. Pub/Sub 이벤트를 놓친 Backend는 GET SSE에서 PostgreSQL의 partial snapshot 또는 최종 응답을 읽어 보정합니다.

## 연결 복구 정책

Frontend의 `chatStreamService.ts`가 Fetch `ReadableStream`을 처리합니다.

1. POST SSE에서 `accepted`를 받은 뒤 연결이 끊기면 동일 `message_id`의 GET SSE로 전환합니다.
2. GET은 0초, 1초, 2초, 5초에 시도하며 1/2/5초에는 ±20% jitter를 적용합니다.
3. 최대 4회 실패하면 한 번만 최종 오류를 표시합니다.
4. GET 401은 인증 토큰을 한 번 갱신하고 동일 GET을 한 번 재요청합니다.
5. 페이지 이탈이나 content 변경 시 AbortController로 POST/GET/대기 timer를 함께 취소합니다.

다음은 자동 재시도하지 않습니다.

- `accepted` 이전 POST 실패: 요청 생성 여부가 불명확해 중복 메시지 위험이 있음
- Worker가 저장한 `failed`/`cancelled` 및 terminal `error`
- 4xx, UI callback 오류, 사용자 abort

사용자 abort는 브라우저의 SSE 연결과 재접속만 중단합니다. 현재 별도 Agent 취소 API는 없으므로 Worker 작업은 완료될 때까지 계속됩니다.

POST 자체의 안전한 자동 재시도는 `Idempotency-Key`를 도입한 뒤 추가합니다.

## 동시성과 장애 복구

- `agent-worker`의 기본 동시성은 1이며 `AGENT_WORKER_CONCURRENCY`로 조절합니다.
- `worker_prefetch_multiplier=1`로 한 child가 앞선 작업을 과도하게 선점하지 않습니다.
- `active_agent_thread:{thread_id}`로 한 thread에는 한 응답만 실행합니다.
- `lock:agent:message:{message_id}`로 Celery redelivery의 중복 실행을 막습니다.
- DB commit 후 broker enqueue가 실패한 queued 메시지는 Watchdog이 dispatch marker를 확인해 다시 enqueue합니다.

## 추론 라우팅

Agent Worker는 OpenAI 호환 요청을 ai-gateway로 보냅니다.

```text
RoutingProfile(chat, none, local_only)
  → Windows Host FastFlowLM NPU 우선
  → full/unhealthy/첫 chunk 전 실패 시 ai-llm GPU fallback

RoutingProfile(chat, medium|high, local_only)
  → WSL Docker ai-llm (vLLM Gemma 4)
```

FastFlowLM은 Compose 외부 프로세스입니다. `NPU_LLM_BASE_URL`이 비어 있으면 NPU 경로는 비활성화되며 GPU로 바로 라우팅합니다.

## 주요 파일

| 역할 | 파일 |
|------|------|
| 요청 영속화·enqueue·SSE relay | `backend/app/controllers/ai_chat_controller.py` |
| Agent Celery 설정 | `backend/app/agent_celery.py` |
| LangGraph 실행·Pub/Sub·DB 저장 | `backend/app/tasks/agent_task.py` |
| queued handoff 복구 | `backend/app/services/agent_job_recovery.py` |
| Frontend SSE 파싱·재접속 | `frontend/src/shared/services/chatStreamService.ts` |
| NPU/GPU 라우팅 | `infra/ai-gateway/routes/chat.py` |
