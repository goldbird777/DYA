@echo off
chcp 65001 > nul
echo.
echo  ========================================
echo   DYA BOM 검증 시스템 시작 중...
echo  ========================================
echo.
cd /d "%~dp0"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
