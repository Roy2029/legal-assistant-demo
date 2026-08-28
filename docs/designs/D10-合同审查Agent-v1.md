# D10 合同审查 Agent v1

> 版本：v0.1（交付稿）
> 状态：已交付 v1
> 关联：D06（实务 Skill）、D09（Tool Agent ReAct 基类）

## 1. 目标

合同审查 agent 独立入口：上传 docx/pdf 合同 → 本地脱敏 → 规则审查 → 报告 → 脱敏版/还原版下载。

## 2. 已实现

| 需求 | 实现 |
|---|---|
| 上传一份或多份 docx/pdf | `POST /api/contracts/upload` |
| 原件存入 agent 不可访问目录 | `data/contracts/raw/{cid}/` |
| 本地脱敏 | vendored [LegalMask](https://github.com/RayBagel/LegalMask)（MIT），`online_core/legal_mask.py` |
| 脱敏后存入 agent 工作区 | `data/agent_workspace/contract-{cid}/contracts/` |
| mapping 不可访问 | `data/contracts/mappings/{cid}/` |
| 审查 | `POST /api/contracts/{cid}/review`：规则引擎扫描 `skills/contract_review/rules.jsonl` + 用户上传规则 |
| 报告 | `GET /api/contracts/{cid}/report` |
| 脱敏版下载 | `GET /api/contracts/{cid}/download?kind=redacted` |
| 一键还原 | `GET /api/contracts/{cid}/download?kind=restored`（LegalMask mapping 反向替换） |
| 删除 | `DELETE /api/contracts/{cid}` |
| 用户上传规则 | `POST /api/contracts/skills`（.jsonl），存 `skills/contract_review/user_rules/` |
| 独立前端入口 | 「合同审查」tab：上传/列表/审查/报告/脱敏版/还原版/删除 |

## 3. 目录安全边界

```
data/contracts/raw/{cid}/          ← agent 读写工具禁止访问
data/contracts/mappings/{cid}/     ← agent 读写工具禁止访问
data/contracts/reports/{cid}/      ← 报告（只读给用户）
data/agent_workspace/contract-{cid}/contracts/  ← 脱敏版（agent 可读）
```

## 4. 内置规则

`skills/contract_review/rules.jsonl`：6 条 builtin-v1 规则（结算支付/违约金/管辖/工期/安全/背靠背），`status=enabled`，`source=builtin-v1`。**上线前须经法务朋友核验替换。**

## 5. 测试

- `tests/test_legal_mask.py`：脱敏 + 还原
- `tests/test_contract_api.py`：上传→列表→审查→报告→还原下载→删除
- 全量测试：65 passed

## 6. 待办 / 下一迭代

1. 审查 agent 接入 LLM 工作流（当前为规则引擎，未使用 SKILL.md steps 与 LLM prompt）；
2. 支持上传完整 SKILL.md 用户 skill（当前仅 rules.jsonl）；
3. 生成修订版合同（track changes / 批注），而不只是报告 + 脱敏版；
4. 合同文件在线编辑（前端编辑器）；
5. 扫描版 PDF OCR（可参考 PrivacyGuard 思路，M3 专项）。
