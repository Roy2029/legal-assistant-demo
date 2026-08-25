---
skill_id: contract_review
name: 合同审查
version: 0.1.0
status: stub
description: 上传合同，输出结构化审查报告（M0 流程桩）
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

# 合同审查（M0 桩）

## 流程
1. 检索合同审查相关法条与要点；
2. 生成简化审查报告框架。

## 失败处理
- 深度规则库在 M1 建设，当前返回流程桩报告。
