# Changelog

## [Unreleased] - M0 能跑 开发中

## W3 关闭 - 知识库问答闭环（2026-08-26）
- PreFilter 保守模式（只拦明显闲聊，宁放勿杀）
- Trace 面板展示 dense/BM25 各自召回 chunk 列表（可折叠展开）与 BM25 查询分词
- 全部单元测试 19/19 通过
- 已知 M0 剩余：引用卡片点击定位原文（列入待开发清单）

### Fixed
- 精确法条号检索 0 召回（第32条）：根因是命中 chunk 以第13条开头、目标条文在中后部，LLM 未找到；改为 exact_match 时按目标条文截取上下文片段
- reranker 在 GTX 1650 上极慢（580s/批）：M0 默认关闭 rerank，exact_match 跳过精排；语义检索 10 分钟 → 3 秒
- 一键启动脚本闪退：移至根目录、全英文、空标题 start /B、ping 延时、pause 停留

### Added (W3)
- /api/chat SSE 流式问答（retrieval→generation→citation_check→final）
- 引用校验器（打回 1 次 + 追加未验证提示；真实法条/虚构法条测试通过）
- LLM 客户端（OpenAI 兼容，.env 读取 LLM_API_KEY/LLM_BASE_URL）
- React 前端骨架：知识库问答（流式 + Trace 面板）、设置页（LLM 热更新 + 词典管理）
- 一键启动脚本 scripts/start_all.bat / start_all.py / stop_all.bat
- 真实 LLM 链路冒烟验证通过（民法典第580条 → 回答+引用+免责声明）

## W2 关闭 - 检索服务适配（2026-08-25）
- 索引重建完成：451 份法规，4,052 parents / 18,058 children（chunker_v2 目标策略）
- 精确法条号检索验证通过（民法典第580条命中 3 条含 580 的 chunk）
- 引用校验器验证通过（真实法条 verified，虚构法条 unverifiable）
- 集成测试全部通过（tests/test_integration_core.py）
- 旧 qrels 评估跳过（新 qrels 由用户后续重建）
### Added (W1)
- 项目仓库骨架（server/ frontend/ skills/ tools/ tests/ scripts/ config/）
- 迁入 RAG1.0 代码基线（offline_core/online_core/evaluation/utils/prompts）
- FastAPI 骨架（/health /api/config）+ SQLite 11 张表
- 开发启动器 scripts/start_dev.py
- 索引 Gap 分析报告（结论：必须重建）
### Added (W2)
- chunker_v2：D01 目标分块器（节最小单位/首部保留/均分/父子索引）
- query_parser：规则+法规名词典（否定排除/多候选）
- difficulty：难度分档规则版
- lexicon_service：查询期用户词典
- retrieval_service：统一检索服务（解析→难度→词典→混合检索→rerank）
- rebuild_index_v2：GPU 重建脚本 + doc-level 评估脚本
### Fixed
- transformers 5.x 与 sentence-transformers 5.5 不兼容（降级 4.57.6）
- chunker_v2 递归死循环（深度保护+切不动硬切）
- article_no 文档级提取错误（改为 chunk 级提取）
- Qdrant filter 路径（metadata 嵌套字段）
