"""在线查询路由模块 — 三层架构：QueryPreFilter → Router LLM → StrategyDispatcher。

QueryPreFilter（规则层）：
  零 LLM 开销的快速过滤器，覆盖闲聊/KB 无关检测、模式匹配定策略等场景。
  输出 FilterResult，need_rag=False 时直接返回，不进入后续流程。

QueryRouter（LLM 层）：
  单次 LLM 调用完成问题分类、策略选择、查询变换、多意图分解。
  输出 RouteDecision，传递给 StrategyDispatcher。
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from online_core.data_model import (
    FilterResult,
    RouteDecision,
    SubQuery,
    SubSubQuery,
)

logger = logging.getLogger(__name__)

# Router 重试配置
ROUTER_MAX_RETRIES = 3
ROUTER_RETRY_BASE_DELAY = 2.0  # 基础等待秒数，指数退避: 2 → 4 → 8

# ── 闲聊 / 社交用语词表 ──────────────────────────────────────

GREETING_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(你好|您好|嗨|hi|hello|hey)\s*$", re.IGNORECASE),
    re.compile(r"^(谢谢|感谢|多谢|thanks|thank you)\s*$", re.IGNORECASE),
    re.compile(r"^(再见|拜拜|bye|goodbye|88)\s*$", re.IGNORECASE),
    re.compile(r"^(好的|好的吧|嗯|嗯嗯|ok|okay|可以|没问题)\s*$", re.IGNORECASE),
    re.compile(r"^(在吗|在不在|are you there)\s*$", re.IGNORECASE),
    re.compile(r"^(你好棒|你真棒|good bot|nice)\s*$", re.IGNORECASE),
]

# ── 模式匹配 → 策略 ───────────────────────────────────────────

STRATEGY_PATTERNS: list[tuple[re.Pattern, str, Optional[str]]] = [
    # (pattern, strategy, matched_pattern_name)
    (re.compile(r"^第[零一二三四五六七八九十百千\d]+[条章节]"), "simple", "article_law"),
    (re.compile(r"(什么是|什么叫|何为|的定义|是指|指)"), "simple", "definition"),
    (re.compile(r"(区别|异同|对比|vs|VS|与.*不同|与.*差异|与.*区别)"), "simple", "comparison"),
    (re.compile(r"(哪些|几种|如何|怎样|多少|有什么)"), "simple", "multi_intent"),
]

# ── 纯无意义检测 ──────────────────────────────────────────────

NONSENSE_PATTERN = re.compile(r"^[\d\s\W]+$")

# ── 默认 KB 词表路径 ─────────────────────────────────────────

DEFAULT_KB_VOCAB_PATH = "index_store/kb_vocab.json"


def load_kb_vocab(path: str = DEFAULT_KB_VOCAB_PATH) -> set[str]:
    """从文件加载 KB 核心词表。"""
    p = Path(path)
    if not p.exists():
        return set()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data)
    except Exception as e:
        logger.warning(f"KB 词表加载失败 ({path}): {e}")
        return set()


# ══════════════════════════════════════════════════════════════════════════════
# QueryPreFilter（规则层）
# ══════════════════════════════════════════════════════════════════════════════


class QueryPreFilter:
    """零 LLM 开销的规则层过滤器。

    优先级（从高到低）：
    1. 空查询检测
    2. 闲聊/社交用语
    3. 纯符号/数字/表情
    4. KB 无关检测
    5. 模式匹配 → 策略
    6. 未匹配 → needs_llm=True
    """

    def __init__(self, kb_vocab_path: Optional[str] = None):
        self.kb_vocab = load_kb_vocab(kb_vocab_path or DEFAULT_KB_VOCAB_PATH)

    def filter(self, query: str) -> FilterResult:
        """执行规则过滤。

        Args:
            query: 用户原始查询

        Returns:
            FilterResult: 过滤结果
        """
        result = FilterResult(origin_query=query)

        if not query or not query.strip():
            result.need_rag = False
            result.skip_reason = "empty_query"
            result.needs_llm = False
            return result

        # 闲聊检测
        for pattern in GREETING_PATTERNS:
            if pattern.match(query.strip()):
                result.need_rag = False
                result.skip_reason = "greeting"
                result.needs_llm = False
                return result

        # 纯无意义检测
        if NONSENSE_PATTERN.match(query.strip()):
            result.need_rag = False
            result.skip_reason = "nonsense"
            result.needs_llm = False
            return result

        # KB 无关检测（词表非空时启用）
        if self.kb_vocab:
            import jieba
            query_tokens = set(jieba.lcut(query))
            if not query_tokens:
                query_tokens = set(query.split())
            overlap = query_tokens & self.kb_vocab
            result.kb_overlap = len(overlap) / max(len(query_tokens), 1)
            if result.kb_overlap == 0.0 and len(query_tokens) > 1:
                # 完全无重叠且非单字查询 → 视为 KB 无关
                result.need_rag = False
                result.skip_reason = "irrelevant"
                result.needs_llm = False
                return result

        # 模式匹配 → 策略
        for pattern, strategy, pattern_name in STRATEGY_PATTERNS:
            if pattern.search(query):
                result.matched_pattern = pattern_name
                result.suggested_strategy = strategy
                if pattern_name == "comparison":
                    result.multi_intent = True
                    result.compare = True
                elif pattern_name == "multi_intent":
                    result.multi_intent = True
                # 对于 article_law 和 definition，直接定策略，不走 LLM
                if pattern_name in ("article_law", "definition"):
                    result.needs_llm = False
                return result

        # 未匹配 → needs_llm=True
        result.needs_llm = True
        return result


# ══════════════════════════════════════════════════════════════════════════════
# Router LLM
# ══════════════════════════════════════════════════════════════════════════════

ROUTER_SYSTEM_PROMPT = """你是一个 RAG 系统的智能查询路由器。你的任务是对用户查询进行分析，输出结构化的 JSON 路由决策。

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
6. 所有 subsubquery 的结果会合并去重后统一截断，单个 top_k=20 即足够，无需因担心总预算而压低该值"""


class QueryRouter:
    """LLM 驱动的查询路由器。

    单次 LLM 调用完成问题分类、策略选择、查询变换、意图分解。
    输出 RouteDecision，传递给 StrategyDispatcher。

    内置指数退避重试：网络错误 / JSON 解析失败 / API 过载时自动重试，
    最多 3 次。全部失败后返回 RouteDecision.fallback()。
    """

    def __init__(self, llm, fallback_llm=None,
                 max_retries: int = ROUTER_MAX_RETRIES,
                 retry_base_delay: float = ROUTER_RETRY_BASE_DELAY):
        """
        Args:
            llm: 主 LLM 实例，需要有 generate(messages) 方法
            fallback_llm: 备用 LLM 实例（主 LLM 全部重试失败后使用）
            max_retries: LLM 调用最大重试次数（默认 3）
            retry_base_delay: 重试基础等待秒数（指数退避: delay × 2^attempt）
        """
        self.llm = llm
        self.fallback_llm = fallback_llm
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    def route(self, query: str, filter_result: Optional[FilterResult] = None) -> RouteDecision:
        """路由判断。

        结合 PreFilter 的判断，输出 RouteDecision。

        Args:
            query: 用户原始查询
            filter_result: PreFilter 的输出（可选）

        Returns:
            RouteDecision: 路由决策
        """
        # 如果 PreFilter 已经确定了不需要 LLM
        if filter_result and not filter_result.needs_llm:
            return self._build_from_filter(query, filter_result)

        # 构造 prompt context
        system_msg = ROUTER_SYSTEM_PROMPT
        user_content = f"查询: {query}"

        if filter_result:
            # 将 PreFilter 的判断作为 context 传递给 LLM
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
                    logger.info("Router LLM 第 %d 次重试成功", attempt)
                return decision
            except json.JSONDecodeError as e:
                last_error = e
                logger.warning("Router JSON 解析失败 (attempt %d/%d): %s",
                             attempt + 1, self.max_retries + 1, e)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                # 判断是否为可重试错误
                is_retryable = any(kw in error_str for kw in (
                    "timeout", "rate", "overload", "capacity",
                    "503", "502", "429", "connection", "reset",
                ))
                if not is_retryable:
                    logger.warning("Router LLM 不可重试错误 (attempt %d): %s",
                                 attempt + 1, e)
                    break
                logger.warning("Router LLM 可重试错误 (attempt %d/%d): %s",
                             attempt + 1, self.max_retries + 1, e)

            if attempt < self.max_retries:
                delay = self.retry_base_delay * (2 ** attempt)
                logger.info("Router 等待 %.1fs 后重试...", delay)
                time.sleep(delay)

        # 主 LLM 全部失败 → 尝试 fallback LLM
        if self.fallback_llm is not None:
            logger.warning(
                "主 LLM 全部 %d 次尝试失败 (%s)，切换到 fallback LLM",
                self.max_retries + 1, last_error,
            )
            fb_decision = self._try_fallback_llm(query, filter_result)
            if fb_decision is not None:
                return fb_decision

        logger.warning("Router LLM 全部 %d 次尝试失败 (%s)，走 fallback",
                      self.max_retries + 1, last_error)
        return RouteDecision.fallback(query)

    def _try_fallback_llm(self, query: str,
                           filter_result: Optional[FilterResult] = None) -> Optional[RouteDecision]:
        """尝试用 fallback LLM 路由（单次调用，不重试）。

        Returns:
            RouteDecision 如果成功，None 如果失败
        """
        try:
            # 构建简化的 messages
            system_msg = ROUTER_SYSTEM_PROMPT
            user_content = f"查询: {query}"
            if filter_result:
                user_content = f"PreFilter 判断: 模式={filter_result.matched_pattern or '无'}\n\n查询: {query}"

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
            logger.info("Fallback LLM 调用成功")
            return decision
        except Exception as e:
            logger.warning("Fallback LLM 也失败: %s", e)
            return None

    def _build_from_filter(self, query: str, filter_result: FilterResult) -> RouteDecision:
        """当 PreFilter 已确定策略时，直接构建 RouteDecision（免 LLM 调用）。"""
        strategy = filter_result.suggested_strategy or "simple"
        # 根据匹配模式推断难度
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
                        )
                    ],
                )
            ],
        )

    def _parse_route_decision(self, origin_query: str, data: dict) -> RouteDecision:
        """将 LLM 输出的 dict 转为 RouteDecision 对象。"""
        subquerys = []
        for sq_data in data.get("subquerys", []):
            subsubquerys = []
            for ssq_data in sq_data.get("subsubquerys", []):
                subsubquerys.append(SubSubQuery(
                    subsubquery_id=ssq_data.get("subsubquery_id", 0),
                    subsubquery=ssq_data.get("subsubquery", ""),
                    strategy=ssq_data.get("strategy", "simple"),
                    top_k=ssq_data.get("top_k", 10),
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
