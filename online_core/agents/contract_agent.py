"""合同审查 agent（D11）：ReAct 子代理。

工作区：data/agent_workspace/contract-{cid}/
- 仅暴露 contracts/ 下的脱敏产物，raw 原件与 mapping 在 workspace 之外，agent 不可访问。
- 工具：list_contracts / read_contract / check_rules / annotate_contract / write_file / finish。
"""
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
USER_RULE_FILES_DIR = PROJECT_ROOT / "skills" / "contract_review" / "user_rule_files"
USER_RULES_DIR = PROJECT_ROOT / "skills" / "contract_review" / "user_rules"


class ContractAgent(BaseReActAgent):
    """合同审查 agent。workspace = data/agent_workspace/contract-{cid}/"""

    def __init__(self, contract_id: str, llm=None, current_file: str = "", rule_files: list | None = None, **kwargs):
        session_id = f"contract-{contract_id}"
        super().__init__(llm=llm, session_id=session_id, **kwargs)
        self.contract_id = contract_id
        self.current_file = current_file
        self.rule_files = rule_files or []

    # ── 工具 Schema ──────────────────────────────────────────────
    def tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_contracts",
                    "description": "列出工作区内已脱敏的合同文件（仅脱敏版本，不含原始文件）。",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_contract",
                    "description": "读取工作区内指定脱敏合同文件的文本内容。",
                    "parameters": {"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_rules",
                    "description": "对给定文本运行合同审查规则引擎，返回命中的风险条款（规则引擎初筛）。",
                    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "annotate_contract",
                    "description": "在脱敏合同文本上添加批注，生成批注版 Markdown 文件并写入工作区。risks 为风险对象数组，每个对象可含 snippet/clause、risk_level、risk_desc、suggestion。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "description": "脱敏合同文件名"},
                            "risks": {"type": "array", "items": {"type": "object"}, "description": "风险清单"},
                        },
                        "required": ["file"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "将中间结果或报告写入工作区（仅支持 .md / .json）。",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "提交合同审查报告并结束。必须在读取合同、运行规则、生成批注版之后调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "report": {"type": "string", "description": "Markdown 格式的完整审查报告"},
                            "answer": {"type": "string", "description": "给用户的一句话结论"},
                            "risks": {"type": "array", "items": {"type": "object"}, "description": "风险清单，每项含 risk_level/risk_desc/suggestion/snippet 等"},
                            "needs_human": {"type": "boolean", "default": False},
                        },
                        "required": ["report", "answer"],
                    },
                },
            },
        ]

    # ── 工具实现 ─────────────────────────────────────────────────
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
            return chr(10).join(par.text for par in doc.paragraphs if par.text.strip())
        if ext == ".pdf":
            import fitz
            with fitz.open(str(p)) as pdf:
                return chr(10).join(page.get_text() for page in pdf)
        return p.read_text(encoding="utf-8", errors="ignore")

    def _annotate_contract_file(self, file: str, risks: list) -> dict:
        text = self._read_contract_file(file)
        annotated = contract_rules.annotate_text_markdown(text, risks)
        stem = Path(file).stem
        out = f"{stem}_批注版.md"
        d = self.workspace / "contracts"
        d.mkdir(parents=True, exist_ok=True)
        (d / out).write_text(annotated, encoding="utf-8")
        return {"ok": True, "file": out, "notes": len(risks), "bytes": len(annotated.encode("utf-8"))}

    def _build_rule_library_text(self) -> str:
        parts = []
        USER_RULE_FILES_DIR.mkdir(parents=True, exist_ok=True)
        USER_RULES_DIR.mkdir(parents=True, exist_ok=True)
        USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        for name in self.rule_files:
            candidates = [
                USER_RULE_FILES_DIR / name,
                USER_RULES_DIR / name,
                USER_SKILLS_DIR / name,
            ]
            for cand in candidates:
                if cand.exists():
                    try:
                        body = cand.read_text(encoding="utf-8", errors="ignore")
                        parts.append(f"规则库文件（{name}）：{chr(10)}{body[:3000]}")
                    except Exception:
                        pass
                    break
        return chr(10).join(parts)

    def build_system(self, query: str, **kwargs) -> str:
        skills = []
        if SKILL_PATH.exists():
            body = SKILL_PATH.read_text(encoding="utf-8")
            if "---" in body:
                parts = body.split("---", 2)
                body = parts[2] if len(parts) >= 3 else body
            skills.append("内置合同审查流程：\n" + body[:3000])
        for md in sorted(USER_SKILLS_DIR.glob("*.md")):
            skills.append(f"用户上传 skill（{md.name}）：\n{md.read_text(encoding='utf-8')[:2000]}")

        rules = contract_rules.load_rules()
        rules_summary = chr(10).join(
            f"- {r.get('rule_id')}: {r.get('dimension')} / {r.get('risk_level')} / {r.get('risk_desc')}"[:200]
            for r in rules[:30]
        )
        rule_library = self._build_rule_library_text()

        file_hint = ""
        if self.current_file:
            file_hint = f"\n当前脱敏文件引用：{self.current_file}\n"
        if self.rule_files:
            file_hint += f"当前规则库文件引用：{', '.join(self.rule_files)}\n"

        return (
            "你是合同审查 agent。只能依据工作区内脱敏后的合同文本、规则引擎结果和规则库文件输出意见，不得编造，不得试图还原或猜测任何被脱敏的信息。\n"
            + chr(10).join(skills)
            + "\n\n当前可用内置规则：\n" + rules_summary
            + ("\n\n" + rule_library if rule_library else "")
            + file_hint
            + "\n\n工作流程（必须完整执行）：\n"
            "1) 用 list_contracts 查看工作区内的脱敏合同文件；\n"
            "2) 用 read_contract 逐份读取脱敏合同文本；\n"
            "3) 用 check_rules 对合同文本运行规则引擎，获取初筛风险；\n"
            "4) 结合规则库文件和合同条款，补充/修正风险清单（等级、说明、建议、原文片段），不得虚构条款；\n"
            "5) 用 annotate_contract 在脱敏版本上生成批注版（edit 版）；\n"
            "6) 用 finish 提交：answer 为一句话结论；report 为 Markdown 报告，含风险总数、风险清单（等级/说明/建议/原文片段）和整体结论。"
        )

    async def execute_tool(self, name: str, args: dict) -> dict:
        timeout = 90 if name in ("read_contract", "check_rules", "annotate_contract") else 15
        try:
            if name == "list_contracts":
                return await asyncio.wait_for(asyncio.to_thread(self._list_contract_files), timeout)
            if name == "read_contract":
                return await asyncio.wait_for(asyncio.to_thread(self._read_contract_file, args.get("file", "")), timeout)
            if name == "check_rules":
                text = args.get("text", "")
                return await asyncio.wait_for(asyncio.to_thread(contract_rules.scan_text, text), timeout)
            if name == "annotate_contract":
                file = args.get("file", "")
                risks = args.get("risks") or []
                return await asyncio.wait_for(asyncio.to_thread(self._annotate_contract_file, file, risks), timeout)
            if name == "write_file":
                return await asyncio.wait_for(asyncio.to_thread(self._write_file, args.get("path", ""), args.get("content", "")), timeout)
            if name == "finish":
                return {"ok": True, **args}
            return {"ok": False, "error": f"未知工具 {name}"}
        except asyncio.TimeoutError:
            return {"ok": False, "error": f"工具超时（>{timeout}s）"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
