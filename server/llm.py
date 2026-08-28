"""LLM 客户端（OpenAI 兼容 API），配置热更新生效。"""
from __future__ import annotations

from typing import AsyncIterator, Optional

import httpx

from .config_service import config_service


class LLMNotConfiguredError(RuntimeError):
    pass


def parse_tool_calls(payload: dict) -> dict:
    """从 OpenAI 兼容 /chat/completions 响应中解析 content 与 tool_calls。"""
    try:
        msg = payload["choices"][0].get("message", {})
    except Exception:
        return {"content": "", "tool_calls": []}
    content = msg.get("content") or ""
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        arguments = fn.get("arguments") or "{}"
        if isinstance(arguments, dict):
            args = arguments
        else:
            import json
            try:
                args = json.loads(arguments)
            except Exception:
                args = {}
        tool_calls.append({
            "id": tc.get("id") or "",
            "name": name,
            "arguments": args,
        })
    return {"content": content, "tool_calls": tool_calls}


class LLMClient:
    def __init__(self):
        pass

    def _cfg(self) -> dict:
        llm = config_service.get_llm()
        return llm or {}

    @property
    def configured(self) -> bool:
        c = self._cfg()
        return bool(c.get("base_url") and c.get("api_key") and c.get("model"))

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        """流式聊天，yield 增量文本。"""
        c = self._cfg()
        if not self.configured:
            raise LLMNotConfiguredError("LLM 未配置，请在设置页填写 Base URL / API Key / Model")
        url = c["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {c['api_key']}"}
        payload = {
            "model": c["model"],
            "messages": messages,
            "stream": True,
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    import json
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        piece = delta.get("content")
                        if piece:
                            yield piece
                    except Exception:
                        continue

    async def chat_with_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        """OpenAI 兼容 function calling。返回 {content, tool_calls:[{id,name,arguments}]}。"""
        c = self._cfg()
        if not self.configured:
            raise LLMNotConfiguredError("LLM 未配置，请在设置页填写 Base URL / API Key / Model")
        url = c["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {c['api_key']}"}
        payload = {
            "model": c["model"],
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return parse_tool_calls(resp.json())

    async def chat(self, messages: list[dict]) -> str:
        parts = []
        async for p in self.stream_chat(messages):
            parts.append(p)
        return "".join(parts)


llm_client = LLMClient()
