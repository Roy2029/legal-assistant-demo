---
skill_id: case_retrieval
name: 案例检索
version: 0.1.0
status: stub
description: 检索相关案例并结构化展示
input_schema:
  type: object
  properties:
    query: {type: string}
  required: [query]
visible_tools: [case_retrieval]
steps:
  - id: retrieve
    tool: case_retrieval
    params: {query: "{{input.query}}"}
---

# 案例检索

## 流程
1. 调用 case_retrieval 工具检索案例；
2. 返回结构化案例列表（案号/案由/法院/裁判要旨）。

## 失败处理
- 离线案例库为空：提示"案例库数据准备中"，不编造案例。
