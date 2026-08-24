"""运行时计费追踪器。

提供 Engine 级别的会话计费追踪，从 API response 提取真实 usage。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

from utils.models import (
    PRICING,
    ModelPricing,
    SessionCost,
    UsageRecord,
    get_pricing,
)

logger = logging.getLogger(__name__)


class CostTracker:
    """运行时计费追踪器，线程安全。

    用法:
        tracker = CostTracker()
        tracker.record(usage=response.usage, model="deepseek-v4-flash")

        # 查看会话汇总
        summary = tracker.session_summary()
        print(tracker.format_report())

        # 开启新会话（归零会话计数器，保留全量累计）
        tracker.new_session()
    """

    def __init__(self):
        self._lock = threading.Lock()

        # 会话级累计
        self._session = SessionCost()

        # 全量累计（不受 new_session 影响）
        self._total = SessionCost()

    # ── 公共方法 ───────────────────────────────────────────────

    def record(
        self,
        usage,
        model: str,
        latency_ms: Optional[float] = None,
    ) -> Optional[UsageRecord]:
        """记录一次 LLM 调用的 usage。

        Args:
            usage: OpenAI 兼容 response.usage 对象
            model: 模型名称
            latency_ms: 调用延迟（毫秒），可选

        Returns:
            UsageRecord 或 None（usage 无效时）
        """
        if usage is None:
            logger.warning("record() 收到 None usage，跳过")
            return None

        # 提取 usage 字段
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or (
            prompt_tokens + completion_tokens
        )

        cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
        cache_miss = getattr(usage, "prompt_cache_miss_tokens", None)

        # 计算费用
        cost = self._calculate_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_hit=cache_hit,
            cache_miss=cache_miss,
        )

        record = UsageRecord(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
            cost=cost,
            latency_ms=latency_ms,
        )

        with self._lock:
            self._add_to_session(self._session, record)
            self._add_to_session(self._total, record)

        logger.info(
            "CostTracker: model=%s, prompt=%d, completion=%d, cost=¥%.4f",
            model, prompt_tokens, completion_tokens, cost,
        )

        return record

    def session_summary(self) -> SessionCost:
        """返回当前会话的累计统计。"""
        with self._lock:
            return self._copy_session(self._session)

    def total_summary(self) -> SessionCost:
        """返回全量累计统计（不受 new_session 影响）。"""
        with self._lock:
            return self._copy_session(self._total)

    def new_session(self) -> None:
        """开启新会话，归零会话计数器。全量累计保持不变。"""
        with self._lock:
            self._session = SessionCost()
        logger.info("CostTracker: 新会话已开启")

    def format_report(self, session_only: bool = True) -> str:
        """生成人类可读的计费报告。

        Args:
            session_only: True 仅当前会话，False 包含全量累计
        """
        s = self.session_summary()
        lines = [
            "=" * 50,
            "Token 计费报告",
            "=" * 50,
            f"  调用次数:        {s.call_count}",
            f"  输入 tokens:     {s.total_input:,}",
            f"  输出 tokens:     {s.total_output:,}",
        ]
        if s.total_cache_hit > 0:
            lines.append(f"  缓存命中 tokens:  {s.total_cache_hit:,}")
        if s.total_cache_miss > 0:
            lines.append(f"  缓存未命中 tokens: {s.total_cache_miss:,}")
        lines.append(f"  总费用:          ¥{s.total_cost:.4f}")

        if not session_only:
            t = self.total_summary()
            lines.extend([
                f"",
                f"全量累计:",
                f"  调用次数:        {t.call_count}",
                f"  总费用:          ¥{t.total_cost:.4f}",
            ])

        lines.append("=" * 50)
        return "\n".join(lines)

    # ── 内部方法 ───────────────────────────────────────────────

    def _calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_hit: Optional[int],
        cache_miss: Optional[int],
    ) -> float:
        """计算单次调用的费用。"""
        pricing = get_pricing(model)
        if pricing is None:
            logger.warning(
                "未知模型 '%s'，费用计为 0。已知模型: %s",
                model, list(PRICING.keys()),
            )
            return 0.0

        # 输入费用：区分缓存命中/未命中
        if cache_hit is not None and cache_miss is not None:
            # API 明确返回了缓存分布
            cost = (
                cache_hit / 1_000_000 * pricing.input_price_cache_hit
                + cache_miss / 1_000_000 * pricing.input_price_cache_miss
            )
        else:
            # 默认按缓存未命中计费（保守估计）
            cost = prompt_tokens / 1_000_000 * pricing.input_price_cache_miss

        # 输出费用
        cost += completion_tokens / 1_000_000 * pricing.output_price

        return cost

    @staticmethod
    def _add_to_session(session: SessionCost, record: UsageRecord) -> None:
        """向 SessionCost 累加一条记录。"""
        session.records.append(record)
        session.total_input += record.prompt_tokens
        session.total_output += record.completion_tokens
        session.total_cache_hit += record.cache_hit_tokens or 0
        session.total_cache_miss += record.cache_miss_tokens or 0
        session.total_cost += record.cost

    @staticmethod
    def _copy_session(session: SessionCost) -> SessionCost:
        """浅拷贝 SessionCost（records 列表共享，数值原样）。"""
        return SessionCost(
            records=list(session.records),
            total_input=session.total_input,
            total_output=session.total_output,
            total_cache_hit=session.total_cache_hit,
            total_cache_miss=session.total_cache_miss,
            total_cost=session.total_cost,
        )
