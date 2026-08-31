"""裁判文书网 MCP 服务器（stdio）。

暴露 7 个工具给上层 agent，把所有反爬摩擦（登录、软拦截、验证码、会话过期、
站点漂移）包裹在 AgentSession 内核后面，对 agent 完全透明——agent 只需说
「查一下 XX 的判例」「下载这篇文书」，无需关心任何底层机制。

工具签名只讲「业务语言」，所有返回均为结构化 JSON（见 errors.py）：
    {ok, error_code, data | message}

⚠ 线程模型：mcp ≥1.2x 的 FastMCP 在事件循环线程内直接执行同步工具
（func_metadata.call_fn_with_arg_validation 无 to_thread），而 wenshu 内核的
浏览器后端依赖 sync_playwright（在循环线程内会死锁/报 Sync API inside
asyncio loop），ddddocr 的运行中首次导入也会病态卡死循环线程。因此所有工具
均为 async def，同步实现体统一委托给单线程 _TOOL_EXECUTOR（无事件循环的
专用线程；max_workers=1 与 AgentSession 单会话非线程安全的设计相匹配）。

入口：``python -m wenshu_mcp.server`` 或 ``wenshu_mcp.server:main``。
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

# ⚠ 预导入重依赖：ddddocr（onnxruntime）在 stdio server 运行中被首次导入会
# 病态卡死（实测 20s～150s+，普通进程 0.3s）。在启动期主线程一次性导入后，
# 后续均为 sys.modules 命中。若删除本导入，务必确认登录链路不受影响。
import ddddocr  # noqa: F401

from mcp.server.fastmcp import FastMCP

from . import errors as E
from .agent_session import get_session

logger = logging.getLogger("wenshu_mcp.server")

mcp = FastMCP("wenshu-mcp")

# 工具同步实现体专用单线程：循环线程绝不直接碰 playwright/ddddocr/站点请求
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wenshu-tool")


async def _run_sync(fn, *args):
    """把同步实现体调度到专用线程，返回其结果（异常原样上抛由 FastMCP 包装）。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_TOOL_EXECUTOR, functools.partial(fn, *args))


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #
def _backend() -> str:
    return os.getenv("WENSHU_BACKEND", "browser")


def _search_result_to_dict(res) -> dict:
    return {
        "total": res.total,
        "page": res.page,
        "page_size": res.page_size,
        "items": [
            {
                "doc_id": d.doc_id,
                "title": d.title,
                "court_name": d.court_name,
                "case_number": d.case_number,
                "publish_date": d.publish_date,
                "summary": d.summary,
            }
            for d in res.documents
        ],
    }


def _doc_to_json(doc) -> dict:
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "court_name": doc.court_name,
        "case_number": doc.case_number,
        "case_type": doc.case_type,
        "trial_procedure": doc.trial_procedure,
        "publish_date": doc.publish_date,
        "judgment_date": doc.judgment_date,
        "cause": doc.cause,
        "keywords": doc.keywords,
        "legal_basis": [lb.__dict__ for lb in doc.legal_basis],
        "title_block": doc.title_block,
        "background": doc.background,
        "claims": doc.claims,
        "court_opinion": doc.court_opinion,
        "judgment_result": doc.judgment_result,
        "signatures": doc.signatures,
        "view_count": doc.view_count,
        "full_text": doc.full_text,
    }


def _login_impl(solve_mode: str, human_timeout: int, force: bool) -> dict:
    s = get_session(backend=_backend())
    return E.ok({"session_status": s.login_now(
        solve_mode=solve_mode, human_timeout=human_timeout, force=force)})


@mcp.tool()
async def login(solve_mode: str = "human", human_timeout: int = 300,
                force: bool = False) -> dict:
    """登录裁判文书网（读取 MCP 配置或 .env 中的凭据）。

    solve_mode 默认为 ``human``：站点登录验证码已改型为「点选文字」
    （tianai WORD_IMAGE_CLICK），离线 OCR 无法可靠识别，必须由人在浏览器窗口里点。
    ``auto`` 仅对历史「输入字符」型验证码有效，当前基本必失败。

    大多数情况下无需手动调用本工具——业务工具会自动确保登录，且会优先复用
    ``~/.wenshu/browser_session.json`` 里的会话快照，从而跳过验证码。

    :param solve_mode: human（默认，弹窗人工点选）/ auto / auto_then_human。
    :param human_timeout: 人工模式等待秒数。
    :param force: True 时忽略会话快照，强制重新走 OAuth（换账号时用）。
    """
    try:
        return await _run_sync(_login_impl, solve_mode, human_timeout, force)
    except Exception as e:  # noqa: BLE001
        return E.map_exception(e)


def _reset_cooldown_impl() -> dict:
    s = get_session(backend=_backend())
    return E.ok({"session_status": s.reset_cooldown()})


@mcp.tool()
async def reset_cooldown() -> dict:
    """清除限频冷却窗口。

    站点被触发风控（网关 code=9）后，本库会记录一个跨进程的冷却期，期间直接
    快速失败以避免无谓重登消耗人工验证码。若你确认站点已恢复（或换过网络出口），
    调用本工具立即解除冷却。
    """
    try:
        return await _run_sync(_reset_cooldown_impl)
    except Exception as e:  # noqa: BLE001
        return E.map_exception(e)


def _search_impl(keyword: str, page: int, page_size: int) -> dict:
    s = get_session(backend=_backend())
    res = s.search(keyword=keyword, page=page, page_size=page_size)
    return E.ok(_search_result_to_dict(res))


@mcp.tool()
async def search(keyword: str, page: int = 1, page_size: int = 10) -> dict:
    """关键词检索裁判文书。内部自动确保登录，对 agent 透明。"""
    try:
        return await _run_sync(_search_impl, keyword, page, page_size)
    except Exception as e:  # noqa: BLE001
        return E.map_exception(e)


def _advanced_search_impl(keyword: str, court: str, case_type: str, cause: str,
                          trial_procedure: str, sort: str, page: int, page_size: int) -> dict:
    s = get_session(backend=_backend())
    res = s.search(
        keyword=keyword or None,
        cause=cause or None,
        court_name=court or None,
        case_type=case_type or None,
        trial_procedure=trial_procedure or None,
        sort=sort or None,
        page=page,
        page_size=page_size,
    )
    return E.ok(_search_result_to_dict(res))


@mcp.tool()
async def advanced_search(
    keyword: str = "",
    court: str = "",
    case_type: str = "",
    cause: str = "",
    trial_procedure: str = "",
    sort: str = "s50:desc",
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """多条件高级检索。各条件留空表示不限定；审判程序如「一审/二审」。

    注：日期区间过滤当前未接入（站点接口待校准），如需按日期请用 sort 排序近似。
    """
    try:
        return await _run_sync(_advanced_search_impl, keyword, court, case_type,
                               cause, trial_procedure, sort, page, page_size)
    except Exception as e:  # noqa: BLE001
        return E.map_exception(e)


def _get_document_impl(doc_id: str, format: str) -> dict:
    doc = get_session(backend=_backend()).get_document(doc_id)
    if format == "text":
        return E.ok({"doc_id": doc.doc_id, "text": doc.full_text})
    if format == "html":
        return E.ok({"doc_id": doc.doc_id, "html": doc.html})
    return E.ok(_doc_to_json(doc))


@mcp.tool()
async def get_document(doc_id: str, format: str = "json") -> dict:
    """获取单篇文书全文。format=json（结构化字段）| text（纯文本）| html（完整 HTML）。"""
    try:
        return await _run_sync(_get_document_impl, doc_id, format)
    except Exception as e:  # noqa: BLE001
        return E.map_exception(e)


def _batch_download_impl(doc_ids: List[str], format: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    s = get_session(backend=_backend())
    saved, failed = [], []
    for did in doc_ids:
        try:
            path = s.download(
                did, save_format=format,
                save_path=os.path.join(out_dir, f"{did}.{format}"),
            )
            saved.append({"doc_id": did, "path": path})
        except Exception as e:  # noqa: BLE001
            failed.append({"doc_id": did, "error": str(e)})
    return E.ok({
        "saved": saved,
        "failed": failed,
        "count": len(saved),
        "failed_count": len(failed),
    })


@mcp.tool()
async def batch_download(
    doc_ids: List[str],
    format: str = "text",
    out_dir: str = "./wenshu_docs",
) -> dict:
    """批量下载文书到 out_dir。format=text|html|pdf|docx；内建限流与失败隔离。"""
    try:
        return await _run_sync(_batch_download_impl, doc_ids, format, out_dir)
    except Exception as e:  # noqa: BLE001
        return E.map_exception(e)


def _session_status_impl() -> dict:
    s = get_session(backend=_backend())
    return E.ok(s.session_status())


@mcp.tool()
async def session_status() -> dict:
    """返回当前会话健康度（登录态 / 年龄 / 后端 / 心跳）。"""
    try:
        return await _run_sync(_session_status_impl)
    except Exception as e:  # noqa: BLE001
        return E.map_exception(e)


def _find_chromium() -> str:
    """纯文件系统检测 chromium（不启动 Playwright）。

    ⚠ 不能用 sync_playwright() 探测：它会在调用线程留下 asyncio 事件循环，
    与后续浏览器后端的 sync_playwright 冲突（Sync API inside asyncio loop）。
    与 research/e2e_agent_session.py::_find_chromium 同一结论，此处返回 exe 路径。
    """
    import glob

    candidates = []
    env_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if env_path:
        candidates.append(env_path)
    home = os.path.expanduser("~")
    candidates += [
        os.path.join(os.getenv("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.join(home, "Library", "Caches", "ms-playwright"),
        os.path.join(home, ".cache", "ms-playwright"),
    ]
    patterns = ["chromium-*/chrome-win*/chrome.exe",
                "chromium-*/chrome-mac*/**/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-linux/chrome"]
    for base in candidates:
        if not base or not os.path.isdir(base):
            continue
        for pat in patterns:
            hits = glob.glob(os.path.join(base, pat), recursive=True)
            if hits:
                return hits[0]
    return ""


def _health_check_impl() -> dict:
    checks: dict = {}
    for mod in ("requests", "Crypto", "ddddocr", "playwright",
                "reportlab", "docx", "mcp"):
        try:
            __import__(mod)
            checks[mod] = "ok"
        except Exception as e:  # noqa: BLE001
            checks[mod] = f"missing:{type(e).__name__}"

    # chromium 二进制是否就绪（纯文件系统扫描，绝不启动 sync_playwright）
    ep = _find_chromium()
    checks["chromium"] = "installed" if ep else "missing"

    # 凭据
    from wenshu_api import constants as C
    checks["creds"] = "ok" if os.getenv(C.ENV_USER_NAME) else "missing"

    # 算法配置（站点漂移热更新）
    checks["algo_config"] = "loaded" if C.ALGO else "default"

    guidance = []
    if str(checks.get("playwright", "")).startswith("missing"):
        guidance.append("pip install playwright && playwright install chromium")
    if str(checks.get("chromium", "")).startswith("missing"):
        guidance.append("playwright install chromium")
    if checks.get("creds") == "missing":
        guidance.append("在 MCP 配置 env 中设置 WENSHU_USER_NAME / WENSHU_PASSWORD")
    if str(checks.get("ddddocr", "")).startswith("missing"):
        guidance.append("pip install ddddocr")
    return E.ok({"checks": checks, "guidance": guidance})


@mcp.tool()
async def health_check() -> dict:
    """自检：依赖 / chromium / 凭据 / 算法配置；失败时给出修复指引。"""
    try:
        return await _run_sync(_health_check_impl)
    except Exception as e:  # noqa: BLE001
        return E.map_exception(e)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
