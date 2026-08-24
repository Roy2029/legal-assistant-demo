"""Token 计费相关数据模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ModelPricing:
    """模型价格配置 (¥ / 百万 tokens)。

    Attributes:
        model: 模型标识符
        input_price_cache_hit: 输入缓存命中价格
        input_price_cache_miss: 输入缓存未命中价格
        output_price: 输出价格
        context_window: 最大上下文窗口 (tokens)
    """

    model: str
    input_price_cache_hit: float
    input_price_cache_miss: float
    output_price: float
    context_window: int = 128_000


# ── DeepSeek-V4 内置价格表 ─────────────────────────────────────────
# 数据来源: docs/designs/FastAPI前后端.md

PRICING: dict[str, ModelPricing] = {
    "deepseek-v4-flash": ModelPricing(
        model="deepseek-v4-flash",
        input_price_cache_hit=0.02,
        input_price_cache_miss=1.0,
        output_price=2.0,
        context_window=128_000,
    ),
    "deepseek-v4-pro": ModelPricing(
        model="deepseek-v4-pro",
        input_price_cache_hit=0.025,
        input_price_cache_miss=3.0,
        output_price=6.0,
        context_window=128_000,
    ),
}


def get_pricing(model: str) -> Optional[ModelPricing]:
    """根据模型名获取价格配置。

    Args:
        model: 模型标识符 (如 "deepseek-v4-flash")

    Returns:
        ModelPricing 或 None (模型不在价格表中)
    """
    return PRICING.get(model)


@dataclass
class UsageRecord:
    """单次 LLM 调用记录。

    Attributes:
        model: 模型名称
        prompt_tokens: 输入 token 数
        completion_tokens: 输出 token 数
        total_tokens: 总 token 数
        cache_hit_tokens: 缓存命中 token 数 (None 表示 API 未返回)
        cache_miss_tokens: 缓存未命中 token 数 (None 表示 API 未返回)
        cost: 本次调用费用 (元)
        timestamp: 调用时间
        latency_ms: 调用延迟 (毫秒)
    """

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float
    timestamp: datetime = field(default_factory=datetime.now)
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    latency_ms: Optional[float] = None


@dataclass
class SessionCost:
    """会话累计计费。

    Attributes:
        records: 本会话所有调用记录
        total_input: 累计输入 token
        total_output: 累计输出 token
        total_cache_hit: 累计缓存命中 token
        total_cache_miss: 累计缓存未命中 token
        total_cost: 累计费用 (元)
    """

    records: list[UsageRecord] = field(default_factory=list)
    total_input: int = 0
    total_output: int = 0
    total_cache_hit: int = 0
    total_cache_miss: int = 0
    total_cost: float = 0.0

    @property
    def call_count(self) -> int:
        return len(self.records)


@dataclass
class EstimationResult:
    """批量文档 Token 预评估结果。

    Attributes:
        total_tokens: 预估总 token 数
        model: 目标模型
        context_window: 模型上下文窗口大小
        context_usage_pct: 上下文窗口占用百分比
        exceeds_window: 是否超出上下文窗口
        estimated_cost_input: 预估输入费用 (元)
        estimated_cost_output: 预估输出费用 (元，基于预估输出比例)
        estimated_cost_total: 预估总费用 (元)
        estimation_strategy: 使用的估算策略 ('char' | 'deepseek')
        breakdown: 各级 token 明细
    """

    total_tokens: int
    model: str
    context_window: int
    context_usage_pct: float
    exceeds_window: bool
    estimated_cost_input: float
    estimated_cost_output: float
    estimated_cost_total: float
    estimation_strategy: str
    breakdown: dict = field(default_factory=dict)
