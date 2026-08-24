# 法律助手 Demo（legal-assistant-demo）

面向执业律师/企业法务的本地化法律 AI 助手桌面应用。当前处于 **M0 能跑** 开发阶段。

## 设计文档

- [SPEC](docs/SPEC.md)：总体技术规格
- [Roadmap](docs/ROADMAP.md)：M0-M4 开发排期
- [行动计划](docs/PLAN.md)：分阶段设计协作计划
- [设计文档](docs/designs/)：D01-D08 模块设计
- [资产索引](docs/INVENTORY_RAG1.0.md)：RAG1.0 资产盘点

## 开发状态

见 [CHANGELOG.md](CHANGELOG.md) 与 `docs/DEVLOG.md`。

## 技术栈

- 后端：FastAPI + LangGraph（规划中）+ SQLite
- 检索：Qdrant（dense=bge-base-zh + BM25 sparse + bge-reranker-v2-m3）
- 前端：React + Vite + Ant Design（规划中）
- 模型：本地 embedding/rerank + 云端 LLM API
