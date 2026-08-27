# chunker_v2 审查记录（迭代需求 #6）

审查时间：2026-08-27
对象：`offline_core/chunker_v2.py`（LegalStructureChunker）
数据：`data/indices/法律/chunk_v2_intermediate/chunks.jsonl`（471 份法规，4,079 parents + 20,018 children）

## 结论
可用，核心设计（节为最小单位、分点条款携带引导语、短块合并、父子索引）在中间结果中表现符合 D01 预期。以下为需关注的边界问题。

## 统计验证
- children 超长（>512 token）：0
- children 无 article 号：721（3.6%）——主要是标题/目录/序言块，以及少量非条文列表的延续块
- children 命中 GUIDE_RE：589 块携带分点引导语
- chunk_id = sha1(text)[:16]，中间结果 20,018 child → 按 chunk_id 去重 17,598（跨文档重复 2,420）

## 问题与建议

### 1. chunk_id 内容哈希导致跨文档去重/覆盖（重要，M1 需处理）
- 现状：`_make_chunk` 用 `sha1(text)[:16]`，QdrantStore.upsert 用 `abs(hash(chunk_id))` 作 point_id。
- 后果：不同文档中出现完全相同文本时（如修正案重复条文、用户上传与公共库相同条文），后写会覆盖先写的 point，丢失 doc_id/metadata 差异。
- 公共库（单 corpus）影响可控：重复文本向量相同，检索不受影响；但 law_name 标注可能被覆盖。
- 用户库（user corpus）有真实风险：用户上传与公共库相同条文，可能覆盖公共库 chunk，或两个用户之间互相覆盖。
- 建议：M1 将 point_id 改为 `hash((doc_id, chunk_id))`，检索时 `get_chunks_by_ids` 同源修正；或 chunk_id 在入库时加 `doc_id` 盐，但会破坏 qrels_v2 的 chunk_id 引用（评估数据集已按纯文本哈希标注，暂不动）。

### 2. 非条文列表块缺少引导语/法条上下文
- 现状：第一章总则中直接以“（一）（二）”开头的非条文列表（如《危险化学品安全法》职责分工），GUIDE_RE 要求必须有“第X条”引导，识别失败后走 `_split_recursive` 按段落切分，子块文本以“（一）”开头，不含法规名/条号。
- 缓解：heading_path 元数据已保存章节信息，Trace 面板可展示；但 build_context 的 `[来源：法规 第?条]` 对这类 chunk 显示为 `第?条`。
- 建议：M2 对无 article_no 的 chunk，在 build_context 中回退用 heading_path 末级作为来源标注（如 `[来源：法规 第一章总则]`）。

### 3. GUIDE_RE 覆盖的引导句式可扩充
- 现覆盖：有下列情形之一的 / 符合下列条件之一的 / 符合下列情形之一的 / 应当认定 / 按照下列。
- 未覆盖示例：`应当符合下列条件：`、`包括下列内容：`、`分为下列几种：` 等，这类分点条款会退化为递归切分（功能正常，只是不保证分点与引导语同块）。
- 建议：M2 视 badcase 增补句式；保持“有界、到冒号为止”的约束不变。

### 4. _split_by_candidates 的候选切分质量
- 自然段 → 自然句 → 硬切分三级策略合理；`ideal` 基于字符而非 token，与 L_child 的 token 约束存在少量偏差，但最终有 `> L_child*1.2` 的兜底递归，未发现超长块。
- `_hard_split` 优先在中文标点处切分，但对“（一）（二）”数字列表切分可能不均衡；可接受。

### 5. 短块合并仅在同 unit 内
- `_merge_short` 只合并同一节内 split 结果，符合“节为最小结构单位”设计；跨节短块不合并是有意为之（避免跨节语义污染）。
- 单节整体 <= L_child 时不强制拆分，直接作为单 child；D01 设计允许。

## 结论
M0 目标分块器满足验收要求。第 1 条（chunk_id 覆盖）必须在 M1 用户知识库上线前处理；第 2/3 条可在 M2 优化。
