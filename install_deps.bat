@echo off
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo 未找到 Python。请先到 https://www.python.org/downloads/ 安装 Python 3.12，
  echo 安装时勾选 "Add Python to PATH"。
  pause
  exit /b 1
)

echo 正在创建虚拟环境 .venv ...
python -m venv .venv

echo 正在安装依赖（首次安装约需几分钟）...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo.
echo 依赖安装完成。双击 start_legal_assistant.bat 启动。
pause
