# Windows 네이티브 Ollama 설정 가이드 (Git Bash / WSL 환경)

## 1. Ollama 설치

### 방법 A: Windows에서 직접 설치 (권장)

**PowerShell 또는 명령 프롬프트(CMD)에서:**
```powershell
winget install Ollama.Ollama
```

**또는 공식 사이트에서 다운로드:**
1. [Ollama 공식 사이트](https://ollama.com/download) 방문
2. Windows용 설치 파일 다운로드 및 실행
3. 설치 완료 후 자동으로 서비스가 시작됩니다

> ⚠️ **참고**: Ollama는 Windows 네이티브 프로그램이므로, 설치 자체는 Windows 환경(PowerShell/CMD)에서 해야 합니다. 설치 후에는 Git Bash나 WSL에서도 사용할 수 있습니다.

## 2. 모델 Import

### Git Bash에서 실행

프로젝트 루트 디렉토리에서:

```bash
# Windows 경로로 ollama.exe 실행
/c/Users/$USER/AppData/Local/Programs/Ollama/ollama.exe create gpt-oss-20b -f Modelfile
```

또는 PATH에 Ollama가 등록되어 있다면:

```bash
ollama create gpt-oss-20b -f Modelfile
```

### WSL에서 실행

WSL에서 Windows 실행 파일을 호출:

```bash
# WSL에서 Windows 경로 접근
/c/Users/$USER/AppData/Local/Programs/Ollama/ollama.exe create gpt-oss-20b -f Modelfile
```

또는 `ollama.exe`를 PATH에 추가:

```bash
# ~/.bashrc 또는 ~/.zshrc에 추가
export PATH="/mnt/c/Users/$USER/AppData/Local/Programs/Ollama:$PATH"

# 그 후 사용
ollama.exe create gpt-oss-20b -f Modelfile
```

> **참고**: `Modelfile`은 프로젝트 루트에 있으며, `./models/gpt-oss-20b-Q4_K_S.gguf` 파일을 참조합니다.  
> Windows 경로는 Git Bash에서는 `/c/...`, WSL에서는 `/mnt/c/...` 형식으로 접근합니다.

## 3. 모델 확인

### Git Bash에서:
```bash
ollama list
# 또는 전체 경로로
/c/Users/$USER/AppData/Local/Programs/Ollama/ollama.exe list
```

### WSL에서:
```bash
ollama.exe list
# 또는 전체 경로로
/mnt/c/Users/$USER/AppData/Local/Programs/Ollama/ollama.exe list
```

`gpt-oss-20b`가 목록에 나타나면 성공입니다.

## 4. Ollama 서비스 확인

Ollama는 Windows 서비스로 자동 실행됩니다. 

### 서비스 상태 확인

**Git Bash에서:**
```bash
# Windows 서비스 확인 (PowerShell 명령 실행)
powershell.exe -Command "Get-Service Ollama"
```

**WSL에서:**
```bash
# Windows 서비스 확인
powershell.exe -Command "Get-Service Ollama"
```

### 수동 시작 (필요한 경우)

**Git Bash에서:**
```bash
/c/Users/$USER/AppData/Local/Programs/Ollama/ollama.exe serve
```

**WSL에서:**
```bash
/mnt/c/Users/$USER/AppData/Local/Programs/Ollama/ollama.exe serve
```

또는 Windows 서비스 관리자에서 "Ollama" 서비스를 확인/시작할 수 있습니다.

## 5. API 테스트

### Git Bash / WSL에서:
```bash
curl http://localhost:11434/api/tags
```

또는 브라우저에서 `http://localhost:11434/api/tags` 접속

### 간편한 사용을 위한 alias 설정

**Git Bash (`~/.bashrc` 또는 `~/.bash_profile`):**
```bash
# Ollama alias
alias ollama='/c/Users/$USER/AppData/Local/Programs/Ollama/ollama.exe'
```

**WSL (`~/.bashrc` 또는 `~/.zshrc`):**
```bash
# Ollama alias
alias ollama='/mnt/c/Users/$USER/AppData/Local/Programs/Ollama/ollama.exe'
# 또는 PATH에 추가
export PATH="/mnt/c/Users/$USER/AppData/Local/Programs/Ollama:$PATH"
```

설정 후:
```bash
source ~/.bashrc  # 또는 ~/.zshrc
ollama list       # 이제 간단하게 사용 가능
```

## 6. .env 파일 설정

프로젝트 루트의 `.env` 파일에 다음 설정 추가:

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL_NAME=gpt-oss-20b
```

## 7. GPU 가속 확인

Ollama는 자동으로 Vulkan을 사용합니다. GPU 사용 여부는 Ollama 로그에서 확인할 수 있습니다.

## 문제 해결

### Ollama가 시작되지 않는 경우
- Windows 서비스 관리자에서 "Ollama" 서비스 상태 확인
- 방화벽에서 포트 11434 허용 확인

### 모델을 찾을 수 없는 경우
- `models/gpt-oss-20b-Q4_K_S.gguf` 파일이 존재하는지 확인
- `Modelfile`의 경로가 올바른지 확인 (프로젝트 루트 기준 상대 경로)

### GPU가 사용되지 않는 경우
- AMD 드라이버가 최신인지 확인
- Windows에서 Vulkan이 지원되는지 확인

