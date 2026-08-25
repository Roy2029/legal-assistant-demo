# Changelog

## [Unreleased] - M0 能跑 开发中

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
