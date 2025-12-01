@echo off
REM 전체 서비스 재시작 스크립트 (Docker Compose + PM2)

echo ========================================
echo 서비스 재시작 중...
echo ========================================

REM PM2 워커 재시작
echo [1/2] Celery 워커 재시작 중...
pm2 restart celery-worker --update-env

if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] PM2 워커 재시작 실패 (시작되지 않았을 수 있음)
    echo PM2 워커를 새로 시작합니다...
    pm2 start ecosystem.config.js
)

echo [1/2] Celery 워커 재시작 완료
echo.

REM Docker Compose 서비스 재시작
echo [2/2] Docker Compose 서비스 재시작 중...
docker compose restart

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker Compose 재시작 실패
    pause
    exit /b 1
)

echo [2/2] Docker Compose 서비스 재시작 완료
echo.

echo ========================================
echo 모든 서비스가 재시작되었습니다!
echo ========================================

pause






