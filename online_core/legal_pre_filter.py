"""在线法律领域预过滤器 — 规则层零 LLM 开销的快速过滤器。

用于 RAG 管线的法律领域预过滤，在 QueryPreFilter 的基础上增强：
  - 法律关键词加权打分（~100 个法律术语）
  - 法律模式匹配（~20 个正则，覆盖法条引用、法律程序、责任义务等）
  - 非法律语境自动排除（计算机、数学、网络协议等）
  - KB 词表重叠率辅助判断

输出 FilterResult，need_rag=False 时直接返回，不进入后续流程。

决策优先级（从高到低）：
1. 空查询 → 拦截 (empty_query)
2. 闲聊/问候 → 拦截 (greeting)
3. 纯符号/数字/表情 → 拦截 (nonsense)
4. 非法律语境 → 检查强法律信号，无信号则拦截 (non_legal_context)
5. 加权总分 >= 阈值 → 放行
6. 关键词分数 >= 0.25 → 放行（后备，低词表重叠时仍有高关键词匹配）
7. 未匹配 → 拦截 (irrelevant)
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from online_core.data_model import FilterResult

logger = logging.getLogger(__name__)

# ── 闲聊 / 社交用语词表（同 query_router.py）────────────────

GREETING_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(你好|您好|嗨|hi|hello|hey)\s*$", re.IGNORECASE),
    re.compile(r"^(谢谢|感谢|多谢|thanks|thank you)\s*$", re.IGNORECASE),
    re.compile(r"^(再见|拜拜|bye|goodbye|88)\s*$", re.IGNORECASE),
    re.compile(r"^(好的|好的吧|嗯|嗯嗯|ok|okay|可以|没问题)\s*$", re.IGNORECASE),
    re.compile(r"^(在吗|在不在|are you there)\s*$", re.IGNORECASE),
    re.compile(r"^(你好棒|你真棒|good bot|nice)\s*$", re.IGNORECASE),
]

# ── 纯无意义检测（同 query_router.py）─────────────────────────

NONSENSE_PATTERN = re.compile(r"^[\d\s\W]+$")

# ── 法律关键词（扩展版，来自 experimental legal_filter.py）───

LEGAL_KEYWORDS: set[str] = {
    # 核心法律名词
    "法律", "法规", "立法", "司法", "执法", "守法", "违法", "合法",
    "宪法", "刑法", "民法", "行政法", "经济法", "商法", "诉讼法",
    "仲裁法", "劳动法", "合同法", "婚姻法", "继承法", "公司法",
    "破产法", "保险法", "证券法", "票据法", "海商法", "知识产权法",
    "侵权", "赔偿", "责任", "权利", "义务",
    "犯罪", "处罚", "刑罚", "刑事", "民事", "行政",
    "诉讼", "上诉", "起诉", "应诉", "辩护", "审判", "仲裁",
    "法官", "律师", "当事人", "原告", "被告", "第三人",
    "检察院", "法院", "公安局",
    "条文", "条款", "章程", "规定", "条例", "细则",
    "案件", "案情", "案由", "判例", "裁决", "判决",
    "合同", "协议", "契约", "违约", "纠纷",
    "代理", "委托", "授权", "公证",
    "继承", "遗嘱", "赠与", "收养",
    "婚姻", "离婚", "抚养", "赡养",
    "股东", "董事", "监事", "法人", "营利法人", "非营利法人", "利润分配",
    "专利", "商标", "著作权", "版权",
    "税收", "纳税", "税率", "征税",
    "劳动争议", "工伤", "社保", "保险",
    "海事", "海商", "航运",
    "复议", "许可", "强制",
    "国际法", "条约", "公约",
    "法律援助", "司法鉴定", "执行",
    "民法典", "刑法典", "司法解释", "指导意见",
    "抵押权", "质权", "留置权", "优先权",
    "定金", "违约金", "赔偿金",
    "不可抗力", "情势变更", "预期违约",
    "善意取得", "不当得利", "无因管理",
    "正当防卫", "紧急避险", "犯罪中止", "犯罪未遂",
    "累犯", "自首", "立功", "缓刑", "假释",
    # 物权 / 债权
    "物权", "所有权", "用益物权", "不动产", "动产",
    "行贿", "受贿", "贪污", "渎职", "职务犯罪",
    "追诉", "数罪并罚",
    "证据", "举证", "举证责任", "证明责任", "举证期限",
    "公示催告", "督促程序", "支付令",
    "保全", "先予执行", "强制执行",
    "权力", "国家权力", "任期", "届", "连任",
    "全国人大", "国务院", "国家机构",
    "选举权", "被选举权",
    "票据", "汇票", "本票", "支票", "追索权",
    "承兑", "背书", "出票",
    "内幕信息", "内幕交易", "操纵市场",
    "股东会", "董事会", "监事会", "股东大会",
    "法定代表人", "注册资本", "出资",
    "用人单位", "劳动者", "劳动合同", "试用期", "加班", "加班费",
    "安理会", "常任理事国", "国际条约", "国际公约",
    "主权", "领土", "管辖权",
    "消费者", "欺诈", "三包", "退货", "投诉",
    "规范性", "法律规范", "法律规则", "法律原则",
    "归责", "责任能力", "行为能力",
    "债权", "债务", "债权人", "债务人",
    "保险人", "投保人", "被保险人", "受益人",
    "仲裁协议", "仲裁裁决", "仲裁委员会",
    "证券公司", "经营范围",
    # ── 社会救助 / 社会保障（补充: 2026-07 民生法律查询覆盖率） ──
    "社会救助", "救助对象", "特困人员", "最低生活保障", "低保",
    "五保", "供养", "赡养人", "赡养义务", "抚养人", "抚养义务",
    "扶养", "困境儿童", "老年人权益", "残疾人保障",
    # ── 反恐 / 国家安全（补充：公共安全领域） ──
    "反恐", "恐怖主义", "恐怖活动", "国家安全", "反间谍",
    # ── 防沙治沙 / 土地管理（补充：自然资源领域） ──
    "治沙", "沙化", "荒漠化", "防沙治沙", "土地管理", "土地使用权",
    "土地承包", "土地经营权", "耕地保护",
    # ── 行政法（补充：行政许可/处罚/强制） ──
    "行政许可", "行政处罚", "行政强制", "行政赔偿", "政府信息公开",
    "依申请公开", "听证",
    # ── 国家工作人员 / 职务犯罪（补充） ──
    "国家工作人员", "国家机关", "滥用职权", "玩忽职守",
    "挪用资金", "挪用公款", "洗钱",
    # ── 合同法补充 ──
    "继续履行", "合同履行", "违约责任", "缔约过失", "格式条款",
    "同时履行", "先履行",
    # ── 归责 / 责任（补充） ──
    "归责原则", "过错责任", "无过错责任", "严格责任",
    "公平责任", "连带责任", "补充责任", "按份责任",
    # ── 物权 / 房地产（补充） ──
    "房地产开发", "房地产开发企业", "商品房", "预售",
    "业主", "物业", "物业管理", "维修资金",
    # ── 公司法 / 证券（补充） ──
    "信息披露", "关联交易", "实际控制人", "控股股东",
    "优先购买权",
    # ── 程序法（补充） ──
    "特别程序", "简易程序",
    "形式审查", "实质审查", "注册登记", "备案",
    "法条", "法条竞合", "法律适用",
    # ── 其他常见法律概念（补充） ──
    "司法协助", "法律意见书", "尽职调查", "合规",
    "保理", "融资租赁", "特许经营", "政府采购",
}

# ── 法律模式匹配（来自 experimental legal_filter.py）────────

LEGAL_PATTERNS: list[re.Pattern] = [
    # 法条引用
    re.compile(r"第[零一二三四五六七八九十百千\d]+[条章节款]"),
    re.compile(r"根据.*[法|条例|规定|办法|细则]"),
    re.compile(r"依照.*[法|条例|规定]"),
    re.compile(r"按照.*[法|条例|规定]"),
    re.compile(r"[《（][^）》]*[法|条例|规定|办法|细则|公约|条约][》）]"),
    # 法律程序
    re.compile(r"向.*法院|向.*检察院|向.*公安局"),
    re.compile(r"提起.*诉讼|申请.*仲裁|申请.*复议"),
    re.compile(r"判处|裁定|判决|裁决|审理"),
    # 责任义务
    re.compile(r"承担.*责任|民事责任|刑事责任|行政责任"),
    re.compile(r"应当.*[赔偿|补偿|返还|支付]"),
    # 法律主体
    re.compile(r"当事人|原告|被告|第三人|申请人|被申请人"),
    re.compile(r"权利人|义务人|债务人|债权人"),
    # 法律行为
    re.compile(r"侵权|违约|犯罪|违法|合法|非法"),
    re.compile(r"继承|遗嘱|收养|离婚|结婚"),
    re.compile(r"专利|商标|著作权|版权"),
    # 法律后果
    re.compile(r"赔偿|补偿|罚款|没收|吊销|拘留"),
    re.compile(r"死刑|无期徒刑|有期徒刑|拘役|罚金"),
    # 补充模式
    re.compile(r"物权|不动产|动产"),
    re.compile(r"行贿|受贿|贪污|渎职"),
    re.compile(r"证据|举证|证明责任"),
    re.compile(r"任期|连任|每届|任职.*届"),
    re.compile(r"票据|汇票|本票|支票|承兑|背书"),
    re.compile(r"内幕信息|内幕交易|操纵市场"),
    re.compile(r"用人单位|劳动者|劳动合同"),
    re.compile(r"全国人大|国务院|国家机构|安理会|常任理事国"),
    re.compile(r"公示催告|督促程序|支付令"),
    re.compile(r"消费者.*[权益|保护]|欺诈.*赔偿"),
    re.compile(r"债权人|债务人|债权债务"),
    re.compile(r"投保人|被保险人|受益人|保险人"),
    # ── 2026-07 补充：行政/民生/公共安全模式 ──
    re.compile(r"行政许可|行政处罚|行政强制|行政赔偿"),
    re.compile(r"政府信息公开|依申请公开"),
    re.compile(r"社会救助|最低生活保障|低保|特困人员|救助对象"),
    re.compile(r"赡养|抚养|扶养|供养"),
    re.compile(r"反恐|恐怖主义|国家安全|反间谍"),
    re.compile(r"治沙|沙化|荒漠化|防沙治沙"),
    re.compile(r"土地使用权|土地承包|耕地保护"),
    re.compile(r"归责|过错责任|严格责任|连带责任"),
    re.compile(r"信息披露|关联交易|实际控制人"),
    re.compile(r"滥用职权|玩忽职守|挪用公款|挪用资金"),
    re.compile(r"继续履行|合同履行|违约责任|格式条款"),
    re.compile(r"房地产开发|商品房|预售|物业"),
    re.compile(r"法条|法律适用|法律意见书|尽职调查"),
    re.compile(r"保理|融资租赁|特许经营|政府采购"),
]

# ── 非法律语境排除（来自 experimental legal_filter.py）──────

NON_LEGAL_CONTEXT: set[str] = {
    # 网络协议
    "TCP", "UDP", "IP", "DNS", "HTTP", "HTTPS", "PPP", "SMTP", "FTP",
    "协议栈", "网络协议", "滑动窗口", "三次握手",
    # 数学
    "函数", "导数", "积分", "微分", "方程", "矩阵", "向量",
    # 计算机
    "算法", "数据结构", "二叉树", "链表", "栈", "队列",
    "进程", "线程", "内存", "Cache", "DMA",
    "排序", "查找", "递归", "迭代",
    # 通用非法律领域
    "考研", "高考", "计算机", "数学",
}

# ── 默认 KB 词表路径 ─────────────────────────────────────────

DEFAULT_KB_VOCAB_PATH = "index_store/kb_vocab.json"
DEFAULT_LEGAL_DICT_PATH = "experiments/data/legal_dict.txt"


def load_kb_vocab(path: str = DEFAULT_KB_VOCAB_PATH) -> set[str]:
    """从 JSON 文件加载 KB 核心词表。

    Args:
        path: 词表 JSON 文件路径，默认 "index_store/kb_vocab.json"

    Returns:
        词表集合；文件不存在或加载失败时返回空集。
    """
    p = Path(path)
    if not p.exists():
        logger.debug("KB 词表文件不存在: %s，使用空词表", path)
        return set()
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = set(data)
        logger.debug("KB 词表加载成功: %d 个词条 (%s)", len(result), path)
        return result
    except Exception as e:
        logger.warning("KB 词表加载失败 (%s): %s", path, e)
        return set()


def load_legal_dict(path: str = DEFAULT_LEGAL_DICT_PATH) -> set[str]:
    """从 jieba 法律自定义词典文件加载法律术语。

    legal_dict.txt 格式为 jieba 自定义词典（词语 词频 词性），
    此函数提取每行的词语部分（排除注释行和空行）。

    Args:
        path: 法律词典文件路径，默认 "experiments/data/legal_dict.txt"

    Returns:
        法律术语集合；文件不存在或加载失败时返回空集。
    """
    p = Path(path)
    if not p.exists():
        logger.debug("法律词典文件不存在: %s，跳过", path)
        return set()
    try:
        terms = set()
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # jieba 词典格式: 词语 词频 词性
                term = line.split()[0]
                terms.add(term)
        logger.debug("法律词典加载成功: %d 个词条 (%s)", len(terms), path)
        return terms
    except Exception as e:
        logger.warning("法律词典加载失败 (%s): %s", path, e)
        return set()


# ══════════════════════════════════════════════════════════════════════════════
# LegalPreFilter
# ══════════════════════════════════════════════════════════════════════════════


class LegalPreFilter:
    """法律领域预过滤器 — 规则层零 LLM 开销的快速过滤器。

    与 QueryPreFilter 的 ``filter(query) -> FilterResult`` 接口兼容，
    可直接替换或串联使用。

    核心能力：
    - **法律关键词加权打分**：对 ~100 个法律术语进行命中计数，归一化为 0~1 分
    - **法律模式匹配**：~20 个正则，覆盖法条引用、法律程序、责任义务等场景
    - **非法律语境排除**：检测计算机/数学/网络协议等关键词，避免误拦截
    - **KB 词表辅助**：利用 KB 词表重叠率辅助判断查询相关性

    决策逻辑（优先级从高到低）：
    1. 空查询 → 拦截
    2. 闲聊/问候 → 拦截
    3. 纯符号/数字 → 拦截
    4. 非法律语境 → 检查强法律信号，无信号则拦截
    5. 加权总分 >= total_thresh → 放行
    6. 关键词分数 >= 0.25 → 放行（后备）
    7. 未匹配 → 拦截

    Attributes:
        kb_vocab: KB 核心词表集合
        kw_weight: 关键词得分在总分中的权重
        total_thresh: 总分放行阈值
    """

    def __init__(
        self,
        kb_vocab_path: Optional[str] = None,
        legal_dict_path: Optional[str] = None,
        kw_weight: float = 0.70,
        total_thresh: float = 0.20,
    ):
        """初始化 LegalPreFilter。

        Args:
            kb_vocab_path: KB 核心词表 JSON 文件路径。
                为 None 时使用默认路径 ``index_store/kb_vocab.json``。
            legal_dict_path: 法律词典文件路径（jieba 格式）。
                为 None 时使用默认路径 ``experiments/data/legal_dict.txt``。
                设为空字符串 "" 禁用外部词典加载。
            kw_weight: 关键词得分权重，取值范围 [0, 1]。
                总分 = kw_weight * kw_score + (1 - kw_weight) * vocab_score。
            total_thresh: 总分放行阈值。
                当总分 >= 此值时查询被放行。
        """
        self.kw_weight = kw_weight
        self.total_thresh = total_thresh
        self.detailed_logs = False

        # 加载并合并词表
        self.kb_vocab = load_kb_vocab(kb_vocab_path or DEFAULT_KB_VOCAB_PATH)
        self.legal_vocab: set[str] = set()
        if legal_dict_path is not None:
            self.legal_vocab = load_legal_dict(
                legal_dict_path or DEFAULT_LEGAL_DICT_PATH
            )
        # 去重合并：legal_dict 补充 kb_vocab
        merged = self.kb_vocab | self.legal_vocab
        added = len(merged) - len(self.kb_vocab)
        self.kb_vocab = merged

        import jieba

        self._jieba = jieba

        logger.info(
            "LegalPreFilter 初始化: kw_weight=%.2f, total_thresh=%.2f, "
            "kb_vocab=%d, legal_dict=%d, merged=%d (新增 %d)",
            self.kw_weight,
            self.total_thresh,
            len(self.kb_vocab) - added if self.legal_vocab else len(self.kb_vocab),
            len(self.legal_vocab),
            len(self.kb_vocab),
            added,
        )

    # ── 公开接口 ──────────────────────────────────────────────

    def set_detailed_logs(self, enabled: bool = True) -> None:
        """启用/禁用详细日志。"""
        self.detailed_logs = enabled

    def filter(self, query: str) -> FilterResult:
        """执行法律领域预过滤。

        Args:
            query: 用户原始查询文本

        Returns:
            FilterResult:
                - ``need_rag=True`` → 放行，进入后续检索流程
                - ``need_rag=False`` → 拦截，``skip_reason`` 标明原因
        """
        result = FilterResult(origin_query=query)

        # ── 1. 空查询检测 ────────────────────────────────────
        if not query or not query.strip():
            result.need_rag = False
            result.skip_reason = "empty_query"
            logger.debug("空查询拦截: query=%r", query)
            return result

        stripped = query.strip()

        # ── 2. 闲聊检测 ────────────────────────────────────
        for pattern in GREETING_PATTERNS:
            if pattern.match(stripped):
                result.need_rag = False
                result.skip_reason = "greeting"
                logger.debug("闲聊拦截: query=%r", query)
                return result

        # ── 3. 纯无意义检测 ──────────────────────────────────
        if NONSENSE_PATTERN.match(stripped):
            result.need_rag = False
            result.skip_reason = "nonsense"
            logger.debug("无意义拦截: query=%r", query)
            return result

        # ── 4. 非法律语境检测 ──────────────────────────────
        in_non_legal = self._in_non_legal_context(query)
        if in_non_legal:
            if not self._has_strong_legal_signal(query):
                result.need_rag = False
                result.skip_reason = "non_legal_context"
                logger.debug("非法律语境拦截: query=%r", query)
                return result
            logger.debug("非法律语境但检测到强法律信号，继续评分: query=%r", query)

        # ── 5. 计算法律关键词得分 ────────────────────────────
        kw_score = self._calc_legal_score(query, in_non_legal)

        # ── 6. 计算 KB 词表重叠得分 ──────────────────────────
        vocab_score = 0.0
        if self.kb_vocab:
            tokens = set(self._jieba.lcut(query))
            if not tokens:
                tokens = set(query.split())
            # 补充：直接从 legal_dict 匹配词条（弥补 jieba 不认识法律术语的问题）
            if self.legal_vocab:
                for term in self.legal_vocab:
                    if len(term) > 2 and term in query:
                        tokens.add(term)
            overlap = tokens & self.kb_vocab
            vocab_score = len(overlap) / max(len(tokens), 1)

        # ── 7. 加权总分 ────────────────────────────────────
        total_score = self.kw_weight * kw_score + (1 - self.kw_weight) * vocab_score
        result.kb_overlap = total_score

        # ── 详细日志（detailed_logs 启用时输出关键词匹配统计） ──
        if self.detailed_logs:
            matched_kw = [kw for kw in LEGAL_KEYWORDS if kw in query]
            matched_pats = []
            for pat in LEGAL_PATTERNS:
                m = pat.search(query)
                if m:
                    matched_pats.append(m.group())
            logger.info(
                "Prefilter 匹配详情 | query=%r | kw_score=%.3f | "
                "vocab_score=%.3f | total=%.3f | "
                "legal_kw_matched=%d/%d | patterns_matched=%d | "
                "vocab_overlap=%d/%d",
                query[:80], kw_score, vocab_score, total_score,
                len(matched_kw), len(LEGAL_KEYWORDS), len(matched_pats),
                len(overlap) if self.kb_vocab else 0,
                len(tokens) if self.kb_vocab else 0,
            )
        else:
            logger.debug(
                "评分结果 query=%r kw_score=%.3f vocab_score=%.3f total_score=%.3f",
                query,
                kw_score,
                vocab_score,
                total_score,
            )

        # ── 8. 决策 ────────────────────────────────────────
        if total_score >= self.total_thresh:
            logger.debug("总分放行 (total_score=%.3f >= total_thresh=%.2f)", total_score, self.total_thresh)
            return result

        if kw_score >= 0.25:
            logger.debug("关键词后备放行 (kw_score=%.3f >= 0.25)", kw_score)
            return result

        result.need_rag = False
        result.skip_reason = "irrelevant"
        logger.debug("无关拦截: query=%r kw_score=%.3f total_score=%.3f", query, kw_score, total_score)
        return result

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _in_non_legal_context(query: str) -> bool:
        """判断查询是否属于非法律语境。

        检查 ``NON_LEGAL_CONTEXT`` 中的计算机 / 数学 / 网络协议等关键词，
        以及数学公式模式（如 f(x)、sin、cos 等）。

        Args:
            query: 查询文本

        Returns:
            属于非法律语境返回 True，否则 False。
        """
        for kw in NON_LEGAL_CONTEXT:
            if kw in query:
                return True
        if re.search(r"(f\(x\)|ln|log|sin|cos|tan|lim|∑|∫)", query):
            return True
        return False

    @staticmethod
    def _has_strong_legal_signal(query: str) -> bool:
        """检查查询中是否包含强法律信号。

        在非法律语境下此方法被调用，用于判断是否应覆盖非法律语境判定。
        检查核心法律关键词和法律模式匹配两方面。

        Args:
            query: 查询文本

        Returns:
            包含强法律信号返回 True，否则 False。
        """
        # 检查核心法律关键词
        core_legal: set[str] = {
            "法律", "法规", "宪法", "刑法", "民法", "诉讼",
            "法院", "检察院", "律师", "违法", "犯罪",
            "立法", "司法", "执法",
        }
        for kw in core_legal:
            if kw in query:
                return True

        # 检查法律模式匹配
        for pat in LEGAL_PATTERNS:
            if pat.search(query):
                return True

        return False

    def _calc_legal_score(self, query: str, in_non_legal: bool = False) -> float:
        """计算法律关键词得分。

        Args:
            query: 查询文本
            in_non_legal: 是否处于非法律语境。
                为 True 时跳过强模式匹配的满分捷径，仅按关键词计数。

        Returns:
            0.0 ~ 1.0 的法律相关度分数。
        """
        if not query:
            return 0.0

        # 强模式匹配：任何 LEGAL_PATTERNS 命中 → kw_score = 1.0
        # （除非处于非法律语境，此时仅按关键词计数）
        if not in_non_legal:
            for pat in LEGAL_PATTERNS:
                if pat.search(query):
                    return 1.0

        # 关键词匹配计数 → 归一化
        match_count = 0
        for kw in LEGAL_KEYWORDS:
            if kw in query:
                match_count += 1

        kw_score = min(match_count / 6.0, 1.0)

        # 兜底：如果关键词分数低且 query 包含 legal_dict 中的法律术语 → 提升 0.2
        if kw_score < 0.5 and self.legal_vocab:
            # 用所有 legal_dict 术语中最长的 3 个匹配来判定
            matched_legal_terms = set()
            for term in self.legal_vocab:
                if len(term) > 2 and term in query:
                    matched_legal_terms.add(term)
                    if len(matched_legal_terms) >= 3:
                        break
            if matched_legal_terms:
                kw_score = max(kw_score, 0.4)

        return kw_score
