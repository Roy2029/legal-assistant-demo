"""PlannerEstimator — ML 驱动的 PlannerLLM 激活判定器。

基于 21 维统计特征 + Logistic 回归分类器，判定 query 是否需要
调用 PlannerLLM（全量 LLM 路由）。替代原先 RouterV2 的静态正则激活逻辑。

架构：
  FeatureExtractor (21维统计特征)
    → Classifier A (query_type 高收益预测)
    → Classifier B (ΔRecall 正收益预测)
    → A∪B 组合策略 → 是否激活 PlannerLLM

用法：
    estimator = PlannerEstimator()
    if estimator.should_activate("什么是违约责任？"):
        decision = planner_llm.route(query)
    else:
        decision = RouteDecision.fallback(query, top_k=30)
"""

import logging
import pickle
import sys
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# 默认模型目录（相对于项目根目录）
DEFAULT_MODEL_DIR = "experiments/planner-utility-estimator/models/"
DEFAULT_STRATEGY = "A∪B"
DEFAULT_THRESHOLD = 0.5

_FEATURE_NAMES = [
    "char_count", "sentence_count", "clause_count", "char_entropy",
    "number_count", "year_count", "punctuation_count",
    "repeat_ratio", "has_question_word",
    "is_compare", "is_timeline", "is_list", "conjunction_count",
    "token_count", "avg_word_length", "stopword_ratio", "compression_ratio",
    "lexical_density",
    "entity_count", "ner_type_count", "has_multiple_entities",
]


def _import_feature_extractor():
    """动态导入 experiments 中的 FeatureExtractor。

    目录名包含连字符（planner-utility-estimator），无法用标准 import，
    需要通过 sys.path + importlib 或 import_hook 处理。
    """
    exp_dir = Path(__file__).resolve().parent.parent / "experiments"
    estimator_dir = exp_dir / "planner-utility-estimator"
    if estimator_dir.exists():
        if str(estimator_dir) not in sys.path:
            sys.path.insert(0, str(estimator_dir))
    from feature_extractor import FeatureExtractor  # noqa: F811
    return FeatureExtractor


class _SharedExtractor:
    """类级别共享的 FeatureExtractor 单例，避免 jieba 重复初始化。"""

    _instance = None
    _extractor_cls = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            if cls._extractor_cls is None:
                cls._extractor_cls = _import_feature_extractor()
            cls._instance = cls._extractor_cls()
            logger.info(
                "PlannerEstimator FeatureExtractor 已初始化 "
                "(cache_size=%d)",
                cls._instance.cache_size,
            )
        return cls._instance


class PlannerEstimator:
    """PlannerLLM 激活判定器。

    使用预训练的 Logistic 回归模型，基于 21 维统计特征判定
    当前 query 是否需要走 PlannerLLM（全量 LLM 路由）。

    Args:
        model_dir: 模型文件所在目录
        strategy: 组合策略（A / B / A∪B / A∩B）
        activation_threshold: predict_proba 激活阈值

    Raises:
        FileNotFoundError: 模型文件不存在时抛出
    """

    def __init__(
        self,
        model_dir: str = DEFAULT_MODEL_DIR,
        strategy: str = DEFAULT_STRATEGY,
        activation_threshold: float = DEFAULT_THRESHOLD,
    ):
        self.strategy = strategy
        self.activation_threshold = activation_threshold

        # 验证策略
        valid_strategies = {"A", "B", "A∪B", "A∩B"}
        if strategy not in valid_strategies:
            raise ValueError(
                f"无效策略: {strategy}，可选: {valid_strategies}"
            )

        # 共享 FeatureExtractor（类级别单例）
        self.extractor = _SharedExtractor.get()

        # 加载模型
        model_dir_path = Path(model_dir)
        self._classifier_a = self._load_model(
            model_dir_path / "classifier_type_Logistic.pkl", "Classifier A"
        )
        self._classifier_b = self._load_model(
            model_dir_path / "classifier_gain_Logistic.pkl", "Classifier B"
        )

        # 加载特征归一化器（模型训练时输入经 StandardScaler 缩放）
        scaler_path = model_dir_path / "feature_scaler.pkl"
        if scaler_path.exists():
            with open(scaler_path, "rb") as f:
                self._scaler = pickle.load(f)
            logger.info("特征归一化器已加载: %s", scaler_path)
        else:
            logger.warning(
                "特征归一化器不存在 (%s)，将使用未缩放特征（精度可能下降）",
                scaler_path,
            )
            self._scaler = None

        logger.info(
            "PlannerEstimator 已加载 (strategy=%s, threshold=%.2f, scaler=%s)",
            self.strategy, self.activation_threshold,
            "✓" if self._scaler else "✗",
        )

    # ── 模型加载 ──────────────────────────────────────────────────

    @staticmethod
    def _load_model(path: Path, label: str):
        """加载 pickle 模型文件。"""
        if not path.exists():
            raise FileNotFoundError(
                f"{label} 模型文件不存在: {path}"
            )
        try:
            with open(path, "rb") as f:
                model = pickle.load(f)
            logger.info("%s 已加载: %s", label, path)
            return model
        except Exception as e:
            raise RuntimeError(
                f"{label} 模型加载失败 ({path}): {e}"
            ) from e

    # ── 主入口 ──────────────────────────────────────────────────

    def should_activate(self, query: str) -> bool:
        """判定 query 是否需要激活 PlannerLLM。

        Args:
            query: 用户原始查询

        Returns:
            True → 需要调用 PlannerLLM；False → 走默认检索
        """
        if not query or not query.strip():
            return False

        try:
            features = self.extractor.extract(query)
            feature_vector = [
                features.get(name, 0.0) for name in _FEATURE_NAMES
            ]
            prob_a, prob_b = self._predict(feature_vector)
            return self._apply_strategy(prob_a, prob_b)

        except Exception as e:
            logger.warning(
                "PlannerEstimator 推理异常 (query='%s...'): %s，"
                "保守放行",
                query[:50], e,
            )
            return True  # 异常时保守放行

    # ── 策略组合 ──────────────────────────────────────────────────

    def _predict(self, feature_vector: list) -> tuple[float, float]:
        """执行模型推理，返回 (prob_a, prob_b)。

        如果特征归一化器存在，先对输入特征进行 StandardScaler 缩放。
        """
        X = np.array([feature_vector])
        if self._scaler is not None:
            X = self._scaler.transform(X)
        prob_a = float(self._classifier_a.predict_proba(X)[0, 1])
        prob_b = float(self._classifier_b.predict_proba(X)[0, 1])
        return prob_a, prob_b

    def _apply_strategy(self, prob_a: float, prob_b: float) -> bool:
        """根据策略组合判定是否激活。"""
        activated_a = prob_a >= self.activation_threshold
        activated_b = prob_b >= self.activation_threshold

        if self.strategy == "A":
            return activated_a
        elif self.strategy == "B":
            return activated_b
        elif self.strategy == "A∪B":
            return activated_a or activated_b
        elif self.strategy == "A∩B":
            return activated_a and activated_b
        return False  # 不会到达

    # ── 调试接口 ──────────────────────────────────────────────────

    def predict_proba(self, query: str) -> dict:
        """返回两个分类器的预测概率（用于调试/trace）。"""
        result = {
            "strategy": self.strategy,
            "threshold": self.activation_threshold,
            "prob_a": None,
            "prob_b": None,
            "activated": False,
        }
        if not query or not query.strip():
            return result

        try:
            features = self.extractor.extract(query)
            feature_vector = [
                features.get(name, 0.0) for name in _FEATURE_NAMES
            ]
            prob_a, prob_b = self._predict(feature_vector)
            result["prob_a"] = round(prob_a, 4)
            result["prob_b"] = round(prob_b, 4)
            result["activated"] = self._apply_strategy(prob_a, prob_b)
        except Exception as e:
            logger.warning("predict_proba 异常: %s", e)
            result["error"] = str(e)

        return result
