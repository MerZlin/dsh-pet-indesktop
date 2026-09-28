# -*- coding: utf-8 -*-
"""用久了提醒的服务壳：约 5 秒一拍，看前台应用 + 键鼠空闲，到点冒一句。

判定在 ``pet/app_usage.py``（纯逻辑、可离线单测）；前台进程名与空闲秒数复用
``pet/vision.py`` 的既有实现（``foreground_window_info`` / ``get_system_idle_seconds``），
不重写 Win32。

为什么 5 秒而不是报时那种 20 秒：累计精度要靠它——20 秒拍在"刚过阈值"时会晚最多
20 秒才提示，而 5 秒只晚 5 秒；空闲冻结判定也同理。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QTimer

from . import app_usage

logger = logging.getLogger("dsh-pet-standalone")

TICK_INTERVAL_MS = 5_000
_TRACKER_ATTR = "_app_usage_tracker"
_TIMER_ATTR = "_app_usage_timer"


def _cfg_value(host, key, default=None):
    cfg = getattr(host, "cfg", None)
    try:
        return cfg.get(key, default) if cfg is not None else default
    except Exception:
        return default


def enabled(host) -> bool:
    return bool(_cfg_value(host, "app_usage_on", True))


def rules(host) -> dict:
    return app_usage.clean_rules(_cfg_value(host, "app_usage_rules", None))


def tracker(host) -> app_usage.UsageTracker:
    current = getattr(host, _TRACKER_ATTR, None)
    if not isinstance(current, app_usage.UsageTracker):
        current = app_usage.UsageTracker(rules(host))
        setattr(host, _TRACKER_ATTR, current)
    return current


def is_running(host) -> bool:
    timer = getattr(host, _TIMER_ATTR, None)
    return bool(timer is not None and timer.isActive())


def ensure_started(host) -> None:
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is None:
        # 无主 QTimer：宿主可能是测试替身（见 quiet_service 同款说明）
        timer = QTimer()
        timer.setInterval(TICK_INTERVAL_MS)
        timer.timeout.connect(lambda: tick(host))
        setattr(host, _TIMER_ATTR, timer)
    if not enabled(host):
        return
    tracker(host).set_rules(rules(host))      # 设置改了立刻生效
    if not timer.isActive():
        timer.start()


def stop_timer(host) -> None:
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is not None:
        timer.stop()


def toggle(host) -> bool:
    cfg = getattr(host, "cfg", None)
    new_value = not enabled(host)
    if cfg is not None:
        cfg.set("app_usage_on", new_value)
        saver = getattr(cfg, "save", None)
        if callable(saver):
            saver()
    if new_value:
        ensure_started(host)
        _present(host, "好呀，人家帮你盯着用时啦")
    else:
        stop_timer(host)
        _present(host, "用久了提醒先关掉咯")
    return new_value


def _foreground() -> tuple[str, float | None]:
    """(前台进程名, 键鼠空闲秒数)。拿不到就回空串 / None（判定侧会按默认走）。"""
    try:
        from .vision import foreground_window_info, get_system_idle_seconds

        info = foreground_window_info() or {}
        idle = None
        try:
            idle = float(get_system_idle_seconds())
        except Exception:
            idle = None
        return str(info.get("process") or "").strip().lower(), idle
    except Exception:
        return "", None


def tick(host, now: float | None = None) -> None:
    """一拍：算一次该不该说话。全局开关关着时**照样喂**（计数照记，不补报）。"""
    import time as _time

    stamp = _time.monotonic() if now is None else float(now)
    exe, idle = _foreground()
    text = tracker(host).feed(exe, stamp, idle)
    if not text:
        return
    if not enabled(host):
        return
    _present(host, text, duration_ms=5200)


def _present(host, text: str, *, duration_ms: int = 4200) -> None:
    from . import window_alerts

    try:
        window_alerts.show_alert(
            host, text, duration_ms=duration_ms, sticky=False,
            alert_type="app_usage", priority=3,
        )
    except Exception:
        logger.exception("用久了提醒气泡展示失败")
