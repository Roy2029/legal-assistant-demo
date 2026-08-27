"""Cross-Encoder 重排器。

基于 sentence-transformers 的 CrossEncoder 对检索结果逐对打分，
用 (query, chunk.text) 的 relevancy 分数替换原始检索分后重新排序。
"""

import logging
from pathlib import Path

from offline_core.data_model import RetrievalResult

logger = logging.getLogger(__name__)


class APIReranker:
    """云 API 重排器（OpenAI 兼容 /rerank 端点，如 SiliconFlow/Jina/Cohere 兼容网关）。

    接口方案（迭代需求 #1）：
    - skip  : 不重排（默认，低配机器推荐）
    - local : 本地 bge-reranker-v2-m3（CPU 推理，候选 <= 30 时可用）
    - api   : 云 rerank API，需配置 reranker_api_url / reranker_api_key / reranker_api_model
    """

    def __init__(self, api_url: str = "", api_key: str = "", model: str = "bge-reranker-v2-m3", timeout: int = 30):
        import os

        self.api_url = (api_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip() or os.getenv("RERANK_API_KEY", "") or os.getenv("LLM_API_KEY", "")
        self.model = model or "bge-reranker-v2-m3"
        self.timeout = timeout
        if not self.api_url:
            raise ValueError("reranker_provider=api 需要配置 reranker_api_url（OpenAI 兼容 /rerank 端点）")

    def rerank(self, query: str, candidates: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
        import requests

        if not candidates:
            return []
        payload = {
            "model": self.model,
            "query": query,
            "documents": [c.chunk.text for c in candidates],
            "top_n": top_k,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        url = self.api_url if self.api_url.endswith("/rerank") else self.api_url + "/rerank"
        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        results_data = data.get("results") or []
        scores = {r.get("index"): float(r.get("relevance_score", 0.0)) for r in results_data}
        for i, c in enumerate(candidates):
            c.score = scores.get(i, 0.0)
            c.retrieval_type = "rerank_api"
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates[:top_k]


class CrossEncoderReranker:
    """Cross-Encoder 重排器。

    用法:
        reranker = CrossEncoderReranker("local_model/bge-reranker-v2-m3")
        reranked = reranker.rerank(query, candidates, top_k=5)
    """

    def __init__(
        self,
        model_path: str = "local_model/bge-reranker-v2-m3",
        device: str = "cuda",
        batch_size: int = 32,
        use_fp16: bool = False,  # GTX 1650 上 .half() 有严重性能 bug（580s/批），FP32 反而快
    ):
        """
        Args:
            model_path: 本地模型路径（如 local_model/bge-reranker-v2-m3）
            device: 推理设备 "cpu" | "cuda"
            batch_size: batch 推理大小
            use_fp16: 是否启用 FP16 量化（仅 CUDA 生效，可提升 1.5-2x 推理速度）

        Raises:
            FileNotFoundError: 模型路径不存在
        """
        self.model_path = model_path
        self.device = device
        self.batch_size = batch_size
        self.use_fp16 = use_fp16

        # 验证模型路径
        model_dir = Path(model_path)
        if not model_dir.exists():
            raise FileNotFoundError(
                f"Cross-encoder 模型路径不存在: {model_path}\n"
                f"请下载模型并保存到该目录：\n"
                f"  python -c \"from huggingface_hub import snapshot_download; "
                f"snapshot_download('BAAI/bge-reranker-v2-m3', "
                f"local_dir='{model_path}', local_dir_use_symlinks=False)\""
            )

        logger.info(
            "加载 CrossEncoder 模型: %s (device=%s, fp16=%s, batch=%d)",
            model_path, device, use_fp16, batch_size,
        )
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(
            model_path, device=device,
        )

        # FP16 量化：转换模型参数为半精度（仅 CUDA）
        if use_fp16 and device == "cuda":
            try:
                self.model.model.half()
                logger.info("CrossEncoder FP16 量化已启用")
            except Exception as e:
                logger.warning("FP16 量化失败，回退到 FP32: %s", e)
        else:
            logger.info("CrossEncoder FP32 模式（device=%s）", device)

        logger.info("CrossEncoder 模型加载完成")

    def rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int,
    ) -> list[RetrievalResult]:
        """对候选 chunks 进行 cross-encoder 重排。

        Args:
            query: 原始用户查询文本
            candidates: 检索器返回的候选结果列表
            top_k: 重排后保留的最大条数

        Returns:
            按 cross-encoder 分数降序排列的 RetrievalResult 列表，
            每项的 score 已替换为 cross-encoder 相关性分。
            结果数 = min(top_k, len(candidates))。
        """
        if not candidates:
            return []

        # 1. 构造 query-chunk pairs
        pairs = [(query, c.chunk.text) for c in candidates]

        # 2. Cross-encoder 推理打分
        #    使用 auto_batch_size：候选数<16 用小 batch 减少 padding 浪费
        bs = min(self.batch_size, max(8, len(candidates)))
        scores = self.model.predict(pairs, batch_size=bs)

        # 3. 替换 score 并排序
        for c, score in zip(candidates, scores):
            c.score = float(score)
            c.retrieval_type = "rerank"

        candidates.sort(key=lambda x: x.score, reverse=True)

        # 4. 取 top_k
        return candidates[:top_k]


# ── Benchmark ─────────────────────────────────────────────────

def benchmark_reranker(
    reranker_fp16: "CrossEncoderReranker",
    reranker_fp32: "CrossEncoderReranker",
    test_pairs: list[tuple[str, str]],
    batch_size: int = 32,
) -> dict:
    """对比 FP16 和 FP32 的推理速度与分数差异。

    Args:
        reranker_fp16: FP16 量化的 reranker 实例
        reranker_fp32: FP32 全精度的 reranker 实例
        test_pairs: (query, chunk_text) 测试数据列表
        batch_size: 推理 batch size

    Returns:
        {
            "fp16_time_ms": ...,
            "fp32_time_ms": ...,
            "speedup_ratio": ...,
            "score_diff_max": ...,       # 最大绝对分数差
            "score_diff_mean": ...,      # 平均绝对分数差
            "n_pairs": ...,
            "rank_agreement": ...,       # top-5 排序一致性 (Jaccard)
        }
    """
    import time

    def _run(ce, pairs, bs):
        t0 = time.perf_counter()
        scores = ce.model.predict(pairs, batch_size=bs)
        elapsed = (time.perf_counter() - t0) * 1000
        return list(scores), elapsed

    scores_fp16, t16 = _run(reranker_fp16, test_pairs, batch_size)
    scores_fp32, t32 = _run(reranker_fp32, test_pairs, batch_size)

    n = min(len(scores_fp16), len(scores_fp32))
    if n == 0:
        return {"error": "no scores produced"}

    diffs = [abs(scores_fp16[i] - scores_fp32[i]) for i in range(n)]

    # 排序一致性：取 top-5 的 chunk index 交集
    top_n = min(5, n)
    top_fp16 = set(sorted(range(n), key=lambda i: scores_fp16[i], reverse=True)[:top_n])
    top_fp32 = set(sorted(range(n), key=lambda i: scores_fp32[i], reverse=True)[:top_n])
    jaccard = len(top_fp16 & top_fp32) / max(len(top_fp16 | top_fp32), 1)

    return {
        "fp16_time_ms": round(t16, 2),
        "fp32_time_ms": round(t32, 2),
        "speedup_ratio": round(t32 / max(t16, 0.01), 2),
        "score_diff_max": round(max(diffs), 4),
        "score_diff_mean": round(sum(diffs) / len(diffs), 4),
        "n_pairs": n,
        "rank_agreement": round(jaccard, 4),
    }
