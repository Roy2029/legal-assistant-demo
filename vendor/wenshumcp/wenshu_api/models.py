"""数据模型：用 dataclass 描述查询结果、文书元数据与数据库结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LegalBasis:
    """文书引用的法律条文（来自 docInfoSearch 的 s47 字段）。"""

    clause: str = ""                 # 条款（如「第一百七十七条第一款第二项」）
    law_name: str = ""               # 法规名称（如「《中华人民共和国民事诉讼法》」）
    law_id: str = ""                 # 法规 id


@dataclass
class DocumentContent:
    """文书全文内容（来自 docInfoSearch 解密后的结构化 JSON）。

    站点把一份文书拆成若干 s* 编号字段：s22 标题区、s23 案件由来/当事人、
    s25 诉辩意见、s26 本院认为、s27 判决主文、s28 尾部署名；
    qwContent 则是完整渲染 HTML（含站点防伪字距，适合 html/pdf 落盘）。
    """

    doc_id: str = ""                 # 文书唯一 ID（= 搜索结果 rowkey）
    title: str = ""                  # 案件名称（s1）
    court_name: str = ""             # 法院名称（s2）
    case_number: str = ""            # 案号（s7）
    case_type: str = ""              # 案件类型（s8，如「民事案件」）
    trial_procedure: str = ""        # 审判程序（s9，如「民事二审」）
    publish_date: str = ""           # 发布日期（s31）
    judgment_date: str = ""          # 裁判日期（s41）
    cause: str = ""                  # 案由（s11，首项）
    keywords: list[str] = field(default_factory=list)   # 关键词（s45）
    legal_basis: list[LegalBasis] = field(default_factory=list)  # 法律依据（s47）
    title_block: str = ""            # 标题区原文（s22，含法院/文书类型/案号）
    background: str = ""             # 案件由来 / 当事人（s23）
    claims: str = ""                 # 诉辩意见（s25）
    court_opinion: str = ""          # 本院认为（s26）
    judgment_result: str = ""        # 判决主文（s27）
    signatures: str = ""             # 尾部署名（s28）
    html: str = ""                   # 完整渲染 HTML（qwContent）
    view_count: str = ""             # 浏览量（viewCount）
    full_text: str = ""              # 由结构化字段拼接的纯文本正文
    raw: dict[str, Any] = field(default_factory=dict)    # 原始解密 JSON


@dataclass
class DocumentMeta:
    """结果列表中的单条文书摘要信息。"""

    doc_id: str = ""                 # 文书唯一 ID（下载全文时使用）
    title: str = ""                  # 案件标题
    case_number: str = ""            # 案号
    court_name: str = ""             # 法院名称
    case_type: str = ""              # 案件类型（刑/民/行/赔/执）
    cause: str = ""                  # 案由
    publish_date: str = ""           # 发布日期
    trial_procedure: str = ""        # 审判程序
    summary: str = ""                # 本院认为 / 裁判要旨摘要
    doc_url: str = ""                # 文书详情/下载链接
    rowkey: str = ""                 # 文书唯一标识（拉详情/下载用）
    raw: dict[str, Any] = field(default_factory=dict)  # 原始字段，便于扩展


@dataclass
class SearchResult:
    """一次查询的返回：总数 + 当前页摘要列表。"""

    total: int = 0                   # 命中总数
    page: int = 1                    # 当前页码
    page_size: int = 10              # 每页大小
    documents: list[DocumentMeta] = field(default_factory=list)

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size


@dataclass
class CourtNode:
    """法院层级结构节点（树形）。"""

    name: str
    code: str = ""
    children: list["CourtNode"] = field(default_factory=list)


@dataclass
class FieldMeta:
    """可查询字段的元信息。"""

    key: str                         # 字段键名（对应 constants.FIELD_KEYS）
    label: str                       # 中文展示名
    example: str = ""                # 取值示例


@dataclass
class DatabaseStructure:
    """裁判文书网公开数据库结构概览。"""

    queryable_fields: list[FieldMeta] = field(default_factory=list)
    case_types: dict[str, str] = field(default_factory=dict)
    court_levels: list[str] = field(default_factory=list)
    cause_examples: list[str] = field(default_factory=list)
    note: str = ""
