"""wenshu_api 命令行工具（CLI + 交互式终端）。

包含两种用法：
  1) 一次性调用：  python wenshu_api/cli.py search "合同纠纷" --case-type 民事案件
  2) 持久交互终端：python wenshu_api/cli.py shell
                   python -m wenshu_api.cli shell

交互终端特性：
  - 会话常驻：Cookie / vjkl5 跨命令复用，只初始化一次。
  - 验证码只解一次：解出的 number 在同一会话内缓存复用，直到服务端拒绝再重解。
  - 内置命令：search / structure / court-tree / cause-tree / download /
    reset（重置会话）/ captcha（强制重新解验证码）/ help / exit。

依赖：仅标准库 + requests。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wenshu_api import WenshuClient, CaptchaRequiredError, WenshuError  # noqa: E402
from wenshu_api.utils.captcha import build_solver  # noqa: E402
from wenshu_api.utils.log import configure_logging, get_logger  # noqa: E402


def _build_client(args) -> WenshuClient:
    # 默认 ddddocr 离线识别；--no-ocr / --no-captcha 时禁用自动识别，回退交互式。
    auto = not (args.no_ocr or args.no_captcha)
    solver = build_solver(use_ddddocr=auto, save_dir=args.captcha_dir)
    client = WenshuClient(
        max_qps=args.max_qps,
        timeout=args.timeout,
        max_retries=args.max_retries,
        proxy=args.proxy or None,
        captcha_solver=solver,
        max_captcha_retries=args.max_captcha_retries,
        captcha_source_url=args.captcha_source_url or None,
        log_level=logging.DEBUG if args.debug else logging.INFO,
        backend=getattr(args, "backend", "requests"),
    )
    # --cookies：跳过 OAuth，直接注入已登录 SESSION
    if getattr(args, "cookies", ""):
        client.login(cookies=_load_cookies(args.cookies))
    return client


def _load_cookies(spec: str) -> dict:
    """把 --cookies 的多种写法解析成 {name: value} 字典。

    支持三种形式：
      ① JSON 文件路径，如 ./fresh_cookies.json（内容 {"SESSION": "..."}）；
      ② 直接粘贴的 SESSION 值（裸串，不含 '='）；
      ③ Cookie 串，如 "SESSION=xxx; other=y"。
    也兼容直接粘贴 JSON 文本（以 '{' 开头）。
    """
    s = (spec or "").strip()
    if not s:
        return {}
    # ① 内联 JSON 文本
    if s.startswith("{"):
        return json.loads(s)
    # ② JSON 文件路径
    if os.path.exists(s):
        with open(s, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    # ③ Cookie 串（含 '='）按 ';' 拆分
    if "=" in s:
        cookies: dict = {}
        for part in s.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()
        if cookies:
            return cookies
    # ④ 裸 SESSION 值
    return {"SESSION": s}


def _print_tree(node, prefix: str = "", max_depth: int = 99, depth: int = 0):
    if depth > max_depth:
        return
    print(prefix + node.name + (f"  (#{node.code})" if node.code else ""))
    for child in node.children:
        connector = "└─ " if child is node.children[-1] else "├─ "
        _print_tree(child, prefix + ("   " if child is node.children[-1] else "│  "),
                    max_depth, depth + 1)


# --------------------------------------------------------------------------- #
# 子命令实现（client 可选：交互终端复用同一实例）
# --------------------------------------------------------------------------- #
def cmd_search(args, client=None):
    own = client is None
    client = client or _build_client(args)
    try:
        result = client.search(
            keyword=args.keyword,
            cause=args.cause,
            court_name=args.court,
            case_type=args.case_type,
            trial_procedure=args.trial,
            page=args.page,
            page_size=args.page_size,
        )
    except WenshuError as e:
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if own:
            client.close()

    get_logger().info("[成功] 命中 %d 条，返回 %d 条摘要", result.total, len(result.documents))

    if args.json:
        from dataclasses import asdict

        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0

    print(f"命中 {result.total} 条，第 {result.page}/{result.total_pages} 页"
          f"（每页 {result.page_size}）\n")
    if not result.documents:
        print("（无结果）")
        return 0
    for i, d in enumerate(result.documents, 1):
        print(f"{i}. {d.title}")
        print(f"   案号: {d.case_number}  |  法院: {d.court_name}"
              f"  |  发布: {d.publish_date}")
        print(f"   类型: {d.case_type}  |  案由: {d.cause}  |  docId: {d.doc_id}")
    return 0


def cmd_structure(args, client=None):
    own = client is None
    client = client or _build_client(args)
    try:
        ds = client.get_db_structure()
    finally:
        if own:
            client.close()

    print("=== 可查询字段 ===")
    for f in ds.queryable_fields:
        print(f"  - {f.key:>8}  {f.label}  (示例: {f.example})")
    print("\n=== 案件类型 ===")
    for k, v in ds.case_types.items():
        print(f"  - {k}: {v}")
    print("\n=== 法院层级 ===")
    for lv in ds.court_levels:
        print(f"  - {lv}")
    return 0


def cmd_court_tree(args, client=None):
    own = client is None
    client = client or _build_client(args)
    try:
        tree = client.get_court_tree()
    except WenshuError as e:
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if own:
            client.close()
    print("=== 法院层级结构 ===")
    _print_tree(tree, max_depth=args.max_depth)
    return 0


def cmd_cause_tree(args, client=None):
    own = client is None
    client = client or _build_client(args)
    try:
        tree = client.get_cause_tree()
    except WenshuError as e:
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if own:
            client.close()
    print("=== 案由分类结构 ===")
    _print_tree(tree, max_depth=args.max_depth)
    return 0


def cmd_download(args, client=None):
    own = client is None
    client = client or _build_client(args)
    try:
        path = client.download_document(
            doc_id=args.doc_id,
            save_format=args.format,
            save_path=args.out,
        )
    except WenshuError as e:
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if own:
            client.close()
    get_logger().info("[成功] 已保存 %s", path)
    print(f"已保存：{path}")
    return 0


def cmd_login(args, client=None):
    own = client is None
    client = client or _build_client(args)
    if client.is_logged_in():
        print("[登录] 已通过 --cookies 注入会话，无需重复登录")
        return 0
    try:
        result = client.login(username=args.username, password=args.password)
        nick = ""
        if isinstance(result.get("result"), dict):
            nick = result["result"].get("nickName", "")
        print(f"[登录] 成功 nickName={nick}")
        return 0
    except WenshuError as e:
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        if own:
            client.close()


def cmd_shell(args, client=None):
    """进入持久交互终端（会话常驻、验证码复用）。"""
    own = client is None
    client = client or _build_client(args)
    try:
        from wenshu_api.shell import run_shell

        run_shell(client)
    finally:
        if own:
            client.close()
    return 0


# --------------------------------------------------------------------------- #
# 参数解析
# --------------------------------------------------------------------------- #
# 全局参数定义（flag -> add_argument 的 kwargs）。build_parser 在「顶层 parser」上
# 用真实默认值注册一遍（保证 namespace 总有值），在每个「subparser」上用
# default=argparse.SUPPRESS 注册一遍——这样当全局参数写在子命令之前时，subparser
# 不会用自身默认值把顶层已解析的全局值覆盖掉（argparse 的经典陷阱）。
_GLOBAL_ARG_DEFS = [
    ("--max-qps", dict(type=float, default=1.0,
                       help="限流上限（每秒请求数），默认 1.0")),
    ("--timeout", dict(type=int, default=15,
                       help="请求超时（秒），默认 15")),
    ("--max-retries", dict(type=int, default=3,
                           help="网络瞬时错误重试次数，默认 3")),
    ("--proxy", dict(default="",
                     help="代理地址，如 http://127.0.0.1:7890")),
    ("--no-captcha", dict(action="store_true",
                          help="禁用自动验证码处理（ddddocr 与交互均不启用，遇验证码直接报错并保存图片）")),
    ("--no-ocr", dict(action="store_true",
                      help="禁用 ddddocr，改用交互式人工输入验证码（需 TTY）")),
    ("--max-captcha-retries", dict(type=int, default=5,
                                   help="验证码识别失败/被拒时的最大刷新重试次数，默认 5")),
    ("--captcha-dir", dict(default="./captcha",
                           help="验证码图片保存目录（用于审计 OCR 准确率），默认 ./captcha")),
    ("--captcha-source-url", dict(default="",
                                  help="验证码所在页面 URL（新版站点把验证码以内嵌 data:image 注入页面）")),
    ("--debug", dict(action="store_true",
                     help="开启 DEBUG 日志（观察请求/重试细节）")),
    ("--cookies", dict(default="",
                       help="跳过 OAuth：直接注入已登录 SESSION Cookie。"
                            "支持 ①JSON 文件路径(如 ./fresh_cookies.json) "
                            "②裸 SESSION 值 ③Cookie 串 SESSION=xxx;other=y。")),
    ("--backend", dict(choices=["requests", "browser"], default="requests",
                       help="请求后端：requests=纯 HTTP 重放（默认）；browser=常驻 "
                            "Playwright 浏览器上下文发请求，绕过 wzws 防火墙软拦截，"
                            "真实环境稳定可用（需本机装 playwright + chromium）。")),
]


def _add_global_args(parser: argparse.ArgumentParser, suppress: bool = False) -> None:
    """在 parser 上注册全部全局参数。

    suppress=True 时把默认值替换为 argparse.SUPPRESS，使 subparser 在命令行未显式
    出现该参数时不要覆盖顶层 parser 已解析出的全局值。
    """
    for flag, kw in _GLOBAL_ARG_DEFS:
        if suppress and "default" in kw:
            kw = dict(kw)
            kw["default"] = argparse.SUPPRESS
        parser.add_argument(flag, **kw)


def build_parser() -> argparse.ArgumentParser:
    # 顶层 parser 用真实默认；subparser 用 SUPPRESS 默认（避免覆盖顶层已解析值）。
    parent = argparse.ArgumentParser(add_help=False)
    _add_global_args(parent, suppress=False)

    parent_suppress = argparse.ArgumentParser(add_help=False)
    _add_global_args(parent_suppress, suppress=True)

    parser = argparse.ArgumentParser(
        prog="wenshu",
        parents=[parent],
        description="中国裁判文书网爬虫 CLI（基于 wenshu_api）",
    )
    parser.add_argument("--version", action="version", version="wenshu_api CLI 0.1.0")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", parents=[parent_suppress], help="关键词组合查询")
    p.add_argument("keyword", nargs="?", default=None, help="全文检索关键词")
    p.add_argument("--cause", help="案由")
    p.add_argument("--court", help="法院名称")
    p.add_argument("--case-type", help="案件类型（中文或 xs/ms/xz/pc/zx）")
    p.add_argument("--trial", help="审判程序（一审/二审…）")
    p.add_argument("--page", type=int, default=1, help="页码，默认 1")
    p.add_argument("--page-size", type=int, default=10, help="每页条数，默认 10")
    p.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("structure", parents=[parent_suppress], help="查看数据库结构")
    p.set_defaults(func=cmd_structure)

    p = sub.add_parser("court-tree", parents=[parent_suppress], help="法院层级树")
    p.add_argument("--max-depth", type=int, default=99, help="最大展开深度")
    p.set_defaults(func=cmd_court_tree)

    p = sub.add_parser("cause-tree", parents=[parent_suppress], help="案由分类树")
    p.add_argument("--max-depth", type=int, default=99, help="最大展开深度")
    p.set_defaults(func=cmd_cause_tree)

    p = sub.add_parser("download", parents=[parent_suppress], help="下载文书全文")
    p.add_argument("--doc-id", required=True, help="文书 ID")
    p.add_argument("--format", choices=["text", "html", "pdf", "docx"], default="text",
                   help="保存格式：text=纯文本(.txt)，html=完整渲染HTML(.html)，"
                        "pdf=转PDF(.pdf，需安装 weasyprint 或 pdfkit)，"
                        "docx=Word 文档(.docx，需 python-docx)。默认 text")
    p.add_argument("--out", default=None, help="保存路径或目录")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("login", parents=[parent_suppress], help="登录（凭据来自 .env 或参数）")
    p.add_argument("--username", default=None,
                   help="账号（默认读 .env 的 WENSHU_USER_NAME）")
    p.add_argument("--password", default=None,
                   help="密码（默认读 .env 的 WENSHU_PASSWORD）")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("shell", parents=[parent_suppress], help="进入持久交互终端")
    p.set_defaults(func=cmd_shell)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
