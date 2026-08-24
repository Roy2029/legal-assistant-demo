"""Token 估算引擎。

支持两种策略:
    - char: 字符系数估算，零依赖，快速
    - deepseek: 使用 deepseek-tokenizer 精确计算
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from utils.models import EstimationResult, get_pricing

if TYPE_CHECKING:
    from offline_core.data_model import StructuredDocument

logger = logging.getLogger(__name__)

# ── 字符分类正则 ──────────────────────────────────────────────────
# CJK 统一表意文字 + 中文标点
_CJK_PATTERN = re.compile(
    r"[一-鿿㐀-䶿豈-﫿"
    r"　-〿＀-￯"
    r"぀-ゟ゠-ヿ"  # 日文假名
    r"가-힯"  # 韩文
    r"]"
)
# 拉丁字母 + 数字 + 常见符号
_LATIN_PATTERN = re.compile(r"[a-zA-Z0-9\s\d\W]")


class CharTokenEstimator:
    """基于字符分类的 token 估算器。

    策略:
        - CJK 字符: 0.6 tokens/char（基于 DeepSeek tokenizer 中文法律文本实测值 0.518，上浮留余量）
        - 其他字符 (拉丁/数字/符号): 0.25 tokens/char
        - 混合文本自动按字符分别计算后求和
    """

    CJK_RATIO = 0.6
    LATIN_RATIO = 0.25

    # Chat 消息格式开销 (参考 OpenAI tiktoken 实现)
    # 每条消息约 4 tokens (role 标记 + 分隔符)
    MESSAGE_OVERHEAD = 4
    # 每次请求约 3 tokens framing
    REQUEST_FRAMING = 3

    def estimate_text(self, text: str) -> int:
        """估算纯文本的 token 数。"""
        if not text:
            return 0

        cjk_chars = len(_CJK_PATTERN.findall(text))
        latin_chars = len(text) - cjk_chars

        tokens = cjk_chars * self.CJK_RATIO + latin_chars * self.LATIN_RATIO
        return max(1, round(tokens))

    def estimate_messages(self, messages: list[dict]) -> int:
        """按 OpenAI Chat API 格式估算消息列表的 token 数。

        计算公式:
            tokens = sum(msg_content_tokens) + len(messages) * MESSAGE_OVERHEAD + REQUEST_FRAMING
        """
        if not messages:
            return 0

        content_tokens = 0
        for msg in messages:
            content = msg.get("content", "")
            content_tokens += self.estimate_text(content)

        overhead = len(messages) * self.MESSAGE_OVERHEAD + self.REQUEST_FRAMING
        return content_tokens + overhead


class DSTokenizerEstimator:
    """基于 deepseek-tokenizer 的精确 token 估算器。

    需要安装: pip install deepseek-tokenizer
    """

    def __init__(self):
        try:
            from deepseek_tokenizer import ds_token

            self._tokenizer = ds_token
        except ImportError as e:
            raise ImportError(
                "deepseek-tokenizer 未安装。请执行: pip install deepseek-tokenizer\n"
                "或使用 TokenEstimator.fast() 进行字符估算。"
            ) from e

    def estimate_text(self, text: str) -> int:
        """精确计算文本 token 数。"""
        if not text:
            return 0
        tokens = self._tokenizer.encode(text)
        return len(tokens)

    def estimate_messages(self, messages: list[dict]) -> int:
        """按 DeepSeek Chat 格式精确计算消息列表 token 数。

        使用 deepseek_tokenizer 的消息编码 (如果有)，
        否则逐条编码后加上格式开销。
        """
        if not messages:
            return 0

        # 尝试使用 tokenizer 的原生消息编码
        if hasattr(self._tokenizer, "encode_messages"):
            tokens = self._tokenizer.encode_messages(messages)
            return len(tokens)

        # Fallback: 逐 content 编码 + 开销估算
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self.estimate_text(content)
            total += 4  # 消息级开销
        total += 3  # 请求 framing
        return total


class TokenEstimator:
    """Token 估算器门面类。

    使用方式:
        # 快速估算（零依赖）
        est = TokenEstimator.fast()
        tokens = est.estimate_text("你好世界")

        # 精确计算（需要 deepseek-tokenizer）
        est = TokenEstimator.accurate()
        tokens = est.estimate_text("你好世界")

        # 批量文档预估
        result = est.estimate_batch(
            docs=["doc1.txt", "doc2.txt"],
            prompt_template="请总结以下文档：\\n{document}",
            model="deepseek-v4-flash",
        )
    """

    def __init__(self, strategy: str = "char"):
        """
        Args:
            strategy: 估算策略 - "char" 或 "deepseek"
        """
        strategy = strategy.lower()
        if strategy not in ("char", "deepseek"):
            raise ValueError(f"不支持的策略: {strategy}，可选: char, deepseek")

        self.strategy = strategy
        if strategy == "deepseek":
            self._estimator = DSTokenizerEstimator()
        else:
            self._estimator = CharTokenEstimator()

    # ── 工厂方法 ───────────────────────────────────────────────

    @classmethod
    def fast(cls) -> "TokenEstimator":
        """创建字符系数估算器（零依赖，快速）。"""
        return cls(strategy="char")

    @classmethod
    def accurate(cls) -> "TokenEstimator":
        """创建 DeepSeek tokenizer 估算器（需要 deepseek-tokenizer 包）。"""
        return cls(strategy="deepseek")

    # ── 估算方法 ───────────────────────────────────────────────

    def estimate_text(self, text: str) -> int:
        """估算纯文本 token 数。

        Args:
            text: 输入文本

        Returns:
            估算 token 数
        """
        return self._estimator.estimate_text(text)

    def estimate_messages(self, messages: list[dict]) -> int:
        """估算 Chat 消息列表的 token 数。

        Args:
            messages: OpenAI 格式的消息列表 [{"role": ..., "content": ...}, ...]

        Returns:
            估算 token 数（含消息格式开销）
        """
        return self._estimator.estimate_messages(messages)

    def estimate_document(
        self,
        doc: Union[str, Path, "StructuredDocument"],
    ) -> int:
        """估算单个文档的 token 数。

        Args:
            doc: 文档，支持:
                - 文件路径 (str / Path): 读取文件内容
                - StructuredDocument: 提取所有 block 的 content

        Returns:
            估算 token 数
        """
        text = self._resolve_document_text(doc)
        return self.estimate_text(text)

    def estimate_batch(
        self,
        docs: list[Union[str, Path, "StructuredDocument"]],
        prompt_template: str = "",
        model: str = "deepseek-v4-flash",
        estimated_output_ratio: float = 0.2,
    ) -> EstimationResult:
        """预估批量文档经 LLM 处理的总 token 开销。

        Args:
            docs: 文档列表
            prompt_template: prompt 模板，用 {document} 占位单文档内容
            model: 目标模型名称
            estimated_output_ratio: 预估输出/输入比例（用于费用估算）

        Returns:
            EstimationResult 包含 token 数、窗口占比、预估成本
        """
        pricing = get_pricing(model)
        context_window = pricing.context_window if pricing else 128_000

        # 计算每个文档的 token
        doc_tokens = []
        total_doc_tokens = 0
        for doc in docs:
            text = self._resolve_document_text(doc)
            tokens = self.estimate_text(text)
            doc_tokens.append({"doc": str(doc)[:80], "tokens": tokens})
            total_doc_tokens += tokens

        # prompt 模板的 token（不包含 document 占位符的替换开销）
        prompt_base_tokens = self.estimate_text(
            prompt_template.replace("{document}", "")
        )
        prompt_overhead = len(docs) * 4  # 每条 document 的格式开销

        total_input_tokens = prompt_base_tokens + total_doc_tokens + prompt_overhead
        estimated_output_tokens = int(total_input_tokens * estimated_output_ratio)

        total_tokens = total_input_tokens + estimated_output_tokens
        context_usage_pct = (total_input_tokens / context_window * 100) if context_window else 0

        # 费用估算 (默认按缓存未命中)
        if pricing:
            cost_input = total_input_tokens / 1_000_000 * pricing.input_price_cache_miss
            cost_output = estimated_output_tokens / 1_000_000 * pricing.output_price
        else:
            cost_input = 0.0
            cost_output = 0.0

        return EstimationResult(
            total_tokens=total_tokens,
            model=model,
            context_window=context_window,
            context_usage_pct=round(context_usage_pct, 1),
            exceeds_window=total_input_tokens > context_window,
            estimated_cost_input=round(cost_input, 4),
            estimated_cost_output=round(cost_output, 4),
            estimated_cost_total=round(cost_input + cost_output, 4),
            estimation_strategy=self.strategy,
            breakdown={
                "doc_tokens": doc_tokens,
                "prompt_base_tokens": prompt_base_tokens,
                "total_input_tokens": total_input_tokens,
                "estimated_output_tokens": estimated_output_tokens,
            },
        )

    # ── 内部方法 ───────────────────────────────────────────────

    @staticmethod
    def _resolve_document_text(
        doc: Union[str, Path, "StructuredDocument"],
    ) -> str:
        """解析文档输入为文本。"""
        from offline_core.data_model import StructuredDocument

        if isinstance(doc, StructuredDocument):
            return "\n\n".join(b.content for b in doc.blocks)

        path = Path(doc)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8", errors="ignore")

        # 兜底：传入的字符串就是文本内容
        return str(doc)
