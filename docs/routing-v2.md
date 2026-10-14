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

`model` 미지정·빈 문자열·`auto`는 위 profile 라우팅을 사용한다. 명시 모델은 운영용 `codex-high`, `codex-medium`, `codex-low`만 허용하며 `execution_scope=remote_allowed`를 함께 지정해야 한다. 물리 모델명이나 다른 별칭은 HTTP 400이다. `routing`이 객체가 아니거나 enum 값이 문자열이 아니거나 허용 범위 밖이면 HTTP 400이다. `local_only`는 모델 선택보다 우선하며 명시 Codex 및 serverless 실행을 Provider 호출 전에 HTTP 403으로 차단한다. media `extra_body.task_type`, vision 메시지, embedding도 `routing`이 있으면 같은 검증과 scope를 적용한다. 기존 media·embedding의 `routing` 없는 요청은 배포 모드에 따른 종전 동작을 유지한다.

Backend의 `routing_profile(..., allow_remote=None)`과 Worker의 자동 요약·검색은 기존 `DEPLOY_MODE`로 기본 scope를 정한다(`local-gpu`→`local_only`, `serverless`→`remote_allowed`). 명시 `False`는 어느 배포에서든 `local_only`, 명시 `True`는 `remote_allowed`다. AI 채팅의 controller와 generator·reasoner metadata는 기존 기본 `False`를 유지한다. 따라서 serverless AI 채팅은 UI의 외부 모델 허용을 켜야 하며, `False` 요청을 배포 모드만으로 외부 실행으로 바꾸지 않는다. Judge 평가는 온프레미스 `local_only`를 유지하므로 serverless Gateway에서 거절되는 것이 정상이다. Codex capacity 벤치마크는 명시 `remote_allowed`를 전달한다.

직접 Python으로 serverless Backend·Worker를 실행할 때는 `DEPLOY_MODE=serverless`를 **프로세스 환경변수**로 지정한다. 설정 모델의 `.env` 읽기만으로 `os.environ`이 채워지지는 않는다. Compose는 기존 `env_file`을 통해 프로세스 환경에 전달한다. 별도 scope 설정 키는 추가하지 않는다.

### 입력 한도와 미디어 형식

Gateway는 인증 이후 요청 stream을 읽으며 `Content-Length` 선검사와 실제 누적 바이트를 모두 확인한다. 누락되거나 실제보다 작은 길이 헤더도 실측 한도를 우회하지 못한다. JSON은 객체여야 하며 잘못된 JSON·64단계를 넘는 객체/배열 중첩·필드 타입 오류는 HTTP 400, 크기 초과는 HTTP 413이다.

| 항목 | 한도·형식 |
|---|---|
| 전체 JSON 요청 | 300 MiB, 객체/배열 중첩 최대 64단계 |
| 텍스트 | 메시지 문자열·text part 합계 또는 embedding 입력 문자열 배열 합계 1,000,000자 |
| 오디오 | 엄격한 base64, 디코딩 후 최대 200 MiB. 인코딩 길이를 먼저 제한하고 ASR/화자 분리에서 검증한 bytes를 사용 |
| 이미지 | `data:image/jpeg;base64`, `data:image/png;base64`, `data:image/webp;base64`만 허용. 디코딩된 이미지 합계 24 MiB와 해당 헤더 서명 검사 |
| 출력 | `max_tokens` 정수 1~32768, bool 제외. 기존 정상 요청의 8192는 허용 |
| 기타 필드 | `stream` bool, `temperature` 유한한 숫자, `task_type` 문자열, `language` 문자열/null, 화자 수는 양의 정수 및 최소≤최대 |

원격 HTTP(S)·file 이미지 URL은 추론 서버로 전달하지 않는다. 일반 chat의 비어 있는 messages와 빈 embedding 입력은 HTTP 400이며, 기존 ASR/화자 분리의 빈 messages는 허용한다. developer·assistant·user 등 역할과 정상 text/image 메시지, assistant tool-call의 null content는 유지한다.

이미지의 바이트/서명 검사는 압축 해제 뒤 픽셀 수를 제한하지 않는다. 이미지 작업량이 늘면 추론 엔진의 decoder/pixel 한도를 별도로 검증한다. Gateway는 한도 안의 JSON과 미디어를 메모리에 보관하므로, 이 제한이 동시 요청의 총 메모리 상한을 보장하지는 않는다. 오디오 base64 decode는 ASR의 local/serverless 분기 전에 한 번 실행하고 재사용한다.

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
{"error":{"message":"...","type":"invalid_request|forbidden|unavailable|overloaded"}}
```

- 잘못된 요청과 upstream 400/413/422: HTTP 400 `invalid_request`, fallback·circuit 증가 없음
- 입력 크기 한도 초과: HTTP 413 `invalid_request`, Provider 호출 없음
- `local_only`와 원격 실행 경로 충돌: HTTP 403 `forbidden`, Provider 호출·fallback·circuit 증가 없음
- 적격 Provider 없음 또는 모두 unhealthy: HTTP 503 `unavailable`, caller는 즉시 실패
- 모든 healthy Provider가 admission timeout까지 full: HTTP 503 `overloaded`, `Retry-After: 5`
- upstream 429: 현재 Provider를 제외하고 fallback 가능, circuit 증가 없음
- 401/403/404, 연결/요청/첫 출력 timeout, 5xx: Provider failure로 기록하고 다음 적격 Provider 시도

Backend와 Worker는 typed `overloaded`만 기존 retry 상한 안에서 `Retry-After`를 존중한다. `unavailable`은 재시도하지 않는다.

Provider 오류 응답 본문·예외 문자열·요청 원문·키는 공개 오류와 Gateway 로그에 출력하지 않는다. chat은 기존 오류 분류와 재시도 동작을 유지하며 정적 메시지를 반환한다. media·embedding의 local/serverless upstream 실패는 HTTP 502 `upstream_error`로 반환한다. 로그에는 작업 이름, HTTP 상태와 예외 종류만 남긴다. 첫 SSE 출력 뒤 실패는 정적 stream 오류로 종료하고 원문 traceback을 로그에 남기지 않는다.

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

local-gpu의 자동 route는 `chat: npu-chat → gpu-llm → codex`, `summary: gpu-llm`이다. Codex는 `high + remote_allowed`에서만 자동 후보가 된다. 명시 `codex-*`도 `remote_allowed`가 필요하며 Codex capacity/circuit는 우회하지 않는다. 첫 출력 전 실패 시 GPU high/local로 한 번 fallback한다.

serverless의 LLM 요청은 `remote_allowed`가 필요하며 `auto` primary는 RunPod LLM이다. RunPod가 첫 출력 전에 실패하고 `high + remote_allowed`인 경우만 Codex로 fallback한다. 명시 Codex 실패는 RunPod로 fallback한다. serverless에는 이번 버전의 in-process capacity pool을 적용하지 않지만 동일한 실제 모델명·응답 헤더 계약을 지킨다.

## 5. Readiness와 배포 불변식

`/v1/chat/completions`(media·vision 분기 포함), `/v1/embeddings`, `/health/readiness`는 `Authorization: Bearer <AI_GATEWAY_API_KEY>`로 인증한다. 인증은 요청 JSON 파싱과 Provider 호출 전에 실행된다. Gateway에 키가 없거나 공백뿐이면 HTTP 503, 인증 헤더가 없거나 중복·잘못된 형식·다른 키면 HTTP 401이다. 인증 오류는 키나 요청 원문 없이 `{"detail":"..."}`를 반환하고, 401에는 `WWW-Authenticate: Bearer`가 포함된다. `/health`와 `/health/liveliness`는 공개 생존 점검으로 계속 HTTP 200을 반환한다.

기존 배포 이관 시 Gateway, Backend, Agent Worker, Batch Worker, 평가·벤치마크 클라이언트에 동일한 비어 있지 않은 `AI_GATEWAY_API_KEY`를 지정하고 함께 재시작한다. 이전의 ASR용 기본 키는 제거했다. `.env`와 키를 커밋하거나 요청 헤더를 로그에 남기지 않는다. 평가·벤치마크는 이미 키를 전달하지만 별도 실행 환경에도 값이 있어야 한다. 키 교체도 같은 절차를 따르며 여러 키의 동시 유효 기간은 제공하지 않는다.

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

## 7. 정보용 LLM Judge 계약

`scripts/run_eval.py`는 `model=auto`, `chat/none/local_only`만 요청한다. 별도 `JUDGE_MODEL` 환경변수나 CI secret으로 물리 모델명을 지정하지 않는다. 실제 모델 교체는 기존 Gateway Provider 설정에서 처리한다. 이 계약은 연결 가능한 모델이 채점 형식도 잘 지킨다는 뜻은 아니다.

Judge는 suite 시작 전 합성 질문으로 한 번 확인한다. 비활성화되었거나 대상 turn이 없으면 호출하지 않는다. 이 확인이 실패하거나 turn 채점이 실패하면 남은 Judge 호출을 중단하고 기존 규칙 검증은 계속한다. 연결과 응답 본문 수신을 합쳐 호출당 30초로 제한한다. HTTP 오류 진단은 상태와 허용된 Gateway 오류 유형, 연결 오류는 예외 종류만 출력하며 원문·키·URL을 출력하지 않는다.

채점 응답은 내부 `<think>` 태그 제거 후 `SCORE: 0.0~1.0`과 비어 있지 않은 한 줄 `REASON`이 각각 한 번만 있어야 한다. 중복·추가 텍스트·NaN/Inf·범위 밖 점수·토큰 한도로 잘린 응답은 `format_error`다. HTTP 성공 응답의 Provider와 모델은 길이와 허용 문자를 검사해 형식 실패에서도 기록하며, 식별할 수 없으면 `unknown`으로 표시한다. 결과에는 preflight 상태와 실제 turn의 성공·오류·미실행 수를 분리한다. Judge 점수와 오류는 계속 **정보용**이며 CI 종료 코드는 기존 규칙 검증 결과로만 정한다.
