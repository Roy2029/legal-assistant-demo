---
skill_id: legal_memo
name: 法律研究备忘录
version: 0.1.0
status: stub
description: 针对法律问题产出结构化研究备忘录
input_schema:
  type: object
  properties:
    query: {type: string}
  required: [query]
visible_tools: [kb_retrieval, case_retrieval]
steps:
  - id: retrieve_law
    tool: kb_retrieval
    params: {query: "{{input.query}}"}
  - id: retrieve_cases
    tool: case_retrieval
    params: {query: "{{input.query}}"}
  - id: summarize
    type: analyze
    params: {}
---

# 法律研究备忘录

## 流程
1. 检索法规；
2. 检索案例；
3. 汇总为备忘录（M0 简化版）。
