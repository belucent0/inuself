#!/bin/bash
# Ollama alias 설정 스크립트
# Git Bash 또는 WSL에서 실행

# 현재 사용자 확인
if [ -n "$USER" ]; then
    USERNAME="$USER"
elif [ -n "$USERNAME" ]; then
    USERNAME="$USERNAME"
else
    USERNAME=$(whoami)
fi

# Ollama 경로 확인
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "${MSYSTEM:-}" ]]; then
    # Git Bash
    OLLAMA_PATH="/c/Users/$USERNAME/AppData/Local/Programs/Ollama/ollama.exe"
    SHELL_RC="$HOME/.bashrc"
    if [ ! -f "$SHELL_RC" ]; then
        SHELL_RC="$HOME/.bash_profile"
    fi
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # WSL
    OLLAMA_PATH="/mnt/c/Users/$USERNAME/AppData/Local/Programs/Ollama/ollama.exe"
    SHELL_RC="$HOME/.bashrc"
    if [ -f "$HOME/.zshrc" ]; then
        SHELL_RC="$HOME/.zshrc"
    fi
else
    echo "지원하지 않는 환경입니다."
    exit 1
fi

# Ollama가 설치되어 있는지 확인
if [ ! -f "$OLLAMA_PATH" ]; then
    echo "❌ Ollama를 찾을 수 없습니다: $OLLAMA_PATH"
    echo "먼저 Windows에서 Ollama를 설치하세요:"
    echo "  winget install Ollama.Ollama"
    echo "  또는 https://ollama.com/download"
    exit 1
fi

echo "✓ Ollama 경로 확인: $OLLAMA_PATH"

# alias 추가
if grep -q "alias ollama=" "$SHELL_RC" 2>/dev/null; then
    echo "⚠️  이미 alias가 설정되어 있습니다."
    echo "기존 alias를 업데이트하시겠습니까? (y/n)"
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        # 기존 alias 제거
        if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "${MSYSTEM:-}" ]]; then
            sed -i "s|alias ollama=.*|alias ollama='$OLLAMA_PATH'|" "$SHELL_RC"
        else
            sed -i "s|alias ollama=.*|alias ollama='$OLLAMA_PATH'|" "$SHELL_RC"
        fi
        echo "✓ alias 업데이트 완료"
    else
        echo "취소되었습니다."
        exit 0
    fi
else
    # 새 alias 추가
    echo "" >> "$SHELL_RC"
    echo "# Ollama alias" >> "$SHELL_RC"
    echo "alias ollama='$OLLAMA_PATH'" >> "$SHELL_RC"
    echo "✓ alias 추가 완료"
fi

echo ""
echo "설정이 완료되었습니다!"
echo ""
echo "다음 명령으로 설정을 적용하세요:"
echo "  source $SHELL_RC"
echo ""
echo "또는 새 터미널을 열면 자동으로 적용됩니다."
echo ""
echo "이제 'ollama' 명령을 사용할 수 있습니다:"
echo "  ollama list"
echo "  ollama create gpt-oss-20b -f Modelfile"

