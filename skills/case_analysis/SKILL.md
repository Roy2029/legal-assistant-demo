---
skill_id: case_analysis
name: 案情分析
version: 0.1.0
status: stub
description: 基于用户案情检索相关法条并给出初步分析框架
input_schema:
  type: object
  properties:
    query: {type: string}
  required: [query]
visible_tools: [kb_retrieval]
steps:
  - id: retrieve
    tool: kb_retrieval
    params: {query: "{{input.query}}"}
  - id: analyze
    type: analyze
    params: {}
---

# 案情分析

## 流程
1. 检索相关法条；
2. 基于检索结果给出初步分析（M0 为简化分析）。

## 决策思路
- 先定位法律关系，再检索对应法条；不编造。
