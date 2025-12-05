# llama-server 메모리 최적화 가이드

## 메모리 사용량이 많은 이유

llama-server는 다음 메모리를 사용합니다:

1. **모델 가중치**: ~18GB (Qwen3-VL-30B Q4_K_M)
2. **KV 캐시**: 컨텍스트 길이에 비례 (가장 중요)
3. **임시 버퍼**: 추론 중 사용
4. **mmproj (Vision 모델)**: ~2GB

### KV 캐시 메모리 계산

```
KV 캐시 메모리 ≈ (컨텍스트 길이) × (모델 차원) × (레이어 수) × (데이터 타입 크기) × 2
```

예시:
- 컨텍스트 15000 토큰: ~7-10GB
- 컨텍스트 8192 토큰: ~4-5GB
- 컨텍스트 4096 토큰: ~2-3GB

## 메모리 최적화 설정

### 1. 컨텍스트 크기 줄이기 (가장 효과적)

`.env` 파일에서 설정:

```env
# 메모리 절약 (권장)
LLAMA_SERVER_CTX_SIZE=8192
LLM_CONTEXT_LENGTH=8192

# 더 많은 메모리 절약 (컨텍스트가 짧아도 되는 경우)
LLAMA_SERVER_CTX_SIZE=4096
LLM_CONTEXT_LENGTH=4096

# 최대 컨텍스트 (메모리 충분한 경우)
LLAMA_SERVER_CTX_SIZE=15000
LLM_CONTEXT_LENGTH=15000
```

**효과**: 컨텍스트를 절반으로 줄이면 KV 캐시 메모리도 약 절반으로 감소

### 2. 배치 크기 설정

`.env` 파일에 추가:

```env
# 작은 배치 크기 (메모리 절약)
LLAMA_SERVER_BATCH_SIZE=256

# 기본값 (현재 설정)
LLAMA_SERVER_BATCH_SIZE=512

# 큰 배치 크기 (더 빠른 처리, 더 많은 메모리)
LLAMA_SERVER_BATCH_SIZE=1024
```

### 3. 동시 요청 수 제한

`ecosystem.config.js`에서 이미 `--parallel 1`로 설정되어 있습니다.

### 4. 전체 최적화 설정 예시

`.env` 파일:

```env
# 메모리 최적화 설정 (약 10-15GB 절약)
LLAMA_SERVER_CTX_SIZE=8192
LLAMA_SERVER_BATCH_SIZE=256
LLAMA_SERVER_THREADS=8
LLAMA_SERVER_GPU_LAYERS=99

LLM_CONTEXT_LENGTH=8192
```

**예상 메모리 사용량**:
- 모델 가중치: ~18GB
- KV 캐시: ~4-5GB (8192 토큰)
- 임시 버퍼: ~2-3GB
- mmproj: ~2GB
- **총: 약 26-28GB** (기존 30-35GB에서 감소)

## worker-llm 종료 후 프로세스가 남아있는 문제

### 문제

worker-llm이 종료된 후에도 `llama-server.exe`가 여전히 실행 중이고 메모리가 방출되지 않습니다.

### 해결 방법

#### 방법 1: stop.bat 사용 (권장)

```bash
stop.bat
```

이 스크립트는:
1. PM2로 모든 프로세스 중지
2. 남아있는 `llama-server.exe` 강제 종료
3. Docker Compose 서비스 중지

#### 방법 2: kill_llama_server.bat 사용

```bash
kill_llama_server.bat
```

llama-server만 강제 종료합니다.

#### 방법 3: 수동 종료

```bash
# PM2로 워커 종료
pm2 stop worker-llm

# 강제 종료
taskkill /F /IM llama-server.exe
```

### 프로세스 확인

```bash
# 실행 중인 llama-server.exe 확인
tasklist | findstr llama-server

# PM2 상태 확인
pm2 status
```

## 현재 설정 확인

`ecosystem.config.js`에서 현재 설정:

- **기본 컨텍스트 크기**: 8192 토큰 (기존 15000에서 감소)
- **배치 크기**: 512
- **동시 요청**: 1개
- **PM2 종료 타임아웃**: 5초

## 메모리 모니터링

### Windows 작업 관리자

1. 작업 관리자 열기 (Ctrl+Shift+Esc)
2. 성능 탭 → 메모리 확인
3. 세부 정보 탭 → `llama-server.exe` 메모리 사용량 확인

### PowerShell

```powershell
# llama-server.exe 메모리 사용량 확인
Get-Process llama-server -ErrorAction SilentlyContinue | Select-Object ProcessName, @{Name="Memory(MB)";Expression={[math]::Round($_.WorkingSet64/1MB,2)}}
```

## 권장 설정

### 메모리가 부족한 경우 (20GB 이하 사용 목표)

```env
LLAMA_SERVER_CTX_SIZE=4096
LLAMA_SERVER_BATCH_SIZE=256
LLM_CONTEXT_LENGTH=4096
```

**예상 메모리**: ~22-24GB

### 메모리가 충분한 경우

```env
LLAMA_SERVER_CTX_SIZE=8192
LLAMA_SERVER_BATCH_SIZE=512
LLM_CONTEXT_LENGTH=8192
```

**예상 메모리**: ~26-28GB

### 최대 성능 (메모리 충분)

```env
LLAMA_SERVER_CTX_SIZE=15000
LLAMA_SERVER_BATCH_SIZE=1024
LLM_CONTEXT_LENGTH=15000
```

**예상 메모리**: ~30-35GB

## 참고

- 컨텍스트 크기를 줄이면 긴 텍스트 요약 시 여러 청크로 나눠서 처리해야 할 수 있습니다
- 배치 크기를 줄이면 처리 속도가 약간 느려질 수 있지만 메모리는 절약됩니다
- worker-llm 종료 후에도 프로세스가 남아있다면 `kill_llama_server.bat`를 실행하세요

