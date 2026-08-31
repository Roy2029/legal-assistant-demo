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

echo 正在安装 Chromium 浏览器内核（案例检索备用后端，约 2-3 分钟）...
.venv\Scripts\python.exe -m playwright install chromium

echo.
echo 依赖安装完成。双击 start_legal_assistant.bat 启动。
echo 注意：案例检索依赖外部 WenshuMCP 项目（默认 C:\Users\Roy\WorkBuddy\WenshuMCP，
echo 可用环境变量 WENSHU_MCP_PROJECT 指定位置），其环境需自行安装 chromium。
pause
