@echo off
REM LM Studio 시작 스크립트 (VBScript 래퍼)

echo [LM Studio] 시작 중...

REM VBScript를 사용하여 최소화 상태로 실행
cscript //nologo "%~dp0start_lmstudio.vbs"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [LM Studio] 최소화 상태로 시작 완료
    echo.
    echo ⚠ 중요: LM Studio에서 다음을 확인하세요:
    echo   1. 작업 표시줄에서 LM Studio 복원
    echo   2. 모델을 로드하세요 (예: gpt-oss-20b^)
    echo   3. Local Server를 시작하세요 (포트 1234^)
    echo.
    timeout /t 3 >nul
    exit /b 0
) else (
    echo.
    echo [LM Studio] 시작 실패
    echo [LM Studio] 수동으로 실행하세요.
    pause
    exit /b 1
)

