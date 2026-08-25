"""PreFilter 保守模式（D02 §3.2）：只拦截高置信非法律/无意义输入，宁放勿杀。"""
from __future__ import annotations

import re

# 明显闲聊/无意义模式
TRIVIAL_PATTERNS = [
    r"^(你好|您好|hi|hello|在吗|在不在|谢谢|再见|好的|嗯|哦|哈哈)+$",
    r"^(今天天气|天气怎么样|吃饭了吗|你是谁|你叫什么).*$",
]

LAW_HINTS = [
    "法", "条", "款", "判决", "裁定", "仲裁", "合同", "起诉", "诉讼",
    "劳动", "婚姻", "继承", "侵权", "赔偿", "犯罪", "辩护", "证据",
    "协议", "违约", "纠纷", "执行", "管辖", "时效", "担保", "物权",
    "债权", "离婚", "工伤", "社保", "税务", "公司", "股东", "专利",
    "商标", "著作权", "破产", "招标", "投标", "建设", "工程",
]


def is_trivial(query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    if len(q) <= 1:
        return True
    if re.fullmatch(r"[\s\W_]+", q):
        return True
    for pat in TRIVIAL_PATTERNS:
        if re.match(pat, q, re.IGNORECASE):
            return True
    return False


def is_likely_legal(query: str) -> bool:
    return any(h in query for h in LAW_HINTS)


def prefilter(query: str) -> dict:
    """返回 {passed, reason}。passed=False 表示应直接回复固定话术。"""
    if is_trivial(query):
        return {"passed": False, "reason": "trivial"}
    # 保守：只拦明显闲聊；法律相关或不确定一律放行
    if not is_likely_legal(query) and len(query) < 8:
        return {"passed": False, "reason": "short_non_legal"}
    return {"passed": True, "reason": None}


TRIVIAL_REPLY = "请提出具体的法律问题，例如：民法典第32条说了什么？"
