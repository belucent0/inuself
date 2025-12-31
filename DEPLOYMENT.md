# 프로세스 상시 구동 가이드

이 문서는 프론트엔드(Next.js), 백엔드(FastAPI), Celery 워커를 상시 구동하고 자동 재시작을 설정하는 방법을 설명합니다.

## 아키텍처 개요

```
┌─────────────────────────────────────────────┐
│           Windows 호스트                      │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  WSL2 (Docker Containers)            │   │
│  │                                      │   │
│  │  ┌──────────┐  ┌──────────┐        │   │
│  │  │ Frontend │  │ Backend  │        │   │
│  │  │  :3000   │  │  :8000   │        │   │
│  │  └──────────┘  └──────────┘        │   │
│  │                                      │   │
│  │  ┌──────────┐  ┌──────────┐        │   │
│  │  │ Redis    │  │ Postgres │        │   │
│  │  │  :6379   │  │  :5432   │        │   │
│  │  └──────────┘  └──────────┘        │   │
│  │                                      │   │
│  │  ┌──────────┐                       │   │
│  │  │  MinIO   │                       │   │
│  │  │  :9000   │                       │   │
│  │  └──────────┘                       │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  PM2 (Windows Native)                │   │
│  │                                      │   │
│  │  ┌────────────────────┐             │   │
│  │  │  Celery Worker     │             │   │
│  │  │  (GPU 가속)         │             │   │
│  │  └────────────────────┘             │   │
│  └──────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```

## 프로세스 구성

### 1. WSL Docker 컨테이너 (프론트엔드 + 백엔드 + 인프라)
- **Frontend**: Next.js (포트 3000)
- **Backend**: FastAPI (포트 8000)
- **Redis**: 작업 큐용 (포트 6379)
- **Postgres**: 데이터베이스 (포트 5432)
- **MinIO**: 오브젝트 스토리지 (포트 9000, 9001)

### 2. Windows PM2 (Celery 워커)
- **worker-asr**: ASR 작업 처리 (화자 분리 포함, GPU 가속)
- **worker-llm**: LLM 요약 작업 처리
- **worker-ocr**: OCR 문서 처리 (기본 모드: Qwen3-VL API, Docling 모드: Docling)
- 각 워커는 독립적으로 실행 및 스케일링 가능

### 3. Windows Native (LM Studio)
- **LM Studio**: LLM 추론 서버 (포트 1234)
- GPU 가속 필요
- 수동 시작 필요 (자동 시작 불가)

---

## 초기 설정

### 1. 환경 변수 설정

프로젝트 루트에 `.env` 파일을 생성/확인:

```bash
# 데이터베이스
DATABASE_URL=postgresql://user:pass@localhost:5432/asr

# Redis
REDIS_URL=redis://localhost:6379/0

# 작업 큐 설정
TASK_QUEUE_TYPE=celery

# 워커 개수
NUM_ASR_WORKERS=1
NUM_LLM_WORKERS=1

# MinIO (S3)
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=torchdev
S3_SECRET_KEY=torchdev-secret
S3_BUCKET_NAME=asr-media
# LLM Provider (LM Studio 사용 중)
LLM_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://localhost:1234
```

### 2. 로그 디렉토리 생성

PM2용 로그 디렉토리를 생성합니다:

```bash
mkdir logs
```

### 3. LM Studio 설정

LM Studio는 Windows 재시작 시 자동으로 시작되지 않으므로 수동으로 관리해야 합니다.

#### LM Studio 시작 방법

**방법 1: 서비스 관리자 사용 (권장)**
```bash
manage.bat
# 메뉴에서 9번 선택: LM Studio 시작
```

**방법 2: 스크립트 사용**
```bash
start_lmstudio.bat
```

**방법 3: 수동 시작**
1. LM Studio 애플리케이션 실행
2. 모델 로드 (예: gpt-oss-20b)
3. Local Server 시작 (포트 1234)

#### LM Studio 상태 확인

```bash
# Python 스크립트로 확인
python check_lmstudio.py

# 또는 curl로 확인
curl http://localhost:1234/v1/models
```

**중요**: Celery 워커가 LLM 요약 작업을 처리하려면 LM Studio가 실행 중이어야 합니다. LM Studio가 중지되면 요약 작업이 실패하고 재시도됩니다.

---

## WSL Docker 서비스 시작

### 1. WSL에서 Docker Compose 실행

```bash
# WSL 터미널에서
cd /mnt/c/timblo/torch-test

# 모든 서비스 시작 (백그라운드)
docker-compose up -d

# 특정 서비스만 시작
docker-compose up -d postgres redis minio backend frontend
```

### 2. 서비스 상태 확인

```bash
# 모든 컨테이너 상태 확인
docker-compose ps

# 로그 확인
docker-compose logs -f backend
docker-compose logs -f frontend

# 헬스체크 확인
docker-compose ps | grep healthy
```

### 3. 서비스 중지/재시작

```bash
# 모든 서비스 중지
docker-compose down

# 특정 서비스 재시작
docker-compose restart backend
docker-compose restart frontend

# 볼륨까지 삭제 (데이터 초기화)
docker-compose down -v
```

---

## 전체 서비스 시작 (권장)

### Windows에서 시작 스크립트 사용

프로젝트 루트에 있는 배치 파일을 사용하여 모든 서비스를 한 번에 시작/중지할 수 있습니다:

```bash
# 모든 서비스 시작 (Docker Compose + PM2)
start.bat

# 모든 서비스 중지
stop.bat

# 모든 서비스 재시작
restart.bat
```

**시작 스크립트 동작:**
1. Docker Compose 서비스 시작 (Backend, Frontend, Redis, Postgres, MinIO)
2. PM2 Celery 워커 시작

**수동 시작 방법:**
```bash
# 1. Docker Compose 시작
docker compose up -d

# 2. PM2 워커 시작
pm2 start ecosystem.config.js
```

---

## Windows PM2 워커 시작

### 1. PM2 설치 (처음 한 번만)

```bash
# Git Bash 또는 PowerShell에서
npm install -g pm2
npm install -g pm2-windows-startup
```

### 2. Celery 워커 시작

```bash
# 프로젝트 루트에서
cd C:\timblo\torch-test

# PM2로 워커 시작
pm2 start ecosystem.config.js

# 상태 확인
pm2 status

# 워커별 로그 확인
pm2 logs worker-asr
pm2 logs worker-llm
pm2 logs worker-ocr

# 모든 워커 로그 확인
pm2 logs --lines 100
```

### 3. Windows 부팅 시 자동 시작 설정

```bash
# 현재 PM2 프로세스 목록 저장
pm2 save

# Windows 부팅 시 자동 시작 설정
pm2-startup install

# 또는 수동으로 (PowerShell 관리자 권한)
pm2 startup
# 출력된 명령어를 복사해서 실행
```

### 4. 워커 관리 명령어

```bash
# 모든 워커 재시작
pm2 restart all

# 특정 워커 재시작
pm2 restart worker-asr
pm2 restart worker-llm
pm2 restart worker-ocr

# 워커 중지
pm2 stop worker-asr
pm2 stop worker-llm
pm2 stop worker-ocr

# 워커 삭제
pm2 delete worker-asr
pm2 delete worker-llm
pm2 delete worker-ocr

# 모든 PM2 프로세스 중지
pm2 stop all

# PM2 모니터링 대시보드
pm2 monit
```

---

## 전체 시스템 시작 순서

### 올바른 시작 순서:

1. **WSL Docker 인프라 시작** (Redis, Postgres, MinIO)
   ```bash
   docker-compose up -d postgres redis minio
   ```

2. **백엔드 시작** (DB 마이그레이션 포함)
   ```bash
   docker-compose up -d backend
   ```

3. **프론트엔드 시작**
   ```bash
   docker-compose up -d frontend
   ```

4. **Windows PM2 Celery 워커 시작**
   ```bash
   pm2 start ecosystem.config.js
   ```

### 빠른 시작 (한 번에):

```bash
# WSL에서
docker-compose up -d

# Windows에서
pm2 start ecosystem.config.js
```

---

## 로그 확인

### Docker 컨테이너 로그

```bash
# 백엔드 로그
docker logs -f asr-backend

# 프론트엔드 로그
docker logs -f asr-frontend

# 모든 서비스 로그
docker-compose logs -f

# 최근 100줄만 보기
docker logs --tail 100 asr-backend
```

### PM2 워커 로그

```bash
# 모든 워커 실시간 로그
pm2 logs

# 특정 워커 로그
pm2 logs worker-asr
pm2 logs worker-llm
pm2 logs worker-ocr

# 로그 파일 직접 확인
tail -f C:\timblo\torch-test\logs\worker-asr-out.log
tail -f C:\timblo\torch-test\logs\worker-llm-out.log
tail -f C:\timblo\torch-test\logs\worker-ocr-out.log

# 로그 초기화
pm2 flush
```

---

## 헬스체크 및 모니터링

### 서비스 엔드포인트 확인

```bash
# 백엔드 헬스체크
curl http://localhost:8000/health

# 백엔드 API 문서
# 브라우저: http://localhost:8000/docs

# 프론트엔드
# 브라우저: http://localhost:3000

# MinIO Console
# 브라우저: http://localhost:9001

# Redis Insight
# 브라우저: http://localhost:5540
```

### PM2 모니터링

```bash
# 간단한 상태 확인
pm2 status

# 실시간 대시보드
pm2 monit

# 메트릭 확인
pm2 describe worker-asr
pm2 describe worker-llm
pm2 describe worker-ocr

# PM2 Plus (옵션 - 고급 모니터링)
pm2 link <secret_key> <public_key>
```

---

## 트러블슈팅

### 1. Docker 컨테이너가 시작되지 않을 때

```bash
# 컨테이너 상태 확인
docker-compose ps

# 특정 컨테이너 로그 확인
docker logs asr-backend

# 컨테이너 재빌드
docker-compose build --no-cache backend
docker-compose up -d backend

# 포트 충돌 확인
netstat -an | grep :8000
netstat -an | grep :3000
```

### 2. Celery 워커가 작업을 처리하지 않을 때

```bash
# PM2 로그 확인
pm2 logs --lines 200

# 특정 워커 로그 확인
pm2 logs worker-asr --lines 200
pm2 logs worker-llm --lines 200
pm2 logs worker-ocr --lines 200

# Redis 연결 확인 (Git Bash)
redis-cli ping

# Celery 작업 큐 확인
redis-cli
> KEYS celery*
> LLEN celery

# 워커 재시작
pm2 restart all
```

### 3. GPU 관련 문제

Celery 워커는 Windows에서 직접 실행되어 GPU를 사용합니다:

```bash
# ROCm 환경 확인 (ASR)
python -c "import torch; print(torch.cuda.is_available())"

# Vulkan 확인 (whisper.cpp)
vulkaninfo | grep "deviceName"
```

### 4. 네트워크 연결 문제

Windows Celery 워커가 WSL Docker의 Redis/Postgres에 접근할 수 없을 때:

```bash
# WSL IP 확인
wsl hostname -I

# .env 파일에서 localhost 대신 WSL IP 사용
# REDIS_URL=redis://172.x.x.x:6379/0
# DATABASE_URL=postgresql://user:pass@172.x.x.x:5432/asr
```

### 5. 데이터베이스 마이그레이션 실패

```bash
# 백엔드 컨테이너에서 수동 마이그레이션
docker exec -it asr-backend bash
alembic upgrade head
exit
```

### 6. 프론트엔드 빌드 실패

```bash
# 로컬에서 빌드 테스트
cd client
npm run build

# 캐시 없이 재빌드
docker-compose build --no-cache frontend
docker-compose up -d frontend
```

---

## 성능 최적화

### Celery 워커 Concurrency 조정

GPU 성능에 따라 `ecosystem.config.js`에서 concurrency 조정:

```javascript
// 고성능 GPU
args: '... --concurrency=4 ...'  // ASR 2개 + LLM 2개

// 저성능 GPU
args: '... --concurrency=2 ...'  // ASR 1개 + LLM 1개 (기본)
```

### Docker 리소스 제한

`docker-compose.yml`에서 리소스 제한:

```yaml
backend:
  # ... 기존 설정 ...
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G
```

---

## 백업 및 복구

### 데이터베이스 백업

```bash
# Postgres 백업
docker exec asr-postgres pg_dump -U user asr > backup_$(date +%Y%m%d).sql

# 복구
docker exec -i asr-postgres psql -U user asr < backup_20251129.sql
```

### MinIO 데이터 백업

```bash
# MinIO 볼륨 백업
docker run --rm -v torch-test_minio-data:/data -v $(pwd):/backup alpine tar czf /backup/minio_backup.tar.gz /data
```

---

## 업데이트 및 배포

### 코드 업데이트

```bash
# 1. Git pull
git pull origin main

# 2. Docker 이미지 재빌드
docker-compose build backend frontend

# 3. 서비스 재시작 (무중단)
docker-compose up -d backend frontend

# 4. PM2 워커 재시작
pm2 restart all
```

### 의존성 업데이트

```bash
# 백엔드 (Poetry)
cd backend
poetry update
docker-compose build backend
docker-compose up -d backend

# 프론트엔드 (npm)
cd client
npm update
docker-compose build frontend
docker-compose up -d frontend
```

---

## 서비스 URL 요약

| 서비스 | URL | 설명 |
|--------|-----|------|
| Frontend | http://localhost:3000 | Next.js 웹 인터페이스 |
| Backend API | http://localhost:8000 | FastAPI 서버 |
| API Docs | http://localhost:8000/docs | Swagger UI |
| MinIO Console | http://localhost:9001 | 오브젝트 스토리지 관리 |
| Redis Insight | http://localhost:5540 | Redis 모니터링 |

---

## Windows 재부팅 후 체크리스트

1. ✅ WSL2가 자동 시작되었는지 확인
2. ✅ Docker Desktop이 실행 중인지 확인
3. ✅ `docker-compose ps`로 모든 컨테이너 확인
4. ✅ `pm2 status`로 모든 워커(worker-asr, worker-llm, worker-ocr) 확인
5. ✅ 각 서비스 URL 접속 테스트
6. ✅ LM Studio가 실행 중인지 확인 (LLM 사용 시)

---

## 추가 리소스

- [Docker Compose 공식 문서](https://docs.docker.com/compose/)
- [PM2 공식 문서](https://pm2.keymetrics.io/)
- [Celery 공식 문서](https://docs.celeryq.dev/)
- [Next.js 배포 가이드](https://nextjs.org/docs/deployment)

---

## 문의 및 지원

문제가 발생하면 다음을 확인하세요:
1. 로그 파일 확인 (`docker logs`, `pm2 logs`)
2. 서비스 상태 확인 (`docker-compose ps`, `pm2 status`)
3. 네트워크 연결 확인 (ping, curl)
4. 디스크 공간 확인 (`df -h`)
5. 메모리 사용량 확인 (`free -m`)

