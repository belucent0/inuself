# Torch ASR Backend

FastAPI 기반 ASR(자동 음성 인식) 및 화자 분리 백엔드 서버

## 기능

- 오디오 파일 업로드 및 처리 큐잉
- Whisper 기반 ASR 전사
- PyAnnote 기반 화자 분리
- Redis 큐를 통한 비동기 처리
- PostgreSQL 데이터 저장

## 개발 환경 설정

```bash
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload
```

## 워커 실행

```bash
poetry run python -m app.worker.run_worker
```

