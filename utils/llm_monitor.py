"""LLM 调用监控层。

从 OpenAI 兼容 API response 中提取 usage，兼容同步和流式模式。
"""

from __future__ import annotations

import logging
from typing import Optional

from utils.cost_tracker import CostTracker
from utils.token_estimator import TokenEstimator

logger = logging.getLogger(__name__)


def extract_usage(response) -> Optional[dict]:
    """从 OpenAI 兼容 API response 中提取 usage 信息。

    兼容:
        - 同步 response (response.usage)
        - 流式 chunk (chunk.usage)

    Args:
        response: OpenAI chat.completions.create() 的返回值或流式 chunk

    Returns:
        dict with keys: prompt_tokens, completion_tokens, total_tokens,
                        prompt_cache_hit_tokens, prompt_cache_miss_tokens
        如果 response 不含 usage 则返回 None
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        "prompt_cache_hit_tokens": getattr(usage, "prompt_cache_hit_tokens", None),
        "prompt_cache_miss_tokens": getattr(usage, "prompt_cache_miss_tokens", None),
    }


# 一个简单的 Usage 包装类，使 extract_usage 的 dict 输出也能传给 CostTracker.record()
class _UsageWrapper:
    """将 dict 包装为具有属性访问的 usage 对象。"""

    def __init__(self, data: dict):
        self.prompt_tokens = data.get("prompt_tokens", 0)
        self.completion_tokens = data.get("completion_tokens", 0)
        self.total_tokens = data.get("total_tokens", 0)
        self.prompt_cache_hit_tokens = data.get("prompt_cache_hit_tokens")
        self.prompt_cache_miss_tokens = data.get("prompt_cache_miss_tokens")


def record_usage_from_response(
    response,
    model: str,
    cost_tracker: CostTracker,
    latency_ms: Optional[float] = None,
) -> bool:
    """从 response 提取 usage 并记录到 CostTracker。

    Args:
        response: API response 对象
        model: 模型名称
        cost_tracker: CostTracker 实例
        latency_ms: 延迟

    Returns:
        True 如果成功记录，False 如果 response 不含 usage
    """
    data = extract_usage(response)
    if data is None:
        logger.warning("API response 不含 usage 信息，跳过记录")
        return False

    wrapped = _UsageWrapper(data)
    cost_tracker.record(usage=wrapped, model=model, latency_ms=latency_ms)
    return True


def estimate_usage_from_messages(
    messages: list[dict],
    output_token_count: int,
    model: str,
    cost_tracker: CostTracker,
) -> None:
    """流式 fallback: 用 TokenEstimator 估算 input + 实际 output token 计数。

    当 API 流式响应不支持 usage chunk 时使用。

    Args:
        messages: 发送给 LLM 的消息列表
        output_token_count: 流式收到的 output token 总数
        model: 模型名称
        cost_tracker: CostTracker 实例
    """
    est = TokenEstimator.fast()
    estimated_input = est.estimate_messages(messages)

    # 构造一个模拟的 usage 对象
    usage = _UsageWrapper({
        "prompt_tokens": estimated_input,
        "completion_tokens": output_token_count,
        "total_tokens": estimated_input + output_token_count,
    })

    logger.warning(
        "流式 usage 不可用，使用估算值: input~%d (估算), output=%d (实际)",
        estimated_input, output_token_count,
    )

    cost_tracker.record(usage=usage, model=model)
