# Valkey (Redis) Architecture & Strategy v1.0

> **Version:** 1.1 (Enhanced Visualization)  
> **Last Updated:** 2026-02-01  
> **Role:** Celery Broker, Browser Session Store, Event Bus (Pub/Sub) & High-Speed Cache

> ⚠️ **v1.2.0 마이그레이션 경고** (2026-05-04 갱신)
>
> 본 문서의 추론 라우팅용 Stream 키들(`stream:media:requests`, `stream:chat:requests`,
> `stream:recap:requests`, `gpu:responses`)은 **Provider Manager + LiteLLM 프록시
> 구조를 위한 것으로 v1.2.0에서 폐기되었습니다.** 현재 backend/worker는 ai-gateway가
> 추론 컨테이너(`ai-llm`/`ai-asr-vllm`/`ai-embedding`/`ai-diarize`)를 httpx로
> 직접 호출하며, 워커 작업 큐는 **Celery (Redis broker)** 만 사용합니다.
>
> Pub/Sub `events:*` 채널은 유효하며, 브라우저 인증은 `auth:*` 세션 키를 사용합니다.
> 현재 `allkeys-lru` 정책에서는 세션도 eviction 대상이므로 배포 acceptance에서
> `evicted_keys=0`을 확인하고 증가 시 auth 전용 Valkey 분리를 검토합니다.
> 본 문서는 v1.1 시점의 SoT로 보존되며, 현행 운영 다이어그램은
> [`architecture-v1.2.0.md`](./architecture-v1.2.0.md)를 참조하세요.

---

## 📋 Quick Overview (핵심 요약)

| 구분 | 이름 (Key/Channel) | 용도 | 특징 |
| :--- | :--- | :--- | :--- |
| **Stream** ⚠️ | `stream:media:requests` | ASR/OCR (파일 처리) — *v1.2.0 폐기* | MAXLEN 50. 무거운 데이터 격리. |
| **Stream** ⚠️ | `stream:chat:requests` | Chat/Reasoning — *v1.2.0 폐기* | MAXLEN 3000. 가벼운 텍스트. |
| **Stream** ⚠️ | `stream:recap:requests`| Summary — *v1.2.0 폐기* | MAXLEN 1000. 백그라운드 처리. |
| **Pub/Sub**| `events:*` | **실시간 알림** | 저장 안 됨. Frontend 진행률 표시용. |
| **Session** | `auth:session:{sha256}` | **HttpOnly 브라우저 세션** | 원문 token 미저장, idle 12시간/absolute 14일. |
| **Policy** | `allkeys-lru` | **메모리 정책** | 모든 키가 eviction 대상이므로 `evicted_keys=0` 운영 감시 필수. |

---

## 1. 🏗 System Architecture (Data Flow)

**Worker**와 **Backend**가 **Valkey**를 중심으로 어떻게 데이터를 주고받는지 보여주는 흐름도입니다.

```text
                                [ User / Frontend ]
                                        ▲
                                        │ authenticated SSE (Real-time Events)
                                        ▼
+-------------------------------------------------------------------------------+
| [ Backend Service ]                                                           |
|  - API Server                                                                 |
|  - Session validation + user-scoped SSE ◀──────────────────────┐             |
+-----------------------------------------------------------------|--------------+
            │ (Enqueue Task)                                      │
            ▼                                                     │
+-----------------------+       +---------------------------------|--------------+
| [ Worker (Celery) ]   |       |      [ Valkey (Redis) ]         |              |
|                       |──────▶|                                |              |
| 1. Upload File        | XADD  |  [ Streams (Persistent) ]       |              |
| 2. Publish Request    |──────▶|  ● media:requests (Max: 50)    |───┐          |
| 3. Wait Response      |◀──────|  ● chat:requests  (Max: 3000)  |   │          |
|                       |       |  ● recap:requests (Max: 1000)   |   │          |
|                       |       |  ● gpu:responses  (Max: 300)    |◀─┼──┐       |
| 4. Publish Event      |──┐    |                                 |   │  │       |
+-----------------------+  │    |  [ Pub/Sub (Volatile) ]         |   │  │       |
                           └───▶|  ⚡ events:file_progress:*     |───┘  │      |
                          PUBLISH|  ⚡ events:asr_stream:*        |     │       |
                                +---------------------------------+     │       |
                                                                        │       |
                                           XREADGROUP (Consumer)        │       |
                                        ┌───────────────────────────────┘       |
                                        ▼                                       │
                                +-----------------------+                       │
                                | [ Provider Manager ]  |                       │
                                |  - Stream Consumer    |───────────────────────┘
                                +-----------------------+      XADD (Response)
                                        │ HTTP
                                        ▼
                                +-----------------------+
                                | [ GPU Servers ]       |
                                |  - Whisper / LLM      |
                                |  - FLM / Llama        |
                                +-----------------------+
```

---

## 2. 🚨 Emergency Operations (트러블슈팅)

메모리 부족(OOM)이나 지연 발생 시 즉시 실행할 명령어들입니다.

### 메모리 급증 시 (긴급 조치)
특정 스트림의 데이터가 너무 많이 쌓였을 때, 최신 데이터만 남기고 잘라냅니다.
```bash
# 컨테이너 접속
docker exec -it asr-valkey valkey-cli

# 1. 미디어 스트림 정리 (가장 무거움 - 50개 유지)
XTRIM stream:media:requests MAXLEN 50

# 2. 채팅 스트림 정리 (3000개 유지)
XTRIM stream:chat:requests MAXLEN 3000

# 3. 요약 스트림 정리 (1000개 유지)
XTRIM stream:recap:requests MAXLEN 1000

# 4. 메모리 파편화 정리 (OS 반납 강제)
MEMORY PURGE
```

### 개발 환경 초기화
모든 데이터를 삭제하고 초기 상태(약 1~2MB)로 되돌립니다.
```bash
FLUSHALL
MEMORY PURGE
```

---

## 3. 🌊 Stream Topology (3-Track Strategy)

V8.1 아키텍처부터는 데이터의 크기와 목적에 따라 **3개의 독립된 트랙**으로 스트림을 분리하여 운영합니다.

| 트랙 (Track) | 스트림 키 (Key) | 권장 MAXLEN | 데이터 성격 | 용도 |
| :--- | :--- | :--- | :--- | :--- |
| **Media** | `stream:media:requests` | **50** | **Very High** (MB) | **ASR(Audio), OCR(Vision)**<br>파일 바이너리가 포함되므로 가장 짧게 유지해야 함. |
| **Chat** | `stream:chat:requests` | **3,000** | Low (KB) | **Simple Chat, Thinking**<br>텍스트 위주라 길게 보관 가능. 사용자 응답 속도 중요. |
| **Recap** | `stream:recap:requests` | **1,000** | Medium (KB) | **Summarization**<br>백그라운드 작업. |
| **Control** | `stream:provider:events` | 1,000 | Low | 시스템 제어 신호. |
| **Response** | `stream:gpu:responses` | 300 | Medium | 작업 결과 반환. |

> **💡 설계 의도:** 무거운 Media 데이터가 Chat 처리를 방해하거나 메모리를 잠식하지 않도록 격리함.

---

## 4. 📢 Pub/Sub Topology (Real-time Events)

Redis Pub/Sub은 **"데이터 저장"이 아닌 "실시간 알림(Notification)"** 용도로 사용합니다.
Worker가 발행(Publish)하고, Backend가 구독(Subscribe)하여 인증된 SSE로 전달하는 구조입니다.
파일 진행률은 Backend가 `events:file_progress:global`을 구독한 뒤 DB 소유권을 확인해
현재 HttpOnly 세션 사용자에게만 SSE로 전달합니다. 공개 `/ws/*` 경로는 없습니다.

| 채널 패턴 (Channel) | 용도 | 발행자 (Producer) | 구독자 (Consumer) |
| :--- | :--- | :--- | :--- |
| `events:file_progress:{file_id}` | 파일 처리 진행률 (Processing, Download...) | Worker | 내부 소비자 |
| `events:file_progress:global` | 파일 처리 진행률 집계 | Worker | Backend 사용자 격리 SSE |
| `events:asr_stream:{file_id}` | 실시간 ASR 텍스트 스트리밍 | Worker | 내부 소비자 |
| `events:llm_stream:{file_id}` | 실시간 LLM 토큰 스트리밍 | Worker | 내부 소비자 |
| `events:content_created` | 새 콘텐츠 생성 알림 (목록 갱신용) | Worker | Backend |

> **주의:** Pub/Sub 메시지는 소비자가 없으면 **즉시 사라집니다 (휘발성).** 데이터 저장이 필요한 경우 Stream을 사용해야 합니다.

---

## 5. 💾 Memory Management & TTL

### 용량 제한 (Capacity)
*   **Limit:** **2GB** (Docker Container Limit)
*   **Safe Zone:** 1GB 이하 권장 (Copy-on-Write 버퍼 고려)

### 관리 정책 (Eviction Policy)
*   **현재 설정:** `maxmemory-policy allkeys-lru`
*   **동작 방식:** 메모리가 가득 차면 모든 키가 LRU eviction 대상입니다. 세션 유실을 로그인 만료로 오인하지 않도록 `INFO stats`의 `evicted_keys=0`을 배포와 운영에서 확인합니다.

### TTL (Time-To-Live) Policies
| 데이터 종류 | 패턴 (Pattern) | TTL (유효기간) | 비고 |
| :--- | :--- | :--- | :--- |
| **검색 캐시** | `cache:search:*` | **1시간** (3600s) | 실시간성 보장을 위해 짧게 유지. |
| **대화 히스토리** | `history:{session_id}` | **7일** | 단기 기억 유지. 장기 기억은 DB(Vector Store)로 이관. |
| **작업 임시 데이터** | `job:{job_id}` | **24시간** | 작업 실패 시 디버깅용. 성공 시 즉시 삭제 권장. |
| **브라우저 세션** | `auth:session:{sha256(token)}` | **idle 12시간**, absolute 14일 이내 | 원문 token은 cookie에만 존재. 일반 HTTP 요청에서 sliding touch. |
| **사용자 세션 generation** | `auth:user-session-version:{user_id}` | 만료 없음 | logout-all 시 증가. |
| **로그인 실패** | `auth:login-failure:{sha256(login_id)}` | **5분** | 5회 실패 fixed window. |
