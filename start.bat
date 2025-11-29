@echo off
REM 전체 서비스 시작 스크립트 (Docker Compose + PM2)

echo ========================================
echo 서비스 시작 중...
echo ========================================

REM Docker Compose 서비스 시작
echo [1/2] Docker Compose 서비스 시작 중...
docker compose up -d

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker Compose 시작 실패
    pause
    exit /b 1
)

echo [1/2] Docker Compose 서비스 시작 완료
echo.

REM PM2 워커 시작
echo [2/2] Celery 워커 시작 중...
pm2 start ecosystem.config.js

if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] PM2 워커 시작 실패 (이미 실행 중일 수 있음)
    pm2 restart celery-worker --update-env
)

echo [2/2] Celery 워커 시작 완료
echo.

echo ========================================
echo 모든 서비스가 시작되었습니다!
echo ========================================
echo.
echo 서비스 상태 확인:
echo   - Docker: docker compose ps
echo   - PM2:    pm2 status
echo.
echo 로그 확인:
echo   - Docker: docker compose logs -f
echo   - PM2:    pm2 logs
echo.

pause

