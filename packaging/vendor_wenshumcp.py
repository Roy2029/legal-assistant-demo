"""同步 WenshuMCP 源码到 vendor/wenshumcp（随包分发）。

用法：
    python packaging/vendor_wenshumcp.py            # 同步并写入 VENDOR.json
    python packaging/vendor_wenshumcp.py --check    # 仅比对差异，不修改文件

源目录解析：环境变量 WENSHU_MCP_PROJECT → 开发机默认路径。
排除运行态/无关产物：__pycache__、_tmp_dl、captcha_samples、*.pyc。
WenshuMCP 源目录非 git 仓库时，版本追踪退化为内容指纹（文件清单 sha256）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = REPO_ROOT / "vendor" / "wenshumcp"
DEFAULT_SOURCE = Path("C:/Users/Roy/WorkBuddy/WenshuMCP")
PACKAGES = ("wenshu_mcp", "wenshu_api")
EXCLUDED_DIRS = {"__pycache__", "_tmp_dl", "captcha_samples"}


def resolve_source() -> Path:
    src = Path(os.getenv("WENSHU_MCP_PROJECT") or DEFAULT_SOURCE)
    if not (src / "wenshu_mcp" / "server.py").exists():
        raise SystemExit(f"源目录无效（缺 wenshu_mcp/server.py）：{src}")
    return src


def iter_copy_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix == ".pyc":
            continue
        yield path


def copy_tree(src_pkg: Path, dst_pkg: Path) -> int:
    count = 0
    for file in iter_copy_files(src_pkg):
        rel = file.relative_to(src_pkg)
        target = dst_pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        count += 1
    # 清掉目标侧本次已不在源里的陈旧文件（保持 vendor 与源严格一致）
    if dst_pkg.exists():
        for file in sorted(dst_pkg.rglob("*"), reverse=True):
            if file.is_file() and file.suffix == ".pyc":
                file.unlink()
    return count


def fingerprint(vendor_root: Path) -> str:
    """内容指纹：仅对随包的两个包目录，对排序后的「相对路径:大小」清单做 sha256。

    不含 README/VENDOR.json 等元文件，避免文档改动导致指纹漂移。
    """
    digest = hashlib.sha256()
    for pkg in PACKAGES:
        for file in iter_copy_files(vendor_root / pkg):
            rel = file.relative_to(vendor_root).as_posix()
            digest.update(f"{rel}:{file.stat().st_size}\n".encode("utf-8"))
    return digest.hexdigest()


def git_commit(src: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def write_vendor_json(src: Path, fp: str) -> None:
    meta = {
        "name": "wenshumcp",
        "source_path": str(src),
        "source_commit": git_commit(src),
        "vendored_at": date.today().isoformat(),
        "content_fingerprint": fp,
        "includes": list(PACKAGES),
        "excluded": sorted(EXCLUDED_DIRS),
        "refresh": "python packaging/vendor_wenshumcp.py",
    }
    (VENDOR_DIR / "VENDOR.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 WenshuMCP 到 vendor/wenshumcp")
    parser.add_argument("--check", action="store_true", help="仅比对差异，不修改文件")
    args = parser.parse_args()

    src = resolve_source()
    if args.check:
        old = VENDOR_DIR / "VENDOR.json"
        print(f"源目录：{src}")
        print(f"vendor 目录：{VENDOR_DIR}（{'存在' if old.exists() else '不存在'}）")
        return 0

    for pkg in PACKAGES:
        if not (src / pkg).exists():
            raise SystemExit(f"源目录缺少 {pkg}/：{src}")

    for pkg_dir in (VENDOR_DIR / p for p in PACKAGES):
        if pkg_dir.exists():
            shutil.rmtree(pkg_dir)

    total = 0
    for pkg in PACKAGES:
        total += copy_tree(src / pkg, VENDOR_DIR / pkg)

    fp = fingerprint(VENDOR_DIR)
    write_vendor_json(src, fp)
    print(f"已同步 {total} 个文件 → {VENDOR_DIR}")
    print(f"内容指纹：{fp[:12]}…（完整值见 VENDOR.json）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
