# LiteLLM 세마포어: 프로세스 경계를 넘는 GPU 자원 조율

> **프로젝트**: LLM 기반 문서요약 + AI 채팅 서비스
> **기간**: 2025년 하반기 ~ 2026년 1월
> **역할**: 인프라 설계 및 구현 (단독)

---

## 1. Situation — 어떤 상황이었나

GPU 한 대로 여러 AI 기능을 동시에 제공하는 구조였다. 하나의 GPU에서 실행되는 작업들:

- **LLM 추론**: 채팅 답변 생성, 문서 요약
- **ASR(음성 인식)**: Whisper 모델로 음성 → 텍스트 변환
- **OCR**: PDF/이미지 텍스트 추출

이 요청들은 각기 다른 프로세스에서 발생했다:
- **Backend**: 사용자 실시간 채팅 요청 처리
- **Worker**: 파일 업로드 후 배경 처리 (요약, ASR, OCR)
- **LiteLLM Proxy**: 모든 LLM 요청의 통합 게이트웨이

문제는 이 세 프로세스가 동일한 GPU를 공유하면서도 서로의 상태를 전혀 몰랐다는 것이다.

---

## 2. Problem — 구체적으로 뭐가 문제였나

**OOM(Out of Memory) 에러가 간헐적으로 발생했다.**

재현 시나리오:
1. Worker가 ASR 작업을 위해 Whisper를 GPU에 로드
2. (거의 동시에) Backend가 채팅 요청을 LLM 서버로 전송
3. LLM이 GPU에 로드되려는 순간, 이미 Whisper가 GPU 메모리를 점유 중
4. CUDA OOM → 요청 실패

문제의 본질은 두 가지였다:

1. **프로세스 간 공유 상태 없음**: "현재 GPU가 사용 중인가?"를 확인할 수 있는 단일 진실의 원천(Single Source of Truth)이 없었다.

2. **비결정적 타이밍**: 트래픽이 적을 때는 문제가 없고, 피크 타임에만 발생했다. 간헐적이라 더 디버깅이 어려웠다.

---

## 3. Attempted Solution — 처음에 어떻게 시도했나

**첫 번째 시도: 프로세스 내 asyncio.Lock**

```python
# Backend process 내부 잠금
_gpu_lock = asyncio.Lock()

async def call_llm(prompt):
    async with _gpu_lock:
        return await llm_client.generate(prompt)
```

`asyncio.Lock`은 같은 이벤트 루프 내에서만 유효하다. Worker는 별도 프로세스이므로 이 잠금의 영향을 전혀 받지 않았다. **프로세스 경계를 넘지 못하는 잠금**이었다.

**두 번째 시도: 파일 기반 잠금(filelock)**

```python
from filelock import FileLock

with FileLock("/tmp/gpu.lock"):
    call_gpu_model()
```

파일 잠금은 같은 호스트의 프로세스 간에는 동작했다. 그러나 두 가지 문제가 있었다:

- **Lock 파일 정리 실패**: 프로세스가 비정상 종료되면 잠금 파일이 남아 영구 잠금 상태가 됐다
- **컨테이너 경계**: Worker와 LiteLLM Proxy가 서로 다른 Docker 컨테이너에서 실행되므로 파일 시스템을 공유하지 않았다

두 시도 모두 공통된 실패 원인이 있었다: **분산 환경에서 공유 상태를 관리할 인프라가 없었다.**

---

## 4. Final Solution — 최종적으로 어떻게 해결했나

### 설계 원칙: "Redis를 분산 세마포어의 단일 진실 원천으로"

이미 메시지 큐로 Valkey(Redis 호환)를 사용하고 있었다. 모든 컨테이너가 동일한 Valkey 인스턴스에 접근 가능했다. **기존 인프라를 분산 잠금 저장소로 재활용**했다.

### 구현 아키텍처

```
[Backend]  [Worker]  [LiteLLM Proxy]
    │           │           │
    └───────────┴───────────┘
                │
           [Valkey]
           worker:gpu:active = "1"  ← GPU 사용 중 표시
           worker:npu:active = "1"  ← NPU 사용 중 표시
```

### 핵심 구현: WorkerSemaphore

```python
# worker/utils/semaphore.py
class WorkerSemaphore:
    """Redis를 통해 프로세스 경계를 넘는 분산 세마포어."""

    def __init__(self, resource_name: str, timeout: int = 600):
        self.key = f"worker:{resource_name}:active"
        self.timeout = timeout  # TTL: 비정상 종료 시 자동 해제

    def __enter__(self):
        self.redis_client.set(self.key, "1", ex=self.timeout)
        # key가 존재하는 동안 = 자원 점유 중

    def __exit__(self, *args):
        self.redis_client.delete(self.key)
        # key 삭제 = 자원 해제

# 사용 예시
with WorkerSemaphore("gpu"):
    run_whisper_asr(audio_file)
```

**TTL(Time-To-Live) 설계**가 핵심이었다. 프로세스가 비정상 종료되면 `__exit__`이 호출되지 않는다. TTL을 600초로 설정해, 최악의 경우에도 10분 후 자동으로 잠금이 해제되도록 했다.

### LiteLLM custom_handler: 요청 전 자원 확인

LiteLLM Proxy의 `custom_handler.py`는 요청을 보내기 전에 Redis를 조회한다:

```python
async def is_provider_busy_async(provider: str) -> bool:
    if provider == "npu":
        return await redis.exists("worker:npu:active")
    elif provider == "gpu":
        return await redis.exists("worker:gpu:active")

async def wait_for_available_provider_async(
    primary: str,
    fallback: str,
    max_wait: float = 3600.0,  # 최대 1시간 대기 (긴 ASR 작업 대응)
    poll_interval: float = 0.5,
) -> tuple:
    """Primary가 busy면 fallback으로, 둘 다 busy면 대기"""
    while True:
        if not await is_provider_busy_async(primary):
            lock_id = await acquire_device_lock(primary)  # SETNX 원자적 획득
            if lock_id:
                return get_provider_config(primary), lock_id

        if not await is_provider_busy_async(fallback):
            lock_id = await acquire_device_lock(fallback)
            if lock_id:
                return get_provider_config(fallback), lock_id

        await asyncio.sleep(poll_interval)
```

### SETNX로 경쟁 조건(Race Condition) 방지

"busy 여부 확인"과 "잠금 획득"이 두 단계로 분리되면 TOCTOU(Time-of-Check to Time-of-Use) 문제가 발생한다. Redis의 `SETNX`(SET if Not eXists)를 사용해 **확인과 획득을 원자적으로** 처리했다.

```python
async def acquire_device_lock(device: str) -> str | None:
    lock_id = str(uuid.uuid4())
    key = f"worker:{device}:active"
    # SETNX: key가 없을 때만 set, 성공 여부 반환
    acquired = await redis.set(key, lock_id, ex=600, nx=True)
    return lock_id if acquired else None
```

### Provider Manager: asyncio.Semaphore와 Redis 세마포어의 조합

Provider Manager 내부에서는 `asyncio.Semaphore`로 동시성을 1차 제어하고, 외부 프로세스(Worker)를 위해 Redis 세마포어를 2차 지표로 병행 관리했다.

```python
# infra/provider_manager/services/stream_processor.py
self._device_semaphores = {
    "gpu": asyncio.Semaphore(2),  # GPU 동시 작업 최대 2개
    "npu": asyncio.Semaphore(1),  # NPU 동시 작업 최대 1개
}

async with self._device_semaphores[device_group]:
    # asyncio.Semaphore: 같은 프로세스 내 동시성 제어
    # Redis 세마포어: 외부 프로세스에게 상태 공유
    await self._set_redis_semaphore(device_group, active=True)
    try:
        result = await run_model(request)
    finally:
        await self._set_redis_semaphore(device_group, active=False)
```

---

## 5. Result — 결과와 임팩트

| 지표 | Before | After |
|------|--------|-------|
| GPU OOM 에러 | 피크 타임 간헐적 발생 | 제거 |
| 프로세스 비정상 종료 후 잠금 | 영구 잠금 (수동 해제 필요) | TTL로 최대 10분 내 자동 해제 |
| 컨테이너 간 자원 조율 | 불가능 | Redis 공유 상태로 해결 |
| 경쟁 조건 | TOCTOU 취약 | SETNX 원자적 획득으로 방지 |

가장 눈에 띄는 변화는 **운영 부담 감소**였다. 이전에는 OOM 에러가 발생하면 수동으로 컨테이너를 재시작해야 했다. 이후엔 에러 자체가 발생하지 않았다.

---

## 6. Lessons Learned — 이 경험에서 배운 것

**"분산 시스템의 동시성 제어는 공유 인프라 위에서만 가능하다"**

`asyncio.Lock`이나 `filelock`이 작동하지 않은 이유는 그것들이 "같은 공간을 공유하는 존재들"만 조율할 수 있기 때문이다. 컨테이너로 분리된 프로세스들은 메모리도, 파일 시스템도 공유하지 않는다.

세 가지를 배웠다:

1. **이미 있는 인프라를 다목적으로 활용하라**: 새로운 잠금 서비스를 추가하는 대신, 이미 운영 중이던 Valkey를 세마포어 저장소로 재활용했다. 인프라 복잡도를 늘리지 않으면서 문제를 해결했다.

2. **실패 경로를 먼저 설계하라**: TTL 설정은 "정상 경로"가 아니라 "비정상 종료" 경로를 위한 설계다. 잠금 해제 코드보다 TTL 설계가 더 중요했다. 장애 상황에서 시스템이 스스로 복구할 수 있어야 한다.

3. **원자성이 중요한 이유를 직접 경험했다**: SETNX를 도입하기 전, "확인 후 획득" 방식에서 레이스 컨디션이 실제로 발생했다. 두 프로세스가 동시에 "여유 있음"을 확인하고 동시에 잠금을 획득하는 상황이었다. 원자적 연산의 필요성을 코드로 증명한 경험이었다.

---

## 7. 기술 스택

- **Valkey (Redis 호환)**: 분산 세마포어 저장소 (`SETNX`, TTL 활용)
- **Python asyncio**: 프로세스 내 동시성 제어 (`asyncio.Semaphore`)
- **LiteLLM Proxy**: 모델 라우팅 및 Fallback 조율 (`custom_handler.py`)
- **Docker Compose**: 멀티 컨테이너 네트워크 (공유 Valkey 접근)

---

*관련 문서: `docs/architecture-v1.1.0.md` (Section 12), `infra/litellm/custom_handler.py`, `worker/utils/semaphore.py`*
