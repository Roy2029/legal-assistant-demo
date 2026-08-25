@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ============================================
echo  法律助手 Demo 一键启动
echo  后端: http://127.0.0.1:8000
echo  前端: http://127.0.0.1:5173
echo ============================================

start "legal-backend" .venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000
cd frontend
start "legal-frontend" cmd /c "npm run dev"
timeout /t 5 /nobreak >nul
start http://127.0.0.1:5173
echo 已启动。关闭请分别关闭两个窗口，或运行 scripts\stop_all.bat
