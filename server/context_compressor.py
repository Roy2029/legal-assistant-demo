"""上下文压缩（M1 W7）：历史消息超阈值时压缩最旧部分，保留最近 N 条。"""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """粗估 token（中文约 1.5 字符/token，英文约 4 字符/token）。"""
    return max(1, len(text) // 2)


def compress_history(history: list[dict], max_tokens: int = 200_000, keep_recent: int = 5) -> list[dict]:
    """返回压缩后的消息列表。超阈值时对最旧消息做摘要（M1 无 LLM 时截断）。"""
    if not history:
        return []
    total = sum(estimate_tokens(m.get("content", "")) for m in history)
    if total <= max_tokens:
        return history
    recent = history[-keep_recent:]
    old = history[:-keep_recent]
    old_text = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in old)
    # M1 简化：无 LLM 摘要时，仅保留首尾各 1 条旧消息作为摘要占位
    summary_content = f"[历史摘要] 更早 {len(old)} 条消息已压缩：{old_text[:2000]}..."
    summary_msg = {"role": "system", "content": summary_content}
    return [summary_msg] + recent


if __name__ == "__main__":
    h = [{"role": "user", "content": "问题" + "很" * 100 + "长" * 100}] * 30
    out = compress_history(h, max_tokens=500, keep_recent=2)
    print("压缩前", len(h), "压缩后", len(out))
    print("首条 role:", out[0]["role"])
