# GPU Worker 패키지

Backend API 서버와 분리된 독립적인 GPU 워커 패키지입니다.

## 아키텍처

```
worker/
├── celery_app.py         # Celery 앱 설정
├── config.py             # 환경변수 기반 설정 (WorkerSettings)
├── logging_config.py     # loguru 기반 로깅
├── storage.py            # S3/MinIO 클라이언트
├── result_publisher.py   # Redis Pub/Sub (실시간 결과 전송)
├── distributed_lock.py   # 분산 락 (동시 처리 방지)
├── tasks/                # Celery 태스크
│   ├── asr_task.py       # ASR + 화자분리
│   ├── llm_task.py       # LLM 요약
│   ├── ocr_task.py       # OCR/PDF 처리
│   └── youtube_task.py   # YouTube 다운로드
├── pipelines/            # 파이프라인 코드
│   ├── asr/              # Whisper + PyAnnote
│   ├── llm/              # LlamaCpp 요약
│   └── ocr/              # OCR 처리
└── pyproject.toml        # 독립 의존성
```

## 설치

### 1. Poetry 가상환경 생성

```bash
cd worker
poetry install
```

### 2. ROCm PyTorch 설치 (GPU 가속)

Poetry 가상환경에 ROCm 빌드 PyTorch를 설치합니다:

```bash
# Poetry 가상환경 경로 확인
poetry env info --path

# 가상환경 활성화
poetry shell

# ROCm PyTorch 설치 (Windows + gfx1150)
pip install --no-cache-dir --no-deps "https://rocm.nightlies.amd.com/v2-staging/gfx1150/torch-2.9.0%2Brocm7.10.0a20251105-cp312-cp312-win_amd64.whl"
pip install --no-cache-dir --no-deps "https://rocm.nightlies.amd.com/v2-staging/gfx1150/torchaudio-2.9.0%2Brocm7.10.0a20251105-cp312-cp312-win_amd64.whl"
```

### 3. 환경변수 설정

프로젝트 루트의 `.env` 파일에서 설정을 읽어옵니다:

```env
# Redis (필수)
REDIS_URL=redis://127.0.0.1:6379/0

# PostgreSQL (필수)
POSTGRES_DSN=postgresql://user:password@localhost:5432/dbname

# S3/MinIO (필수)
S3_ENDPOINT=http://127.0.0.1:9000
S3_ACCESS_KEY=torchdev
S3_SECRET_KEY=torchdev-secret
S3_BUCKET=asr-media

# LLM 설정 (선택)
LLM_BASE_URL=http://ai-llm:8000
LLM_MODEL_NAME=gemma-4-E4B-it
```

## 실행

### Docker Compose (권장 · 운영 기본)

```bash
# 프로젝트 루트에서
docker compose up -d worker

# 로그 확인
docker compose logs -f worker
```

`docker-compose.yml`의 `worker` 서비스는 `asr-worker-unified` 컨테이너로
ASR/LLM/OCR/YouTube 큐를 모두 처리합니다.

### 직접 Celery 실행 (개발/디버그)

```bash
# 프로젝트 루트에서 (worker 패키지 접근용)
cd C:\timblo\torch-test

# 단일 워커가 모든 큐 처리
celery -A worker.celery_app worker --pool=solo --queues=asr,llm,ocr,youtube --hostname=worker-unified@%h
```

## 큐 구성

| 큐 | 워커 | 설명 |
|----|------|------|
| `asr` | worker-asr | ASR + 화자분리 처리 |
| `llm` | worker-llm | LLM 요약 처리 |
| `ocr` | worker-ocr | OCR/PDF 처리 |
| `youtube` | worker-asr | YouTube 다운로드 (ASR 워커에서 처리) |

## 점진적 마이그레이션

현재 워커 태스크들은 `backend/app/worker/processor.py` 등을 호출합니다.
완전 분리 후에는 `worker/pipelines/` 코드를 직접 사용하도록 전환 예정입니다.

```python
# 현재 (점진적 마이그레이션)
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
from app.worker.processor import process_asr_task

# 완전 분리 후
from worker.pipelines.asr import process_asr
```

## API 서버에서 태스크 호출

Backend API 서버는 `backend/app/task_client.py`를 통해 워커 태스크를 호출합니다:

```python
from app.task_client import send_asr_task, send_llm_task, send_ocr_task

# ASR 태스크 전송
result = send_asr_task(content_id=123, file_path="/path/to/audio.wav")

# LLM 요약 태스크 전송
result = send_llm_task(content_id=123, text="요약할 텍스트")
```

## 트러블슈팅

### 워커가 태스크를 받지 못함

1. Redis 연결 확인: `redis-cli ping`
2. Celery 브로커 URL 확인: `.env`의 `REDIS_URL`
3. 워커 로그 확인: `docker compose logs -f worker`

### GPU가 인식되지 않음

1. PyTorch ROCm 버전 확인:
   ```python
   import torch
   print(torch.cuda.is_available())  # True여야 함
   print(torch.version.hip)          # ROCm 버전
   ```

2. ROCm 드라이버 확인

### ModuleNotFoundError: No module named 'worker'

프로젝트 루트에서 실행해야 합니다:
```bash
cd C:\timblo\torch-test
celery -A worker.celery_app worker ...
```
