# AI 채팅 워크플로우 (V8.3)

## 개요

AI 채팅 시스템의 전체 데이터 플로우와 각 컴포넌트의 역할을 설명합니다.

## 아키텍처 계층

```
┌─────────────────────────────────────────────────────────────┐
│ 1. API Layer (FastAPI)                                      │
│    - HTTP 요청/응답 처리                                      │
│    - 인증, 유효성 검사                                         │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Controller Layer                                          │
│    - 비즈니스 로직 조율                                        │
│    - 대화 히스토리 관리 (Redis)                               │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Agent Orchestration Layer (LangGraph)                    │
│    - 워크플로우 정의 및 실행                                   │
│    - 노드 간 상태 관리                                        │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Node Layer (비즈니스 로직)                                │
│    - Intent 분석, 검색, RAG, 추론, 생성                      │
│    - 각 노드는 독립적으로 작동                                 │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Service/Tool Layer                                       │
│    - 외부 API 호출 (LLM, 검색, DB)                          │
│    - 유틸리티 함수                                           │
└─────────────────────────────────────────────────────────────┘
```

## LangChain/LangGraph의 역할

### LangGraph가 담당하는 것 (Agent Orchestration)

```python
# graph.py
workflow = StateGraph(GraphState)
workflow.add_node("intent_parser", IntentParserNode)
workflow.add_node("searcher", SearcherNode)
workflow.add_node("generator", GeneratorNode)
workflow.add_edge("intent_parser", "route_by_mode")
workflow.compile()
```

**역할**:
- ✅ **워크플로우 정의**: 노드 간 실행 순서와 조건부 라우팅
- ✅ **상태 관리**: GraphState를 노드 간 전달하며 업데이트
- ✅ **병렬 실행**: 독립적인 노드를 동시 실행 (예: Searcher + RAG)
- ✅ **재시도/에러 처리**: 노드 실패 시 재시도 로직

**NOT 담당**:
- ❌ 비즈니스 로직 (검색, 추론 등은 Node에서 처리)
- ❌ LLM 호출 (llm_client.py가 담당)
- ❌ 대화 히스토리 관리 (conversation_service.py가 담당)

### LangChain Core가 담당하는 것 (메시지 타입)

```python
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
```

**역할**:
- ✅ **메시지 표준화**: User/AI 메시지를 일관된 포맷으로 관리
- ✅ **타입 안정성**: TypedDict로 메시지 구조 정의

**사용 위치**:
- `GraphState.messages`: 대화 히스토리 저장
- `ContextualizeTransformer`: 대화 맥락 분석 시 메시지 타입 체크

---

## 전체 데이터 플로우

### 1. 요청 수신 (API → Controller)

```
클라이언트
    ↓ POST /api/ai/chat
┌─────────────────────────────────────────────────────────────┐
│ FastAPI Router (main.py)                                     │
│ - CORS, 인증, 요청 유효성 검사                                 │
└─────────────────────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────────────────────┐
│ ai_chat_controller.py:ai_chat()                              │
│                                                              │
│ 1. conversation_service.get_or_create_conversation()         │
│    - Redis에서 기존 대화 조회 또는 새 대화 생성               │
│                                                              │
│ 2. conversation_service.add_message(role="user")             │
│    - 사용자 메시지를 Redis에 저장                            │
│                                                              │
│ 3. run_ai_agent() 호출                                       │
└─────────────────────────────────────────────────────────────┘
```

**주요 파일**:
- `app/controllers/ai_chat_controller.py:70-120`
- `app/services/conversation_service.py:154-173` (대화 조회)
- `app/services/conversation_service.py:223-247` (메시지 추가)

---

### 2. Agent 실행 (LangGraph Workflow)

```
┌─────────────────────────────────────────────────────────────┐
│ graph.py:run_ai_agent()                                      │
│                                                              │
│ A. 대화 히스토리 로드 (V8.3 추가)                             │
│    ┌──────────────────────────────────────────────┐         │
│    │ conversation_service.get_conversation()       │         │
│    │ → Redis에서 최근 10개 메시지 로드             │         │
│    │                                               │         │
│    │ messages = []                                 │         │
│    │ for msg in conversation.messages[-10:]:       │         │
│    │     if msg.role == "user":                    │         │
│    │         messages.append(HumanMessage(...))    │         │
│    │     elif msg.role == "assistant":             │         │
│    │         messages.append(AIMessage(...))       │         │
│    │                                               │         │
│    │ messages.append(HumanMessage(현재 쿼리))      │         │
│    └──────────────────────────────────────────────┘         │
│                                                              │
│ B. 초기 상태 구성                                            │
│    initial_state = {                                         │
│        "messages": messages,  # ← 대화 히스토리!            │
│        "query": "그것의 용도는?",                            │
│        "mode": AIMode.SEARCH,                                │
│        ...                                                   │
│    }                                                         │
│                                                              │
│ C. LangGraph 실행                                            │
│    graph = create_ai_graph(settings)                         │
│    result = await graph.ainvoke(initial_state)              │
└─────────────────────────────────────────────────────────────┘
```

**주요 파일**:
- `app/agents/graph.py:159-176` (대화 히스토리 로드)
- `app/agents/graph.py:178-202` (초기 상태 구성 및 실행)

**LangGraph 역할**:
- StateGraph 실행 엔진
- 노드 간 state 전달 및 업데이트
- 조건부 라우팅 (mode에 따라 다른 노드 실행)

---

### 3. 워크플로우 노드 실행

#### 3.1 Intent Parser 노드

```
┌─────────────────────────────────────────────────────────────┐
│ IntentParserNode.__call__(state)                             │
│                                                              │
│ 1. 모드 감지 (search/rag/reasoning/simple)                  │
│    - 패턴 매칭 또는 LLM 분류                                 │
│                                                              │
│ 2. Tier 라우팅 (tier-simple/tier-complex/tier-multimodal)  │
│    - 쿼리 복잡도 분석                                        │
│                                                              │
│ 3. Query Transformation (V8.3)                               │
│    ┌──────────────────────────────────────────────┐         │
│    │ reformulate_query(query, state)               │         │
│    │                                               │         │
│    │ Phase 1: Transformer 플러그인 실행            │         │
│    │   for transformer in self.transformers:       │         │
│    │       if transformer.should_apply():          │         │
│    │           result = await transformer.transform│         │
│    │                                               │         │
│    │   ┌─────────────────────────────────────┐    │         │
│    │   │ ContextualizeTransformer            │    │         │
│    │   │ - 대화 히스토리 분석                │    │         │
│    │   │ - LLM으로 쿼리 재작성               │    │         │
│    │   │ "그것" → "파이썬"                   │    │         │
│    │   └─────────────────────────────────────┘    │         │
│    │                                               │         │
│    │   ┌─────────────────────────────────────┐    │         │
│    │   │ DecomposeTransformer                │    │         │
│    │   │ - 복잡한 질문 분해                  │    │         │
│    │   │ - 3-5개 하위 쿼리 생성              │    │         │
│    │   └─────────────────────────────────────┘    │         │
│    │                                               │         │
│    │ Phase 2: 형태소 분석 (Kiwi)                  │         │
│    │   - 키워드 추출 (명사, 동사, 외국어)        │         │
│    │   - 코드/에러 패턴 감지                      │         │
│    │                                               │         │
│    │ Phase 3: 중복 제거 및 최종 목록              │         │
│    │   - 최대 5개로 제한                          │         │
│    └──────────────────────────────────────────────┘         │
│                                                              │
│ 4. 상태 업데이트                                             │
│    return {                                                  │
│        "mode": AIMode.SEARCH,                                │
│        "selected_model": "tier-simple",                      │
│        "search_queries": [재작성된 쿼리들],                  │
│        "query_analysis": {...}                               │
│    }                                                         │
└─────────────────────────────────────────────────────────────┘
```

**주요 파일**:
- `app/agents/nodes/intent_parser.py:389-452` (reformulate_query)
- `app/agents/nodes/intent_parser.py:154-243` (ContextualizeTransformer)
- `app/agents/nodes/intent_router.py:380-387` (플러그인 등록)

**LangGraph 역할**:
- 노드 실행 후 state 병합
- 다음 노드 결정 (route_by_mode 함수)

---

#### 3.2 Searcher 노드 (mode=SEARCH인 경우)

```
┌─────────────────────────────────────────────────────────────┐
│ SearcherNode.__call__(state)                                 │
│                                                              │
│ 1. search_queries 추출                                       │
│    queries = state["search_queries"]                         │
│    # ["그것의 용도는?", "파이썬의 주요 용도는?", ...]        │
│                                                              │
│ 2. 각 쿼리로 웹 검색 (SearXNG)                               │
│    results = []                                              │
│    for query in queries:                                     │
│        r = await search_web(query, categories="general")     │
│        results.extend(r)                                     │
│                                                              │
│ 3. 결과 리랭킹 (RRF - Reciprocal Rank Fusion)               │
│    - 여러 검색 결과를 융합                                   │
│    - 관련성 점수 계산                                        │
│                                                              │
│ 4. 상태 업데이트                                             │
│    return {                                                  │
│        "search_results": ranked_results[:20]                 │
│    }                                                         │
└─────────────────────────────────────────────────────────────┘
```

**주요 파일**:
- `app/agents/nodes/searcher.py`
- `app/agents/tools/web_search.py`

---

#### 3.3 Generator 노드 (최종 응답 생성)

```
┌─────────────────────────────────────────────────────────────┐
│ GeneratorNode.__call__(state)                                │
│                                                              │
│ 1. 컨텍스트 구성                                             │
│    - search_results (검색 결과)                              │
│    - messages (대화 히스토리)                                │
│                                                              │
│ 2. 프롬프트 생성                                             │
│    system_prompt = """검색 결과를 바탕으로 답변..."""         │
│    user_prompt = f"질문: {query}\n\n검색 결과: {results}"    │
│                                                              │
│ 3. LLM 호출                                                  │
│    response = await async_llm_completion_stream(             │
│        messages=[...],                                       │
│        model=state["selected_model"]  # tier-simple 등       │
│    )                                                         │
│                                                              │
│ 4. 상태 업데이트                                             │
│    return {                                                  │
│        "response": "파이썬의 주요 용도는..."                 │
│    }                                                         │
└─────────────────────────────────────────────────────────────┘
```

**주요 파일**:
- `app/agents/nodes/generator.py`
- `app/agents/tools/llm_client.py` (실제 LLM API 호출)

**LangGraph 역할**:
- 모든 노드 실행 완료 후 최종 state 반환
- 각 노드에서 업데이트한 state를 병합

---

### 4. 응답 저장 및 반환 (Controller)

```
┌─────────────────────────────────────────────────────────────┐
│ ai_chat_controller.py:ai_chat()                              │
│                                                              │
│ result = await run_ai_agent(...)  # ← LangGraph 실행 완료   │
│                                                              │
│ # AI 응답 저장 (Redis)                                       │
│ await conv_service.add_message(                              │
│     conversation_id,                                         │
│     role="assistant",                                        │
│     content=result["response"],                              │
│     metadata={                                               │
│         "mode": "search",                                    │
│         "sources": result["sources"]                         │
│     }                                                        │
│ )                                                            │
│                                                              │
│ # 클라이언트에 응답 반환                                      │
│ return ChatResponse(                                         │
│     response=result["response"],                             │
│     conversation_id=conversation_id,                         │
│     sources=result["sources"],                               │
│     thinking_steps=result["thinking_steps"]                  │
│ )                                                            │
└─────────────────────────────────────────────────────────────┘
    ↓
클라이언트
```

**주요 파일**:
- `app/controllers/ai_chat_controller.py:99-116`

---

## V8.3 Query Transformation 상세

### 플러그인 아키텍처

```python
# intent_parser.py

# 1. 추상 인터페이스
class QueryTransformer(ABC):
    @abstractmethod
    async def transform(query, state, settings) -> list[str]:
        pass

    @abstractmethod
    def should_apply(query, state) -> bool:
        pass

# 2. 구현체 등록
self.transformers = [
    ContextualizeTransformer(),  # 대화 맥락
    DecomposeTransformer(),       # 질의 분해
    # HyDETransformer(),          # Phase 1B
    # StepBackTransformer(),      # Phase 5+
]

# 3. 순차 실행
for transformer in self.transformers:
    if transformer.should_apply(query, state):
        transformed = await transformer.transform(query, state, settings)
        search_queries.extend(transformed)
```

### ContextualizeTransformer 동작 원리

```python
async def transform(self, query, state, settings):
    messages = state.get("messages", [])  # GraphState에서 추출

    if len(messages) < 2:
        return [query]  # 첫 질문이면 패스

    # 최근 3턴 추출
    recent_messages = messages[-6:]

    # 대화 맥락 포맷팅
    conversation_context = """
    사용자: 파이썬이란?
    AI: 파이썬은 고수준 프로그래밍 언어입니다...
    """

    # LLM에 재작성 요청
    prompt = f"""
    이전 대화:
    {conversation_context}

    현재 질문: "{query}"

    독립적으로 이해 가능한 질문으로 재작성하세요.
    """

    response = await async_llm_completion(prompt)
    # → "파이썬의 주요 용도는 무엇인가요?"

    return [response]
```

---

## 데이터 저장 위치

### Redis (대화 히스토리)

```
키: "ai:conversation:{conversation_id}"
TTL: 7일
값: JSON {
    "conversation_id": "...",
    "messages": [
        {"role": "user", "content": "...", "timestamp": 123},
        {"role": "assistant", "content": "...", "timestamp": 456}
    ],
    "created_at": 123,
    "updated_at": 456
}
```

**사용 위치**:
- `conversation_service.py:142-143` (저장)
- `conversation_service.py:164` (조회)
- `graph.py:165-175` (로드 → LangChain messages 변환)

### PostgreSQL (사용자 콘텐츠, 문서)

- 사용자가 업로드한 문서 (RAG용)
- 메타데이터, 임베딩

### 없는 것

- ❌ 대화 히스토리 영구 저장 (Redis만, 7일 후 삭제)
- ❌ LLM 응답 캐싱 (매번 새로 생성)

---

## LangChain vs 전체 로직 비중

### LangChain/LangGraph 담당 영역 (~10%)

```
✅ StateGraph 워크플로우 정의
✅ 노드 간 상태 전달
✅ 조건부 라우팅 (if/else 로직)
✅ 메시지 타입 (HumanMessage, AIMessage)
```

### 자체 구현 영역 (~90%)

```
✅ 비즈니스 로직 (Intent 분석, 검색, RAG, 추론, 생성)
✅ LLM 호출 (llm_client.py → LiteLLM → OpenAI/Anthropic)
✅ 대화 히스토리 관리 (Redis)
✅ 웹 검색 (SearXNG)
✅ Query Transformation (V8.3 플러그인)
✅ Tier 라우팅 (모델 선택)
✅ 텔레메트리 (OpenTelemetry, Langfuse)
```

**LangGraph는 "오케스트레이션 프레임워크"일 뿐**:
- 워크플로우 실행 엔진 역할
- 실제 로직은 모두 Node 클래스와 Service에서 구현
- LangGraph 없이도 Node를 직접 순차 호출하면 동일하게 작동 가능

---

## 주요 파일 맵

| 계층 | 파일 | 역할 |
|-----|------|------|
| **API** | `app/main.py` | FastAPI 앱 진입점 |
| **Controller** | `app/controllers/ai_chat_controller.py` | 요청 처리 및 조율 |
| **Service** | `app/services/conversation_service.py` | 대화 히스토리 관리 (Redis) |
| **Agent** | `app/agents/graph.py` | LangGraph 워크플로우 정의 |
| **Node** | `app/agents/nodes/intent_parser.py` | Intent 분석 + Query Transformation (V8.3) |
| **Node** | `app/agents/nodes/searcher.py` | 웹 검색 |
| **Node** | `app/agents/nodes/generator.py` | LLM 응답 생성 |
| **Tool** | `app/agents/tools/llm_client.py` | LLM API 호출 |
| **Tool** | `app/agents/tools/web_search.py` | SearXNG 검색 |
| **State** | `app/agents/state.py` | GraphState 타입 정의 |

---

## 요약

1. **LangGraph 역할**: 워크플로우 오케스트레이션 (노드 간 상태 전달 및 라우팅)
2. **전체 로직에서 비중**: ~10% (대부분은 자체 구현)
3. **대화 히스토리**: Redis에 저장 (7일 TTL)
4. **V8.3 핵심**: 대화 맥락 기반 쿼리 재작성 (ContextualizeTransformer)
5. **데이터 플로우**: API → Controller → LangGraph → Nodes → Tools → LLM/Search

**LangGraph 없이도 가능하지만**, 워크플로우 시각화, 조건부 라우팅, 병렬 실행 등의 편의 기능을 제공합니다.
