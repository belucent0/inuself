# AI 모드 개선 계획 (V8.0)

> 작성일: 2026-01-26
> 아키텍처 버전: V7.6 → V8.0 (AI Mode Enhancement)

---

## 기능 개발 계획 요약

### Phase 1: 기반 구축 (1주차)
- [ ] LangGraph 설치 및 기본 워크플로우 구현
- [ ] Redis 기반 대화 히스토리 관리
- [ ] Intent Parser 노드 구현
- [ ] 기본 AI 채팅 모드 (Simple Chat)

### Phase 2: 검색 통합 (2주차)
- [ ] SearXNG 연동 강화 (기존 Deep Search 확장)
- [ ] Web Search 모드 구현
- [ ] Source Citation 시스템 구현
- [ ] 검색 결과 캐싱 (Valkey)

### Phase 3: RAG 시스템 (3주차)
- [ ] LlamaIndex 설치 및 설정
- [ ] 문서 인덱싱 파이프라인 (콘텐츠 요약 기반)
- [ ] Vector Store 연동 (pgvector 또는 Qdrant)
- [ ] 내부 문서 검색 모드 구현

### Phase 4: 고급 기능 (4주차)
- [ ] Reasoning 모드 (Chain-of-Thought)
- [ ] Hybrid 모드 (Web + RAG 통합)
- [ ] Reflector 노드 (품질 검증)
- [ ] 스트리밍 응답 개선

### Phase 5: UI/UX 개선 (5주차)
- [ ] AI 모드 채팅 인터페이스 (하이브리드: 고정섹션 + 모달)
- [ ] 소스 인용 표시 UI
- [ ] 사고 과정 시각화
- [ ] 모드 전환 UX

---

## 아키텍처 개요

### 현재 상태 (V7.6)

```
┌─────────────────────────────────────────────────────────────┐
│                      Frontend (Next.js)                     │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  ChatInterface → ChatPrompt → MarkdownContent       │   │
│   │  SourceCarousel, ThinkingProcessAccordion           │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Backend (FastAPI)                          │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐    │
│   │ SearchCtrl   │   │ ASR Pipeline │   │ LLM Summary  │    │
│   │ (Deep Search)│   │              │   │              │    │
│   └──────────────┘   └──────────────┘   └──────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    LiteLLM (prometheus-router)               │
│                              │                               │
│                    Redis Stream (V6.6)                       │
│                              │                               │
│              ┌───────────────┴───────────────┐               │
│              ▼                               ▼               │
│         GPU Server                      NPU Server           │
│      (llama-server)                   (flm-server)           │
└─────────────────────────────────────────────────────────────┘
```

### 목표 상태 (V8.0)

```
┌─────────────────────────────────────────────────────────────┐
│                      Frontend (Next.js)                      │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  AI Mode Chat Interface (Hybrid Layout)              │   │
│   │  - Fixed Section (기본)                              │   │
│   │  - Modal Expansion (상세 대화)                       │   │
│   │  - Source Citations, Thinking Visualization          │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Backend (FastAPI)                          │
│   ┌─────────────────────────────────────────────────────┐   │
│   │              AI Agent Orchestrator                   │   │
│   │                   (LangGraph)                        │   │
│   │                                                      │   │
│   │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌────────┐  │   │
│   │  │ Intent  │→│Clarifier│→│ Planner │→│Executor│  │   │
│   │  │ Parser  │  │         │  │         │  │        │  │   │
│   │  └─────────┘  └─────────┘  └─────────┘  └────────┘  │   │
│   │                                             │        │   │
│   │                    ┌────────────────────────┘        │   │
│   │                    ▼                                 │   │
│   │              ┌───────────┐      ┌───────────┐        │   │
│   │              │ Reflector │  →   │ Generator │        │   │
│   │              └───────────┘      └───────────┘        │   │
│   └─────────────────────────────────────────────────────┘   │
│                              │                               │
│   ┌──────────────────────────┴──────────────────────────┐   │
│   │                    Tool Layer                        │   │
│   │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌────────┐  │   │
│   │  │ SearXNG │  │LlamaIdx │  │ Content │  │  LLM   │  │   │
│   │  │(WebSrch)│  │  (RAG)  │  │   DB    │  │ Client │  │   │
│   │  └─────────┘  └─────────┘  └─────────┘  └────────┘  │   │
│   └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## LangGraph 워크플로우

### 전체 워크플로우

```
                    ┌─────────────────┐
                    │   User Query    │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  Intent Parser  │
                    │  (의도 분석)     │
                    └────────┬────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
    ┌───────────────┐ ┌───────────┐ ┌───────────────┐
    │ Simple Chat   │ │  Search   │ │   Reasoning   │
    │    Mode       │ │   Mode    │ │     Mode      │
    └───────┬───────┘ └─────┬─────┘ └───────┬───────┘
            │               │               │
            │         ┌─────┴─────┐         │
            │         ▼           ▼         │
            │   ┌─────────┐ ┌─────────┐     │
            │   │ SearXNG │ │LlamaIdx │     │
            │   │  (Web)  │ │  (RAG)  │     │
            │   └────┬────┘ └────┬────┘     │
            │        │           │          │
            │        └─────┬─────┘          │
            │              │                │
            └──────────────┼────────────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │    Clarifier    │
                  │  (필요시 질문)   │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │     Planner     │
                  │  (응답 계획)     │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │    Executor     │
                  │  (계획 실행)     │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │    Reflector    │
                  │  (품질 검증)     │
                  └────────┬────────┘
                           │
              ┌────────────┴────────────┐
              │ 품질 미달?              │ 품질 통과
              ▼                         ▼
        (Planner로 복귀)        ┌─────────────────┐
                                │    Generator    │
                                │  (최종 응답)     │
                                └────────┬────────┘
                                         │
                                         ▼
                                ┌─────────────────┐
                                │  User Response  │
                                │  + Sources      │
                                └─────────────────┘
```

### 모드별 워크플로우

#### 1. Simple Chat Mode
```
User Query → Intent Parser → Generator → Response
```
- 단순 대화, 인사, 일반 질문
- LLM 직접 호출

#### 2. Web Search Mode
```
User Query → Intent Parser → SearXNG Search → Planner → Generator → Response + Citations
```
- 최신 정보, 뉴스, 실시간 데이터
- 검색 결과 기반 응답 생성

#### 3. RAG Mode (LlamaIndex)
```
User Query → Intent Parser → LlamaIndex Query → Planner → Generator → Response + Citations
```
- 내부 콘텐츠 검색 (기존 요약된 콘텐츠)
- Vector Store 기반 유사도 검색

#### 4. Reasoning Mode
```
User Query → Intent Parser → Clarifier → Planner → Executor → Reflector → Generator
```
- 복잡한 분석, 비교, 추론
- Chain-of-Thought 적용
- 사고 과정 시각화

#### 5. Hybrid Mode
```
User Query → Intent Parser → [SearXNG + LlamaIndex] → Planner → Executor → Reflector → Generator
```
- 웹 검색 + 내부 문서 검색 병합
- 가장 포괄적인 응답

---

## LangGraph 노드 상세

### 1. Intent Parser (의도 분석)

```python
class IntentParserNode:
    """사용자 쿼리의 의도를 분석하여 적절한 모드 결정."""

    async def __call__(self, state: GraphState) -> GraphState:
        query = state["query"]

        # LLM으로 의도 분석
        intent = await self.analyze_intent(query)

        return {
            **state,
            "intent": intent,  # simple | search | rag | reasoning | hybrid
            "requires_clarification": intent.confidence < 0.7,
        }
```

### 2. Clarifier (명확화)

```python
class ClarifierNode:
    """모호한 쿼리에 대해 사용자에게 추가 질문."""

    async def __call__(self, state: GraphState) -> GraphState:
        if not state["requires_clarification"]:
            return state

        clarification_question = await self.generate_clarification(
            query=state["query"],
            intent=state["intent"]
        )

        return {
            **state,
            "clarification_needed": True,
            "clarification_question": clarification_question,
        }
```

### 3. Planner (계획 수립)

```python
class PlannerNode:
    """응답 생성을 위한 계획 수립."""

    async def __call__(self, state: GraphState) -> GraphState:
        plan = await self.create_plan(
            query=state["query"],
            intent=state["intent"],
            context=state.get("search_results", []),
        )

        return {
            **state,
            "plan": plan,
            "steps": plan.steps,
        }
```

### 4. Executor (실행)

```python
class ExecutorNode:
    """계획된 단계들을 순차적으로 실행."""

    async def __call__(self, state: GraphState) -> GraphState:
        results = []

        for step in state["steps"]:
            result = await self.execute_step(step)
            results.append(result)

        return {
            **state,
            "execution_results": results,
        }
```

### 5. Reflector (검증)

```python
class ReflectorNode:
    """생성된 응답의 품질 검증."""

    async def __call__(self, state: GraphState) -> GraphState:
        quality_score = await self.evaluate_quality(
            query=state["query"],
            response=state["draft_response"],
            sources=state.get("sources", []),
        )

        return {
            **state,
            "quality_score": quality_score,
            "needs_revision": quality_score < 0.8,
        }
```

### 6. Generator (응답 생성)

```python
class GeneratorNode:
    """최종 응답 생성."""

    async def __call__(self, state: GraphState) -> GraphState:
        response = await self.generate_response(
            query=state["query"],
            context=state.get("execution_results", []),
            plan=state.get("plan"),
        )

        return {
            **state,
            "response": response,
            "sources": state.get("sources", []),
            "thinking_process": state.get("thinking_steps", []),
        }
```

---

## LlamaIndex 통합

### RAG 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                    Document Ingestion                        │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│   │   Content   │ →  │   Chunker   │ →  │  Embedding  │     │
│   │  Summaries  │    │  (LlamaIdx) │    │   Model     │     │
│   └─────────────┘    └─────────────┘    └─────────────┘     │
│                                                │             │
│                                                ▼             │
│                                         ┌─────────────┐     │
│                                         │Vector Store │     │
│                                         │  (pgvector) │     │
│                                         └─────────────┘     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                      Query Pipeline                          │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│   │ User Query  │ →  │  Embedding  │ →  │  Retriever  │     │
│   │             │    │             │    │             │     │
│   └─────────────┘    └─────────────┘    └─────────────┘     │
│                                                │             │
│                                                ▼             │
│                            ┌───────────────────────────┐    │
│                            │     Response Synthesizer   │    │
│                            │      (LLM + Context)       │    │
│                            └───────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 인덱싱 대상

1. **콘텐츠 요약** (`summary_md`)
   - 기존 LLM 요약 결과
   - 구조화된 마크다운 (키워드, 목차, 핵심 요약, 주요 내용)

2. **전사 텍스트** (선택적)
   - 원본 transcription
   - 더 세밀한 검색 필요시

3. **메타데이터**
   - 제목, 생성일, 태그
   - 필터링 및 정렬용

### LlamaIndex 설정

```python
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
from llama_index.vector_stores.postgres import PGVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

# Embedding 모델 (로컬)
embed_model = HuggingFaceEmbedding(
    model_name="BAAI/bge-m3"  # 다국어 지원
)

# Vector Store (PostgreSQL + pgvector)
vector_store = PGVectorStore.from_params(
    database="torch_asr",
    host="localhost",
    password=os.environ["POSTGRES_PASSWORD"],
    port="5432",
    user="postgres",
    table_name="content_embeddings",
    embed_dim=1024,  # bge-m3 차원
)

# Index 생성
index = VectorStoreIndex.from_vector_store(
    vector_store=vector_store,
    embed_model=embed_model,
)
```

---

## 디렉토리 구조 (예상)

```
backend/
├── app/
│   ├── agents/                    # LangGraph 에이전트
│   │   ├── __init__.py
│   │   ├── graph.py               # 메인 LangGraph 정의
│   │   ├── nodes/
│   │   │   ├── intent_parser.py
│   │   │   ├── clarifier.py
│   │   │   ├── planner.py
│   │   │   ├── executor.py
│   │   │   ├── reflector.py
│   │   │   └── generator.py
│   │   ├── tools/
│   │   │   ├── web_search.py      # SearXNG 연동
│   │   │   ├── rag_search.py      # LlamaIndex 연동
│   │   │   └── llm_client.py      # LiteLLM 클라이언트
│   │   └── state.py               # GraphState 정의
│   ├── rag/                       # LlamaIndex RAG
│   │   ├── __init__.py
│   │   ├── indexer.py             # 문서 인덱싱
│   │   ├── retriever.py           # 검색
│   │   └── embeddings.py          # 임베딩 모델
│   └── controllers/
│       └── ai_chat_controller.py  # AI 채팅 API
```

---

## API 설계

### AI Chat Endpoint

```python
# POST /api/ai/chat
{
    "query": "string",
    "mode": "auto | simple | search | rag | reasoning | hybrid",
    "conversation_id": "uuid (optional)",
    "context": {
        "content_ids": [1, 2, 3]  # RAG 컨텍스트
    }
}

# Response (SSE Stream)
{
    "type": "thinking | source | content | done",
    "data": {
        "step": "intent_parsing",
        "content": "사용자 질문을 분석중입니다...",
        "sources": [
            {
                "title": "소스 제목",
                "url": "https://...",
                "snippet": "관련 내용..."
            }
        ]
    }
}
```

### Conversation History

```python
# GET /api/ai/conversations/{conversation_id}
# GET /api/ai/conversations (목록)
# DELETE /api/ai/conversations/{conversation_id}
```

---

## 기술 스택

| 구성요소 | 기술 |
|----------|------|
| Agent Framework | LangGraph |
| RAG Framework | LlamaIndex |
| Vector Store | pgvector (PostgreSQL) |
| Embedding Model | BAAI/bge-m3 (다국어) |
| LLM Backend | LiteLLM (prometheus-router) |
| Web Search | SearXNG |
| Cache | Valkey (Redis) |
| Message Queue | Redis Stream |

---

## 마일스톤

### M1: 기본 AI 채팅 (2주)
- LangGraph 기본 구조
- Intent Parser + Generator
- Simple Chat 모드 동작

### M2: 검색 통합 (2주)
- SearXNG 연동
- Web Search 모드
- 소스 인용 표시

### M3: RAG 시스템 (2주)
- LlamaIndex 설정
- 콘텐츠 인덱싱
- RAG 모드 동작

### M4: 고급 모드 (2주)
- Reasoning 모드
- Hybrid 모드
- Reflector 검증

### M5: UI/UX 완성 (1주)
- 채팅 인터페이스 완성
- 사고 과정 시각화
- 모드 전환 UX

---

## 참고 자료

- [LangGraph Documentation](https://python.langchain.com/docs/langgraph)
- [LlamaIndex Documentation](https://docs.llamaindex.ai/)
- [pgvector](https://github.com/pgvector/pgvector)
- [SearXNG](https://docs.searxng.org/)
