@echo off
REM 전체 서비스 중지 스크립트 (Docker Compose + PM2)

echo ========================================
echo 서비스 중지 중...
echo ========================================

REM PM2 워커 중지
echo [1/2] Celery 워커 중지 중...
pm2 stop celery-worker

if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] PM2 워커 중지 실패 (이미 중지되었을 수 있음)
)

echo [1/2] Celery 워커 중지 완료
echo.

REM Docker Compose 서비스 중지
echo [2/2] Docker Compose 서비스 중지 중...
docker compose down

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker Compose 중지 실패
    pause
    exit /b 1
)

echo [2/2] Docker Compose 서비스 중지 완료
echo.

echo ========================================
echo 모든 서비스가 중지되었습니다!
echo ========================================

pause






