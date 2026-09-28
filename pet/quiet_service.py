# -*- coding: utf-8 -*-
"""免打扰服务壳：状态挂载、每秒节拍、菜单回调、今日汇总入口。

为什么单独一个模块：``pet/window.py`` 与 ``pet/modern_settings_dialog.py`` 都有
行数预算（见 ``tests/test_architecture.py``），免打扰的定时器、菜单回调与汇总拼装
一律不进这两个上帝文件。宿主（PetWindow 实例）只在运行时被挂几个私有属性，
模块内所有函数都以 ``host`` 入参工作，因此没有跨模块的窗口生命周期耦合。

与 ``pet/window_alerts.py`` 的分工：

- 本模块负责**判定与调度**（什么时候压、压到什么时候、结束后汇报什么）；
- ``window_alerts`` 负责**在闸门处执行**（普通提醒进暂存而不是丢弃）。

抑制源是"或"的关系：设置窗口打开、免打扰、前台全屏，任意一个成立就压住普通展示。
三者各自记在 ``host`` 的不同属性上，所以设置窗口关闭时不会把免打扰一起关掉。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QTimer

from . import daily_summary as daily_summary_mod
from . import quiet_mode

logger = logging.getLogger("dsh-pet-standalone")

TICK_INTERVAL_MS = 1000

_STATE_ATTR = "_quiet_state"
_TIMER_ATTR = "_quiet_timer"
_SETTINGS_FLAG_ATTR = "_bubble_suppressed_settings"
_FULLSCREEN_ATTR = "_fullscreen_suppressed"


# ------------------------------------------------------------------ 状态
def state(host) -> quiet_mode.QuietState:
    """取（必要时创建）宿主上的免打扰状态。"""
    current = getattr(host, _STATE_ATTR, None)
    if not isinstance(current, quiet_mode.QuietState):
        current = quiet_mode.QuietState()
        setattr(host, _STATE_ATTR, current)
    return current


def quiet_active(host) -> bool:
    return state(host).active()


def left(host) -> int:
    return state(host).left()


def fullscreen_suppressed(host) -> bool:
    return bool(getattr(host, _FULLSCREEN_ATTR, False))


def suppression_active(host) -> bool:
    """是否处于"该闭嘴"的抑制态（免打扰中，或前台全屏且在跟踪）。"""
    return quiet_active(host) or fullscreen_suppressed(host)


def _config(host):
    return getattr(host, "cfg", None)


def _cfg_value(host, key, default):
    cfg = _config(host)
    try:
        return cfg.get(key, default) if cfg is not None else default
    except Exception:      # 配置对象异常不该让免打扰把自己搞崩
        return default


def default_minutes(host) -> int:
    return quiet_mode.normalize_minutes(
        _cfg_value(host, "quiet_minutes_default", quiet_mode.DEFAULT_QUIET_MINUTES)
    )


def fullscreen_watch_enabled(host) -> bool:
    return bool(_cfg_value(host, "quiet_also_on_fullscreen", True))


# ------------------------------------------------------------------ 抑制闸门
def refresh_suppression(host) -> None:
    """重新计算"气泡要不要压住"，并让 window_alerts 执行隐藏/恢复。

    只改设置窗口那一份标记，真正生效的值由 ``window_alerts.set_bubble_suppressed``
    按"或"关系算出——这样三个抑制源互不覆盖。
    """
    from . import window_alerts

    settings_open = bool(getattr(host, _SETTINGS_FLAG_ATTR, False))
    window_alerts.set_bubble_suppressed(host, settings_open)


def hold(host, text: str) -> bool:
    """把一条被压住的提醒记进暂存队列（``window_alerts`` 的闸门调用）。"""
    return state(host).hold(text)


# ------------------------------------------------------------------ 开始 / 结束
def start(host, minutes=None, *, announce: bool = True) -> int:
    """开始免打扰：压住普通气泡，提醒进暂存，到点汇报。返回生效分钟数。"""
    minutes = state(host).start(
        default_minutes(host) if minutes is None else minutes
    )
    ensure_started(host)
    refresh_suppression(host)
    if announce:
        _bubble(host, f"好嘛，我安静 {minutes} 分钟，有事我先攒着")
    logger.info("quiet start minutes=%s", minutes)
    return minutes


def stop(host, *, announce: bool = True) -> int:
    """结束免打扰（到点也走这里）：解除抑制并汇报攒下的提醒。"""
    left_before = state(host).stop()
    held = state(host).take_held()
    refresh_suppression(host)
    if announce:
        head = "我回来啦" if left_before <= 0 else "好啦，不憋着了"
        _bubble(host, f"{head}～{quiet_mode.held_summary(len(held), held[-1] if held else '')}")
    logger.info("quiet stop left=%s held=%s", left_before, len(held))
    return left_before


def _bubble(host, text: str, *, duration_ms: int = 4200) -> None:
    """用提醒队列展示一句话（走 window_alerts，保证一次只出一个气泡）。"""
    from . import window_alerts

    try:
        window_alerts.show_alert(
            host, text, duration_ms=duration_ms, sticky=False,
            alert_type="quiet/summary", priority=3,
        )
    except Exception:
        logger.exception("免打扰气泡展示失败")


# ------------------------------------------------------------------ 定时器
def ensure_started(host) -> None:
    """确保节拍器在跑（幂等）：开始免打扰、或启动全屏跟踪时调用。"""
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is None:
        timer = QTimer(host)
        timer.setInterval(TICK_INTERVAL_MS)
        timer.timeout.connect(lambda: tick(host))
        setattr(host, _TIMER_ATTR, timer)
    if not timer.isActive():
        timer.start()


def stop_timer(host) -> None:
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is not None:
        timer.stop()


def tick(host, *, probe_index: int | None = None) -> None:
    """每秒节拍：到点自动结束免打扰并汇报（全屏状态由窗口层信号驱动）。"""
    if state(host).expire_if_due():
        stop(host)
        stop_timer(host)


def set_fullscreen_active(host, active: bool) -> None:
    """前台全屏状态变化（由 pet/window_screen.py 的既有 watcher 调用）。

    刻意**不自己探测**：窗口层已经有一条 1 秒的全屏 watcher（`fullscreen_changed`
    信号，服务自动隐藏桌宠），再起一条既费系统调用又会和它打架。
    """
    active = bool(active) and fullscreen_watch_enabled(host)
    if active == fullscreen_suppressed(host):
        return
    host._fullscreen_suppressed = active
    refresh_suppression(host)


# ------------------------------------------------------------------ 今日汇总
def summary_text(host) -> str:
    """拼今天的汇总句：余额 + 应用时长 + Agent 消耗，缺哪段少哪段。"""
    config_dir = ""
    cfg = _config(host)
    config_dir = getattr(cfg, "dir", "") or getattr(cfg, "config_dir", "")
    used = daily_summary_mod.read_today_usage(
        daily_summary_mod.usage_book_path(config_dir)
    ) if config_dir else 0.0
    return daily_summary_mod.build_daily_summary(
        balance_used=used or None,
        balance_text=str(getattr(host, "_last_balance_text", "") or ""),
        app_times=getattr(host, "_app_time_book", None),
        agent_cost_text=str(getattr(host, "_last_agent_cost_text", "") or ""),
        min_seconds=int(_cfg_value(host, "summary_min_seconds",
                                   daily_summary_mod.DEFAULT_SUMMARY_MIN_SECONDS) or 0),
        max_apps=int(_cfg_value(host, "summary_max_apps",
                                daily_summary_mod.DEFAULT_SUMMARY_MAX_APPS) or 1),
    )


def show_daily_summary(host) -> None:
    """菜单里点「今日汇总」：把今天的小结冒出来看一眼。"""
    _bubble(host, summary_text(host), duration_ms=5200)


def remember_balance(host, text: str) -> None:
    """记下最近一次余额文案，供「今日汇总」复用（余额气泡渲染后由 app 调用）。

    刻意不解析余额数字：余额域已经有自己的措辞（币种、峰谷说明），汇总直接引用
    同一句话可以避免同一件事出现两种说法。
    """
    line = str(text or "").strip()
    if line:
        host._last_balance_text = line


# ------------------------------------------------------------------ 菜单文案
def menu_stop_label(host) -> str:
    """结束免打扰的菜单文案（开着的时候把剩余时间写进去）。"""
    if not quiet_active(host):
        return "结束免打扰"
    return f"结束免打扰（{quiet_mode.format_left(left(host))}）"
