"""常量定义：接口地址、请求头、字段映射、法院层级与案件类型。

说明：中国裁判文书网的反爬与接口实现长期处于变动中。下列地址与字段键名
均来自对该站历史版本的公开逆向分析，若线上版本已更新，请按 README 中
“反爬机制 / 接口校准”一节重新核对并替换此处常量。
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# 基础地址
# --------------------------------------------------------------------------- #
BASE_URL = "https://wenshu.court.gov.cn"

# 搜索/计数/分组 网关（2026-07 实测）。POST 到该地址，由表单字段 cfg 指定
# 真正的后端 DTO 服务。网关校验 ciphertext 反爬令牌 + 已登录 SESSION Cookie +
# __RequestVerificationToken（前端 base.random(24) 生成的随机串，非服务端下发）。
GATEWAY_URL = f"{BASE_URL}/website/parse/rest.q4w"

# 首页：首次访问用于建立会话、获取初始 Cookie（含 vjkl5 的雏形）。
HOME_URL = f"{BASE_URL}/"

# 验证码图片接口：新版站点把图形验证码放在 /code/image（GET，带随机参数 bust 缓存），
# 返回 image/jpeg，需本地 OCR 出 number。历史上的 /ValiCode/GetCode 现已废弃。
GET_CODE_URL = f"{BASE_URL}/code/image"

# 携带验证码的页面（用于设置 Referer，提升通过率）。
LOGIN_PAGE_URL = f"{BASE_URL}/website/wenshu/181010CARHS5BS3C/index.html?open=login"

# 搜索应用页（Referer 用）。pageId 为站点分配的模块 id（2026-07 实测稳定值）。
# 若线上搜索页路径/ pageId 变化，重跑 research/oauth_login.py 抓最新即可。
PAGE_ID = "6f08bb13c52123bee1d0c4cc5100c94a"
SEARCH_APP_PATH = "181217BMTKHNT2W0"
SEARCH_APP_URL = f"{BASE_URL}/website/wenshu/{SEARCH_APP_PATH}/index.html"
SEARCH_REFERER = f"{SEARCH_APP_URL}?pageId={PAGE_ID}"

# 搜索网关的 cfg（DTO 服务名，逆向自认证态搜索应用 JS）。
SEARCH_CFG = "com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@queryDoc"        # 主搜索（文书列表）
SEARCH_CFG_COUNT = "com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@wsCountSearch"  # 命中总数
SEARCH_CFG_LEFT = "com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@leftDataItem"   # 左侧分组统计
SEARCH_CFG_TIP = "com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@smartTipSearch"  # 关键词联想

# 分类树接口：用于获取法院层级、案由等下拉结构（历史接口，可能需按线上校准）。
TREE_URL = f"{BASE_URL}/website/query/getTreeList"

# 文书全文内容接口（历史版本，已废弃；当前走 docInfoSearch，见下）。
DOC_CONTENT_URL = f"{BASE_URL}/website/query/GetDocContentByDocId"

# 文书列表接口（requestUri 取值，旧版；新搜索走 GATEWAY_URL + cfg）。
SERVICE_LIST = "/website/query/ListContent"

# 文书详情（全文）网关：与搜索同属 /website/parse/rest.q4w，由 cfg 区分。
# 2026-07 实测：docInfoSearch 需要 已登录 SESSION + ciphertext + docId
# （= 搜索结果 rowkey），**不含** pageId；Referer 指向详情页。
DOC_INFO_CFG = "com.lawyee.judge.dc.parse.dto.SearchDataDsoDTO@docInfoSearch"
DETAIL_APP_PATH = "181107ANFZ0BXSK4"   # 详情/阅读器应用模块 id（线上若变更需重抓）
DETAIL_PAGE_URL = f"{BASE_URL}/website/wenshu/{DETAIL_APP_PATH}/index.html"


def detail_referer(doc_id: str) -> str:
    """构造 docInfoSearch 所需的 Referer（详情页 URL，带 docId 查询参数）。"""
    return f"{DETAIL_PAGE_URL}?docId={doc_id}"

# --------------------------------------------------------------------------- #
# 默认请求头
# 注意：wenshu 对 User-Agent、Referer、X-Requested-With 等校验较严，
# 不要随意缺省；建议配合随机化 UA 池使用（见 utils/crypto.random_ua）。
# --------------------------------------------------------------------------- #
DEFAULT_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/website/wenshu/181010CARHS5BS3C/index.html",
    "X-Requested-With": "XMLHttpRequest",
    # User-Agent 在会话初始化时由 CookieManager 注入随机值
}

# 默认 User-Agent：裁判文书网对 UA 校验较严，docInfoSearch / queryDoc 网关均
# 拒绝非 Chrome UA（含 Firefox、macOS/Linux Chrome），故默认固定为 Windows Chrome。
# utils.crypto.random_ua() 的多 UA 池仅在明确需要时使用，不建议作为默认值。
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# --------------------------------------------------------------------------- #
# 查询条件字段键名映射
# 站点使用 key/value 结构描述筛选条件，下列为公开逆向中常见的键名。
# 若线上版本键名有变，只需在此处集中修改即可。
# --------------------------------------------------------------------------- #
FIELD_KEYS = {
    "keyword": "s21",         # 全文检索关键词（2026-07 实测搜索网关字段）
    "cause": "s4",            # 案由（beta：线上键名可能随版本变化）
    "court_name": "s2",       # 法院名称（beta）
    "case_type": "docType",   # 案件类型
    "trial_procedure": "spcx",  # 审判程序
    "court_level": "fy",      # 法院层级（部分版本）
    "doc_id": "docId",        # 文书 ID
    "publish_date": "s50",    # 发布日期（用于排序/筛选）
}

# 排序字段（s50=发布日期，默认倒序）
DEFAULT_SORT = "s50:desc"

# --------------------------------------------------------------------------- #
# 案件类型（案件类型 docType 取值，常见枚举）
# --------------------------------------------------------------------------- #
CASE_TYPES = {
    "刑事案件": "xs",
    "民事案件": "ms",
    "行政案件": "xz",
    "赔偿案件": "pc",
    "执行案件": "zx",
}

# --------------------------------------------------------------------------- #
# 法院层级（用于组合查询/结构展示）
# --------------------------------------------------------------------------- #
COURT_LEVELS = [
    "最高人民法院",
    "高级人民法院",
    "中级人民法院",
    "基层人民法院",
    "专门人民法院",  # 含海事、知识产权、金融、互联网、军事等专门法院
]

# --------------------------------------------------------------------------- #
# 常用案由示例（完整案由树极大，此处给出代表性子集，更多可在运行时
# 通过 get_cause_tree() 从站点拉取）。
# --------------------------------------------------------------------------- #
COMMON_CAUSES = [
    "民事案由>合同、准合同纠纷>借款合同纠纷>民间借贷纠纷",
    "民事案由>物权纠纷>所有权纠纷>相邻关系纠纷",
    "刑事案由>侵犯财产罪>盗窃罪",
    "行政案由>行政处罚>罚款",
    "执行案由>申请执行",
]

# 请求默认超时（秒）
DEFAULT_TIMEOUT = 15

# 默认分页大小
DEFAULT_PAGE_SIZE = 10

# --------------------------------------------------------------------------- #
# 登录相关（逆向自 lawyee WebSite 框架 + 登录页 index.js）
# --------------------------------------------------------------------------- #
# 登录/保存数据网关：$.WebSite.saveData 实际 POST 到 crud 网关（非 parse 网关）。
CRUD_URL = f"{BASE_URL}/website/crud/rest.q4w"

# 登录接口在 WebSite 框架中的 DTO 配置串（saveData 的 param.cfg 字段）。
LOGIN_CFG = "com.lawyee.wbsttools.web.parse.dto.AppUserDTO@login"
LOGIN_AUTH_CODE = "WenshuWebUser"

# 站点编码：saveData 会附带 siteEnCode 字段（$website.enCode）。
SITE_ENCODE = "bFVTbc2ti9IK5NtwcfmepCaB"

# 密码 3DES 加密密钥（DES3.encrypt(val, key)，iv 为当天 yyyyMMdd）。
DES3_KEY = "sL9p4mS2mSVTSBzWn4p16Mu7"

# .env 中的凭据键名（脱密调用，不硬编码账号密码）。
ENV_USER_NAME = "WENSHU_USER_NAME"
ENV_PASSWORD = "WENSHU_PASSWORD"

# --------------------------------------------------------------------------- #
# 算法配置热更新（站点漂移韧性）
# --------------------------------------------------------------------------- #
# 裁判文书网的反爬/协议长期处于变动中。下列「易变常量」（pageId、cfg 字符串、
# 字段映射、UA 模板、应用模块路径）抽到外部 algo_config.json，运行时优先加载并
# 覆盖上方硬编码默认值；加载失败或缺字段时回退到默认值，保证向后兼容。
#
# 覆盖优先级（从高到低）：
#   1. 环境变量 WENSHU_ALGO_CONFIG 指向的文件
#   2. 包目录下的 algo_config.json（随包发布，可作为本地可替换文件）
#   3. 当前工作目录下的 algo_config.json
#   4. 上方硬编码默认值
#
# 效果：站点小改版时只需替换 algo_config.json（或推送新文件），用户无需重装——
# 这正是「韧性优先于完美混淆」原则的体现（详见项目封装计划文档）。
# --------------------------------------------------------------------------- #
import json as _json
import os as _os


def _load_algo_config() -> dict:
    candidates = []
    env_path = _os.getenv("WENSHU_ALGO_CONFIG")
    if env_path:
        candidates.append(env_path)
    _here = _os.path.dirname(_os.path.abspath(__file__))
    candidates.append(_os.path.join(_here, "algo_config.json"))   # 包内置（可替换）
    candidates.append(_os.path.join(_os.getcwd(), "algo_config.json"))
    for p in candidates:
        if p and _os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as _f:
                    data = _json.load(_f)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue
    return {}


ALGO = _load_algo_config()
if ALGO:
    PAGE_ID = ALGO.get("page_id", PAGE_ID)
    SEARCH_CFG = ALGO.get("search_cfg", SEARCH_CFG)
    SEARCH_CFG_COUNT = ALGO.get("search_cfg_count", SEARCH_CFG_COUNT)
    SEARCH_CFG_LEFT = ALGO.get("search_cfg_left", SEARCH_CFG_LEFT)
    SEARCH_CFG_TIP = ALGO.get("search_cfg_tip", SEARCH_CFG_TIP)
    DOC_INFO_CFG = ALGO.get("doc_info_cfg", DOC_INFO_CFG)
    if "search_app_path" in ALGO:
        SEARCH_APP_PATH = ALGO["search_app_path"]
        SEARCH_APP_URL = f"{BASE_URL}/website/wenshu/{SEARCH_APP_PATH}/index.html"
        SEARCH_REFERER = f"{SEARCH_APP_URL}?pageId={PAGE_ID}"
    if "detail_app_path" in ALGO:
        DETAIL_APP_PATH = ALGO["detail_app_path"]
        DETAIL_PAGE_URL = f"{BASE_URL}/website/wenshu/{DETAIL_APP_PATH}/index.html"
    if ALGO.get("field_keys"):
        FIELD_KEYS.update(ALGO["field_keys"])
    DEFAULT_SORT = ALGO.get("default_sort", DEFAULT_SORT)
    if ALGO.get("default_user_agent"):
        DEFAULT_USER_AGENT = ALGO["default_user_agent"]
