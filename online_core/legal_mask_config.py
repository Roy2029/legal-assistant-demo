"""可配置脱敏 API（D11）：可选脱敏项目 / 三种脱敏方式 / 扫描清单 / 映射与还原。

与 legal_mask.LegalMask 的关系：
- legal_mask.process_document 为上传时自动执行的初始脱敏（docx/pdf 版式脱敏）；
- 本模块提供用户可配置的二次脱敏：选择项目、脱敏方式、扫描勾选、拖选片段、
  映射配置持久化与按需还原。输出为 Markdown 文本版，供预览和 ReAct 审查。
"""
from __future__ import annotations

import hashlib
import re

MASK_CATEGORIES = [
    {"key": "person_name", "label": "人名"},
    {"key": "company_name", "label": "企业名"},
    {"key": "credit_code", "label": "信用代码"},
    {"key": "phone", "label": "电话"},
    {"key": "email", "label": "邮箱"},
    {"key": "id_card", "label": "身份证号"},
]

MASK_METHODS = [
    {"key": "mask", "label": "中间打码"},
    {"key": "placeholder", "label": "占位符"},
    {"key": "hash", "label": "哈希值"},
]

CATEGORY_LABELS = {c["key"]: c["label"] for c in MASK_CATEGORIES}
CATEGORY_LABELS["manual"] = "手动片段"

_CATEGORY_PLACEHOLDER = {
    "person_name": "PERSON",
    "company_name": "COMPANY",
    "credit_code": "CREDIT_CODE",
    "phone": "PHONE",
    "email": "EMAIL",
    "id_card": "ID_CARD",
    "manual": "MANUAL",
}

_PERSON_ROLE_PATTERN = (
    r"(?:"
    r"原告|被告|第三人|上诉人|被上诉人|申请人|被申请人\b"
    r"|再审申请人|再审被申请人|申请执行人|被执行人\b"
    r"|法定代表人|负责人|执行事务合伙人\b"
    r"|委托诉讼代理人|委托代理人|诉讼代理人|辩护人\b"
    r"|联系人|经办人|授权代表|经纪人|代理人\b"
    r"|反诉原告|反诉被告|本诉原告|本诉被告\b"
    r"|债权人|债务人|担保人|保证人|抵押人|出质人\b"
    r"|买方|卖方|出租方|承租方|发包方|承包方\b"
    r"|甲方|乙方|丙方|丁方\b"
    r")"
)

_PERSON_LABEL_PATTERN = (
    r"(?:姓名|名字|联系人|法定代表人|委托代理人|委托诉讼代理人|授权代表|经办人|负责人"
    r"|买方|卖方|出租方|承租方|发包方|承包方|甲方|乙方|丙方|丁方)"
)


def _dedupe_spans(spans: list) -> list:
    """按起始位置排序，重叠时保留更长的命中。"""
    spans = [s for s in spans if s.get("value", "").strip()]
    spans.sort(key=lambda s: (s["start"], -(s["end"] - s["start"])))
    out = []
    last_end = -1
    for s in spans:
        if s["start"] < last_end:
            continue
        out.append(s)
        last_end = s["end"]
    return out


def _find_person_spans(text: str) -> list:
    spans = []

    # 角色 + 姓名：甲方：张三 / 被告 李四
    pattern = (
        r"(" + _PERSON_ROLE_PATTERN + r")([\s：:是为]*)([一-龥])([一-龥]{0,3})\b"
    )
    for m in re.finditer(pattern, text):
        name = m.group(3) + (m.group(4) or "")
        if name:
            spans.append({
                "category": "person_name",
                "value": name,
                "start": m.start(3),
                "end": m.end(4) if m.group(4) else m.end(3),
            })

    # 标签 + 姓名：姓名：王五 / 联系人 赵六
    label_pattern = _PERSON_LABEL_PATTERN + r"[\s：:]*([一-龥]{2,4})"
    for m in re.finditer(label_pattern, text):
        spans.append({
            "category": "person_name",
            "value": m.group(1),
            "start": m.start(1),
            "end": m.end(1),
        })

    # 张某 / 张某某
    for m in re.finditer(r"([一-龥])某某", text):
        spans.append({
            "category": "person_name",
            "value": m.group(0),
            "start": m.start(0),
            "end": m.end(0),
        })
    for m in re.finditer(r"([一-龥])某(?!某)", text):
        spans.append({
            "category": "person_name",
            "value": m.group(0),
            "start": m.start(0),
            "end": m.end(0),
        })

    # 上下文 + 姓名：与张三签订 / 由李四承担
    ctx = r"(?:与|向|对|由|为|和|跟|同|被|让|给)"
    ctx_pattern = (
        ctx + r"([一-龥]{2,4})(?:签订|签署|履行|承担|支付|主张|起诉|上诉|辩称|诉称|称|表示|认为|承诺|确认|负责)"
    )
    for m in re.finditer(ctx_pattern, text):
        spans.append({
            "category": "person_name",
            "value": m.group(1),
            "start": m.start(1),
            "end": m.end(1),
        })

    return _dedupe_spans(spans)


def _find_company_spans(text: str) -> list:
    org_suffix = (
        r"(?:有限公司|有限责任公司|股份有限公司|公司|企业|集团|厂|院|所|中心|站|社|店|馆|部|处|科|室"
        r"|银行|保险|证券|信托|基金|期货|合伙企业|事务所)"
    )
    spans = []
    for m in re.finditer(r"[一-龥]{2,20}?" + org_suffix, text):
        value = m.group(0)
        # 过滤“统一社会信用代码”等标签被误判为企业名
        if value.endswith("社") and text[m.end(0):m.end(0) + 5] == "会信用代码":
            continue
        if value in {"统一社会信用代码", "社会信用代码", "身份证号", "信用代码"}:
            continue
        spans.append({
            "category": "company_name",
            "value": value,
            "start": m.start(0),
            "end": m.end(0),
        })
    return _dedupe_spans(spans)


def _find_regex_spans(text: str, pattern: str, category: str) -> list:
    return [
        {"category": category, "value": m.group(0), "start": m.start(0), "end": m.end(0)}
        for m in re.finditer(pattern, text)
    ]


def scan_pii(text: str, categories: list | None = None) -> list:
    """扫描文本中的敏感信息，返回可勾选清单。"""
    if not text:
        return []
    keys = set(categories or [c["key"] for c in MASK_CATEGORIES])
    spans = []
    if "person_name" in keys:
        spans.extend(_find_person_spans(text))
    if "company_name" in keys:
        spans.extend(_find_company_spans(text))
    if "id_card" in keys:
        spans.extend(_find_regex_spans(text, r"(?<!\d)\d{17}[\dXx](?!\d)", "id_card"))
    if "credit_code" in keys:
        spans.extend(_find_regex_spans(
            text,
            r"(?<![0-9A-Z])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-Z])",
            "credit_code",
        ))
    if "phone" in keys:
        spans.extend(_find_regex_spans(text, r"(?<!\d)1[3-9]\d{9}(?!\d)", "phone"))
    if "email" in keys:
        spans.extend(_find_regex_spans(text, r"[\w.+-]+@[\w-]+\.[\w.-]+", "email"))

    spans = _dedupe_spans(spans)
    for i, s in enumerate(spans):
        s["id"] = "pii_%d" % i
        s["category_label"] = CATEGORY_LABELS.get(s["category"], s["category"])
        s["preview"] = _mask_value_middle(s["value"], s["category"])
    return spans


def _mask_value_middle(value: str, category: str = "") -> str:
    """中间打码：保留首尾，中间用 * 替换。"""
    if not value:
        return value
    if category == "email" and "@" in value:
        local, _, domain = value.partition("@")
        if len(local) <= 2:
            return local[0] + "*" * max(1, len(local) - 1) + "@" + domain
        return local[0] + "*" * min(6, len(local) - 2) + local[-1] + "@" + domain
    if len(value) == 1:
        return "*"
    if len(value) == 2:
        return value[0] + "*"
    mid_len = min(len(value) - 2, 6)
    return value[0] + "*" * mid_len + value[-1]


def _hash_value(value: str, category: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10].upper()
    return "[%s:%s]" % (_CATEGORY_PLACEHOLDER.get(category, "PII"), digest)


def mask_text(text: str, items: list, method: str = "placeholder") -> tuple:
    """按指定清单脱敏。items 至少含 start/end/value/category。
    返回 (脱敏后文本, mapping_entries)。mapping_entries 即脱密映射配置。
    """
    if method not in {"mask", "placeholder", "hash"}:
        method = "placeholder"
    items = sorted(items, key=lambda x: x.get("start", 0), reverse=True)
    out = text
    counters = {}
    entries = []
    n = 0
    for item in items:
        start = int(item.get("start", -1))
        end = int(item.get("end", -1))
        value = item.get("value") or ""
        category = item.get("category") or "manual"
        if start < 0 or end < start or end > len(out):
            if value and value in out:
                start = out.find(value)
                if start < 0:
                    continue
                end = start + len(value)
            else:
                continue
        if out[start:end] != value and value:
            idx = out.find(value)
            if idx < 0:
                continue
            start, end = idx, idx + len(value)
        key = _CATEGORY_PLACEHOLDER.get(category, "MANUAL")
        counters[key] = counters.get(key, 0) + 1
        n += 1
        placeholder = "{{%s_%d}}" % (key, counters[key])
        if method == "placeholder":
            replacement = placeholder
        elif method == "hash":
            replacement = _hash_value(value, category)
        else:
            replacement = _mask_value_middle(value, category)
        out = out[:start] + replacement + out[end:]
        entries.append({
            "id": "map_%d" % n,
            "placeholder": placeholder,
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, category),
            "original": value,
            "masked_value": replacement,
            "method": method,
            "start": start,
            "end": end,
        })
    return out, entries


def restore_masked(text: str, entries: list) -> tuple:
    """按选中映射配置还原。返回 (还原后文本, 警告列表)。哈希法不可逆。"""
    out = text
    warnings = []
    for e in sorted(entries, key=lambda x: len(x.get("masked_value", "")), reverse=True):
        original = e.get("original") or ""
        masked_value = e.get("masked_value") or ""
        if not masked_value:
            continue
        if e.get("method") == "hash":
            warnings.append(masked_value + " 为哈希值，不可还原，已跳过")
            continue
        out = out.replace(masked_value, original)
    return out, warnings
