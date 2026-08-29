---
skill_id: contract_review
name: 合同审查
version: 0.2.0
status: available
description: 脱敏合同审查：规则引擎初筛 + LLM ReAct 深度审查，生成批注版与审查报告
input_schema:
  type: object
  properties:
    query: {type: string}
  required: [query]
visible_tools: [kb_retrieval]
steps:
  - id: retrieve_rules
    tool: kb_retrieval
    params: {query: "合同审查要点 {{input.query}}"}
  - id: generate_report
    type: analyze
    params: {}
---

# 合同审查（脱敏版）

## 基本流程
1. 列出工作区脱敏合同文件，确认当前审查对象；
2. 逐份读取脱敏合同文本；
3. 用规则引擎（check_rules）对合同条款做初筛；
4. 结合规则库文件和专门规则，对合同条款逐条分析：权利义务是否对等、违约责任是否过高、管辖与送达是否明确、付款与结算节点是否清晰、安全与保险责任划分是否合法；
5. 在脱敏版本上添加批注，生成 edit 版（annotate_contract）；
6. 输出审查报告（finish）：风险总数、风险清单（等级/说明/建议/原文片段）、整体结论。

## 专门规则（内置规则引擎）
- 逾期审核视为认可送审价：高风险，建议删除或改为双方书面确认。
- 违约金日千分之一：过高，建议万分之五/日并设上限。
- 守约方所在地管辖：不确定性，建议明确合同签订地或被告住所地法院。
- 开工日期以发包人/监理通知为准：工期起算不确定，建议附加现场具备开工条件。
- 安全责任全部转嫁承包方：可能无效，建议按过错划分并购买保险。
- 背靠背付款：对承包方风险极高，建议删除并约定明确付款期限。

## 输出要求
- 只基于脱敏文本，不得猜测或还原被脱敏信息；
- 不虚构条款；每条风险须有原文片段依据；
- 报告使用 Markdown；风险等级仅限 high/medium/low。

## 失败处理
- 工具失败时，重试一次；仍失败则用 finish 提交已发现的风险，并设置 needs_human=true。
