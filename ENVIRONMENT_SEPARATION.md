# 환경 분리 완료 가이드

## 개요

API 서버와 GPU 워커의 의존성을 분리하여 각각 최적화된 환경에서 실행할 수 있도록 구성했습니다.

## 아키텍처

```
┌─────────────────────────────────────────────┐
│  API 컨테이너 (WSL/Docker)                   │
│  - FastAPI, SQLAlchemy, Redis, Celery       │
│  - 가벼운 웹 서비스 패키지만 포함            │
│  - librosa, soundfile (오디오 처리 기본)     │
│  - torch/pyannote 등 GPU 관련 패키지 제외    │
│  - 빌드 시간: 30초~1분                       │
│  - 이미지 크기: ~500MB                       │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  GPU 워커 (Windows + PM2)                    │
│  - torch, pyannote.audio                     │
│  - whisper, diarization, LLM 등              │
│  - GPU 가속 기능 전부 포함                   │
│  - ROCm/Vulkan 지원                          │
└─────────────────────────────────────────────┘
```

---

## 변경 사항

### 1. `backend/pyproject.toml`

**오디오 처리 공통 의존성**을 API/워커 공통으로 설치하고, GPU 관련 패키지만 `optional = true`로 설정하여 `extras` 그룹으로 분리:

```toml
# API 서버 공통 (항상 설치)
fastapi = "^0.111.0"
sqlalchemy = "^2.0.29"
redis = "^5.0.4"
# ... 등

# 오디오 처리 공통 의존성 (API/워커 공통)
librosa = "^0.10.0"
soundfile = "^0.12.0"

# GPU 워커 전용 (선택적 설치)
torch = {version = "*", optional = true}
"pyannote.audio" = {version = "^3.1.0", optional = true}
llama-cpp-python = {version = "^0.2.80", optional = true}

[tool.poetry.extras]
gpu_worker = ["torch", "pyannote.audio", "llama-cpp-python"]
```

### 2. `backend/Dockerfile`

API 컨테이너는 GPU 관련 패키지 없이 설치 (librosa/soundfile은 기본으로 포함):

```dockerfile
RUN poetry install --no-interaction --no-ansi --no-root --without dev
# --extras gpu_worker 옵션 없음 → GPU 전용 패키지(torch, pyannote) 설치 안 됨
```

### 3. `backend/app/worker/queue.py` & `llm_queue.py`

API 서버 시작 시 GPU 워커 코드를 import하지 않도록 함수 경로를 문자열로 지정:

```python
# 변경 전
from .processor import process_transcription_job
job = queue.enqueue(process_transcription_job, ...)

# 변경 후
PROCESSOR_FUNCTION_PATH = "app.worker.processor.process_transcription_job"
job = queue.enqueue(PROCESSOR_FUNCTION_PATH, ...)
```

이로 인해 API 서버가 시작될 때 `torch`, `pyannote.audio` 등을 로드하지 않게 되어, GPU 라이브러리가 없어도 정상 동작합니다.

### 4. `backend/app/core/config.py`

`DATABASE_URL` 환경변수를 내부 필드명 `database_url`로 매핑하고, 하위 호환성을 위해 `postgres_dsn` 프로퍼티 제공:

```python
database_url: str = Field(
    "postgresql+asyncpg://user:pass@localhost:5432/asr",
    validation_alias="DATABASE_URL",
)

@property
def postgres_dsn(self) -> str:
    """하위 호환성을 위한 postgres_dsn 프로퍼티."""
    return self.database_url
```

### 5. `docker-compose.yml`

Backend 서비스에서 환경변수를 명시적으로 지정 (`.env` 의존성 제거):

```yaml
environment:
  - TASK_QUEUE_TYPE=celery
  - DATABASE_URL=postgresql+asyncpg://user:pass@asr-postgres:5432/asr
  - REDIS_URL=redis://asr-redis:6379/0
  - S3_ENDPOINT=http://asr-minio:9000
```

---

## 설치 방법

### API 서버용 (Docker 컨테이너)

```bash
cd backend
poetry install --no-root --without dev
# GPU 관련 패키지(torch, pyannote)는 설치되지 않음
# librosa, soundfile은 기본으로 설치됨
```

또는 Docker 빌드:

```bash
docker-compose build backend
# Dockerfile에서 자동으로 가벼운 환경만 설치
```

### GPU 워커용 (Windows 호스트)

```bash
cd C:\timblo\torch-test\backend
poetry install --no-root --extras gpu_worker
# torch, pyannote.audio 등 모든 GPU 관련 패키지 설치
```

---

## 확인 방법

### API 환경 확인 (GPU 패키지 없어야 함)

```bash
cd backend
poetry run python -c "import fastapi; print('FastAPI OK')"
# 정상 동작

poetry run python -c "import torch"
# ImportError 발생 → 정상 (GPU 패키지 없음)
```

### GPU 워커 환경 확인 (모든 패키지 있어야 함)

```bash
cd backend
poetry run python -c "import torch; print('Torch version:', torch.__version__)"
# Torch version: 2.7.1+cpu (또는 다른 버전)

poetry run python -c "import pyannote.audio; print('Pyannote OK')"
# Pyannote OK

poetry run python -c "import librosa; print('Librosa OK')"
# Librosa OK
```

---

## PM2 워커 재시작

환경 분리 후 PM2 워커를 재시작해야 합니다:

```bash
# PM2 워커 재시작
pm2 restart celery-worker

# 또는 처음 시작하는 경우
pm2 start ecosystem.config.js

# 로그 확인
pm2 logs celery-worker
```

---

## 기대 효과

### 빌드 시간

- **이전**: 6분 이상 (torch/pyannote 컴파일)
- **이후**: 30초~1분 (API 서버만)

### 이미지 크기

- **이전**: 2~3GB (GPU 패키지 포함)
- **이후**: 500MB 이하 (웹 서비스만)

### 배포 속도

- API 서버 업데이트 시 빠른 빌드/배포 가능
- GPU 워커는 Windows에서 독립적으로 관리

---

## 트러블슈팅

### Q: 워커에서 "ModuleNotFoundError: No module named 'torch'" 에러

A: GPU 워커 환경에서 `--extras gpu_worker` 옵션으로 재설치:

```bash
cd backend
poetry install --no-root --extras gpu_worker
pm2 restart celery-worker
```

### Q: API 서버에서 torch 관련 import 에러

A: API 서버에서는 GPU 관련 기능을 직접 사용하지 않아야 합니다.  
모든 GPU 연산은 Celery 작업으로 워커에 위임되어야 합니다.

### Q: 의존성 추가/변경 후 작업

1. `pyproject.toml` 수정
2. `poetry lock` 실행
3. API 환경: `poetry install --no-root --without dev`
4. GPU 환경: `poetry install --no-root --extras gpu_worker`
5. 서비스 재시작

---

## 다음 단계

1. ✅ 환경 분리 완료
2. ⏳ Docker 이미지 재빌드 (필요시)
3. ⏳ PM2 워커 재시작
4. ⏳ 전체 시스템 테스트

---

## 참고

- API 서버: `DEPLOYMENT.md` 참조
- Docker 빌드: `docker-compose build backend`
- 워커 관리: `pm2 status`, `pm2 logs celery-worker`

