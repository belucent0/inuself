#!/usr/bin/env bash

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



set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="${ROOT_DIR}/scripts"

# 공통 함수 로드
if [ -f "${SCRIPTS_DIR}/common.sh" ]; then
    source "${SCRIPTS_DIR}/common.sh"
else
    echo "[dev] ⚠ 공통 스크립트를 찾을 수 없습니다: ${SCRIPTS_DIR}/common.sh"
    exit 1
fi
BACKEND_DIR="${ROOT_DIR}/backend"
CLIENT_DIR="${ROOT_DIR}/client"
DEFAULT_LLM_MODEL="models/gpt-oss-20b.gguf"
ENV_FILE="${ROOT_DIR}/.env"

resolve_model_path() {
  local raw_path="$1"
  if [[ "$raw_path" =~ ^[A-Za-z]:[\\/].* ]] || [[ "$raw_path" == /* ]]; then
    # 절대 경로 (Windows 드라이브 또는 POSIX)
    echo "$raw_path"
  else
    echo "${ROOT_DIR}/${raw_path}"
  fi
}

check_llm_model() {
  # .env에서 환경 변수를 로드 (있을 경우)
  if [[ -f "$ENV_FILE" ]]; then
    echo "[dev] .env 로드: ${ENV_FILE}"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi

  # LLM_PROVIDER 확인
  local llm_provider="${LLM_PROVIDER:-ollama}"
  if [[ "$llm_provider" == "ollama" ]]; then
    echo "[dev] LLM Provider: Ollama"
    echo "[dev]   Ollama 모델 확인은 LLM 워커 헬스체크에서 수행됩니다."
    return 0
  elif [[ "$llm_provider" == "lmstudio" ]]; then
    local lmstudio_base="${LMSTUDIO_BASE_URL:-http://localhost:1234}"
    echo "[dev] LLM Provider: LM Studio"
    echo "[dev]   Endpoint: ${lmstudio_base}"
    if curl -fsS --max-time 3 "${lmstudio_base%/}/v1/models" >/dev/null 2>&1; then
      echo "[dev] ✓ LM Studio API 응답 확인"
      return 0
    fi
    echo "[dev] ✗ LM Studio API 응답 없음. 서버(LM Studio)와 포트를 확인하세요."
    exit 1
  fi

  # llama_cpp를 사용하는 경우에만 로컬 모델 파일 확인
  local configured_path="${LLM_MODEL_PATH:-$DEFAULT_LLM_MODEL}"
  local resolved_path
  resolved_path="$(resolve_model_path "$configured_path")"

  echo "[dev] LLM 모델 확인 중: ${resolved_path}"
  if [[ -f "$resolved_path" ]]; then
    echo "[dev] ✓ LLM 모델이 존재합니다 (${configured_path})"
  else
    echo "[dev] ✗ LLM 모델을 찾을 수 없습니다 (${configured_path})"
    echo "[dev]    파일을 준비하거나 LLM_MODEL_PATH 환경변수를 올바르게 설정하세요."
    exit 1
  fi
}

# Windows Git Bash에서 실행 중인지 확인
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "${MSYSTEM:-}" ]]; then
  # Windows Git Bash에서 직접 실행 (ROCm은 Windows에서 동작)
  echo "[dev] Windows 환경 감지. Windows에서 직접 실행합니다..."
  
  # Windows에서 Poetry 경로 확인 및 찾기 (공통 함수 사용)
  # if ! setup_poetry "[dev]"; then
  #   exit 1
  # fi
  
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
    echo "[dev] Poetry lock 파일 업데이트 중..."
    (cd "${BACKEND_DIR}" && poetry lock > /dev/null 2>&1) || {
      echo "[dev] ⚠ Poetry lock 실패, 계속 진행합니다..."
    }
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
  check_llm_model
  
  # 워커 수 설정 (환경변수 또는 기본값)
  NUM_ASR_WORKERS="${NUM_ASR_WORKERS:-1}"
  NUM_LLM_WORKERS="${NUM_LLM_WORKERS:-1}"
  
  echo "[dev] 워커 설정:"
  echo "[dev]   ASR 워커 수: ${NUM_ASR_WORKERS}"
  echo "[dev]   LLM 워커 수: ${NUM_LLM_WORKERS}"
  
  API_PID=""
  WORKER_PIDS=()  # ASR 워커 PID 배열
  LLM_WORKER_PIDS=()  # LLM 워커 PID 배열
  CLIENT_PID=""
  
  cleanup() {
    echo ""
    echo "[dev] 정리 중..."
    
    # Windows에서 프로세스 종료 (taskkill 사용)
    if command -v taskkill >/dev/null 2>&1; then
      # Windows 환경: taskkill 사용
      # 클라이언트 종료
      if [[ -n "${CLIENT_PID}" ]]; then
        if tasklist /FI "PID eq ${CLIENT_PID}" 2>/dev/null | grep -q "${CLIENT_PID}"; then
          taskkill /F /PID "${CLIENT_PID}" >/dev/null 2>&1 || true
        fi
      fi
      
      # LLM 워커들 종료
      for pid in "${LLM_WORKER_PIDS[@]}"; do
        if [[ -n "${pid}" ]]; then
          if tasklist /FI "PID eq ${pid}" 2>/dev/null | grep -q "${pid}"; then
            taskkill /F /PID "${pid}" >/dev/null 2>&1 || true
          fi
        fi
      done
      
      # ASR 워커들 종료
      for pid in "${WORKER_PIDS[@]}"; do
        if [[ -n "${pid}" ]]; then
          if tasklist /FI "PID eq ${pid}" 2>/dev/null | grep -q "${pid}"; then
            taskkill /F /PID "${pid}" >/dev/null 2>&1 || true
          fi
        fi
      done
      
      # FastAPI 종료
      if [[ -n "${API_PID}" ]]; then
        if tasklist /FI "PID eq ${API_PID}" 2>/dev/null | grep -q "${API_PID}"; then
          taskkill /F /PID "${API_PID}" >/dev/null 2>&1 || true
        fi
      fi
      
      # 추가로 이름으로 찾아서 정리 (혹시 놓친 것들)
      taskkill /F /IM python.exe /FI "WINDOWTITLE eq *uvicorn*" >/dev/null 2>&1 || true
      taskkill /F /IM python.exe /FI "COMMANDLINE eq *run_worker*" >/dev/null 2>&1 || true
      taskkill /F /IM python.exe /FI "COMMANDLINE eq *run_llm_worker*" >/dev/null 2>&1 || true
    else
      # Unix 환경: kill 사용
      for pid in "$CLIENT_PID" "${LLM_WORKER_PIDS[@]}" "${WORKER_PIDS[@]}" "$API_PID"; do
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
          kill "${pid}" 2>/dev/null || true
          wait "${pid}" 2>/dev/null || true
        fi
      done
    fi
    
    # 잠시 대기 (프로세스 종료 시간 확보)
    sleep 1
    echo "[dev] 정리 완료"
  }
  
  trap cleanup EXIT
  trap 'exit 1' INT
  
  echo "[dev] FastAPI 서버 시작..."
  # FastAPI 출력을 파일로 리다이렉트하여 확인 가능하도록
  FASTAPI_LOG_FILE="${ROOT_DIR}/fastapi.log"
  # 기존 로그 파일이 있으면 백업
  if [ -f "${FASTAPI_LOG_FILE}" ]; then
    mv "${FASTAPI_LOG_FILE}" "${FASTAPI_LOG_FILE}.old" 2>/dev/null || true
  fi
  (cd "${BACKEND_DIR}" && poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 > "${FASTAPI_LOG_FILE}" 2>&1) &
  API_PID=$!
  echo "[dev] FastAPI PID: ${API_PID}, 로그: ${FASTAPI_LOG_FILE}"
  
  # FastAPI 서버가 시작될 때까지 대기 (최대 10초)
  echo "[dev] FastAPI 서버 시작 대기 중..."
  FASTAPI_STARTED=false
  for wait_count in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    # 프로세스가 살아있는지 먼저 확인
    if ! kill -0 "${API_PID}" 2>/dev/null; then
      echo "[dev] ⚠ FastAPI 서버 프로세스가 종료되었습니다."
      echo "[dev]   FastAPI 로그 확인: tail -n 50 ${FASTAPI_LOG_FILE}"
      break
    fi
    
    # 헬스체크 시도
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
      echo "[dev] ✓ FastAPI 서버가 시작되었습니다."
      FASTAPI_STARTED=true
      break
    fi
    
    # 진행 상황 표시 (2초마다)
    if [ $((wait_count % 4)) -eq 0 ]; then
      echo "[dev]   FastAPI 서버 시작 대기 중... ($((wait_count / 2))초 경과, PID: ${API_PID})"
    fi
    
    sleep 0.5
  done
  
  # 최종 확인
  if [ "$FASTAPI_STARTED" = false ]; then
    if kill -0 "${API_PID}" 2>/dev/null; then
      echo "[dev] ⚠ FastAPI 서버가 실행 중이지만 헬스체크에 응답하지 않습니다."
      echo "[dev]   프로세스는 실행 중이므로 계속 진행합니다."
      echo "[dev]   로그 확인: tail -n 50 ${FASTAPI_LOG_FILE}"
    else
      echo "[dev] ⚠ FastAPI 서버 시작 실패."
      echo "[dev]   로그 확인: tail -n 50 ${FASTAPI_LOG_FILE}"
    fi
  fi
  
  # Task Queue 타입 확인 (.env에서)
  TASK_QUEUE_TYPE="${TASK_QUEUE_TYPE:-rq}"
  if [ -f "${ENV_FILE}" ]; then
    # .env 파일에서 TASK_QUEUE_TYPE 읽기
    set -a
    source "${ENV_FILE}" 2>/dev/null || true
    set +a
    TASK_QUEUE_TYPE="${TASK_QUEUE_TYPE:-rq}"
  fi
  
  if [ "${TASK_QUEUE_TYPE}" = "celery" ]; then
    # Celery 워커 시작
    echo "[dev] Celery 워커 시작 (ASR: ${NUM_ASR_WORKERS}개, LLM: ${NUM_LLM_WORKERS}개)..."
    TOTAL_CELERY_WORKERS=$((NUM_ASR_WORKERS + NUM_LLM_WORKERS))
    CELERY_LOG_FILE="${ROOT_DIR}/celery_worker.log"
    if [ -f "${CELERY_LOG_FILE}" ]; then
      mv "${CELERY_LOG_FILE}" "${CELERY_LOG_FILE}.old" 2>/dev/null || true
    fi
    
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "${MSYSTEM:-}" ]]; then
      # Windows: solo pool
      (cd "${BACKEND_DIR}" && poetry run celery -A app.worker.celery_app worker \
        --pool=solo \
        --concurrency=${TOTAL_CELERY_WORKERS} \
        --loglevel=info \
        --max-tasks-per-child=100 \
        --task-events > "${CELERY_LOG_FILE}" 2>&1) &
    else
      # Unix: prefork pool
      (cd "${BACKEND_DIR}" && poetry run celery -A app.worker.celery_app worker \
        --pool=prefork \
        --concurrency=${TOTAL_CELERY_WORKERS} \
        --loglevel=info \
        --max-tasks-per-child=100 \
        --task-events > "${CELERY_LOG_FILE}" 2>&1) &
    fi
    CELERY_PID=$!
    WORKER_PIDS+=("$CELERY_PID")
    echo "[dev]   Celery 워커 PID: ${CELERY_PID}, 로그: ${CELERY_LOG_FILE}"
    sleep 1
  else
    # RQ 워커 시작 (기본)
    echo "[dev] RQ ASR 워커 시작 (${NUM_ASR_WORKERS}개)..."
    for ((i=1; i<=NUM_ASR_WORKERS; i++)); do
      (cd "${BACKEND_DIR}" && poetry run python -m app.worker.run_worker) &
      worker_pid=$!
      WORKER_PIDS+=("$worker_pid")
      echo "[dev]   ASR 워커 #${i} PID: ${worker_pid}"
    done
    sleep 1
  fi

  # LLM 워커는 Celery를 사용할 때는 시작하지 않음 (Celery 워커가 처리)
  if [ "${TASK_QUEUE_TYPE}" != "celery" ]; then
    echo "[dev] RQ LLM 워커 시작 (${NUM_LLM_WORKERS}개, 헬스체크 포함)..."
    # LLM 워커 출력을 파일로 리다이렉트하여 확인 가능하도록
    for ((i=1; i<=NUM_LLM_WORKERS; i++)); do
      LLM_LOG_FILE="${ROOT_DIR}/llm_worker_${i}.log"
      # 기존 로그 파일이 있으면 백업
      if [ -f "${LLM_LOG_FILE}" ]; then
        mv "${LLM_LOG_FILE}" "${LLM_LOG_FILE}.old" 2>/dev/null || true
      fi
      (cd "${BACKEND_DIR}" && poetry run python -m app.worker.run_llm_worker > "${LLM_LOG_FILE}" 2>&1) &
      llm_worker_pid=$!
      LLM_WORKER_PIDS+=("$llm_worker_pid")
      echo "[dev]   LLM 워커 #${i} PID: ${llm_worker_pid}, 로그: ${LLM_LOG_FILE}"
    done
  else
    echo "[dev] LLM 워커는 Celery 워커가 처리합니다."
    LLM_WORKER_PIDS=()  # Celery 사용 시 LLM 워커 PID 없음
  fi
  
  # LLM 워커 헬스체크 (RQ 사용 시에만)
  LLM_WORKER_SUCCESS=false
  if [ "${TASK_QUEUE_TYPE}" != "celery" ]; then
    # 프로세스가 실제로 시작되었는지 확인
    sleep 0.5
    LLM_WORKERS_STARTED=0
    for pid in "${LLM_WORKER_PIDS[@]}"; do
      if kill -0 "${pid}" 2>/dev/null; then
        LLM_WORKERS_STARTED=$((LLM_WORKERS_STARTED + 1))
      fi
    done
    
    if [ "$LLM_WORKERS_STARTED" -eq 0 ]; then
      echo "[dev] ⚠ 모든 LLM 워커가 즉시 종료되었습니다."
      echo "[dev]   LLM 워커 로그 확인:"
      if [ -f "${ROOT_DIR}/llm_worker_1.log" ]; then
        if command -v tail >/dev/null 2>&1; then
          tail -n 50 "${ROOT_DIR}/llm_worker_1.log" 2>/dev/null || cat "${ROOT_DIR}/llm_worker_1.log" 2>/dev/null | head -n 50
        else
          head -n 50 "${ROOT_DIR}/llm_worker_1.log" 2>/dev/null || echo "[dev]   로그 파일을 읽을 수 없습니다."
        fi
      else
        echo "[dev]   로그 파일을 찾을 수 없습니다."
      fi
      echo "[dev]   LLM 기능 없이 계속 진행합니다."
      LLM_WORKER_PIDS=()
    else
      echo "[dev] ${LLM_WORKERS_STARTED}개의 LLM 워커가 시작되었습니다."
    fi
    
    # LLM 워커 헬스체크 대기 (최대 10초, 짧게 설정하여 프론트엔드 시작 지연 최소화)
    if [ "${#LLM_WORKER_PIDS[@]}" -gt 0 ]; then
    echo "[dev] LLM 워커 헬스체크 대기 중..."
    for llm_wait in {1..20}; do
      # 프로세스가 살아있는지 확인
      alive_count=0
      for pid in "${LLM_WORKER_PIDS[@]}"; do
        if kill -0 "${pid}" 2>/dev/null; then
          alive_count=$((alive_count + 1))
        fi
      done
      
      if [ "$alive_count" -eq 0 ]; then
        echo "[dev] ⚠ 모든 LLM 워커가 헬스체크 실패로 종료되었습니다."
        echo "[dev]   LLM 워커 로그 확인:"
        if [ -f "${ROOT_DIR}/llm_worker_1.log" ]; then
          if command -v tail >/dev/null 2>&1; then
            tail -n 30 "${ROOT_DIR}/llm_worker_1.log" 2>/dev/null || echo "[dev]   로그 파일을 읽을 수 없습니다."
          else
            echo "[dev]   로그 파일: ${ROOT_DIR}/llm_worker_1.log"
          fi
        else
          echo "[dev]   로그 파일을 찾을 수 없습니다."
        fi
        echo "[dev]   LLM 기능 없이 계속 진행합니다. (ASR 워커와 클라이언트는 정상 작동)"
        LLM_WORKER_SUCCESS=false
        break
      fi
      
      # 로그 파일이 존재하고 프로세스가 살아있으면 성공으로 간주
      if [ -f "${ROOT_DIR}/llm_worker_1.log" ] && [ "$alive_count" -gt 0 ]; then
        # 로그 파일 크기가 증가했는지 확인 (워커가 출력을 하고 있다는 증거)
        if [ "$llm_wait" -ge 5 ]; then
          echo "[dev] ✓ ${alive_count}개의 LLM 워커가 실행 중입니다."
          LLM_WORKER_SUCCESS=true
          break
        fi
      fi
      
      # 진행 상황 표시 (2초마다)
      if [ $((llm_wait % 4)) -eq 0 ]; then
        echo "[dev]   LLM 워커 헬스체크 대기 중... ($((llm_wait / 2))초 경과, ${alive_count}개 실행 중)"
      fi
      
      sleep 0.5
      
      # 타임아웃 처리 (10초)
      if [ "$llm_wait" -ge 20 ]; then
        echo "[dev] ⚠ LLM 워커 헬스체크 타임아웃 (10초)"
        # 프로세스가 살아있으면 성공으로 간주
        if [ "$alive_count" -gt 0 ]; then
          echo "[dev]   ${alive_count}개의 LLM 워커 프로세스는 실행 중입니다. 계속 진행합니다."
          LLM_WORKER_SUCCESS=true
        else
          echo "[dev]   LLM 워커가 실행 중이지만 헬스체크 완료를 확인하지 못했습니다."
          echo "[dev]   LLM 워커 로그 확인:"
          if [ -f "${ROOT_DIR}/llm_worker_1.log" ]; then
            if command -v tail >/dev/null 2>&1; then
              tail -n 30 "${ROOT_DIR}/llm_worker_1.log" 2>/dev/null || echo "[dev]   로그 파일을 읽을 수 없습니다."
            else
              echo "[dev]   로그 파일: ${ROOT_DIR}/llm_worker_1.log"
            fi
          else
            echo "[dev]   로그 파일을 찾을 수 없습니다."
          fi
          LLM_WORKER_SUCCESS=false
        fi
        break
      fi
    done
  else
    echo "[dev] LLM 워커가 시작되지 않았습니다. 헬스체크를 건너뜁니다."
  fi
  else
    # Celery 사용 시
    echo "[dev] ✓ Celery 워커가 실행 중입니다 (ASR + LLM 모두 처리)."
    LLM_WORKER_SUCCESS=true
  fi
  
  # LLM 워커 상태 출력 (RQ 사용 시에만)
  if [ "${TASK_QUEUE_TYPE}" != "celery" ]; then
    if [ "$LLM_WORKER_SUCCESS" = true ]; then
      echo "[dev] ✓ LLM 워커가 정상적으로 시작되었습니다."
    else
      echo "[dev] ⚠ LLM 워커가 시작되지 않았거나 헬스체크를 통과하지 못했습니다."
      echo "[dev]   LLM 기능 없이 계속 진행합니다."
    fi
  fi
  
  echo "[dev] Next.js 클라이언트 시작..."
  (cd "${CLIENT_DIR}" && npm run dev) &
  CLIENT_PID=$!
  echo "[dev] 클라이언트 PID: ${CLIENT_PID}"
  
  # 클라이언트 시작 대기 (최대 10초)
  echo "[dev] 클라이언트 시작 대기 중..."
  CLIENT_STARTED=false
  for client_wait in {1..20}; do
    if ! kill -0 "${CLIENT_PID}" 2>/dev/null; then
      echo "[dev] ⚠ 클라이언트가 시작되지 않았습니다."
      break
    fi
    # 포트 3000이 열렸는지 확인
    if command -v netstat >/dev/null 2>&1; then
      if netstat -an 2>/dev/null | grep -q ":3000.*LISTEN" || netstat -an 2>/dev/null | grep -q "3000.*LISTENING"; then
        CLIENT_STARTED=true
        break
      fi
    elif command -v ss >/dev/null 2>&1; then
      if ss -an 2>/dev/null | grep -q ":3000.*LISTEN"; then
        CLIENT_STARTED=true
        break
      fi
    else
      # netstat/ss가 없으면 일정 시간 후 진행
      if [ "$client_wait" -ge 10 ]; then
        CLIENT_STARTED=true
        break
      fi
    fi
    sleep 0.5
  done
  
  echo ""
  echo "[dev] ========================================"
  echo "[dev] 모든 프로세스가 실행 중입니다."
  echo "[dev] - API: http://localhost:8000"
  echo "[dev] - API Docs: http://localhost:8000/docs"
  echo "[dev] - Client: http://localhost:3000"
  if [ "$LLM_WORKER_SUCCESS" = true ]; then
    echo "[dev] - LLM Worker: 실행 중"
  else
    echo "[dev] - LLM Worker: 비활성화됨"
  fi
  echo "[dev] ========================================"
  echo "[dev] 종료하려면 Ctrl+C 를 누르세요."
  echo ""
  
  wait
  exit $?
fi

# WSL/Linux 환경에서 직접 실행
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# Poetry 환경 확인 (공통 함수 사용)
if ! setup_poetry "[dev]"; then
    exit 1
fi

# 의존성 설치 확인 (한 번만 설치)
echo "[dev] 백엔드 의존성 확인 중..."
if [ ! -f "${BACKEND_DIR}/poetry.lock" ] || ! (cd "${BACKEND_DIR}" && poetry check > /dev/null 2>&1); then
  echo "[dev] Poetry lock 파일 업데이트 중..."
  (cd "${BACKEND_DIR}" && poetry lock > /dev/null 2>&1) || {
    echo "[dev] ⚠ Poetry lock 실패, 계속 진행합니다..."
  }
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
check_llm_model

# Task Queue 타입 확인 (.env에서)
TASK_QUEUE_TYPE="${TASK_QUEUE_TYPE:-rq}"
if [ -f "${ENV_FILE}" ]; then
  # .env 파일에서 TASK_QUEUE_TYPE 읽기
  set -a
  source "${ENV_FILE}" 2>/dev/null || true
  set +a
  TASK_QUEUE_TYPE="${TASK_QUEUE_TYPE:-rq}"
fi

API_PID=""
WORKER_PID=""
LLM_WORKER_PID=""
CELERY_PID=""
CLIENT_PID=""

cleanup() {
  echo ""
  echo "[dev] 정리 중..."
  for pid in "$CLIENT_PID" "$LLM_WORKER_PID" "$WORKER_PID" "$CELERY_PID" "$API_PID"; do
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

if [ "${TASK_QUEUE_TYPE}" = "celery" ]; then
  # Celery 워커 시작
  echo "[dev] Celery 워커 시작..."
  CELERY_LOG_FILE="/tmp/celery_worker.log"
  (cd "${BACKEND_DIR}" && poetry run celery -A app.worker.celery_app worker \
    --pool=prefork \
    --concurrency=2 \
    --loglevel=info \
    --max-tasks-per-child=100 \
    --task-events > "${CELERY_LOG_FILE}" 2>&1) &
  CELERY_PID=$!
  echo "[dev] Celery 워커 PID: ${CELERY_PID}, 로그: ${CELERY_LOG_FILE}"
  sleep 1
else
  # RQ 워커 시작 (기본)
  echo "[dev] RQ 워커 시작..."
  (cd "${BACKEND_DIR}" && poetry run python -m app.worker.run_worker) &
  WORKER_PID=$!
  sleep 1

  echo "[dev] LLM 워커 시작 (헬스체크 포함)..."
  (cd "${BACKEND_DIR}" && poetry run python -m app.worker.run_llm_worker) &
  LLM_WORKER_PID=$!

  # LLM 워커 헬스체크 대기 (최대 30초)
  echo "[dev] LLM 워커 헬스체크 대기 중..."
  for llm_wait in {1..60}; do
    if ! kill -0 "${LLM_WORKER_PID}" 2>/dev/null; then
      echo "[dev] ✗ LLM 워커가 예기치 않게 종료되었습니다."
      exit 1
    fi
    sleep 0.5
    if [ "$llm_wait" -ge 60 ]; then
      echo "[dev] ✓ LLM 워커가 실행 중입니다."
      break
    fi
  done
fi

echo "[dev] Next.js 클라이언트 시작..."
(cd "${CLIENT_DIR}" && npm run dev) &
CLIENT_PID=$!
echo "[dev] 클라이언트 PID: ${CLIENT_PID}"

# 클라이언트 시작 대기 (최대 10초)
echo "[dev] 클라이언트 시작 대기 중..."
CLIENT_STARTED=false
for client_wait in {1..20}; do
  if ! kill -0 "${CLIENT_PID}" 2>/dev/null; then
    echo "[dev] ⚠ 클라이언트가 시작되지 않았습니다."
    break
  fi
  # 포트 3000이 열렸는지 확인 (Windows에서는 netstat 사용)
  if command -v netstat >/dev/null 2>&1; then
    if netstat -an 2>/dev/null | grep -q ":3000.*LISTEN" || netstat -an 2>/dev/null | grep -q "3000.*LISTENING"; then
      CLIENT_STARTED=true
      break
    fi
  elif command -v ss >/dev/null 2>&1; then
    if ss -an 2>/dev/null | grep -q ":3000.*LISTEN"; then
      CLIENT_STARTED=true
      break
    fi
  else
    # netstat/ss가 없으면 일정 시간 후 진행
    if [ "$client_wait" -ge 10 ]; then
      CLIENT_STARTED=true
      break
    fi
  fi
  sleep 0.5
done

echo ""
echo "[dev] ========================================"
echo "[dev] 모든 프로세스가 실행 중입니다."
echo "[dev] - API: http://localhost:8000"
echo "[dev] - API Docs: http://localhost:8000/docs"
echo "[dev] - Client: http://localhost:3000"
if [ "${TASK_QUEUE_TYPE}" = "celery" ]; then
  echo "[dev] - Celery Worker: 실행 중 (ASR + LLM 모두 처리)"
else
  echo "[dev] - RQ Worker: 실행 중"
  if [ -n "${LLM_WORKER_PID:-}" ] && kill -0 "${LLM_WORKER_PID}" 2>/dev/null; then
    echo "[dev] - LLM Worker: 실행 중"
  else
    echo "[dev] - LLM Worker: 비활성화됨"
  fi
fi
echo "[dev] ========================================"
echo "[dev] 종료하려면 Ctrl+C 를 누르세요."
echo ""

wait

