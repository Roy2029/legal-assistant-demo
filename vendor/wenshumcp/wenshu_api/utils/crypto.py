"""加密/动态参数工具。

⚠️ 2026-07 实测：裁判文书网的反爬模型已升级。当前搜索网关
（`/website/parse/rest.q4w`）校验的核心令牌是 **ciphertext**（见下方
generate_ciphertext），不再依赖历史上的 vjkl5 / vl5x / guid / number。
旧版 vl5x 相关代码保留为可注入的兼容层（register_vl5x_generator），但
对当前站点已不再有效。

ciphertext 算法（逆向自站点 strToBinary.js::cipher + website.js）：
    timestamp = Date.now()                 // 毫秒时间戳
    salt      = $.WebSite.random(24)        // 24 位随机串
    iv        = yyyyMMdd                    // 当天日期
    enc       = DES3.encrypt(timestamp, salt, iv).toString()  // 3DES/CBC/Pkcs7, base64
    str       = salt + iv + enc
    ciphertext = strTobinary(str)           // 逐字符 charCodeAt().toString(2)，空格分隔

此外本模块还提供：
  - des3_encrypt：登录密码 / ciphertext 共用的 3DES/CBC/PKCS7/base64 加密；
  - wenshu_random / str_to_binary：ciphertext 的底层构件；
  - get_vl5x / register_vl5x_generator：历史兼容层（当前站点已弃用）。

登录为 OAuth 流程（account.court.gov.cn），旧的 crud `AppUserDTO@login`
通道已失效；详见 README “登录 / OAuth”。
"""

from __future__ import annotations

import base64
import datetime
import random
import re
import string
import uuid

from Crypto.Cipher import DES3
from Crypto.Util.Padding import pad, unpad

# 一组较新的桌面浏览器 UA，按会话随机选用，降低指纹集中度。
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# 站点动态脚本使用的置换字符集（公开逆向版本，线上若更新需替换）。
_VL5X_KEY = (
    "PJzers3W7yfx8i0OkzuJv9aVmNp2hU4c1tB5d6jXAYSqQRTZCEoG6LMIeKwFgHn"
    "p2o1i0u9y8t7r6e5w4q3a2s1d0f9g8h7j6k5l4p3o2i1u0y9t8r7e6w5q4a3s2d"
    "1f0g9h8j7k6l5p4o3i2u1y0t9r8e7w6q5a4s3d2f1g0h9j8k7l6p5o4i3u2y1t0"
)

# 用户可注入自定义 vl5x 生成器（见 register_vl5x_generator）。
_custom_vl5x_generator = None

# 站点登录密码加密所用的 3DES 密钥（逆向自 lawyee WebSite 框架 DES3.encrypt）。
DES3_DEFAULT_KEY = "sL9p4mS2mSVTSBzWn4p16Mu7"


def register_vl5x_generator(func):
    """注入你从线上提取的精确 vl5x 生成函数。

    用法：
        def my_vl5x(vjkl5: str) -> str:
            ...  # 从浏览器网络面板抓取 dynamic 脚本后移植
        register_vl5x_generator(my_vl5x)
    """
    global _custom_vl5x_generator
    _custom_vl5x_generator = func


def random_guid() -> str:
    """生成大写、无连字符的 GUID（站点要求格式，如 'A1B2C3...'）。"""
    return uuid.uuid4().hex.upper()


def random_ua() -> str:
    """随机返回一个 User-Agent。"""
    return random.choice(USER_AGENTS)


def random_string(length: int = 10) -> str:
    """生成指定长度的随机字母数字串（部分动态参数需要）。"""
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def get_vl5x(vjkl5: str) -> str:
    """根据 vjkl5 推导 vl5x 令牌。

    若已通过 register_vl5x_generator 注入精确实现，则优先使用它；
    否则回退到公开逆向版本（可能因为站点更新而失效）。

    重要：vl5x 是搜索网关校验的核心。若线上接口返回“参数错误/验证失败”，
    多半是此算法与当前站点不一致，请按 README 校准。
    """
    if _custom_vl5x_generator is not None:
        return _custom_vl5x_generator(vjkl5)

    # —— 公开逆向版本（起点，可能需按线上校正）——
    # 算法：遍历 vjkl5 每个字符，按其在字符集中的位置做映射并拼接。
    if not vjkl5:
        raise ValueError("vjkl5 为空，无法推导 vl5x；请先初始化会话。")
    out = []
    key_len = len(_VL5X_KEY)
    for ch in vjkl5:
        # 用字符的 ASCII 码定位到置换集，叠加索引避免相邻重复
        idx = (ord(ch) + len(out)) % key_len
        out.append(_VL5X_KEY[idx])
    return "".join(out)


def extract_vjkl5_from_cookie(cookie_header: str) -> str | None:
    """从 Set-Cookie 文本中提取 vjkl5 的值（便于调试）。"""
    m = re.search(r"vjkl5=([^;]+)", cookie_header)
    return m.group(1) if m else None


def des3_encrypt(plaintext: str, key: str = DES3_DEFAULT_KEY,
                 iv: str | None = None) -> str:
    """用与站点一致的 3DES/CBC/PKCS7/base64 加密明文（登录密码 / ciphertext 等字段）。

    站点实现（lawyee WebSite 框架）：
        CryptoJS.TripleDES.encrypt(val, key, {
            iv: 当天日期 yyyyMMdd, mode: CBC, padding: Pkcs7
        }).toString()   // CryptoJS 默认 base64

    :param plaintext: 待加密明文（如登录密码）
    :param key: 3DES 密钥（24 字节 ASCII）。默认站点密钥。
    :param iv: 初始化向量；默认取当天日期 yyyyMMdd（与站点一致）。
    :return: base64 编码的密文；明文为空时返回空串。
    """
    if not plaintext:
        return ""
    key_b = key.encode("utf-8")
    if iv is None:
        iv = datetime.datetime.now().strftime("%Y%m%d")
    iv_b = iv.encode("utf-8")
    cipher = DES3.new(key_b, DES3.MODE_CBC, iv_b)
    ct = cipher.encrypt(pad(plaintext.encode("utf-8"), DES3.block_size))
    return base64.b64encode(ct).decode("ascii")


def des3_decrypt(ciphertext_b64: str, key: str,
                 iv: str | None = None) -> str:
    """用与站点一致的 3DES/CBC/PKCS7/base64 解密（搜索响应的 result 字段）。

    站点实现（lawyee WebSite 框架 DES3.decrypt）：
        CryptoJS.TripleDES.decrypt(b, CryptoJS.enc.Utf8.parse(key), {
            iv: CryptoJS.enc.Utf8.parse(iv || 当天 yyyyMMdd),
            mode: CBC, padding: Pkcs7
        })  ->  UTF-8 明文

    搜索网关返回的 JSON 形如 {"code":1, "secretKey":<key>, "result":<密文>}，
    其中 result 即用 secretKey 作为 3DES 密钥、iv=当天 yyyyMMdd 加密后的 base64。

    :param ciphertext_b64: base64 密文（来自响应 result 字段）。
    :param key: 3DES 密钥（站点用 secretKey，24 字节 ASCII）。
    :param iv: 初始化向量；默认取当天日期 yyyyMMdd。
    :return: 解密后的 UTF-8 明文（通常为 JSON 字符串）。
    """
    if not ciphertext_b64 or not key:
        return ""
    key_b = key.encode("utf-8")
    if iv is None:
        iv = datetime.datetime.now().strftime("%Y%m%d")
    iv_b = iv.encode("utf-8")
    raw = base64.b64decode(ciphertext_b64)
    cipher = DES3.new(key_b, DES3.MODE_CBC, iv_b)
    pt = unpad(cipher.decrypt(raw), DES3.block_size)
    return pt.decode("utf-8")


# ---------------------------------------------------------------------------
# 搜索网关反爬令牌：ciphertext（逆向自站点 strToBinary.js / website.js）
# ---------------------------------------------------------------------------
# 站点字符集（$.WebSite.random）：数字 -> 小写字母 -> 大写字母，共 62 个。
_WENSHU_RANDOM_CHARS = string.digits + string.ascii_lowercase + string.ascii_uppercase


def wenshu_random(size: int = 24) -> str:
    """复刻 $.WebSite.random(size)：从 0-9a-zA-Z 中取 size 个随机字符。

    站点用 Math.round(Math.random() * (len-1)) 取索引，结果落在 [0, 61]，
    与 random.choice 等价（仅分布细节不同，不影响令牌合法性）。
    """
    return "".join(random.choice(_WENSHU_RANDOM_CHARS) for _ in range(size))


def str_to_binary(text: str) -> str:
    """复刻站点 strTobinary：每个字符 charCodeAt().toString(2)，空格分隔。

    注：站点字符均为 ASCII（salt/iv 为字母数字，enc 为 base64），
    故 ord() 与 JS charCodeAt() 完全一致。
    """
    return " ".join(bin(ord(ch))[2:] for ch in text)


def generate_ciphertext() -> str:
    """生成搜索网关所需的 ciphertext 反爬令牌。

    站点算法（strToBinary.js::cipher）：
        timestamp = Date.now()                 // 毫秒时间戳
        salt      = $.WebSite.random(24)        // 24 位随机串
        iv        = yyyyMMdd                    // 当天日期
        enc       = DES3.encrypt(timestamp, salt, iv).toString()  // 3DES/CBC/Pkcs7, base64
        str       = salt + iv + enc
        ciphertext = strTobinary(str)           // 逐字符转二进制，空格分隔

    服务端持有同一套逻辑：从 ciphertext 的明文段解出 salt+iv，再用 3DES 解密
    enc 得到 timestamp 并校验时效，因此无需 vjkl5 / guid / number。

    :return: 形如 "110101 1001101 1100001 ..." 的二进制分组串。
    """
    now = datetime.datetime.now()
    timestamp = str(int(now.timestamp() * 1000))
    salt = wenshu_random(24)
    iv = now.strftime("%Y%m%d")
    enc = des3_encrypt(timestamp, salt, iv)  # 复用同一 3DES 实现
    raw = salt + iv + enc
    return str_to_binary(raw)
