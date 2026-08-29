@echo off
cd /d "%~dp0"

echo ============================================
echo   Legal Assistant Demo - Start
echo   Browser: http://127.0.0.1:8000
echo ============================================
echo.

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 未找到，请先安装 Python 3.12 并执行 install_deps.bat
    pause
    exit /b 1
  )
  set "PY=python"
)

echo Starting backend ...
start "" /B %PY% scripts\start_prod.py

ping -n 6 127.0.0.1 >nul
start http://127.0.0.1:8000

echo.
echo Backend is running. Close this window to keep it running? No, this window can be closed after startup.
pause
