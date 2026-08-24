"""Router V2 — 查询路由器，用作 PlannerLLM。

V2 相对于 V1 (query_router.py) 的核心改进：
1. **预算意识 Prompt** ── 告知 LLM 检索池会合并去重后截断，无需手动压低 top_k
2. **SSQ top_k=20** ── 基于 TopK 实验的 elbow 点

注：选择性激活逻辑已移除，激活判定由外部的 PlannerEstimator 管理。

架构关系：
  LegalPreFilter → PlannerEstimator → QueryRouterV2 (LLM 路由)
  → StrategyDispatcher (策略编排，不变) → Collector → flat
"""

import json
import logging
import time
from typing import Optional

from online_core.data_model import (
    FilterResult,
    RouteDecision,
    SubQuery,
    SubSubQuery,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Router V2 System Prompt
# ══════════════════════════════════════════════════════════════════════════════

ROUTER_V2_SYSTEM_PROMPT = """你是一个 RAG 系统的智能查询路由器。你的任务是对用户查询进行分析，输出结构化的 JSON 路由决策。

## 输入格式
你接收：
1. 用户原始查询文本
2. PreFilter 的初步判断（如果有）

## 输出格式
你必须输出如下 JSON 结构（不要包含其他内容）：

```json
{
    "norm_issue": [],
    "norm_process": [],
    "reasoning": "",
    "difficulty": "medium",
    "query_type": ["factoid"],
    "rewrite_result": [],
    "subquerys": [
        {
            "subquery": "",
            "subsubquerys": [
                {
                    "subsubquery": "",
                    "strategy": "simple",
                    "transform": "none",
                    "top_k": 20,
                    "filters": null
                }
            ]
        }
    ]
}
```

## 字段说明

### difficulty (str)
查询的整体难度级别，决定精排阶段保留多少条上下文，可选值：
- "simple": 简单查询，事实性/定义/实体属性/关系类 → 精排保留 5 条
- "medium": 中等复杂度，对比/聚合/条件过滤/流程步骤类 → 精排保留 8 条
- "hard": 困难查询，多跳推理类 → 精排保留 10 条

难度判定规则：
- 如果 query_type 全部是简单类型（factoid/definition/entity-attribute/relation）→ difficulty="simple"
- 如果 query_type 包含中等类型（comparison/aggregation/constraint/procedural）→ difficulty="medium"
- 如果 query_type 包含 "multi-hop" → difficulty="hard"
- 如果跨类别，取最高难度

### norm_issue (list[str])
识别到的规范性问题类型，可选值：
- "ambiguous": 查询有歧义
- "noisy": 包含无关信息
- "multi-intent": 包含多个意图
- "unclear-intent": 意图不明确

### norm_process (list[str])
应用的处理方法，可选值：
- "rewrite": 需要进行查询改写
- "decompose": 需要分解为子查询
- "hyde": 需要生成 HyDE 假设答案

### query_type (list[str])
查询类型分类，可选值（可多选）：
- "factoid": 事实性问答
- "definition": 定义解释
- "entity-attribute": 实体属性查询
- "relation": 关系查询
- "comparison": 对比分析
- "aggregation": 聚合统计
- "constraint": 带约束条件的查询（含过滤条件）
- "procedural": 流程步骤查询
- "multi-hop": 多跳推理

### subquerys (list)
分解后的子查询列表。简单查询通常只有一个 subquery。
每个 subquery 包含：
- subquery (str): 语义独立的分查询文本
- subsubquerys (list): 该分查询下的检索单元列表

每个 subsubquery 包含：
- subsubquery (str): 实际检索文本（可能是改写后的）
- strategy (str): 检索策略 "simple" | "filter" | "hierarchical" | "parent-child"
- transform (str): 变换方式 "none" | "hyde" | "step-back" | "decompose"
- top_k (int): 返回结果数量（默认 20，检索系统会合并去重后截断到全局预算，无需手动压低）
- filters (dict | null): strategy="filter" 时的过滤条件

## 策略选择指南
- "factoid"/"definition" → strategy="simple"
- "constraint" → strategy="filter"（需提供 filters）
- "procedural" → strategy="hierarchical"
- "comparison" → 需要分解为多个 subquerys
- "multi-hop" → strategy="simple" + transform="hyde"

## 重要提示
1. 如果查询可以在 KB 中直接通过关键词匹配解决，使用 strategy="simple"
2. 如果查询有明确的过滤条件（如文档类型、时间范围等），使用 strategy="filter" 并填充 filters
3. 如果查询涉及法律条文、法规章节等结构化的文档，考虑使用 strategy="hierarchical"
4. 对于复杂查询，合理分解为多个 subquery，每个 subquery 再分解为多个 subsubquery
5. 不要过度分解。简单查询只输出 1 个 subquery + 1 个 subsubquery
6. 所有 subsubquery 的结果会合并去重后统一截断到全局预算（30 条），单个 top_k=20 即足够覆盖每路检索的 elbow 点，无需因担心总预算而压低该值。分解越多 → 覆盖角度越广→ 更有机会覆盖 Hybrid 单角度遗漏的 chunk。"""


# ══════════════════════════════════════════════════════════════════════════════
# Router V2
# ══════════════════════════════════════════════════════════════════════════════

class QueryRouterV2:
    """查询路由器（PlannerLLM）。

    激活判定由外部的 PlannerEstimator 管理，route() 对收到的所有 query
    都执行完整 LLM 路由判断。

    用法（与现有接口兼容）：
        router = QueryRouterV2(llm)
        decision = router.route(query, filter_result)
    """

    def __init__(self, llm, fallback_llm=None,
                 max_retries: int = 3,
                 retry_base_delay: float = 2.0,
                 fallback_top_k: int = 20):
        """
        Args:
            llm: 主 LLM 实例，需要有 generate(messages) 方法
            fallback_llm: 备用 LLM 实例（主 LLM 全部重试失败后使用）
            max_retries: LLM 调用最大重试次数（默认 3）
            retry_base_delay: 重试基础等待秒数（指数退避: delay × 2^attempt）
            fallback_top_k: 未激活时的检索深度（默认 20）
        """
        self.llm = llm
        self.fallback_llm = fallback_llm
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.fallback_top_k = fallback_top_k

    # ── 主入口 ──────────────────────────────────────────────────

    def route(self, query: str,
              filter_result: Optional[FilterResult] = None) -> RouteDecision:
        """路由判断。

        所有 query 都走完整 LLM 路由，激活判定由外部的 PlannerEstimator 负责。
        如果 prefilter 已拦截（need_rag=False），直接根据 filter_result 构建决策。

        Args:
            query: 用户原始查询
            filter_result: PreFilter 的输出（可选）

        Returns:
            RouteDecision: 路由决策
        """
        if filter_result and not filter_result.need_rag:
            return self._build_from_filter(query, filter_result)

        return self._llm_route(query, filter_result)

    # ── LLM 路由（仅在激活时调用） ─────────────────────────────

    def _llm_route(self, query: str,
                   filter_result: Optional[FilterResult] = None) -> RouteDecision:
        """LLM 驱动的路由判断。

        使用 V2 Prompt（top_k=20 默认 + 预算意识）。
        """
        system_msg = ROUTER_V2_SYSTEM_PROMPT
        user_content = f"查询: {query}"

        if filter_result:
            context_parts = ["PreFilter 初步判断:"]
            if filter_result.matched_pattern:
                context_parts.append(f"- 匹配模式: {filter_result.matched_pattern}")
            if filter_result.suggested_strategy:
                context_parts.append(f"- 建议策略: {filter_result.suggested_strategy}")
            if filter_result.multi_intent:
                context_parts.append("- 标记多意图")
            if filter_result.compare:
                context_parts.append("- 标记对比")
            if filter_result.kb_overlap > 0:
                context_parts.append(f"- KB 词表重叠率: {filter_result.kb_overlap:.2f}")
            context_parts.append("")
            context_parts.append("请根据以上信息辅助判断，但以你的分析为准。")
            user_content = "\n".join(context_parts) + f"\n\n查询: {query}"

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_content},
        ]

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.llm.generate(messages, response_format={"type": "json_object"})
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                data = json.loads(text)
                decision = self._parse_route_decision(query, data)
                if attempt > 0:
                    logger.info("Router V2 LLM 第 %d 次重试成功", attempt)
                return decision
            except json.JSONDecodeError as e:
                last_error = e
                logger.warning("Router V2 JSON 解析失败 (attempt %d/%d): %s",
                              attempt + 1, self.max_retries + 1, e)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_retryable = any(kw in error_str for kw in (
                    "timeout", "rate", "overload", "capacity",
                    "503", "502", "429", "connection", "reset",
                ))
                if not is_retryable:
                    logger.warning("Router V2 不可重试错误 (attempt %d): %s",
                                  attempt + 1, e)
                    break
                logger.warning("Router V2 可重试错误 (attempt %d/%d): %s",
                              attempt + 1, self.max_retries + 1, e)

            if attempt < self.max_retries:
                delay = self.retry_base_delay * (2 ** attempt)
                logger.info("Router V2 等待 %.1fs 后重试...", delay)
                time.sleep(delay)

        # 主 LLM 全部失败 → 尝试 fallback LLM
        if self.fallback_llm is not None:
            logger.warning(
                "Router V2 主 LLM 全部 %d 次尝试失败 (%s)，切换到 fallback LLM",
                self.max_retries + 1, last_error,
            )
            fb_decision = self._try_fallback_llm(query, filter_result)
            if fb_decision is not None:
                return fb_decision

        logger.warning("Router V2 LLM 全部 %d 次尝试失败 (%s)，走 fallback",
                      self.max_retries + 1, last_error)
        return RouteDecision.fallback(query, top_k=20)

    def _try_fallback_llm(self, query: str,
                          filter_result: Optional[FilterResult] = None
                          ) -> Optional[RouteDecision]:
        """尝试用 fallback LLM 路由（单次调用，不重试）。"""
        try:
            system_msg = ROUTER_V2_SYSTEM_PROMPT
            user_content = f"查询: {query}"
            if filter_result:
                user_content = (
                    f"PreFilter 判断: 模式={filter_result.matched_pattern or '无'}\n\n"
                    f"查询: {query}"
                )
            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_content},
            ]
            resp = self.fallback_llm.generate(
                messages, response_format={"type": "json_object"})
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(text)
            decision = self._parse_route_decision(query, data)
            logger.info("Router V2 Fallback LLM 调用成功")
            return decision
        except Exception as e:
            logger.warning("Router V2 Fallback LLM 也失败: %s", e)
            return None

    # ── 辅助方法 ───────────────────────────────────────────────

    @staticmethod
    def _build_from_filter(query: str,
                           filter_result: FilterResult) -> RouteDecision:
        """当 PreFilter 已确定策略时，直接构建 RouteDecision（免 LLM 调用）。"""
        strategy = filter_result.suggested_strategy or "simple"
        if filter_result.matched_pattern in ("article_law", "definition"):
            difficulty = "simple"
        elif filter_result.matched_pattern == "comparison":
            difficulty = "medium"
        else:
            difficulty = "simple"

        return RouteDecision(
            origin_query=query,
            need_rag=filter_result.need_rag,
            difficulty=difficulty,
            reasoning=f"PreFilter 规则匹配: {filter_result.matched_pattern or '规则'}",
            query_type=["factoid"],
            subquerys=[
                SubQuery(
                    subquery_id=0,
                    subquery=query,
                    subsubquerys=[
                        SubSubQuery(
                            subsubquery_id=0,
                            subsubquery=query,
                            strategy=strategy,
                            top_k=20,
                        )
                    ],
                )
            ],
        )

    @staticmethod
    def _parse_route_decision(origin_query: str, data: dict) -> RouteDecision:
        """将 LLM 输出的 dict 转为 RouteDecision 对象。"""
        subquerys = []
        for sq_data in data.get("subquerys", []):
            subsubquerys = []
            for ssq_data in sq_data.get("subsubquerys", []):
                subsubquerys.append(SubSubQuery(
                    subsubquery_id=ssq_data.get("subsubquery_id", 0),
                    subsubquery=ssq_data.get("subsubquery", ""),
                    strategy=ssq_data.get("strategy", "simple"),
                    top_k=ssq_data.get("top_k", 20),
                    transform=ssq_data.get("transform", "none"),
                    filters=ssq_data.get("filters"),
                ))
            subquerys.append(SubQuery(
                subquery_id=sq_data.get("subquery_id", 0),
                subquery=sq_data.get("subquery", ""),
                subsubquerys=subsubquerys,
            ))

        return RouteDecision(
            origin_query=origin_query,
            need_rag=True,
            difficulty=data.get("difficulty", "medium"),
            norm_issue=data.get("norm_issue", []),
            norm_process=data.get("norm_process", []),
            reasoning=data.get("reasoning", ""),
            query_type=data.get("query_type", ["factoid"]),
            rewrite_result=data.get("rewrite_result", []),
            subquerys=subquerys,
        )
