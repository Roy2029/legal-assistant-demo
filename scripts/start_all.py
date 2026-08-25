"""一键启动：后端 uvicorn + 前端 vite dev + 打开浏览器（Ctrl+C 退出）。"""
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"


def open_browser():
    time.sleep(6)
    webbrowser.open("http://127.0.0.1:5173")


def main():
    print("启动后端 http://127.0.0.1:8000 ...")
    backend = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(ROOT),
    )
    print("启动前端 http://127.0.0.1:5173 ...")
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    frontend = subprocess.Popen([npm, "run", "dev"], cwd=str(FRONTEND))
    threading.Thread(target=open_browser, daemon=True).start()
    try:
        backend.wait()
        frontend.wait()
    except KeyboardInterrupt:
        print("\n停止服务...")
        frontend.terminate()
        backend.terminate()


if __name__ == "__main__":
    main()
