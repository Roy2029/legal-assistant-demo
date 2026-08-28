"""裁判文书 MCP 适配器可用性测试（不实际连接，避免依赖登录态）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from online_core.mcp.wenshu_adapter import WenshuMCPAdapter


def test_adapter_available():
    adapter = WenshuMCPAdapter()
    assert adapter.available() is True, "MCP 项目或 venv 不存在"
    print("PASS adapter_available")


def test_adapter_list_tools_offline():
    # 不连接：list_tools 需要启动 MCP，这里只验证不会抛异常并返回列表（可能为空）
    import asyncio
    async def _run():
        adapter = WenshuMCPAdapter()
        tools = await adapter.list_tools()
        assert isinstance(tools, list)
    asyncio.run(_run())
    print("PASS adapter_list_tools_shape")


if __name__ == "__main__":
    test_adapter_available()
    test_adapter_list_tools_offline()
    print("ALL WENSHU ADAPTER TESTS PASSED")
