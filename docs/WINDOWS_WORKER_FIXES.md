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
    
    # Proactor 초기화 (asyncpg 호환성)
    async def _init_proactor():
        await asyncio.sleep(0)
    
    loop.run_until_complete(_init_proactor())
```

#### LLM 워커 (`backend/app/worker/llm_processor.py`)

LLM 워커도 동일한 이벤트 루프 처리 적용

### 핵심 포인트
- Windows에서는 `ProactorEventLoop` 사용 필수
- RQ 워커의 각 작업마다 새로운 이벤트 루프 생성
- Proactor 초기화로 asyncpg 호환성 확보

---

## 문제 2: asyncpg 연결 충돌

### 증상
```
InterfaceError: cannot perform operation: another operation is in progress
```

### 원인
- 전역 SQLAlchemy 엔진이 다른 이벤트 루프의 연결을 사용하려고 시도
- Windows에서 각 RQ 작업이 독립적인 이벤트 루프를 사용하므로 연결 충돌 발생

### 해결 방법

```python
# Windows에서 각 작업마다 새로운 엔진과 세션 생성
current_engine = None
if sys.platform == "win32":
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
    # DB 작업 수행
    repo = ContentRepository(session)
    await repo.update_content_status(content_id, ContentStatus.PROCESSING)
    await session.commit()
finally:
    await session.close()
    # Windows에서 생성한 엔진 정리
    if current_engine:
        await current_engine.dispose()
```

### 핵심 포인트
- Windows에서 작업별 독립 엔진 생성
- 작업 완료 후 엔진 dispose 필수
- Linux/Mac에서는 전역 엔진 사용으로 성능 유지

---

## 문제 3: Unicode 인코딩 문제

### 증상
```
UnicodeEncodeError: 'cp949' codec can't encode character '✓' in position 9: illegal multibyte sequence
```

### 원인
- Windows 콘솔이 `cp949` 인코딩 사용
- 유니코드 특수 문자 (✓, ✗, ⚠ 등) 출력 불가

### 해결 방법

`backend/app/worker/utils.py` 생성:

```python
import sys

def safe_print(text: str) -> None:
    """
    Windows cp949 인코딩 환경에서도 안전하게 출력하는 함수.
    """
    if sys.platform == "win32":
        # Windows에서 문제되는 특수 문자를 ASCII로 변환
        replacements = {
            '✓': '[OK]',
            '✗': '[X]',
            '⚠': '[!]',
            '→': '->',
            '←': '<-',
            '↑': '^',
            '↓': 'v',
            '…': '...',
            '—': '--',
            '─': '-',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
    
    try:
        print(text)
    except UnicodeEncodeError:
        # 그래도 실패하면 ASCII로 강제 변환
        print(text.encode('ascii', 'replace').decode('ascii'))
```

모든 `print()` 호출을 `safe_print()`로 변경:

```python
from .utils import safe_print

# 기존
print(f"[Worker] 작업 시작 ✓")

# 변경 후
safe_print(f"[Worker] 작업 시작 ✓")  # Windows에서는 "[Worker] 작업 시작 [OK]"로 출력
```

### 적용 파일
- `backend/app/worker/utils.py` (새로 생성)
- `backend/app/worker/processor.py`
- `backend/app/worker/llm_processor.py`
- `backend/app/worker/queue.py`
- `backend/app/worker/llm_queue.py`
- `backend/app/worker/run_worker.py`
- `backend/app/worker/run_llm_worker.py`

---

## 문제 4: 순환 import 문제

### 증상
```
ImportError: cannot import name 'LLM_QUEUE_NAME' from partially initialized module 'app.worker.llm_queue'
(most likely due to a circular import)
```

### 원인
- `safe_print` 함수를 `run_llm_worker.py`에 정의
- 다른 모듈들이 `run_llm_worker.py`를 import하면서 순환 참조 발생

### 해결 방법

`safe_print` 함수를 독립적인 유틸리티 모듈로 분리:

1. `backend/app/worker/utils.py` 생성 (위의 문제 3 참조)
2. 모든 파일에서 `from .utils import safe_print` 사용

### 핵심 포인트
- 공통 유틸리티는 독립 모듈로 분리
- 순환 import 방지

---

## 문제 5: LLM 워커 헬스체크 실패

### 증상
```
LM Studio 응답 message.content가 비어 있습니다.
```

### 원인
- LM Studio의 일부 모델 (특히 reasoning 모델)은 `content` 필드 대신 `reasoning` 필드에 응답 저장
- 헬스체크의 `max_tokens`가 너무 작아 응답 생성 실패 (64 토큰)

### 해결 방법

#### 1. `reasoning` 필드 지원 (`backend/app/worker/lmstudio_client.py`)

```python
def request_chat_completion(
    model: str,
    messages: list[dict],
    temperature: float = 0.4,
    max_tokens: int = 1024,
    stream: bool = False,
) -> str:
    # ... API 호출 ...
    
    content = message.get("content", "").strip()
    
    # content가 비어 있으면 reasoning 필드 확인 (일부 모델 지원)
    if not content:
        reasoning = message.get("reasoning", "").strip()
        if reasoning:
            logger.info("Using reasoning field as content (content was empty)")
            content = reasoning
    
    # finish_reason이 length인 경우 (토큰 제한)
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        logger.warning("Response truncated due to max_tokens limit")
        if not content and not is_health_check:
            raise ValueError("LM Studio 응답이 max_tokens 제한으로 잘렸습니다.")
    
    return content
```

#### 2. 헬스체크 `max_tokens` 증가 (`backend/app/worker/run_llm_worker.py`)

```python
def health_check_llm() -> bool:
    try:
        response = request_chat_completion(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "간단한 헬스체크 문장을 요약해 주세요."}],
            temperature=0.4,
            max_tokens=256,  # 64 → 256으로 증가
        )
        return True
    except Exception as exc:
        logger.error("LLM health check failed: %s", exc)
        return False
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

## 문제 7: SUMMARIZING 상태 작업 자동 재큐잉 누락

### 증상
- 워커 재시작 후 `SUMMARIZING` 상태에서 멈춘 콘텐츠가 재처리되지 않음
- `SUMMARY_FAILED` 상태의 콘텐츠도 재시도되지 않음
- 수동으로 재큐잉해야 함

### 원인
- `requeue_summarizing_contents()` 함수가 워커 시작 시에만 실행됨
- 워커가 실행 중일 때 `SUMMARIZING` 상태로 멈춘 콘텐츠는 자동으로 재큐잉되지 않음
- `SUMMARY_FAILED` 상태가 재큐잉 대상에 포함되지 않음

### 해결 방법

#### 1. 재큐잉 로직 개선 (`backend/app/worker/requeue.py`)

```python
async def requeue_summarizing_contents() -> int:
    """
    SUMMARIZING 또는 SUMMARY_FAILED 상태에서 멈춘 콘텐츠를 다시 LLM 큐에 등록한다.
    """
    contents = await _fetch_contents_by_status([
        ContentStatus.SUMMARIZING,
        ContentStatus.SUMMARY_FAILED,  # 추가
    ])
    
    for content in contents:
        # SUMMARY_FAILED 상태인 경우 SUMMARIZING으로 변경 (재시도)
        if content.status == ContentStatus.SUMMARY_FAILED:
            await repo.update_content_status(content.id, ContentStatus.SUMMARIZING)
        
        enqueue_llm_job(content_id=content.id)
```

#### 2. 자동 재큐잉 백그라운드 태스크 추가 (`backend/app/main.py`)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 기존 시작 로직 ...
    
    # 백그라운드 태스크: 주기적으로 SUMMARIZING 상태 콘텐츠 자동 재큐잉
    async def auto_requeue_llm_jobs():
        """주기적으로 SUMMARIZING 상태의 콘텐츠를 자동으로 재큐잉."""
        from .worker.requeue import requeue_summarizing_contents
        
        while True:
            try:
                await asyncio.sleep(60)  # 60초마다 체크
                requeued = await requeue_summarizing_contents()
                if requeued > 0:
                    logger.info("Auto-requeued %d LLM jobs", requeued)
            except Exception as exc:
                logger.exception("Failed to auto-requeue LLM jobs")
    
    # 백그라운드 태스크 시작
    auto_requeue_task = asyncio.create_task(auto_requeue_llm_jobs())
    
    yield
    
    # 종료 시: 백그라운드 태스크 취소
    auto_requeue_task.cancel()
```

### 핵심 포인트
- **자동 재큐잉**: FastAPI 백그라운드 태스크로 60초마다 자동 체크
- **SUMMARY_FAILED 포함**: 실패한 요약 작업도 자동 재시도
- **상태 관리**: 재시도 시 상태를 적절히 변경
- **중복 방지**: 이미 큐에 있는 작업은 재큐잉하지 않음 (무한 루프 방지)

#### 중복 재큐잉 방지 (`backend/app/worker/llm_queue.py`)

```python
def is_llm_job_in_queue(*, content_id: int) -> bool:
    """해당 content_id의 LLM 작업이 큐에 이미 있는지 확인."""
    queue = _get_queue()
    job_ids = queue.get_job_ids()
    
    # 시작된 작업도 확인 (처리 중인 작업)
    started_job_ids = queue.started_job_registry.get_job_ids()
    all_job_ids = set(job_ids) | set(started_job_ids)
    
    for job_id in all_job_ids:
        job = queue.fetch_job(job_id)
        job_kwargs = getattr(job, "kwargs", {})
        if job_kwargs.get("content_id") == content_id:
            return True
    return False
```

재큐잉 전에 중복 체크:

```python
# 이미 큐에 있는 작업은 재큐잉하지 않음
if is_llm_job_in_queue(content_id=content.id):
    logger.debug("LLM job already in queue, skipping requeue")
    continue
```

---

## 문제 8: 상태 구조 재구성

### 기존 문제
- `FAILED` 상태가 ASR 실패인지 명확하지 않음
- `RETRYING` 상태가 정의되어 있으나 실제 사용되지 않음
- 상태 이름이 단계를 명확히 드러내지 못함

### 개선 사항

#### 변경된 상태 구조

```python
class ContentStatus(str, enum.Enum):
    """콘텐츠 처리 상태."""
    
    # 초기 상태
    QUEUED = "QUEUED"  # 처리 대기중 (큐에 등록됨)
    
    # 진행 중 상태
    PROCESSING = "PROCESSING"  # ASR/화자분리 진행 중
    SUMMARIZING = "SUMMARIZING"  # LLM 요약 중
    
    # 완료 상태
    COMPLETED = "COMPLETED"  # 전체 파이프라인 완료
    
    # 실패 상태
    ASR_FAILED = "ASR_FAILED"  # ASR/화자분리 단계 실패 (명확화)
    SUMMARY_FAILED = "SUMMARY_FAILED"  # LLM 요약 실패
    
    # 취소 상태
    CANCELLED = "CANCELLED"  # 취소됨 (사용자 취소 또는 타임아웃)
```

#### 주요 변경사항
1. `FAILED` → `ASR_FAILED`: 실패 단계 명확화
2. `RETRYING` 제거: 사용되지 않는 상태 제거

#### 상태 흐름

```
파일 업로드
    ↓
QUEUED (대기 중)
    ↓
PROCESSING (ASR 처리 중)
    ↓
    ├─→ ASR_FAILED (ASR 실패) ─→ 재큐잉 가능
    │
    └─→ SUMMARIZING (LLM 요약 중)
            ↓
            ├─→ SUMMARY_FAILED (요약 실패) ─→ 자동 재큐잉 (60초마다)
            │
            └─→ COMPLETED (완료)
```

#### 마이그레이션

데이터베이스 마이그레이션 파일: `backend/alembic/versions/20251128_01_rename_failed_to_asr_failed.py`

```bash
# 마이그레이션 실행
alembic upgrade head
```

SQL 변경사항:
```sql
-- FAILED → ASR_FAILED 변경
UPDATE content SET status = 'ASR_FAILED' WHERE status = 'FAILED';

-- RETRYING 상태 정리 (혹시 있다면)
UPDATE content SET status = 'QUEUED' WHERE status = 'RETRYING';
```

### 적용 파일
- `backend/app/db/models.py`: enum 정의
- `backend/app/worker/processor.py`: ASR_FAILED 사용
- `client/lib/api.ts`: TypeScript 타입 정의
- `client/components/ContentList.tsx`: UI 라벨 및 색상
- `backend/alembic/versions/20251128_01_rename_failed_to_asr_failed.py`: 마이그레이션

---

## 요약

### 해결된 문제들
1. ✅ Windows 이벤트 루프 문제 (ProactorEventLoop 사용)
2. ✅ asyncpg 연결 충돌 (작업별 독립 엔진 생성)
3. ✅ Unicode 인코딩 문제 (safe_print 유틸리티)
4. ✅ 순환 import 문제 (utils.py 분리)
5. ✅ LLM 워커 헬스체크 실패 (reasoning 필드 지원)
6. ✅ QUEUED 상태 재큐잉 누락 (상태 추가)
7. ✅ SUMMARIZING 상태 자동 재큐잉 누락 (백그라운드 태스크 추가)
8. ✅ 상태 구조 재구성 (FAILED → ASR_FAILED, RETRYING 제거)

### 핵심 원칙
- **Windows 특화 처리**: 플랫폼별 분기 처리 (`sys.platform == "win32"`)
- **작업별 독립성**: Windows에서 각 RQ 작업이 독립적인 이벤트 루프와 DB 엔진 사용
- **안전한 출력**: 모든 콘솔 출력은 `safe_print()` 사용
- **상세한 로깅**: 각 단계별 로깅으로 디버깅 용이성 향상
- **명확한 상태**: 단계별 실패 상태 명확화

### 테스트 확인
- ✅ 파일 업로드 후 ASR 워커 정상 작동
- ✅ LLM 워커 헬스체크 통과
- ✅ 워커 재시작 시 QUEUED 작업 재큐잉
- ✅ Windows 콘솔에서 특수 문자 출력 정상
- ✅ 상태 구조 개선으로 명확성 향상

---

## 참고 자료

- [Python asyncio Windows 지원](https://docs.python.org/3/library/asyncio-platforms.html#windows)
- [asyncpg Windows 호환성](https://github.com/MagicStack/asyncpg)
- [SQLAlchemy 비동기 엔진](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
