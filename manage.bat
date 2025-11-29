@echo off
REM 서비스 관리 TUI 실행

cd /d C:\timblo\torch-test\backend
C:\Users\jg\.local\bin\poetry.exe run python ..\service_manager.py

pause

