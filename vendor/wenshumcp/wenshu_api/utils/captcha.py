"""验证码求解器：优先使用离线 ddddocr 识别，可回退到人工交互。

两种求解器都实现相同的调用约定：
    solver(image_bytes: bytes, url: str) -> str
返回识别出的文本；无法识别时抛 CaptchaRequiredError（携带图片），
由客户端触发“刷新重试”逻辑。

所有关键状态（获取图片 / 识别成功 / 识别失败 / 回退）均通过 wenshu_api
日志输出，便于观测。
"""

from __future__ import annotations

import base64
import os
import re
import sys
import time

from ..exceptions import CaptchaRequiredError
from .log import get_logger


class CaptchaSolver:
    """求解器基类。"""

    def __call__(self, image_bytes: bytes, url: str) -> str:
        raise NotImplementedError

    def _save(self, image_bytes: bytes, save_dir: str | None) -> str | None:
        if not save_dir:
            return None
        os.makedirs(save_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(save_dir, f"captcha_{ts}.png")
        with open(path, "wb") as f:
            f.write(image_bytes)
        return path


class DdddOcrSolver(CaptchaSolver):
    """基于 ddddocr 的离线验证码识别（默认求解器）。

    识别结果为空时抛 CaptchaRequiredError，触发客户端刷新重试；
    若 ddddocr 未安装，构造时直接抛 RuntimeError。
    """

    def __init__(self, save_dir: str | None = None):
        self.save_dir = save_dir
        self.logger = get_logger()
        try:
            import ddddocr  # 延迟导入，避免未安装时影响整体导入
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "未安装 ddddocr，请执行 `pip install ddddocr`"
            ) from exc
        self._ocr = ddddocr.DdddOcr(show_ad=False)
        self.logger.info("[验证码] ddddocr 已加载（离线识别模式）")

    def __call__(self, image_bytes: bytes, url: str) -> str:
        saved = self._save(image_bytes, self.save_dir)
        if saved:
            self.logger.debug("[验证码] 图片已保存 %s (len=%d)", saved, len(image_bytes))
        try:
            text = self._ocr.classification(image_bytes).strip()
        except Exception as exc:  # ddddocr/PIL 解码失败（非图片/损坏）
            self.logger.warning("[验证码] 图片解码/识别失败：%s", exc)
            raise CaptchaRequiredError(
                f"ddddocr 识别异常：{exc}", captcha_image=image_bytes
            )
        if not text:
            self.logger.warning("[验证码] ddddocr 识别为空，将刷新重试")
            raise CaptchaRequiredError(
                "ddddocr 识别为空", captcha_image=image_bytes
            )
        self.logger.info("[验证码] 识别成功: %r", text)
        return text


class InteractiveSolver(CaptchaSolver):
    """人工交互求解器：保存图片到本地，在终端提示输入。

    非交互终端（无 TTY）时直接抛 CaptchaRequiredError（图片仍保存）。
    """

    def __init__(self, save_dir: str | None = None, enabled: bool = True):
        self.save_dir = save_dir
        self.enabled = enabled
        self.logger = get_logger()

    def __call__(self, image_bytes: bytes, url: str) -> str:
        saved = self._save(image_bytes, self.save_dir)
        if not self.enabled:
            raise CaptchaRequiredError(
                f"验证码未处理（已禁用）。图片: {saved}", captcha_image=image_bytes
            )
        if not sys.stdin.isatty():
            raise CaptchaRequiredError(
                f"非交互终端，图片已保存：{saved}，请在交互环境运行",
                captcha_image=image_bytes,
            )
        print(f"[验证码] 图片已保存：{saved}", file=sys.stderr)
        return input("请输入验证码字符：").strip()


def build_solver(use_ddddocr: bool = True, save_dir: str | None = None) -> CaptchaSolver:
    """构造求解器：默认 ddddocr，不可用时回退交互式。"""
    if use_ddddocr:
        try:
            return DdddOcrSolver(save_dir=save_dir)
        except RuntimeError as exc:
            get_logger().warning("[验证码] ddddocr 不可用：%s；回退交互式", exc)
    return InteractiveSolver(save_dir=save_dir)


# --------------------------------------------------------------------------- #
# 内嵌 data URI 提取（新版文书网把验证码以 data:image/jpg;base64,... 注入页面）
# --------------------------------------------------------------------------- #
_DATA_URI_RE = re.compile(
    r'data:image/(?:jpeg|jpg|png|gif|webp|bmp);base64,([A-Za-z0-9+/=]{50,})',
    re.IGNORECASE,
)


def extract_data_uri(text: str | bytes) -> bytes | None:
    """从 HTML/JS 文本中提取第一个 base64 编码的验证码图片并解码。

    返回图片原始字节；未找到或解码失败返回 None。
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", "ignore")
    m = _DATA_URI_RE.search(text)
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(1))
        return raw
    except Exception:
        return None
