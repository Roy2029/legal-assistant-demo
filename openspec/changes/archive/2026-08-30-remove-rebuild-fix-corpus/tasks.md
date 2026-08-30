## 1. 后端：删除重建端点与脚本

- [x] 1.1 删除 `server/kb_api.py` 的 `/api/kb/rebuild` 与 `/api/kb/rebuild/status` 端点，及其 `svc.close()` 调用
- [x] 1.2 删除 `scripts/run_rebuild_managed.py`、`scripts/rebuild_index_from_intermediate.py`、`scripts/rebuild_index_v2.py`
- [x] 1.3 更新 `server/update_api.py` `/api/update/run` 桩提示，不再引用已删的 `rebuild_index_v2.py`
- [x] 1.4 全局 grep `rebuild_index` / `run_rebuild` / `/api/kb/rebuild`，确认无遗留引用

## 2. 前端：删除重建按钮

- [x] 2.1 删除 `frontend/src/KbManagePage.jsx` 中 `rebuildKb()`、`rebuilding`/`rebuildMsg` 状态、状态轮询与"重建法律库"按钮，清理无用 import
- [x] 2.2 删除 `frontend/src/App.jsx` 中对应的重建代码（若仍存活），清理无用 state/import
- [x] 2.3 前端构建验证（`npm run build` 或 dev 编译通过）

## 3. corpus 检索修复

- [x] 3.1 在 `online_core/retrieval_service.py` 添加 `_corpus_public_condition()` helper（`corpus=="public"` OR `corpus` 缺失）
- [x] 3.2 用 helper 替换 `build_kb_filters` 三处公共作用域过滤条件（`folders=["__public__"]` 仅公共、`folders=["__public__"]+用户文件夹`、`corpus_scope="public"`）
- [x] 3.3 为 `build_kb_filters` 补单元测试：公共作用域匹配缺失 corpus 的 chunk、不含 user/case chunk、用户文件夹作用域不受影响

## 4. 验证

- [x] 4.1 重启后端，实跑 `folders=["__public__"]` 与 `corpus_scope="public"` 检索，确认公共库恢复命中
- [x] 4.2 实跑用户文件夹作用域与 `folders=[]`，确认不受影响
- [x] 4.3 复跑 agent 580 查询（`/api/assistant`，无 folders），确认公共库内容可被检索到
