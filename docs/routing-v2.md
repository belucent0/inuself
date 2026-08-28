# RoutingProfile v2 운영 계약

> 현행 LLM 라우팅의 요청·선택·오류·운영 Source of Truth. 과거 tier 기반 설명은 `docs/archived/problem-solving/06-tier-based-routing.md`에만 보존되며 이 문서가 이를 대체한다.

## 1. 요청 계약

Backend와 Worker는 OpenAI SDK의 `extra_body={"routing": profile}`을 사용한다. SDK가 이를 top-level `routing`으로 병합하며 raw HTTP 호출은 다음 형식이다.

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "..."}],
  "stream": true,
  "routing": {
    "workload": "chat",
    "reasoning": "none",
    "execution_scope": "local_only"
  }
}
```

| 필드 | 값 | 기본 | 의미 |
|---|---|---|---|
| `workload` | `chat`, `summary` | `chat` | 필요한 작업 능력 |
| `reasoning` | `auto`, `none`, `low`, `medium`, `high` | `medium` | 추론 의도. raw `auto`는 Gateway에서 `medium`으로 정규화 |
| `execution_scope` | `local_only`, `remote_allowed` | `local_only` | optional Codex 사용 허용 여부 |

`model` 미지정·빈 문자열·`auto`는 위 profile 라우팅을 사용한다. 명시 모델은 운영/WPI용 `codex-high`, `codex-medium`, `codex-low`만 허용하며 명시적 원격 실행 동의로 본다. 물리 모델명이나 다른 별칭은 HTTP 400이다. `routing`이 객체가 아니거나 enum 밖 값·알 수 없는 필드를 가지면 HTTP 400이다. media `extra_body.task_type`과 vision 메시지는 이 검증보다 먼저 기존 media 경로로 분기한다.

Backend의 `resolve_reasoning()`은 `auto`를 다음 순서로 한 번 해석한다.

1. 명시된 non-auto 값
2. `mode=reasoning`이면 `high`
3. context가 3000자를 초과하면 `medium`
4. `search`, `hybrid`, `rag` mode면 `medium`
5. 그 밖에는 `none`

UI는 `자동(auto)`, `일반(medium)`, `심층(high)`만 노출한다. `none/low/medium`의 표시명은 모두 “일반”이다. “심층”은 Codex 강제가 아니며 정상 local-gpu에서는 GPU를 사용한다. “로컬 실패 시 외부 모델 허용”을 켠 경우에만 `execution_scope=remote_allowed`가 된다.

## 2. 성공·오류 계약

JSON과 SSE 응답의 `model`은 실제 서빙 모델명이며 다음 헤더가 항상 함께 온다.

```http
X-Inference-Provider: npu-chat
X-Inference-Model: gemma4-it:e4b
X-Routing-Reason: preferred
```

`X-Routing-Reason`은 `preferred`, `capacity-overflow`, `unhealthy-fallback`, `error-fallback`, `explicit-codex` 중 하나다. 오류 body는 OpenAI 호환 형식이다.

```json
{"error":{"message":"...","type":"invalid_request|unavailable|overloaded"}}
```

- 잘못된 요청과 upstream 400/413/422: HTTP 400 `invalid_request`, fallback·circuit 증가 없음
- 적격 Provider 없음 또는 모두 unhealthy: HTTP 503 `unavailable`, caller는 즉시 실패
- 모든 healthy Provider가 admission timeout까지 full: HTTP 503 `overloaded`, `Retry-After: 5`
- upstream 429: 현재 Provider를 제외하고 fallback 가능, circuit 증가 없음
- 401/403/404, 연결/요청/첫 출력 timeout, 5xx: Provider failure로 기록하고 다음 적격 Provider 시도

Backend와 Worker는 typed `overloaded`만 기존 retry 상한 안에서 `Retry-After`를 존중한다. `unavailable`은 재시도하지 않는다.

## 3. 정책과 ProviderPool

정책은 [`infra/shared/routing_policy.json`](../infra/shared/routing_policy.json)에 있으며 `settings`, `providers`, `routes` 세 top-level 키를 갖는다.

| Provider | Scope | Workload | Reasoning | Capacity | Probe |
|---|---|---|---|---:|---|
| `npu-chat` | local | chat | none | 1 | `/v1/models`에 `gemma4-it:e4b` |
| `gpu-llm` | local | chat, summary | none..high | 4 | `/v1/models`에 `gemma4-a4b` |
| `codex` | remote | chat | high | 2 | inference 결과만 사용 |

`NPU_LLM_BASE_URL`이 비면 NPU는 disabled다. capacity와 모든 interval/timeout/cooldown은 양수여야 한다. 파일·필수키 누락, 잘못된 provider 참조는 Gateway 기동 실패다.

선택 알고리즘:

1. route 순서에서 workload/reasoning/scope/disabled/exclude 조건으로 적격 후보를 만든다.
2. 첫 healthy하고 circuit가 닫혔으며 slot이 있는 Provider를 하나의 `asyncio.Condition` 아래에서 원자적으로 획득한다.
3. 앞 후보가 full이면 `capacity-overflow`, unhealthy이면 `unhealthy-fallback`이다.
4. healthy 후보가 모두 full이면 polling하지 않고 Condition에서 최대 30초 기다린다.
5. 첫 출력 전 retryable failure는 시도한 이름을 `exclude`에 추가해 각 후보를 최대 한 번 시도하고 reason을 `error-fallback`으로 바꾼다.
6. 첫 SSE chunk를 client에 보낸 뒤에는 partial response 오염을 피하려고 fallback하지 않는다.
7. 정상 종료, 예외, timeout, client cancel 모두 Lease와 upstream stream을 `finally`에서 닫는다.

Probe health와 inference circuit는 독립적이다. model ID probe가 2회 연속 실패하면 unhealthy, 성공하면 probe health만 복구한다. inference failure가 2회 누적되면 circuit를 10초 열고, 만료 뒤 단 하나의 half-open 요청만 허용한다. inference 성공만 request failure/circuit를 초기화한다.

## 4. Local과 serverless

local-gpu의 자동 route는 `chat: npu-chat → gpu-llm → codex`, `summary: gpu-llm`이다. Codex는 `high + remote_allowed`에서만 자동 후보가 된다. 명시 `codex-*`는 profile scope와 무관하게 원격 동의를 내포하지만 Codex capacity/circuit는 우회하지 않으며 첫 출력 전 실패 시 GPU high/local로 한 번 fallback한다.

serverless의 `auto` primary는 RunPod LLM이다. RunPod가 첫 출력 전에 실패하고 `high + remote_allowed`인 경우만 Codex로 fallback한다. 명시 Codex 실패는 RunPod로 fallback한다. serverless에는 이번 버전의 in-process capacity pool을 적용하지 않지만 동일한 실제 모델명·응답 헤더 계약을 지킨다.

## 5. Readiness와 배포 불변식

`GET /health/readiness`는 `status`, `mode`, `providers`, `routes`를 반환한다. Provider별 `health`, `inflight`, `max_inflight`, `model`, `circuit_open`과 route별 `ready|unavailable`을 확인한다.

- healthy하고 circuit가 닫힌 local chat Provider가 있으면 HTTP 200
- 일부 local Provider 또는 summary route만 unavailable이면 `status=degraded`, HTTP 200
- local chat Provider가 하나도 없으면 `status=unavailable`, HTTP 503
- 순간적인 `inflight == max_inflight`는 readiness 실패가 아님
- startup probe가 완료되기 전에는 ready가 아님

Capacity는 Gateway 프로세스 메모리 상태다. `infra/ai-gateway/Dockerfile`과 compose의 uvicorn `--workers 1`, 단일 Gateway replica를 반드시 유지한다. 이 불변식을 깨면 NPU 1/GPU 4/Codex 2 상한을 보장할 수 없다.

배포와 실증 명령:

```bash
wsl -e bash -lc 'cd /mnt/c/timblo/torch-test && docker compose build ai-gateway backend worker frontend'
wsl -e bash -lc 'cd /mnt/c/timblo/torch-test && docker compose up -d ai-gateway backend agent-worker worker frontend'
python scripts/bench_routing.py --gateway http://localhost:4000 --out docs/benchmarks/routing-npu-gpu-overflow.md
```

## 6. 계획된 NPU 점검 절차

자동 테스트와 benchmark는 외부 FastFlowLM 서비스를 중단하지 않는다. 운영자가 계획된 점검 창에서만 다음을 수행한다.

1. NPU FastFlowLM 중단 후 readiness가 `degraded`/HTTP 200이고 `npu-chat.health=unhealthy`인지 확인한다.
2. `chat/none/local_only` 요청이 GPU로 가며 `X-Routing-Reason: unhealthy-fallback`인지 확인한다.
3. FastFlowLM을 재기동한다.
4. `/v1/models`에 configured model ID가 다시 보이고 readiness health가 복구되는지 확인한다.
5. 새 `chat/none/local_only` 요청이 NPU `preferred`로 돌아왔는지 확인한다.

GPU 장애로 summary route만 unavailable이면 degraded/200이므로 HTTP 상태뿐 아니라 body의 `status`와 `routes.summary`에도 경보를 건다.
