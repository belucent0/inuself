# Celery 마이그레이션 테스트 가이드

## 준비

### 1. Celery 의존성 설치
```bash
cd backend
poetry install
```

### 2. `.env` 파일 설정
```bash
# RQ 사용 (기본)
TASK_QUEUE_TYPE=rq

# 또는 Celery 사용 (테스트)
TASK_QUEUE_TYPE=celery
```

## Celery 테스트

### 1단계: Celery로 전환
`.env` 파일에서:
```
TASK_QUEUE_TYPE=celery
```

### 2단계: Celery 워커 시작
```bash
cd backend
./run_celery_worker.sh
```

또는 직접:
```bash
cd backend
poetry run celery -A app.worker.celery_app worker --pool=solo --loglevel=info
```

### 3단계: FastAPI 서버 시작
```bash
./run_dev.sh  # 또는 개별 시작
```

### 4단계: 파일 업로드 및 테스트
1. http://localhost:3000 접속
2. 파일 업로드
3. 워커 로그 확인: `[Celery ASR] Starting task...`
4. 작업 완료 확인

## RQ로 롤백

문제가 발생하면 `.env`:
```
TASK_QUEUE_TYPE=rq
```

서버 재시작하면 RQ로 복귀됩니다.

## 비교 테스트

| 항목 | RQ | Celery |
|------|-----|---------|
| Windows 안정성 | ❌ 문제 많음 | ✅ 안정적 |
| 워커 재시작 | 수동 | 자동 (max-tasks-per-child) |
| 작업 추적 | 제한적 | 강력함 |
| 재시도 로직 | 수동 구현 | 내장 |
| 모니터링 | 제한적 | Flower 사용 가능 |

## 모니터링 (선택사항)

Celery Flower 대시보드:
```bash
poetry add flower
poetry run celery -A app.worker.celery_app flower
# http://localhost:5555
```

## 성공 기준

✅ 파일 업로드 시 Celery 워커가 작업 처리
✅ ASR 처리 완료 후 LLM 작업 자동 시작
✅ 100개 작업 후 워커 자동 재시작
✅ 워커 크래시 시 작업 자동 재시도
✅ Windows에서 안정적 동작





