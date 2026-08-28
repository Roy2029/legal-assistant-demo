"""托管式重建法律库：写入状态文件，启动 rebuild_index_from_intermediate.py。"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "data" / "logs" / "rebuild.status.json"


def write_status(**kw):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")


def main():
    write_status({"running": True, "pid": __import__("os").getpid(), "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "rebuild_index_from_intermediate.py")],
            cwd=str(ROOT),
        )
        write_status({"running": False, "ok": True, "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    except Exception as e:
        write_status({"running": False, "ok": False, "error": str(e), "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})


if __name__ == "__main__":
    main()
