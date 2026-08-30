## Context

当前索引（`data/indices/法律/qdrant`，08-27 构建）共 17789 个 point：17598 个公共法律 chunk **没有任何 `corpus` 字段**（重建脚本 `rebuild_index_from_intermediate.py` 的 `to_chunk()` 只写 `law_name/article_no/articles/doc_type`），191 个用户 chunk 带 `corpus="user"`。

检索层 `online_core/retrieval_service.py:build_kb_filters()` 对 `__public__` 作用域（`folders=["__public__"]` 或 `corpus_scope="public"`）施加 `metadata.corpus == "public"` 过滤，但公共 chunk 没有该字段 → **匹配 0 条** → 知识库问答选"公共法律库"、agent 的 `folders:["__public__"]` 检索全部返回空，表现为"公共法律库挂了"。

"重建法律库"按钮链路（前端按钮 → `/api/kb/rebuild` → `run_rebuild_managed.py` → `rebuild_index_from_intermediate.py`）失效且危险：从未真正跑通（无 `rebuild.status.json`、无新备份、后端日志无请求），且跑起来会 `shutil.move` 挪走整个 qdrant 目录、只重建公共库，**清空用户知识库**。

## Goals / Non-Goals

**Goals:**
- 移除"重建法律库"功能的全部前端/后端/脚本实现。
- 修复 `__public__` / `corpus_scope="public"` 作用域检索，使其能召回缺失 `corpus` 字段的历史公共 chunk，公共法律库恢复可检索。
- 修复不泄漏用户库/案例库内容，不依赖停后端或改索引数据。

**Non-Goals:**
- 不为历史公共 chunk 做数据迁移补 `corpus="public"`（需停后端/动索引；过滤容错已足够且更健壮）。
- 不恢复/新增任何重建能力（增量更新本就是 M0 未实现）。
- 不改动用户知识库与案例检索的过滤逻辑。
- 不删除 `RetrievalService.close()` 方法本身（仅移除已删端点中的调用）。

## Decisions

### 1. 过滤容错（`corpus=="public"` OR 缺失）而非数据迁移

公共作用域条件由单一 `{"key":"metadata.corpus","match":{"value":"public"}}` 改为：

```python
{"should": [
    {"key": "metadata.corpus", "match": {"value": "public"}},
    {"is_empty": {"key": "metadata.corpus"}},
]}
```

- **为什么选它**：嵌入式 Qdrant 被运行中后端持有，数据迁移需停服或新增端点；过滤容错零停机、立即生效，且对任何遗漏 `corpus` 的历史/未来 chunk 都健壮。
- **`is_empty` 形态（实施中实测修正）**：Qdrant 的 `IsEmptyCondition` 是独立条件类型，语法为 `{"is_empty": {"key": "..."}}`，**不是** FieldCondition 的字段（`{"key": "...", "is_empty": {}}` 会校验失败）。`is_empty` 命中字段缺失、null 或空数组的 chunk，正覆盖历史公共 chunk（无 `corpus` 字段）。嵌套 `Filter`（`{"should": [...]}`）作为 `must`/`should` 元素经真实嵌入式 Qdrant 验证可用（pydantic 2.11 + qdrant-client，`Condition` union 含 `Filter`）。
- **已验证可行性**：`build_kb_filters` 返回值直接 `_models.Filter(**query.filters)`（`offline_core/store.py:512`）；探针脚本对 6 种形态逐一实跑，`{"must": [{"should": [...]}]}`、`{"should": [{"should": [...]}, ...]}` 均验证通过并正确召回缺失 corpus 的公共 chunk。
- **备选**：①数据迁移脚本补 `corpus="public"`——被否（停后端、动索引、易与删除重建功能的目标冲突）；②`must_not corpus in [user, case]`——可工作但会向公共作用域泄漏未来出现的未知 corpus 值，且不满足 spec 的"`corpus=="public"` 或缺失"语义；③只匹配缺失字段、不等同 `public`——被否（未来正确打标的 chunk 会漏）。

### 2. 单一 helper，三处复用

定义模块级 `_corpus_public_condition()`，替换 `build_kb_filters` 中三处公共作用域条件：
- `folders=["__public__"]` 且仅公共（must 分支，第 82 行）
- `folders=["__public__"] + 用户文件夹`（base_should 分支，第 72 行）
- `corpus_scope="public"`（must 分支，第 92 行）

一处改动，`/api/chat`（`corpus_scope="public"`）与 agent（`folders=["__public__"]`）两条链路同时修复。

### 3. 删除重建功能面

- 前端：`KbManagePage.jsx` 与 `App.jsx` 中 `rebuildKb()`、`rebuilding`/`rebuildMsg` 状态、状态轮询、重建按钮全部移除。
- 后端：删除 `server/kb_api.py` 的 `/api/kb/rebuild` 与 `/api/kb/rebuild/status` 端点及其 `svc.close()` 调用（该调用是 `close()` 唯一线上调用点）。
- 脚本：删除 `run_rebuild_managed.py`、`rebuild_index_from_intermediate.py`、`rebuild_index_v2.py`。
- 文案：`server/update_api.py` 的 `/api/update/run` 桩提示不再引用已删的 `rebuild_index_v2.py`。

### 4. 保留 `RetrievalService.close()` 方法

删除端点后该方法无线上调用，但保留方法体（测试可能复用）；仅删调用点。

## Risks / Trade-offs

- **嵌套过滤不被嵌入式 Qdrant 版本支持** → 已实跑验证：嵌套 `Filter` 可用；真正的坑是 `is_empty` 必须用独立条件形态 `{"is_empty": {"key": "..."}}`（FieldCondition 字段形态会校验失败，已在实现中修正为正确形态）。
- **删除 `App.jsx` 重建代码留下死引用/未用 import** → 删除时同步清理相关 state/import，前端构建验证。
- **`update_api.py` 之外是否还有其他对已删脚本的引用** → 实现前全局 grep `rebuild_index`/`run_rebuild` 确认无遗漏。
- **过滤修复后 agent 可能不再回退，依赖 `__public__` 检索本身可用** → 这正是修复目标；公共库检索恢复后 agent 首轮即命中，行为更直接。

## Migration Plan

- 纯代码变更，不动索引数据与运行中进程；部署即生效。
- 回滚：`git revert` 变更提交即可（删除的文件由 revert 恢复）。
- 无数据迁移步骤。

## Open Questions

无阻塞问题。已确认：过滤语法可行、删除面完整、用户库/案例库过滤不受影响。
