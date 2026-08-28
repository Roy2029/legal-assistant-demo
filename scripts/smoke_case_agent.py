"""Case agent 冒烟测试：MCP 初始化→状态→检索；必要时自动登录。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.mcp.wenshu_adapter import WenshuMCPAdapter


async def main():
    adapter = WenshuMCPAdapter()
    print("MCP available:", adapter.available())

    # 1. 初始化会话
    r = await adapter.call_tool("init_session", {})
    print("init_session:", json.dumps(r, ensure_ascii=False)[:300])

    # 2. 状态
    r = await adapter.call_tool("get_status_tool", {})
    print("get_status:", json.dumps(r, ensure_ascii=False)[:500])

    # 3. 检索
    r = await adapter.call_tool("search_judgments_tool", {
        "fulltext_keyword": "建设工程施工合同 实际施工人",
        "case_type": "民事案件",
        "page_num": 1,
        "page_size": 5,
    })
    print("search raw:", json.dumps(r, ensure_ascii=False)[:800])

    # 4. 若需要登录，尝试自动登录后重试
    text = json.dumps(r, ensure_ascii=False)
    if "登录" in text or "login" in text.lower() or r.get("ok") is False:
        from server.config_service import config_service
        w = config_service.get_wenshu()
        if w.get("username") and w.get("password"):
            print("检测到需要登录，尝试 auto_login_tool ...")
            lr = await adapter.call_tool("auto_login_tool", {"username": w["username"], "password": w["password"]}, timeout=150)
            print("auto_login:", json.dumps(lr, ensure_ascii=False)[:500])
            await adapter.call_tool("init_session", {})
            r = await adapter.call_tool("search_judgments_tool", {
                "fulltext_keyword": "建设工程施工合同 实际施工人",
                "case_type": "民事案件",
                "page_num": 1,
                "page_size": 5,
            })
            print("search retry:", json.dumps(r, ensure_ascii=False)[:800])
        else:
            print("未配置裁判文书网账号密码，无法自动登录")


if __name__ == "__main__":
    asyncio.run(main())
