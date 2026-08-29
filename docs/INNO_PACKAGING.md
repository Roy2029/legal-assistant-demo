# Inno Setup 打包清单与决策（草案）

> 目标：把 legal-assistant-demo 打包为 Windows 本地安装包，双击安装后一键启动，浏览器访问 127.0.0.1。

## 一、必打包内容

| 内容 | 路径 | 大小 | 说明 |
|---|---|---|---|
| 后端源码 | server/ | 391K | FastAPI 路由/DB/会话 |
| 在线检索核心 | online_core/ | 784K | PreFilter/引擎/RAG/Agent |
| 离线核心 | offline_core/ | 598K | Qdrant 封装/embedding/retriever |
| 工具库 | utils/ | 80K | 运行时依赖 |
| 技能库 | skills/ | 11K | SKILL.md 注册表 |
| 提示词 | prompts/ | - | context_manager/engine 读取 |
| 前端构建产物 | frontend/dist/ | 1.1M | 需改为由 FastAPI 统一 serve |
| 检索索引 | data/indices/法律/qdrant/ | 165M | 运行时检索必需（embedded Qdrant） |
| 索引 manifest | data/indices/法律/manifest.json | 345B | kb_index 统计公共库 chunks |
| Embedding 模型 | models/bge-base-zh/ | 391M | 检索编码必需，需改为相对路径 |
| 生产启动脚本 | 新增 start_prod.bat / start_prod.py | - | 启动 uvicorn 并打开浏览器 |

## 二、可选打包内容（建议默认打包，可在安装组件中勾选）

| 内容 | 路径 | 大小 | 说明 |
|---|---|---|---|
| 重建索引中间产物 | data/indices/法律/chunk_v2_intermediate/chunks.jsonl | 98M | 「知识库管理-重建法律库」功能需要 |
| Python 运行时 | runtime/ | 待定 | 若目标机无 Python 必须打包 |
| 依赖 wheelhouse | wheelhouse/ | 待定 | 离线 pip 安装依赖 |

## 三、不打包内容

| 内容 | 路径 | 大小 | 原因 |
|---|---|---|---|
| 开发虚拟环境 | .venv/ | - | 依赖 Anaconda system-site-packages，不可移植 |
| 前端 node_modules | frontend/node_modules/ | 162M | 构建产物已包含，运行时不需要 |
| 前端源码 | frontend/src/ | - | 安装包不含源码（如需可另出源码包） |
| 旧索引备份 | data/indices/法律/qdrant_old_*/ | 222M | 旧备份 |
| 中间 chunks jsonl | data/indices/法律/chunks_v2.jsonl | 30M | 仅评估用，运行时检索不读 |
| 数据库/配置/密钥 | data/sqlite.db, data/config.json, data/keys/, .env | - | 运行时自动生成，且含敏感信息 |
| 运行产物 | data/logs/, data/uploads/, data/agent_workspace/, data/contracts/, data/anonymization/ | - | 用户数据，安装包只创建空目录 |
| 评测数据 | data/eval/, data/eval_reports/, evaluation/, QA_dataset/ | - | 开发/评估用 |
| 文档/设计稿 | docs/ 中非用户手册 | - | 可单独提供 |
| 类案检索 MCP 项目 | D:\个人开发\裁判文书检索MCP | - | 外部依赖，用户自行处理 |
| Reranker 模型 | D:\个人\Research\RAG1.0\local_model\bge-reranker-v2-m3 | 2.2G | 当前默认 skip，不打 |
| Wenshu/LLM 凭据 | .env, data/config.json | - | 用户安装后自行配置 |

## 四、需要先解决的技术问题

1. **前端由 FastAPI 统一服务**：当前前端依赖 Vite dev server（node）。安装包应只跑后端 8000，由 FastAPI mount frontend/dist，浏览器访问 8000。
2. **路径便携化**：
   - `retrieval_service.py` 默认 embedding 路径硬编码 `D:/个人/Research/RAG1.0/local_model/bge-base-zh`；
   - `main.py` 启动时也硬编码该路径；
   - 改为优先 `{安装目录}/models/bge-base-zh`，不存在再回退旧路径。
3. **Python 运行时**：当前 .venv 不可移植（system-site-packages 指向 Anaconda）。需选择：
   - A. 打包 Python 3.12 嵌入式/安装器 + 离线 wheelhouse，安装时建独立 venv 并 pip install；
   - B. 目标机自行安装 Python 3.12，安装包只带 wheelhouse 并 pip install；
   - C. 仅本机安装包（不做 Python runtime），依赖本机现有环境。
4. **生产启动脚本**：新增 start_prod.bat（启动 uvicorn，不依赖 node），并考虑日志目录创建。
5. **安装后首次启动时间**：CPU embedding 模型加载约 20-60s，属正常，需在启动脚本提示。

## 五、建议的安装包目录结构

```
C:\Program Files\LegalAssistantDemo\
├── start_legal_assistant.bat
├── runtime\               # Python 运行时（方案A）
├── app\
│   ├── server\ online_core\ offline_core\ utils\ skills\ prompts\
│   ├── frontend\dist\
│   ├── models\bge-base-zh\
│   ├── data\indices\法律\qdrant\ + manifest.json
│   ├── data\indices\法律\chunk_v2_intermediate\chunks.jsonl   # 可选
│   └── data\{logs,uploads,agent_workspace,contracts,keys}      # 空目录
└── wheelhouse\            # 依赖 wheels（方案A）
```

## 六、待用户确认的决策

1. 目标机是否允许安装 Python 运行时？（推荐 A）
2. 是否打包 bge-base-zh 模型？（推荐是，391M）
3. 是否打包重建索引中间产物？（推荐是，98M）
4. 安装包是否包含源码？（默认不含）
