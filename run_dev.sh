# MinIO 상태 확인 함수
wait_for_minio() {
  local health_endpoint="${S3_HEALTH_ENDPOINT:-http://127.0.0.1:9000/minio/health/live}"
  echo "[dev] MinIO 상태 확인: ${health_endpoint}"
  for _ in {1..10}; do
    if curl -fsS --max-time 2 "${health_endpoint}" >/dev/null 2>&1; then
      echo "[dev] ✓ MinIO 응답 확인"
      return 0
    fi
    sleep 1
  done
  echo "[dev] ⚠ MinIO endpoint에 접속할 수 없습니다. docker compose up -d minio 를 확인하세요."
  return 1
}

#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
CLIENT_DIR="${ROOT_DIR}/client"

# Windows Git Bash에서 실행 중인지 확인
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "${MSYSTEM:-}" ]]; then
  # Windows Git Bash에서 직접 실행 (ROCm은 Windows에서 동작)
  echo "[dev] Windows 환경 감지. Windows에서 직접 실행합니다..."
  
  # Windows에서 Poetry 경로 확인 (일반적인 설치 위치)
  if ! command -v poetry > /dev/null 2>&1; then
    # 일반적인 Poetry 설치 위치 추가
    export PATH="$HOME/.local/bin:$PATH"
    export PATH="$HOME/AppData/Roaming/Python/Scripts:$PATH"
    # 현재 사용자 홈 디렉토리 기반 경로도 추가
    if [ -n "${USER:-}" ]; then
      export PATH="/c/Users/$USER/.local/bin:$PATH"
      export PATH="/c/Users/$USER/AppData/Roaming/Python/Scripts:$PATH"
    fi
    USER_HOME=$(echo "$HOME" | sed 's|/c/|C:/|' | sed 's|/|\\|g' | sed 's|\\|/|g')
    export PATH="$USER_HOME/AppData/Roaming/Python/Scripts:$PATH"
  fi
  
  # Node.js 경로 확인
  if ! command -v npm > /dev/null 2>&1; then
    export PATH="/c/Program Files/nodejs:$PATH"
  fi
  
  # rocm_env 비활성화 (Poetry 가상환경 사용을 위해)
  if [ -n "${VIRTUAL_ENV:-}" ]; then
    OLD_VENV="$VIRTUAL_ENV"
    echo "[dev] 기존 가상환경 비활성화: $OLD_VENV"
    # PATH에서 가상환경 경로 제거
    export PATH=$(echo "$PATH" | tr ':' '\n' | grep -v "$OLD_VENV" | tr '\n' ':' | sed 's/:$//')
    unset VIRTUAL_ENV
  fi
  
  # 의존성 설치 확인 (한 번만 설치)
  echo "[dev] 백엔드 의존성 확인 중..."
  if [ ! -f "${BACKEND_DIR}/poetry.lock" ] || ! (cd "${BACKEND_DIR}" && poetry check > /dev/null 2>&1); then
    echo "[dev] Poetry 의존성 설치 중..."
    (cd "${BACKEND_DIR}" && poetry install --no-interaction --no-root) || (cd "${BACKEND_DIR}" && poetry install --no-interaction)
  else
    echo "[dev] ✓ 백엔드 의존성 이미 설치됨"
  fi
  
  echo "[dev] 클라이언트 의존성 확인 중..."
  if [ ! -d "${CLIENT_DIR}/node_modules" ] || [ ! -f "${CLIENT_DIR}/package-lock.json" ]; then
    echo "[dev] npm 패키지 설치 중..."
    (cd "${CLIENT_DIR}" && npm install)
  else
    echo "[dev] ✓ 클라이언트 의존성 이미 설치됨"
  fi

  wait_for_minio || true
  
  API_PID=""
  WORKER_PID=""
  CLIENT_PID=""
  
  cleanup() {
    echo ""
    echo "[dev] 정리 중..."
    for pid in "$CLIENT_PID" "$WORKER_PID" "$API_PID"; do
      if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}" 2>/dev/null || true
        wait "${pid}" 2>/dev/null || true
      fi
    done
  }
  
  trap cleanup EXIT
  trap 'exit 1' INT
  
  echo "[dev] FastAPI 서버 시작..."
  (cd "${BACKEND_DIR}" && poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) &
  API_PID=$!
  
  # FastAPI 서버가 시작될 때까지 대기 (최대 10초)
  echo "[dev] FastAPI 서버 시작 대기 중..."
  for wait_count in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
      echo "[dev] ✓ FastAPI 서버가 시작되었습니다."
      break
    fi
    if [ "$wait_count" = "20" ]; then
      echo "[dev] ⚠ FastAPI 서버 시작 확인 실패."
    fi
    sleep 0.5
  done
  
  echo "[dev] RQ 워커는 FastAPI에서 자동으로 시작됩니다 (개발 환경)."
  
  echo "[dev] Next.js 클라이언트 시작..."
  (cd "${CLIENT_DIR}" && npm run dev) &
  CLIENT_PID=$!
  
  echo ""
  echo "[dev] ========================================"
  echo "[dev] 모든 프로세스가 실행 중입니다."
  echo "[dev] - API: http://localhost:8000"
  echo "[dev] - API Docs: http://localhost:8000/docs"
  echo "[dev] - Client: http://localhost:3000"
  echo "[dev] ========================================"
  echo "[dev] 종료하려면 Ctrl+C 를 누르세요."
  echo ""
  
  wait
  exit $?
fi

# WSL/Linux 환경에서 직접 실행
export PATH="$HOME/.local/bin:$PATH"
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# 의존성 설치 확인 (한 번만 설치)
echo "[dev] 백엔드 의존성 확인 중..."
if [ ! -f "${BACKEND_DIR}/poetry.lock" ] || ! (cd "${BACKEND_DIR}" && poetry check > /dev/null 2>&1); then
  echo "[dev] Poetry 의존성 설치 중..."
  (cd "${BACKEND_DIR}" && poetry install --no-interaction --no-root) || (cd "${BACKEND_DIR}" && poetry install --no-interaction)
else
  echo "[dev] ✓ 백엔드 의존성 이미 설치됨"
fi

echo "[dev] 클라이언트 의존성 확인 중..."
if [ ! -d "${CLIENT_DIR}/node_modules" ] || [ ! -f "${CLIENT_DIR}/package-lock.json" ]; then
  echo "[dev] npm 패키지 설치 중..."
  (cd "${CLIENT_DIR}" && npm install)
else
  echo "[dev] ✓ 클라이언트 의존성 이미 설치됨"
fi

wait_for_minio || true

API_PID=""
WORKER_PID=""
CLIENT_PID=""

cleanup() {
  echo ""
  echo "[dev] 정리 중..."
  for pid in "$CLIENT_PID" "$WORKER_PID" "$API_PID"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  done
}

trap cleanup EXIT
trap 'exit 1' INT

echo "[dev] FastAPI 서버 시작..."
(cd "${BACKEND_DIR}" && poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) > /tmp/fastapi.log 2>&1 &
API_PID=$!

# FastAPI 서버가 시작될 때까지 대기 (최대 10초)
echo "[dev] FastAPI 서버 시작 대기 중..."
for wait_count in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "[dev] ✓ FastAPI 서버가 시작되었습니다."
    break
  fi
  if [ "$wait_count" = "20" ]; then
    echo "[dev] ⚠ FastAPI 서버 시작 확인 실패. 로그를 확인하세요:"
    echo "[dev]   tail -n 20 /tmp/fastapi.log"
  fi
  sleep 0.5
done

echo "[dev] RQ 워커 시작..."
(cd "${BACKEND_DIR}" && poetry run python -m app.worker.run_worker) &
WORKER_PID=$!
sleep 1

echo "[dev] Next.js 클라이언트 시작..."
(cd "${CLIENT_DIR}" && npm run dev) &
CLIENT_PID=$!

echo ""
echo "[dev] ========================================"
echo "[dev] 모든 프로세스가 실행 중입니다."
echo "[dev] - API: http://localhost:8000"
echo "[dev] - API Docs: http://localhost:8000/docs"
echo "[dev] - Client: http://localhost:3000"
echo "[dev] ========================================"
echo "[dev] 종료하려면 Ctrl+C 를 누르세요."
echo ""

wait

