# 티어 기반 라우팅: 작업 복잡도가 하드웨어를 선택한다

> **프로젝트**: LLM 기반 문서요약 + AI 채팅 서비스
> **기간**: 2025년 ~ 2026년 (V4 NPU 도입 이후 → V8.0)
> **역할**: AI 인프라 설계 및 구현 (단독)

---

## 1. Situation — 어떤 상황이었나

LiteLLM Proxy를 단일 AI 게이트웨이로 도입한 시점부터 NPU(FLM), GPU, Cloud API 세 종류의 컴퓨팅 자원이 동시에 운영됐다.

| 하드웨어 | 서버 | 특성 |
|----------|------|------|
| NPU (AMD Ryzen AI) | FLM (lemonade-server) | 저전력, 소형 모델 특화 |
| GPU | llama-server, whisper-cpp 등 | 고용량, 긴 context, 멀티모달 |
| Cloud API | Codex (OpenAI OAuth 경유) | 고품질 추론, 외부 의존 |

채팅, 문서 요약, ASR, OCR — 성격이 다른 요청들이 동시에 들어왔다. 자원이 늘어날수록 "이 요청을 어디서 처리하는 게 맞는가"라는 질문도 복잡해졌다.

---

## 2. Problem — 구체적으로 뭐가 문제였나

LiteLLM 도입 직후에도 Backend와 Worker는 모델 이름을 직접 지정했다.

```python
response = await litellm_client.chat(
    model="lfm2:2.6b",  # NPU에서 실행되는 모델명 직접 지정
    messages=[...]
)
```

구조적으로 세 가지가 문제였다.

**① 호출하는 쪽이 인프라를 알아야 했다**

`lfm2:2.6b`가 NPU에서 돌아간다는 사실을 Backend 코드가 갖고 있었다. LiteLLM을 단일 관문으로 세운 의미가 절반밖에 없었다.

**② 모델을 바꾸면 코드를 열어야 했다**

`lfm2:2.6b`를 더 나은 모델로 교체하면 이 이름이 박혀 있는 코드를 전부 찾아서 바꿔야 했다. LiteLLM 도입 전과 달라진 게 없었다.

**③ 같은 목적인데 다른 코드 경로**

"간단한 채팅 응답"이라는 같은 목적의 요청이 Backend와 Worker에서 각기 다른 모델명으로 호출됐다. 어느 쪽이 맞는 모델인지 판단 기준이 없었다.

---

## 3. Attempted Solution — 처음에 어떻게 시도했나

`task_type`으로 분기하는 단순 라우팅을 먼저 넣었다.

```python
async def route_by_task(task_type: str) -> str:
    if task_type in ["chat", "summary_short"]:
        return "npu-provider"
    else:
        return "gpu-provider"
```

동작은 했다. 그러나 task_type이 늘수록 분기 조건이 그대로 늘어났고, NPU가 바쁠 때 GPU로 자동으로 넘어가는 처리가 없었다. 하드코딩된 분기를 계속 추가하는 방향은 아니라는 게 금방 보였다.

---

## 4. Final Solution — 최종적으로 어떻게 해결했나

근본 문제는 "모델명"이 하드웨어와 소프트웨어를 동시에 표현하고 있다는 점이었다. 이걸 분리하기로 했다. 호출하는 쪽은 "얼마나 복잡한 작업인가"만 표현하고, 실제 모델과 하드웨어는 인프라 레이어가 결정하도록.

그 결과로 **3개의 능력 티어**를 정의했다.

```
tier-simple   — 간단한 작업 (인사, 짧은 질문)
tier-thinking — 복잡한 분석 + Chain-of-Thought 추론
tier-recap    — 전사 텍스트 요약 (긴 context, 전문 용어)
```

`tier_config.py`가 이 매핑의 단일 진실 원천이다.

```python
TIER_MODEL_MAP = {
    "tier-simple":   "lfm2:2.6b",              # FLM 소형 (NPU)
    "tier-thinking": "qwen3-tk:4b",             # FLM Thinking (NPU)
    "tier-recap":    "gpt-oss-20b-mxfp4-GGUF", # Lemonade 대형 (GPU)
}

TIER_ROUTING_POLICY = {
    "tier-simple":   {"primary": "npu", "fallback": "gpu"},
    "tier-thinking": {"primary": "npu", "fallback": "gpu"},
    "tier-recap":    {"primary": "gpu", "fallback": "gpu"},  # 20B 모델, GPU 전담
}
```

레이어별 책임이 명확하게 분리됐다.

```
Backend(LangGraph)    WHAT  "이 쿼리에 tier-thinking 능력이 필요하다"
       │
       ▼
LiteLLM Proxy         HOW   "tier-thinking → qwen3-tk:4b, NPU 우선"
       │
       ▼
Provider Manager      WHERE "NPU flm-llm-thinking 서버로 전달"
```

### 티어 결정은 Backend TierRouter가

채팅 요청이 들어오면 LangGraph 에이전트 안의 `TierRouter`가 티어를 결정한다. 모드, 컨텍스트 크기, 쿼리 내용을 순서대로 보고 판단한다.

```python
async def select_tier(query: str, mode: str, context_size: int) -> str:
    if mode == "reasoning":               # reasoning 모드 → 항상 thinking
        return LLMTier.THINKING
    if context_size > 3000:               # 문서가 많으면 → thinking
        return LLMTier.THINKING
    selected = await self._embedding_based_routing(query)  # 임베딩 유사도 기반 분류
    if selected:
        return selected
    return self._rule_based_routing(query)                  # 키워드 규칙 기반 폴백
```

티어 결정 로직이 Backend 한 곳에만 있다. Worker는 요약 작업에 `LLMTier.RECAP`을 고정으로 쓰면 그만이다.

### 하드웨어 선택은 Redis 세마포어가

NPU/GPU 중 어디로 보낼지는 Redis 세마포어가 실시간으로 판단한다.

```python
async def is_provider_busy_async(provider: str) -> bool:
    """Redis 세마포어만 사용 — Prometheus 사용률 수치는 쓰지 않음."""
    if provider == "npu":
        return await redis.exists("worker:npu:active")
    elif provider == "gpu":
        gpu_busy, _ = await is_gpu_busy_async()
        return gpu_busy
```

초기에는 Prometheus에서 GPU/NPU 사용률(%)을 읽어서 "70% 이상이면 바쁨"으로 판단했다. 그런데 실제 OOM 발생 패턴과 맞지 않았다. 50%여도 특정 메모리 패턴에서 OOM이 났고, 70%여도 새 작업을 받을 수 있는 경우가 있었다. 사용률 임계값을 아무리 조정해도 근본적으로 맞출 수가 없었다.

이진 상태("지금 작업 중인가/아닌가")로 바꾸고 나서 이 문제가 없어졌다. 작업 시작 시 SETNX로 키를 획득하고, 완료 시 해제하는 단순한 구조다. Prometheus는 지금도 운영 중이지만 라우팅 결정에는 관여하지 않는다. 로그에 사용률을 표시하는 용도로만 남아있다.

---

## 5. Result — 결과와 임팩트

| 항목 | 모델명 직접 지정 | 티어 기반 라우팅 |
|------|-----------------|-----------------|
| 호출 코드의 인프라 지식 | 모델명 + 하드웨어 | 티어 이름만 |
| 모델 교체 영향 범위 | 호출 코드 전체 | `tier_config.py` 1줄 |
| 티어 결정 위치 | 코드 여러 곳에 분산 | TierRouter 단일 위치 |
| 하드웨어 선택 기준 | 없음 | Redis 세마포어 실시간 상태 |

실제로 체감한 변화는 모델 교체 시였다. `lfm2:2.6b`를 교체할 때 `tier_config.py` 한 줄만 바꿨다. Backend도, Worker도, LiteLLM config도 건드리지 않았다.

---

## 6. Lessons Learned — 이 경험에서 배운 것

**"모델명이 아니라 능력 수준을 표현해야 구현을 갈아끼울 수 있다"**

모델명을 직접 쓰는 게 처음엔 명확해 보였다. 어떤 모델을 쓰는지 코드에서 바로 보이니까. 그런데 그게 곧 "모델이 바뀌면 코드도 바뀐다"는 뜻이었다. 티어 이름은 모델이 바뀌어도 바뀌지 않는다. `tier-simple`이 `lfm2:2.6b`를 쓰든 더 나은 모델을 쓰든, 호출하는 쪽은 알 필요가 없다.

세 가지를 배웠다:

1. **레이어 경계가 곧 변경 비용이다**: WHAT과 HOW를 분리한다는 원칙은 실제로 "모델 교체 시 어느 파일을 여는가"라는 측정 가능한 결과로 드러난다. 추상화가 경계에서 제대로 작동할 때만 의미가 있다.

2. **사용률 수치보다 이진 상태가 더 정확하다**: Prometheus 사용률로 라우팅을 결정할 때 임계값을 맞추는 게 계속 어려웠다. OOM은 사용률이 아니라 "지금 작업 중인가"와 더 정확히 일치했다. 간단한 모델이 더 나은 경우였다.

3. **원칙은 일관되게 적용해야 효과가 있다**: 티어 추상화가 제대로 작동하려면 호출 경로 전체가 단일 관문을 통과해야 한다. 예외가 생기는 순간 그 부분만 따로 관리해야 하는 부담이 생긴다.

---

## 7. 기술 스택

- **`tier_config.py`** (`infra/shared/`): Single Source of Truth — 티어 → 모델 매핑, 라우팅 정책
- **`LLMTier` Enum** (`backend/app/core/llm_tier.py`): Backend 레이어의 티어 상수
- **`TierRouter`** (`backend/app/agents/tools/model_router.py`): 임베딩 유사도 + 규칙 기반 티어 결정
- **LiteLLM Proxy** (`litellm_config.yaml`): 티어별 모델 별칭, Cloud/로컬 fallback
- **`custom_handler.py`**: Redis 세마포어 기반 하드웨어 선택 + 동적 폴백
- **Valkey (Redis 호환)**: SETNX 원자적 잠금
- **Prometheus**: 운영 모니터링 전용 (라우팅 결정에 미관여)

---

*관련 문서: `03-architecture-evolution.md`, `04-litellm-semaphore.md`, `05-design-rationale.md`, `infra/shared/tier_config.py`, `backend/app/agents/tools/model_router.py`*
