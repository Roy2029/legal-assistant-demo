"""Online Core — 在线路由与检索组件。

实际 live 模块：retrieval_service / search_orchestrator / reranker / citation_checker /
query_parser / difficulty / lexicon_service / contract_rules / legal_mask / legal_mask_config /
data_model，以及 agents/ 子代理。历史死代码（engine/query_router/strategy_dispatcher 等）已删除，
不再做多余导出，调用方直接 import 具体子模块。
"""
