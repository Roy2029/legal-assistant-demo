import yaml
import json
from pathlib import Path


class ConfigManager:
    """配置管理器 — 合并 default.yaml / user.yaml / runtime.json。"""

    def __init__(self):
        self.config = {}

    def load(self):
        self.config = {}
        self._merge(self._load_yaml("config/default.yaml"))
        self._merge(self._load_yaml("config/user.yaml"))
        self._merge(self._load_json("config/runtime.json"))

    def _merge(self, data):
        if not data:
            return
        for k, v in data.items():
            if isinstance(v, dict) and k in self.config:
                self.config[k].update(v)
            else:
                self.config[k] = v

    def _load_yaml(self, path):
        p = Path(path)
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text())

    def _load_json(self, path):
        p = Path(path)
        if not p.exists():
            return {}
        return json.loads(p.read_text())

    def get(self, key, default=None):
        parts = key.split(".")
        node = self.config
        for p in parts:
            node = node.get(p, {})
        return node or default

    def set_runtime(self, key, value):
        runtime = self._load_json("config/runtime.json")
        parts = key.split(".")
        node = runtime
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
        Path("config/runtime.json").write_text(json.dumps(runtime, indent=2))

    def set(self, key: str, value) -> None:
        """设置配置项并持久化到 runtime.json。

        Args:
            key: 点分配置路径，如 "rerank.enabled"
            value: 配置值
        """
        self.set_runtime(key, value)
        # 同步更新内存中的配置
        parts = key.split(".")
        node = self.config
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    def save(self) -> None:
        """将当前内存中所有配置写回 runtime.json。

        不同于 set() 的单键写入，save() 将完整配置状态
        （含 default.yaml + user.yaml 合并后的覆盖值）持久化。
        """
        runtime = self._load_json("config/runtime.json")
        # 仅写入显式配置过的值（含通过 set_batch/set 的变更）
        # 跳过默认值以避免配置文件膨胀
        for key in self.config:
            if key not in runtime:
                runtime[key] = self.config[key]
        Path("config/runtime.json").write_text(
            json.dumps(runtime, indent=2, ensure_ascii=False)
        )

    def reload(self):
        self.load()

    # ── Web 界面新增方法 ──────────────────────────────────────

    def get_all(self) -> dict:
        """返回完整合并后的配置字典。"""
        return dict(self.config)

    def set_batch(self, kv_pairs: dict) -> int:
        """批量写入 runtime.json。

        Args:
            kv_pairs: {"retriever.mode": "hybrid", "retriever.top_k": 10}

        Returns:
            成功写入的配置项数量
        """
        count = 0
        for key, value in kv_pairs.items():
            try:
                self.set(key, value)
                count += 1
            except Exception:
                pass
        return count

    @staticmethod
    def get_schema() -> list[dict]:
        """返回配置项的元信息（类型、说明、取值范围）。

        用于 Web 配置页面渲染表单控件。
        """
        return [
            # 在线问答配置
            {"key": "retriever.mode", "type": "select", "label": "检索模式",
             "options": ["dense", "bm25", "hybrid"], "default": "hybrid",
             "description": "向量检索模式"},
            {"key": "retriever.top_k", "type": "int", "label": "Top-K 检索数",
             "min": 1, "max": 100, "default": 5,
             "description": "检索返回的 chunk 数量"},
            {"key": "retriever.mode_dense", "type": "select", "label": "密集检索模式",
             "options": ["dense", "hybrid"], "default": "dense",
             "description": "密集检索的子模式"},
            {"key": "retriever.mode_sparse", "type": "select", "label": "稀疏检索模式",
             "options": ["bm25", "hybrid"], "default": "bm25",
             "description": "稀疏检索的子模式"},

            # Rerank 配置
            {"key": "rerank.enabled", "type": "bool", "label": "启用 Rerank",
             "default": True, "description": "是否启用 Cross-Encoder 精排"},
            {"key": "rerank.device", "type": "select", "label": "Rerank 设备",
             "options": ["cpu", "cuda"], "default": "cuda",
             "description": "Rerank 模型运行设备"},

            # LLM 配置
            {"key": "default_set", "type": "text", "label": "LLM 配置集",
             "default": "default_set", "description": "使用的 LLM 配置集名称"},

            # LegalPreFilter 配置
            {"key": "prefilter.legal_filter.enabled", "type": "bool", "label": "启用 LegalPreFilter",
             "default": True, "description": "是否启用法律领域预过滤器"},
            {"key": "prefilter.legal_filter.kw_weight", "type": "float", "label": "关键词权重",
             "min": 0, "max": 1, "default": 0.70,
             "description": "法律关键词得分在总得分中的权重"},
            {"key": "prefilter.legal_filter.total_thresh", "type": "float", "label": "总分放行阈值",
             "min": 0, "max": 1, "default": 0.20,
             "description": "加权总分达到此阈值则放行"},

            # PlannerEstimator 配置
            {"key": "planner_estimator.enabled", "type": "bool", "label": "启用 PlannerEstimator",
             "default": True, "description": "是否启用 ML 驱动的 PlannerLLM 激活判定"},
            {"key": "planner_estimator.strategy", "type": "select", "label": "组合策略",
             "options": ["A", "B", "A∪B", "A∩B"], "default": "A∪B",
             "description": "分类器组合策略"},

            # PlannerLLM 配置
            {"key": "planner_llm.fallback_top_k", "type": "int", "label": "短路检索深度",
             "min": 1, "max": 100, "default": 30,
             "description": "PlannerEstimator 不激活时检索返回的 chunk 数量"},

            # 知识库索引配置
            {"key": "vector_db.embedding_model", "type": "text",
             "label": "Embedding 模型", "default": "local_model/bge-base-zh",
             "description": "文本向量化模型"},
            {"key": "vector_db.dense.dimension", "type": "int", "label": "向量维度",
             "min": 64, "max": 4096, "default": 768,
             "description": "向量维度"},
            {"key": "index_store_dir", "type": "text", "label": "索引存储目录",
             "default": "data/indices",
             "description": "知识库索引文件的存储根目录"},
        ]
