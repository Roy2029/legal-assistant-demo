# 法律助手 Demo（legal-assistant-demo）

面向执业律师/企业法务的本地化法律 AI 助手桌面应用。当前处于 **M0 能跑** 开发阶段（代码开发已关闭，待人工验收：安装包/虚拟机验收/律师体验）。

## 设计文档

- [SPEC](docs/SPEC.md)：总体技术规格
- [Roadmap](docs/ROADMAP.md)：M0-M4 开发排期
- [行动计划](docs/PLAN.md)：分阶段设计协作计划
- [设计文档](docs/designs/)：D01-D08 模块设计
- [资产索引](docs/INVENTORY_RAG1.0.md)：RAG1.0 资产盘点

## 快速启动（开发模式）

1. 准备 Python 环境：`.venv`（已就绪）并安装依赖：`.venv/Scripts/pip install -r requirements.txt`
2. 在项目根目录配置 `.env`（LLM_API_KEY / LLM_BASE_URL，可选 LLM_MODEL）
3. 双击 **`start_all.bat`**（一键启动后端+前端+打开浏览器）
4. 停止：双击 **`stop_all.bat`**

前端开发服务器：http://127.0.0.1:5173 ｜ 后端 API：http://127.0.0.1:8000

## 功能清单（M0 代码开发已关闭）

- **知识库问答**：精确法条号检索（如"民法典第32条"）、语义混合检索（dense+BM25）、引用校验（防法条幻觉）、PreFilter 保守拦截、Trace 面板（dense/BM25 召回 chunk + BM25 分词）、引用卡片点击定位原文
- **用户知识库**：md/txt/docx/pdf 上传、解析入库、corpus 元数据隔离、删除
- **实务助手**：skill 注册表 + 4 个业务动作桩（案例检索/案情分析/合同审查/法律研究备忘录）
- **会话管理**：多会话、历史注入、上下文压缩
- **安全**：可逆脱敏（Fernet 加密映射）、审计日志
- **交付**：一键启动 start_all.bat / stop_all.bat

## 开发状态

见 [CHANGELOG.md](CHANGELOG.md) 与 `docs/DEVLOG.md`。

## 技术栈

- 后端：FastAPI + LangGraph（规划中）+ SQLite
- 检索：Qdrant（dense=bge-base-zh + BM25 sparse + bge-reranker-v2-m3）
- 前端：React + Vite + Ant Design（规划中）
- 模型：本地 embedding/rerank + 云端 LLM API
