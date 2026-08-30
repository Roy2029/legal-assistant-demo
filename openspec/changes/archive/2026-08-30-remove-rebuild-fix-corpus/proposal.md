## Why

"重建知识库"手动重建功能是一个**失效且危险**的入口：它从未真正跑通过（无状态文件、无新备份、无日志请求），且若跑起来会把整个 qdrant 目录挪走、只重建公共库——**清空用户知识库**。同时，公共法律库的 chunk 缺失 `corpus="public"` 元数据，导致一切 `__public__` 作用域检索（知识库问答选"公共法律库"、agent 的 `folders:["__public__"]` 检索）都返回 0，表现为"公共法律库挂了"。

## What Changes

- **BREAKING** 删除"重建法律库"前端按钮、相关状态与轮询逻辑（`KbManagePage.jsx`、`App.jsx`）。
- **BREAKING** 删除后端 `/api/kb/rebuild` 与 `/api/kb/rebuild/status` 两个端点，及其唯一的 `svc.close()` 调用。
- **BREAKING** 删除重建脚本：`run_rebuild_managed.py`、`rebuild_index_from_intermediate.py`、`rebuild_index_v2.py`。
- 修正 `update_api.py` 中引用已删除脚本的提示文案。
- 修复公共库 corpus 检索 bug：`__public__` / `corpus_scope="public"` 作用域从仅匹配 `corpus=="public"` 改为匹配 `corpus=="public"` **或** `corpus` 字段缺失的历史公共 chunk，使公共法律库重新可检索。

## Capabilities

### New Capabilities

- `kb-rebuild-removal`: 移除"重建法律库"手动重建功能——前端入口、后端端点、重建脚本全部删除，更新提示文案。
- `public-corpus-retrieval`: 公共库 corpus 作用域检索修复——`__public__` / `corpus_scope="public"` 能正确召回缺失 `corpus` 字段的历史公共 chunk，同时不泄漏用户库/案例库内容。

### Modified Capabilities

（无既有 spec，openspec/specs 当前为空）

## Impact

- `frontend/src/KbManagePage.jsx`：删除重建按钮、`rebuilding`/`rebuildMsg` 状态、`rebuildKb()` 及状态轮询。
- `frontend/src/App.jsx`：同上（若该处重建代码仍存活）。
- `server/kb_api.py`：删除 `/api/kb/rebuild`、`/api/kb/rebuild/status` 端点及 `svc.close()` 调用。
- `server/update_api.py`：更新 `/api/update/run` 桩中引用已删脚本的提示。
- `scripts/run_rebuild_managed.py`、`scripts/rebuild_index_from_intermediate.py`、`scripts/rebuild_index_v2.py`：删除。
- `online_core/retrieval_service.py`：`build_kb_filters()` 公共库作用域过滤逻辑修复（`folders=["__public__"]` 与 `corpus_scope="public"` 两条路径）。
- 检索链路（`server/chat_api.py`、`server/assistant_api.py`、`online_core/agents/rag_agent.py`）：无需改动，过滤修复在 `build_kb_filters` 统一生效。
