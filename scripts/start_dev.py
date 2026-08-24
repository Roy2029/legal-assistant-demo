"""开发模式启动器：启动 FastAPI 并打开浏览器（M0 W1 骨架）。"""
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PORT = 8000


def open_browser():
    time.sleep(2)
    webbrowser.open(f"http://127.0.0.1:{PORT}/health")


def main():
    threading.Thread(target=open_browser, daemon=True).start()
    import uvicorn

    uvicorn.run("server.main:app", host="127.0.0.1", port=PORT, reload=False)


if __name__ == "__main__":
    main()
