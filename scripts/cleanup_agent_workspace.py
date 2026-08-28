"""清理 data/agent_workspace 中超过 14 天的会话目录（D09 开放问题5）。"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "data" / "agent_workspace"
RETENTION_SECONDS = 14 * 24 * 3600


def main():
    if not WORKSPACE.exists():
        print("workspace 不存在，无需清理")
        return
    now = time.time()
    cleaned = 0
    for p in WORKSPACE.iterdir():
        if not p.is_dir():
            continue
        mtime = p.stat().st_mtime
        age_days = (now - mtime) / 86400
        if age_days > 14:
            shutil.rmtree(p, ignore_errors=True)
            cleaned += 1
            print(f"清理 {p.name}（{age_days:.1f} 天）")
    print(f"清理完成：{cleaned} 个目录")


if __name__ == "__main__":
    main()
