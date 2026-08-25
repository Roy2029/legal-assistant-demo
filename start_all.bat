@echo off
cd /d "%~dp0"

echo ============================================
echo   Legal Assistant Demo - Start All
echo   Backend:  http://127.0.0.1:8000
echo   Frontend: http://127.0.0.1:5173
echo ============================================
echo.

echo [1/3] Starting backend uvicorn ...
start "" /B .venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000

echo [2/3] Starting frontend vite ...
cd frontend
start "" /B npm run dev
cd ..

echo [3/3] Waiting 8 seconds and opening browser ...
ping -n 9 127.0.0.1 >nul
start http://127.0.0.1:5173

echo.
echo Services are running in the background of this window.
echo Run stop_all.bat to stop them. Press any key to close this window.
echo.
pause
