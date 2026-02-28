# 문제해결 이력 (Problem-Solving Records)

이 디렉토리는 AI 서비스 개발 과정에서 마주친 기술적 문제와 해결 과정을 담은 이력 문서입니다.

각 문서는 STAR 기법(Situation-Task-Action-Result)으로 작성되어 있으며, 단순한 구현 기록이 아닌 **판단의 근거와 설계 결정**을 중심으로 서술합니다.

---

## 문서 목록

### [01. 검색 재시도 메커니즘](./01-search-retry-mechanism.md)
> "실패를 인지하고 스스로 복구하는 시스템" 설계

LangGraph 순환 그래프로 적응형 검색 재시도를 구현한 경험. 단순 재시도가 왜 의미 없는지, 실패 원인 진단과 전략 변경이 왜 핵심인지를 다룬다.

**키워드**: LangGraph, SearXNG, RAG, 프롬프트 엔지니어링, SSE

---

### [02. LLM Semantic Observability](./02-llm-semantic-observability.md)
> "HTTP 200인데 왜 사용자는 불만인가"

Langfuse 도입으로 의미론적 관측성을 구축한 경험. 전통적인 APM의 한계와 AI 서비스에 필요한 새로운 관측성 패러다임을 다룬다.

**키워드**: Langfuse, LangGraph Callback, Self-hosted, 프롬프트 디버깅, 토큰 비용 가시성

---

### [03. AI 서비스 아키텍처 진화](./03-architecture-evolution.md)
> 단일 프로세스에서 분산 큐 기반 시스템으로

LiteLLM Proxy + Valkey Stream + Provider Manager 구조로 단계적 진화한 경험. 각 버전에서 "지금 가장 큰 문제 하나"만 해결하는 원칙으로 서비스 다운타임 없이 아키텍처를 전환했다.

**키워드**: LiteLLM, Valkey Stream, Provider Manager, LangGraph, 점진적 리팩토링

---

### [04. LiteLLM 세마포어](./04-litellm-semaphore.md)
> 프로세스 경계를 넘는 GPU 자원 조율

Backend/Worker/LiteLLM Proxy 간 GPU 자원 충돌(OOM)을 Redis 기반 분산 세마포어로 해결한 경험. SETNX 원자적 획득, TTL 자동 해제, asyncio.Semaphore와의 계층 조합을 다룬다.

**키워드**: 분산 세마포어, Redis SETNX, Race Condition, TTL, asyncio

---

## 디렉토리 구조 참고

```
docs/
├── problem-solving/          ← 이 디렉토리: "왜 이 문제를 풀었고, 어떤 판단을 했는가"
│   ├── README.md
│   ├── 01-search-retry-mechanism.md
│   └── 02-llm-semantic-observability.md
└── (기타 기술 문서)          ← "어떻게 구현했는가" 중심
```
