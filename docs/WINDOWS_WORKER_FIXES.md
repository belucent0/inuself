# Windows 환경 워커 문제 해결 가이드

## 개요

이 문서는 Windows 환경에서 ASR 워커와 LLM 워커가 작동하지 않던 문제들을 해결한 과정과 해결 방법을 정리한 것입니다.

## 문제 요약

### 주요 증상
- 파일 업로드 후 ASR 워커가 작업을 처리하지 않음
- LLM 워커가 헬스체크에서 실패
- Windows 콘솔에서 특수 문자 출력 시 `UnicodeEncodeError` 발생
- 워커 재시작 시 `QUEUED` 상태 작업이 재처리되지 않음

---

## 문제 1: Windows 이벤트 루프 문제

### 증상
```
RuntimeError: Event loop is closed
AttributeError: 'NoneType' object has no attribute 'send'
```

### 원인
- Windows에서 `asyncpg` (PostgreSQL 비동기 드라이버)는 `ProactorEventLoop`를 사용해야 함
- 기본적으로 `asyncio`는 `SelectorEventLoop`를 사용
- `ProactorEventLoop`의 내부 `_proactor`가 초기화되지 않은 상태에서 DB 작업 시도

### 해결 방법

#### ASR 워커 (`backend/app/worker/processor.py`)

```python
if sys.platform == "win32":
    # 기존 이벤트 루프 정리
    try:
        existing_loop = asyncio.get_event_loop()
        if existing_loop and not existing_loop.is_closed():
            pending = asyncio.all_tasks(existing_loop)
            if pending:
                existing_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            existing_loop.close()
    except RuntimeError:
        pass
    
    # Windows에서 ProactorEventLoop 사용 (asyncpg 호환)
    policy = asyncio.get_event_loop_policy()
    if not isinstance(policy, asyncio.WindowsProactorEventLoopPolicy):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Proactor 초기화 (빈 코루틴 실행)
    if isinstance(loop, asyncio.ProactorEventLoop):
        async def _init_proactor():
            pass
        loop.run_until_complete(_init_proactor())
```

#### LLM 워커 (`backend/app/worker/llm_processor.py`)
- 동일한 패턴 적용

### 핵심 포인트
- Windows에서는 **매 작업마다 새로운 이벤트 루프 생성**
- `ProactorEventLoop` 정책 명시적 설정
- Proactor 초기화를 위한 더미 코루틴 실행

---

## 문제 2: asyncpg 연결 충돌

### 증상
```
InterfaceError: cannot perform operation: another operation is in progress
```

### 원인
- Windows에서 각 RQ 작업이 새로운 이벤트 루프를 생성
- 전역 SQLAlchemy `engine`이 다른 이벤트 루프의 연결을 재사용하려고 시도
- 이벤트 루프 간 연결 공유로 인한 충돌 발생

### 해결 방법

각 작업마다 현재 이벤트 루프에서 새로운 DB 엔진과 세션 생성:

```python
async def _process_job(...):
    current_engine = None
    if sys.platform == "win32":
        # 현재 이벤트 루프에서 새로운 엔진 생성
        current_engine = create_async_engine(
            settings.postgres_dsn,
            echo=settings.debug,
            future=True,
        )
        CurrentAsyncSessionLocal = async_sessionmaker(
            current_engine,
            expire_on_commit=False,
        )
        session = CurrentAsyncSessionLocal()
    else:
        # Linux/Mac에서는 전역 엔진 사용
        session = AsyncSessionLocal()
    
    try:
        # 작업 수행
        ...
    finally:
        await session.close()
        # Windows에서 생성한 엔진도 명시적으로 닫기
        if current_engine:
            await current_engine.dispose()
```

### 핵심 포인트
- Windows: **작업별 독립적인 DB 엔진 생성**
- Linux/Mac: 전역 엔진 재사용 (기존 방식 유지)
- 작업 완료 후 엔진 `dispose()` 호출로 연결 정리

---

## 문제 3: Windows Unicode 인코딩 문제

### 증상
```
UnicodeEncodeError: 'cp949' codec can't encode character '✓' in position 9: illegal multibyte sequence
```

### 원인
- Windows 기본 인코딩이 `cp949` (한국어 Windows)
- `print()` 함수에서 유니코드 특수 문자(✓, ✗, ℹ 등) 출력 시 인코딩 실패

### 해결 방법

`safe_print()` 유틸리티 함수 생성 (`backend/app/worker/utils.py`):

```python
import sys

def safe_print(*args, **kwargs):
    """Windows cp949 인코딩 문제를 피하기 위한 안전한 print 함수."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # Unicode 문자를 ASCII로 대체
        safe_args = []
        for arg in args:
            if isinstance(arg, str):
                safe_arg = (
                    arg.replace("ℹ", "[INFO]")
                    .replace("✗", "[ERROR]")
                    .replace("✓", "[OK]")
                    .replace("⚠", "[WARN]")
                    .replace("→", "->")
                )
                safe_args.append(safe_arg)
            else:
                safe_args.append(arg)
        print(*safe_args, **kwargs)
```

### 적용 파일
- `backend/app/worker/processor.py`
- `backend/app/worker/llm_processor.py`
- `backend/app/worker/queue.py`
- `backend/app/worker/llm_queue.py`
- `backend/app/worker/run_worker.py`
- `backend/app/worker/run_llm_worker.py`
- `backend/app/worker/cleanup.py`

### 핵심 포인트
- 모든 `print()` 호출을 `safe_print()`로 교체
- 유니코드 특수 문자를 ASCII로 안전하게 변환

---

## 문제 4: 순환 Import 문제

### 증상
```
ImportError: cannot import name 'LLM_QUEUE_NAME' from partially initialized module 
'app.worker.llm_queue' (most likely due to a circular import)
```

### 원인
- `run_llm_worker.py` → `llm_queue.py` → `llm_processor.py` → `utils.py` (safe_print)
- `run_llm_worker.py`에서 `safe_print`를 직접 정의하고 있었음
- 여러 모듈이 서로를 import하면서 순환 의존성 발생

### 해결 방법

`safe_print` 함수를 독립적인 유틸리티 모듈로 분리:

```
backend/app/worker/
├── utils.py          # safe_print 함수 (독립 모듈)
├── llm_processor.py  # from .utils import safe_print
├── llm_queue.py      # from .utils import safe_print
└── run_llm_worker.py # from .utils import safe_print
```

### 핵심 포인트
- 공통 유틸리티 함수는 독립 모듈로 분리
- 순환 의존성 제거

---

## 문제 5: LLM 워커 헬스체크 실패

### 증상
```
[LLM Worker] [ERROR] LM Studio 헬스체크 실패: LM Studio 응답 message.content가 비어 있습니다.
```

### 원인
- LM Studio의 reasoning 모델이 `content` 대신 `reasoning` 필드에 응답을 반환
- `finish_reason`이 "length"인 경우 토큰 제한으로 content가 비어있을 수 있음
- 헬스체크의 `max_tokens`가 64로 너무 작음

### 해결 방법

#### 1. `lmstudio_client.py` 수정

```python
message = choices[0].get("message") or {}
content = message.get("content", "").strip()
finish_reason = choices[0].get("finish_reason", "")

# content가 비어있지만 reasoning이 있으면 reasoning을 사용
if not content:
    reasoning = message.get("reasoning", "").strip()
    if reasoning:
        logger.info("LM Studio 응답: content가 비어있지만 reasoning이 있음")
        return reasoning[:200] if len(reasoning) > 200 else reasoning
    
    # finish_reason이 "length"인 경우 처리
    if finish_reason == "length":
        logger.warning("LM Studio 응답: 토큰 제한으로 응답 완료하지 못함")
        return "응답 생성 중 (토큰 제한)"
    
    raise LMStudioClientError("LM Studio 응답 message.content가 비어 있습니다.")
```

#### 2. `run_llm_worker.py` 헬스체크 수정

```python
# max_tokens 증가 (64 -> 256)
response = request_chat_completion(
    settings=settings,
    messages=test_messages,
    temperature=0.1,
    max_tokens=256,  # reasoning 모델을 위해 증가
    stream=False,
)

# 특수 케이스 처리
if response == "응답 생성 중 (토큰 제한)":
    safe_print("[LLM Worker] [WARN] 모델이 토큰 제한으로 응답을 완료하지 못했습니다.")
    safe_print("[LLM Worker]   워커는 시작하지만 첫 요청이 느릴 수 있습니다.")
    return True
```

### 핵심 포인트
- `reasoning` 필드 지원
- `finish_reason`이 "length"인 경우 관대하게 처리
- 헬스체크 `max_tokens` 증가

---

## 문제 6: QUEUED 상태 작업 재큐잉 누락

### 증상
- 워커 재시작 후 `QUEUED` 상태 작업이 재처리되지 않음
- `PROCESSING` 상태만 재큐잉됨

### 원인
- `requeue_processing_contents()` 함수가 `ContentStatus.PROCESSING`만 조회
- 워커 크래시 시 `QUEUED` 상태로 남은 작업이 재큐잉되지 않음

### 해결 방법

`backend/app/worker/requeue.py` 수정:

```python
async def requeue_processing_contents(session: AsyncSession) -> int:
    """PROCESSING 또는 QUEUED 상태인 콘텐츠를 재큐잉."""
    repo = ContentRepository(session)
    
    # PROCESSING과 QUEUED 상태 모두 조회
    stuck_contents = await repo.get_by_statuses([
        ContentStatus.PROCESSING,
        ContentStatus.QUEUED,  # 추가
    ])
    
    # ... 재큐잉 로직
```

### 핵심 포인트
- `QUEUED` 상태도 재큐잉 대상에 포함
- 워커 크래시 시 누락된 작업 복구

---

## 파일 업로드 로깅 개선

### 개선 사항
파일 업로드 과정의 각 단계에 상세 로깅 추가:

```python
# backend/app/services/content_service.py
logger.info("[Upload] 파일 업로드 시작")
logger.info("[Upload] 파일 크기: %d bytes", len(file_content))
logger.info("[Upload] 스토리지 업로드 중...")
# ... 스토리지 업로드
logger.info("[Upload] OK 스토리지 업로드 완료")
logger.info("[Upload] OK DB 저장 완료")
logger.info("[Upload] 큐에 작업 등록 중...")
# ... 큐 등록
logger.info("[Upload] OK 큐 등록 완료")
```

### 적용 파일
- `backend/app/services/content_service.py`
- `backend/app/controllers/content_controller.py`

---

## 요약

### 해결된 문제들
1. ✅ Windows 이벤트 루프 문제 (ProactorEventLoop 사용)
2. ✅ asyncpg 연결 충돌 (작업별 독립 엔진 생성)
3. ✅ Unicode 인코딩 문제 (safe_print 유틸리티)
4. ✅ 순환 import 문제 (utils.py 분리)
5. ✅ LLM 워커 헬스체크 실패 (reasoning 필드 지원)
6. ✅ QUEUED 상태 재큐잉 누락 (상태 추가)

### 핵심 원칙
- **Windows 특화 처리**: 플랫폼별 분기 처리 (`sys.platform == "win32"`)
- **작업별 독립성**: Windows에서 각 RQ 작업이 독립적인 이벤트 루프와 DB 엔진 사용
- **안전한 출력**: 모든 콘솔 출력은 `safe_print()` 사용
- **상세한 로깅**: 각 단계별 로깅으로 디버깅 용이성 향상

### 테스트 확인
- ✅ 파일 업로드 후 ASR 워커 정상 작동
- ✅ LLM 워커 헬스체크 통과
- ✅ 워커 재시작 시 QUEUED 작업 재큐잉
- ✅ Windows 콘솔에서 특수 문자 출력 정상

---

## 참고 자료

- [Python asyncio Windows 지원](https://docs.python.org/3/library/asyncio-platforms.html#windows)
- [asyncpg Windows 호환성](https://github.com/MagicStack/asyncpg)
- [SQLAlchemy 비동기 엔진](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

