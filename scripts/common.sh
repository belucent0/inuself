#!/usr/bin/env bash
# 공통 유틸리티 함수
# Poetry 경로 찾기 및 설정

# Poetry 경로 찾기 함수 (Windows 및 Unix 지원)
find_poetry_path() {
    # 환경 변수로 지정된 경로 우선 확인
    if [ -n "${POETRY_PATH:-}" ] && [ -f "${POETRY_PATH}" ]; then
        echo "${POETRY_PATH}"
        return 0
    fi
    
    # 이미 PATH에 있는지 확인
    if command -v poetry > /dev/null 2>&1; then
        command -v poetry
        return 0
    fi
    
    # 사용자명 추출 (HOME에서 또는 USER 환경 변수에서)
    local home_user=""
    if [ -n "${HOME:-}" ]; then
        home_user=$(echo "$HOME" | sed 's|^/c/Users/||' | sed 's|^/c/||')
    elif [ -n "${USER:-}" ]; then
        home_user="$USER"
    elif [ -n "${USERNAME:-}" ]; then
        home_user="$USERNAME"
    fi
    
    # Windows 환경인 경우
    if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "${MSYSTEM:-}" ]]; then
        local possible_paths=(
            "$HOME/.local/bin/poetry"                    # pipx로 설치된 경우 (권장)
            "$HOME/.local/bin/poetry.exe"                # pipx로 설치된 경우 (Windows)
            "$HOME/AppData/Roaming/Python/Scripts/poetry" # pip install --user
            "$HOME/AppData/Roaming/Python/Scripts/poetry.exe"
            "$HOME/.poetry/bin/poetry"                    # 공식 설치 스크립트 (구버전)
            "/c/ProgramData/chocolatey/bin/poetry.exe"    # Chocolatey
        )
        
        # 사용자명이 있으면 해당 경로도 추가
        if [ -n "$home_user" ]; then
            possible_paths+=(
                "/c/Users/${home_user}/.local/bin/poetry"
                "/c/Users/${home_user}/.local/bin/poetry.exe"
                "/c/Users/${home_user}/AppData/Roaming/Python/Scripts/poetry"
                "/c/Users/${home_user}/AppData/Roaming/Python/Scripts/poetry.exe"
                "/c/Users/${home_user}/AppData/Local/Programs/Python/Scripts/poetry.exe"
            )
        fi
        
        for path in "${possible_paths[@]}"; do
            if [ -f "$path" ] || [ -x "$path" ] 2>/dev/null; then
                echo "$path"
                return 0
            fi
        done
    else
        # Unix 환경
        local possible_paths=(
            "$HOME/.local/bin/poetry"
            "$HOME/.poetry/bin/poetry"
            "/usr/local/bin/poetry"
            "/usr/bin/poetry"
        )
        
        for path in "${possible_paths[@]}"; do
            if [ -f "$path" ] && [ -x "$path" ]; then
                echo "$path"
                return 0
            fi
        done
    fi
    
    return 1
}

# Poetry 설정 함수 (경로 찾기 및 PATH 추가)
# 사용법: setup_poetry "prefix" (예: "[dev]" 또는 "[Celery]")
setup_poetry() {
    local prefix="${1:-}"
    
    if command -v poetry > /dev/null 2>&1; then
        if [ -n "$prefix" ]; then
            echo "${prefix} ✓ Poetry가 이미 PATH에 있습니다."
        fi
        return 0
    fi
    
    if [ -n "$prefix" ]; then
        echo "${prefix} Poetry 경로 자동 탐색 중..."
    fi
    
    local poetry_cmd
    poetry_cmd=$(find_poetry_path 2>/dev/null || echo "")
    
    if [ -n "$poetry_cmd" ]; then
        if [ -n "$prefix" ]; then
            echo "${prefix} ✓ Poetry 발견: $poetry_cmd"
        fi
        # PATH에 추가
        local poetry_dir
        poetry_dir=$(dirname "$poetry_cmd")
        export PATH="$poetry_dir:$PATH"
        # 함수로 래핑하여 사용 (더 안정적)
        poetry() {
            "$poetry_cmd" "$@"
        }
        export -f poetry
        return 0
    fi
    
    # 일반적인 Poetry 설치 위치를 PATH에 추가 (기존 로직 유지)
    export PATH="$HOME/.local/bin:$PATH"
    export PATH="$HOME/AppData/Roaming/Python/Scripts:$PATH"
    
    # 사용자명이 있으면 해당 경로도 추가
    local home_user=""
    if [ -n "${HOME:-}" ]; then
        home_user=$(echo "$HOME" | sed 's|^/c/Users/||' | sed 's|^/c/||')
    elif [ -n "${USER:-}" ]; then
        home_user="$USER"
    elif [ -n "${USERNAME:-}" ]; then
        home_user="$USERNAME"
    fi
    
    if [ -n "$home_user" ]; then
        export PATH="/c/Users/${home_user}/.local/bin:$PATH"
        export PATH="/c/Users/${home_user}/AppData/Roaming/Python/Scripts:$PATH"
    fi
    
    if ! command -v poetry > /dev/null 2>&1; then
        if [ -n "$prefix" ]; then
            echo "${prefix} ⚠ Poetry를 찾을 수 없습니다."
            echo "${prefix}   다음 중 하나를 시도하세요:"
            echo "${prefix}   1. POETRY_PATH 환경 변수 설정: export POETRY_PATH=/path/to/poetry"
            echo "${prefix}   2. Poetry 설치: curl -sSL https://install.python-poetry.org | python3 -"
            echo "${prefix}   3. Windows 시스템 PATH에 Poetry 경로 추가"
        fi
        return 1
    else
        if [ -n "$prefix" ]; then
            echo "${prefix} ✓ Poetry를 PATH에서 발견했습니다."
        fi
        return 0
    fi
}



