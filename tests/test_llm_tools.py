"""LLM 工具调用解析单元测试（D09 阶段1）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.llm import parse_tool_calls


def test_parse_tool_calls_normal():
    payload = {
        "choices": [{
            "message": {
                "content": "我先查一下知识库。",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "kb_search", "arguments": '{"groups":[{"group_id":"g1","merge_mode":"fuse","queries":["试用期 最长"]}]}'},
                }],
            }
        }]
    }
    r = parse_tool_calls(payload)
    assert r["content"] == "我先查一下知识库。"
    assert len(r["tool_calls"]) == 1
    tc = r["tool_calls"][0]
    assert tc["id"] == "call_1" and tc["name"] == "kb_search"
    assert tc["arguments"]["groups"][0]["group_id"] == "g1"
    print("PASS parse_tool_calls_normal")


def test_parse_tool_calls_no_tools():
    payload = {"choices": [{"message": {"content": "完成", "tool_calls": []}}]}
    r = parse_tool_calls(payload)
    assert r["content"] == "完成" and r["tool_calls"] == []
    print("PASS parse_tool_calls_no_tools")


def test_parse_tool_calls_bad_json():
    payload = {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{"id": "call_2", "type": "function", "function": {"name": "finish", "arguments": "{bad json"}}],
            }
        }]
    }
    r = parse_tool_calls(payload)
    assert len(r["tool_calls"]) == 1
    assert r["tool_calls"][0]["arguments"] == {}
    print("PASS parse_tool_calls_bad_json")


if __name__ == "__main__":
    test_parse_tool_calls_normal()
    test_parse_tool_calls_no_tools()
    test_parse_tool_calls_bad_json()
    print("ALL LLM TOOL PARSE TESTS PASSED")
