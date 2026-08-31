"""跨进程限频冷却窗口。

为什么需要它
------------
裁判文书网的 ``code=9``（网关拒绝）经 2026-08-29/30 实测定性为**服务端限频**，
且绑定 IP：换 SESSION、整轮重登都救不回来。而重登要再走一次「点选文字」验证码，
必须人工介入——也就是说，在被限频的窗口里盲目重登，唯一效果是白白消耗用户一次
人工操作。

本模块把「最近一次被限频的时间」持久化到磁盘（跨进程共享），从而在冷却期内
**快速失败**并明确告知还要等多久，而不是徒劳地重登。

设计取舍
--------
* 只记时间戳，不记任何凭据 / 请求内容（避免敏感信息落盘）。
* 用「写临时文件 + os.replace」做原子写，避免读到半截 JSON。
* 连续被限频时按指数退避拉长冷却（封顶），避免刚恢复又立刻打满。
* 读失败一律视为「不在冷却期」——宁可多试一次，也不能永久锁死自己。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional


def default_rate_path() -> str:
    """冷却状态文件的默认位置：~/.wenshu/rate_state.json。"""
    env = os.getenv("WENSHU_RATE_STATE")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), ".wenshu", "rate_state.json")


class RateGuard:
    """记录并查询「服务端限频」冷却窗口。

    典型用法::

        guard = RateGuard()
        left = guard.remaining()
        if left > 0:
            raise RuntimeError(f"被限频，请 {int(left)}s 后再试")
        try:
            ...  # 业务请求
        except SessionExpiredError:
            guard.mark_blocked()
        else:
            guard.clear()
    """

    def __init__(self, path: Optional[str] = None, cooldown_sec: float = 900.0,
                 max_cooldown_sec: float = 3600.0):
        self.path = path or default_rate_path()
        self.cooldown_sec = float(cooldown_sec)
        self.max_cooldown_sec = float(max_cooldown_sec)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #
    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _store(self, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass  # 持久化失败不能影响主流程

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def _cooldown_for(self, streak: int) -> float:
        """连续被限频第 N 次时的冷却时长（指数退避 + 封顶）。"""
        n = max(1, int(streak))
        val = self.cooldown_sec * (2 ** (n - 1))
        return min(val, self.max_cooldown_sec)

    def remaining(self) -> float:
        """返回剩余冷却秒数；0 表示可以放行。"""
        data = self._load()
        blocked_at = data.get("blocked_at")
        if not isinstance(blocked_at, (int, float)) or blocked_at <= 0:
            return 0.0
        streak = data.get("streak") or 1
        try:
            streak = int(streak)
        except (TypeError, ValueError):
            streak = 1
        window = self._cooldown_for(streak)
        left = (float(blocked_at) + window) - time.time()
        return left if left > 0 else 0.0

    def mark_blocked(self, reason: str = "code=9") -> float:
        """记录一次被限频，返回本次设定的冷却总时长（秒）。"""
        with self._lock:
            data = self._load()
            now = time.time()
            prev_at = data.get("blocked_at")
            prev_streak = 0
            try:
                prev_streak = int(data.get("streak") or 0)
            except (TypeError, ValueError):
                prev_streak = 0
            # 距上次被限频已经过去很久（超过 2 倍窗口）→ 视为新的一轮，重置连击
            if isinstance(prev_at, (int, float)) and prev_at > 0:
                old_window = self._cooldown_for(max(1, prev_streak))
                if now - float(prev_at) > old_window * 2:
                    prev_streak = 0
            streak = max(1, prev_streak + 1)
            window = self._cooldown_for(streak)
            self._store({
                "blocked_at": now,
                "streak": streak,
                "window_sec": window,
                "reason": reason,
            })
            return window

    def clear(self) -> None:
        """一次成功请求后清除冷却（证明服务端已放行）。"""
        with self._lock:
            data = self._load()
            if not data:
                return
            data["blocked_at"] = 0
            data["streak"] = 0
            data["cleared_at"] = time.time()
            self._store(data)

    def reset(self) -> None:
        """彻底删除冷却状态（供人工干预 / 测试用）。"""
        with self._lock:
            try:
                if os.path.exists(self.path):
                    os.remove(self.path)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # 诊断
    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        """返回当前冷却状态（供 session_status() 暴露给上层）。"""
        data = self._load()
        left = self.remaining()
        return {
            "cooling_down": left > 0,
            "remaining_sec": int(left) if left > 0 else 0,
            "streak": data.get("streak", 0),
            "window_sec": int(data.get("window_sec") or 0),
            "reason": data.get("reason"),
            "state_path": self.path,
        }


# --------------------------------------------------------------------------- #
# 模块级默认实例（进程内共享）
# --------------------------------------------------------------------------- #
_GUARD: Optional[RateGuard] = None
_GUARD_LOCK = threading.Lock()


def get_guard(cooldown_sec: Optional[float] = None,
              path: Optional[str] = None) -> RateGuard:
    """返回进程内唯一的 RateGuard（首次调用时按参数创建）。"""
    global _GUARD
    with _GUARD_LOCK:
        if _GUARD is None:
            kwargs = {}
            if cooldown_sec is not None:
                kwargs["cooldown_sec"] = cooldown_sec
            if path is not None:
                kwargs["path"] = path
            _GUARD = RateGuard(**kwargs)
        return _GUARD
