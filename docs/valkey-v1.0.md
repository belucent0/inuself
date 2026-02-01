# Valkey (Redis) Architecture & Strategy v1.0

> **Version:** 1.1 (Enhanced Visualization)  
> **Last Updated:** 2026-02-01  
> **Role:** Message Broker (Streams), Event Bus (Pub/Sub) & High-Speed Cache

---

## 📋 Quick Overview (핵심 요약)

| 구분 | 이름 (Key/Channel) | 용도 | 특징 |
| :--- | :--- | :--- | :--- |
| **Stream** | `stream:media:requests` | **ASR/OCR (파일 처리)** | **MAXLEN 50**. 무거운 데이터 격리. |
| **Stream** | `stream:chat:requests` | **Chat/Reasoning** | **MAXLEN 3000**. 가벼운 텍스트. |
| **Stream** | `stream:recap:requests`| **Summary** | **MAXLEN 1000**. 백그라운드 처리. |
| **Pub/Sub**| `events:*` | **실시간 알림** | 저장 안 됨. Frontend 진행률 표시용. |
| **Policy** | `volatile-lru` | **메모리 정책** | 메모리 부족 시 **TTL 있는 캐시만 삭제**. |

---

## 1. 🏗 System Architecture (Data Flow)

**Worker**와 **Backend**가 **Valkey**를 중심으로 어떻게 데이터를 주고받는지 보여주는 흐름도입니다.

```text
                                [ User / Frontend ]
                                        ▲
                                        │ WebSocket (Real-time Events)
                                        ▼
+-------------------------------------------------------------------------------+
| [ Backend Service ]                                                           |
|  - API Server                                                                 |
|  - RedisListener (Sub: events:*) ◀─────────────────────────────┐             |
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
Worker가 발행(Publish)하고, Backend가 구독(Subscribe)하여 WebSocket으로 전달하는 구조입니다.

| 채널 패턴 (Channel) | 용도 | 발행자 (Producer) | 구독자 (Consumer) |
| :--- | :--- | :--- | :--- |
| `events:file_progress:{file_id}` | 파일 처리 진행률 (Processing, Download...) | Worker | Backend (WebSocket) |
| `events:asr_stream:{file_id}` | 실시간 ASR 텍스트 스트리밍 | Worker | Backend (WebSocket) |
| `events:llm_stream:{file_id}` | 실시간 LLM 토큰 스트리밍 | Worker | Backend (WebSocket) |
| `events:content_created` | 새 콘텐츠 생성 알림 (목록 갱신용) | Worker | Backend (WebSocket) |

> **주의:** Pub/Sub 메시지는 소비자가 없으면 **즉시 사라집니다 (휘발성).** 데이터 저장이 필요한 경우 Stream을 사용해야 합니다.

---

## 5. 💾 Memory Management & TTL

### 용량 제한 (Capacity)
*   **Limit:** **2GB** (Docker Container Limit)
*   **Safe Zone:** 1GB 이하 권장 (Copy-on-Write 버퍼 고려)

### 관리 정책 (Eviction Policy)
*   **권장 설정:** `maxmemory-policy volatile-lru`
*   **동작 방식:** 메모리가 가득 차면 **TTL(유효기간)이 설정된 캐시 키부터 삭제**하고, TTL이 없는 중요 데이터(스트림 큐, 설정값)는 보존하여 시스템 셧다운을 방지함.

### TTL (Time-To-Live) Policies
| 데이터 종류 | 패턴 (Pattern) | TTL (유효기간) | 비고 |
| :--- | :--- | :--- | :--- |
| **검색 캐시** | `cache:search:*` | **1시간** (3600s) | 실시간성 보장을 위해 짧게 유지. |
| **대화 히스토리** | `history:{session_id}` | **7일** | 단기 기억 유지. 장기 기억은 DB(Vector Store)로 이관. |
| **작업 임시 데이터** | `job:{job_id}` | **24시간** | 작업 실패 시 디버깅용. 성공 시 즉시 삭제 권장. |
