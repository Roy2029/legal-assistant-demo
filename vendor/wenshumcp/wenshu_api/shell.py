"""wenshu_api 持久交互终端（REPL）。

由 cli.py 的 `shell` 子命令启动。特点：
  - 会话常驻：一个 WenshuClient 实例贯穿整个终端，Cookie/vjkl5 复用。
  - 验证码只解一次：解出的 number 被客户端缓存，跨命令复用，直到服务端拒绝。
  - 内置指令（在交互终端输入 help 查看）：
        search ...        关键词组合查询（参数同 CLI search）
        structure         查看数据库结构
        court-tree        法院层级树
        cause-tree        案由分类树
        download ...      下载文书（--doc-id / --format / --out）
        reset             重置会话（清空 Cookie 与验证码缓存，重新初始化）
        captcha           强制作废并重新获取/求解一次验证码
        help              显示帮助
        exit / quit       退出

非交互（无 TTY）环境下不建议使用本终端。
"""

from __future__ import annotations

import shlex
import sys

try:  # 仅在类 Unix 提供行内历史，Windows 无 readline 时静默跳过
    import readline  # noqa: F401
except ImportError:
    pass

from wenshu_api import WenshuError  # noqa: E402
from wenshu_api.cli import build_parser  # noqa: E402


_HELP = """\
可用命令（会话常驻，验证码只需解一次）：
  search <关键词> [--cause 案由] [--court 法院] [--case-type 类型]
                [--trial 审判程序] [--page N] [--page-size N] [--json]
  structure                查看可查询字段 / 案件类型 / 法院层级
  court-tree [--max-depth N]   法院层级树
  cause-tree [--max-depth N]    案由分类树
  download --doc-id <ID> [--format text|html|pdf] [--out 路径]
  reset                    重置会话（清空 Cookie 与验证码缓存）
  captcha                  强制重新解一次验证码
  login [--username U] [--password P]   登录（凭据默认来自 .env）
  cookies <SESSION值|JSON文件|Cookie串>  注入已登录 SESSION，跳过 OAuth
  backend [requests|browser]   查看/切换请求后端（browser 模式绕过 wzws 软拦截）
  help                     显示本帮助
  exit / quit              退出
"""


def run_shell(client) -> None:
    parser = build_parser()
    print("=== 中国裁判文书网 交互终端 ===")
    print("输入 help 查看命令；exit 退出。会话已常驻，验证码只需解一次。\n")

    while True:
        try:
            line = input("wenshu> ").strip()
        except EOFError:
            print("\nbye.")
            break
        except KeyboardInterrupt:
            print("  (按 exit 退出)")
            continue

        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            print(_HELP)
            continue
        if line == "reset":
            client.reset_session()
            print("会话已重置（Cookie、登录态与验证码缓存已清空，浏览器后端已关闭）。")
            continue

        if line.startswith("backend"):
            parts = line.split()
            if len(parts) == 1:
                print(f"当前后端：{client._backend_mode}")
                continue
            mode = parts[1].strip()
            if mode not in ("requests", "browser"):
                print("仅支持：backend requests | backend browser")
                continue
            if mode == "requests" and client._browser_backend is not None:
                client._browser_backend.close()
                client._browser_backend = None
            client._backend_mode = mode
            print(f"后端已切换为 {mode}（下次 login / search 生效）。")
            continue
        if line == "captcha":
            client.invalidate_captcha()
            try:
                client._get_code()
                print("验证码已更新。")
            except WenshuError as e:
                print(f"[验证码] {e}")
            continue

        if line.startswith("cookies "):
            spec = line[len("cookies "):].strip()
            from wenshu_api.cli import _load_cookies

            try:
                client.login(cookies=_load_cookies(spec))
                print("已注入 Cookie 并登录（跳过 OAuth）。")
            except WenshuError as e:
                print(f"[错误] {e}")
            continue

        # 其余交给 argparse 解析（复用 cli 的全局参数与子命令定义）
        try:
            args = parser.parse_args(shlex.split(line))
        except SystemExit:
            # argparse 解析失败（参数错误/未知命令）已打印用法，继续循环
            continue

        try:
            args.func(args, client)
        except WenshuError as e:
            print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n[已取消]")

    client.close()
    print("会话已关闭。")
