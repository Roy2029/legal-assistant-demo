"""生产模式启动器：启动 FastAPI（serve 前端 dist）并打开浏览器。"""
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
    time.sleep(3)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


def main():
    threading.Thread(target=open_browser, daemon=True).start()
    import uvicorn
    uvicorn.run("server.main:app", host="127.0.0.1", port=PORT, reload=False)


if __name__ == "__main__":
    main()
