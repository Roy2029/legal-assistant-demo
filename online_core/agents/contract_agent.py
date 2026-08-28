"""合同审查 agent（D10 v1.1）：ReAct 子代理，读脱敏合同 → 规则扫描 → 生成报告。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from online_core.agents.base import BaseReActAgent
from online_core import contract_rules

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = PROJECT_ROOT / "skills" / "contract_review" / "SKILL.md"
USER_SKILLS_DIR = PROJECT_ROOT / "skills" / "contract_review" / "user_skills"


class ContractAgent(BaseReActAgent):
    """合同审查 agent。workspace = data/agent_workspace/contract-{cid}/"""

    def __init__(self, contract_id: str, llm=None, **kwargs):
        session_id = f"contract-{contract_id}"
        super().__init__(llm=llm, session_id=session_id, **kwargs)
        self.contract_id = contract_id

    # ── 工具 Schema ──────────────────────────────────────────────
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_contracts",
                    "description": "列出工作区内已脱敏的合同文件。",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_contract",
                    "description": "读取工作区内指定合同文件的脱敏文本。",
                    "parameters": {"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_rules",
                    "description": "对给定文本运行合同审查规则引擎，返回命中的风险条款。",
                    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "将审查报告写入工作区。",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "提交合同审查报告并结束。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "report": {"type": "string"},
                            "answer": {"type": "string"},
                            "risks": {"type": "array", "items": {"type": "object"}},
                            "needs_human": {"type": "boolean", "default": False},
                        },
                        "required": ["report", "answer"],
                    },
                },
            },
        ]

    def _list_contract_files(self) -> list[str]:
        d = self.workspace / "contracts"
        if not d.exists():
            return []
        return sorted([p.name for p in d.iterdir() if p.is_file()])

    def _read_contract_file(self, file: str) -> str:
        d = self.workspace / "contracts"
        p = (d / file).resolve()
        if not str(p).startswith(str(d.resolve())):
            raise ValueError("路径越界")
        if not p.exists():
            raise FileNotFoundError(file)
        ext = p.suffix.lower()
        if ext == ".docx":
            import docx
            doc = docx.Document(str(p))
            return "\n".join(par.text for par in doc.paragraphs if par.text.strip())
        if ext == ".pdf":
            import fitz
            with fitz.open(str(p)) as pdf:
                return "\n".join(page.get_text() for page in pdf)
        return p.read_text(encoding="utf-8", errors="ignore")

    def build_system(self, query: str, **kwargs) -> str:
        skills = []
        if SKILL_PATH.exists():
            body = SKILL_PATH.read_text(encoding="utf-8")
            if "---" in body:
                parts = body.split("---", 2)
                body = parts[2] if len(parts) >= 3 else body
            skills.append(f"内置合同审查流程：\n{body[:2000]}")
        USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        for md in sorted(USER_SKILLS_DIR.glob("*.md")):
            skills.append(f"用户上传 skill（{md.name}）：\n{md.read_text(encoding='utf-8')[:2000]}")

        rules = contract_rules.load_rules()
        rules_summary = "\n".join(
            f"- {r.get('rule_id')}: {r.get('dimension')} / {r.get('risk_level')} / {r.get('risk_desc')}"[:200]
            for r in rules[:20]
        )

        return (
            "你是合同审查 agent。只能依据脱敏后的合同文本和规则引擎结果输出意见，不得编造。\n"
            + "\n".join(skills)
            + "\n\n当前可用规则：\n" + rules_summary + "\n\n"
            "工作流程：\n"
            "1) 用 list_contracts 查看脱敏合同文件；\n"
            "2) 用 read_contract 读取合同文本；\n"
            "3) 用 check_rules 扫描风险条款；\n"
            "4) 必要时用 write_file 保存中间结果；\n"
            "5) 用 finish 提交报告，报告含：风险总数、风险清单（等级/说明/建议/原文片段）、整体结论。"
        )

    async def execute_tool(self, name: str, args: dict) -> dict:
        timeout = 90 if name in ("read_contract", "check_rules") else 15
        try:
            if name == "list_contracts":
                return await asyncio.wait_for(asyncio.to_thread(self._list_contract_files), timeout)
            if name == "read_contract":
                return await asyncio.wait_for(asyncio.to_thread(self._read_contract_file, args.get("file", "")), timeout)
            if name == "check_rules":
                text = args.get("text", "")
                return await asyncio.wait_for(asyncio.to_thread(contract_rules.scan_text, text), timeout)
            if name == "write_file":
                return await asyncio.wait_for(asyncio.to_thread(self._write_file, args.get("path", ""), args.get("content", "")), timeout)
            if name == "finish":
                return {"ok": True, **args}
            return {"ok": False, "error": f"未知工具 {name}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"工具超时（>{timeout}s）"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
