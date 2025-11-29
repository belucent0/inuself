# Ollama GPU 사용 확인 가이드

## 문제
Ollama가 모델을 시스템 RAM에 로드하고 GPU VRAM을 사용하지 않는 경우

## 확인 방법

### 1. Ollama 프로세스 확인
작업 관리자에서:
- `ollama.exe` 프로세스의 메모리 사용량 확인
- GPU 메모리 사용량 확인 (GPU 탭)

### 2. 모델 언로드 후 재로드
현재 코드는 `keep_alive=0`을 사용하여 매 요청마다 모델을 언로드합니다.
이렇게 하면 다음 요청에서 `num_gpu=-1` 옵션이 적용되어 GPU에 로드될 수 있습니다.

### 3. Ollama 서비스 재시작
Ollama 서비스를 재시작하면 모델이 처음부터 GPU에 로드될 수 있습니다:

```powershell
# PowerShell에서
Stop-Process -Name ollama -Force
# Ollama는 자동으로 재시작됩니다
```

### 4. 환경 변수 확인
Ollama가 GPU를 사용하려면:
- Vulkan 드라이버가 설치되어 있어야 함
- Windows에서 Vulkan이 지원되어야 함

### 5. 로그 확인
서버 시작 시 LLM 워커 로그에서:
- `load_duration`: 모델 로드 시간 (GPU에 로드되면 더 빠를 수 있음)
- `total_duration`: 전체 처리 시간
- `eval_duration`: 추론 시간

## 해결 방법

### 방법 1: keep_alive=0 사용 (현재 구현)
- 매 요청마다 모델을 언로드하고 재로드
- `num_gpu=-1` 옵션이 적용되어 GPU에 로드될 수 있음
- 단점: 매 요청마다 모델 로드 시간이 걸림

### 방법 2: Ollama 서비스 재시작
- Ollama 서비스를 재시작하여 모델을 처음부터 GPU에 로드
- 장점: 모델이 한 번만 로드되고 GPU에 유지됨
- 단점: 서비스 재시작 필요

### 방법 3: 환경 변수 설정
Ollama 서비스 시작 시 환경 변수 설정:
- `OLLAMA_NUM_GPU=1` (GPU 사용 강제)
- Windows 서비스 관리자에서 Ollama 서비스 환경 변수 설정

## 현재 구현
- `num_gpu=-1` 옵션 추가 ✓
- `keep_alive=0` 추가 ✓ (매 요청마다 재로드)
- 로그 추가 ✓ (load_duration, total_duration 확인)

## 다음 단계
1. 파일 업로드 후 요약 요청 시 GPU 메모리 사용량 확인
2. `load_duration`이 짧으면 GPU 사용 가능성 높음
3. GPU 메모리 사용량이 증가하면 성공







