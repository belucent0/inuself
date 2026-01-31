# Architecture Comparison (Phase 1)

## 1. 현재 아키텍처 (AS-IS)

Worker가 과도한 책임을 지고 있으며, WebSocket 프로토콜이 혼재되어 있습니다.

```mermaid
graph TD
    Client[Client]
    Backend[Backend Server]
    Worker[Worker (Celery)]
    PM[Provider Manager]
    Redis[Redis Stream]

    Client -- POST /api/asr/upload --> Backend
    Client -- WS /ws/asr (ASR+Notify) --> Backend
    Client -- SSE /api/ai/chat --> Backend

    subgraph "Backend (FastAPI)"
        API[API Handler]
        WS_H[WebSocket Handler (Mixed)]
        Queue[Task Queue]
    end

    subgraph "Worker (Heavy)"
        Pre[전처리 (Download/Convert)]
        Infer[GPU/NPU 호출]
        Post[후처리 (Merge/Filter)]
        Sum[LLM 요약 (3-Step)]
        Prompt[Backend Code Import]
    end

    Backend --> Queue
    Queue --> Worker
    Worker --> Infer
    Infer --> PM
    PM --> Redis
    Redis --> Backend
```

**주요 문제점:**
1. **Worker 책임 과다**: 전처리 + 추론 호출 + 후처리 + 요약까지 담당
2. **높은 결합도**: Worker가 Backend 코드를 직접 import (`app.prompts...`)
3. **프로토콜 혼재**: WebSocket 하나로 실시간 ASR과 상태 알림을 모두 처리
4. **로직 분산**: 요약/후처리 로직이 Worker와 Backend에 흩어져 있음

---

## 2. 개선 후 아키텍처 (TO-BE)

Worker를 경량화하고 AI 로직을 Backend로 중앙화하며, 프로토콜을 용도별로 명확히 분리합니다.

```mermaid
graph TD
    Client[Client]
    Backend[Backend Server]
    Worker[Worker (Preprocessor)]
    PM[Provider Manager]
    Redis[Redis Stream]

    Client -- POST /api/asr/file --> Backend
    Client -- SSE /events (Notify) --> Backend
    Client -- SSE /api/ai/chat --> Backend
    Client -- WS /ws/asr (Stream Only) --> Backend

    subgraph "Backend (Centralized Logic)"
        API[API Handler]
        WS_ASR[WS Handler (ASR Only)]
        Proc[ProcessingService]
        Prom[PromptManager]
        
        subgraph "Processing Logic"
            Post_Logic[후처리 (Merge/Filter)]
            Sum_Logic[LLM 요약 (LangGraph)]
        end
    end

    subgraph "Worker (Lightweight)"
        W_Pre[전처리 (Download/Convert)]
        W_Infer[GPU/NPU 호출 (Litellm)]
        W_Raw[원시 결과 반환]
    end

    Backend --> Queue
    Queue --> Worker
    Worker --> W_Pre --> W_Infer
    W_Infer --> PM
    PM --> Redis
    Redis --> Backend
    Backend --> Proc
```

**개선 효과:**
1. **Worker 경량화**: 파일 전처리 및 추론 요청만 담당
2. **단일 책임 원칙**: AI 로직/프롬프트는 Backend가 중앙 관리
3. **프로토콜 명확화**: 
   - 단방향 알림 → **SSE**
   - 양방향 오디오 → **WebSocket**
4. **결합도 감소**: Worker와 Backend 간 코드 의존성 제거

---

## 3. 핵심 변경 사항

| 구분 | AS-IS | TO-BE |
|------|-------|-------|
| **Worker 역할** | 전처리+추론+후처리+요약 | **전처리 + 추론 호출** |
| **후처리/요약** | Worker 내부 로직 | **Backend ProcessingService** |
| **프롬프트** | Worker가 Backend import | **Backend 중앙 관리** |
| **상태 알림** | WebSocket | **SSE (/events)** |
| **실시간 ASR** | WebSocket (혼재) | **WebSocket (전용)** |
