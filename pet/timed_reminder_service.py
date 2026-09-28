# -*- coding: utf-8 -*-
"""定时提醒服务：约 20 秒一拍，到点冒一句（可选先带今日汇总）。

判定与去重在 ``pet/timed_reminder.py``（纯逻辑、可离线单测）；本模块只管
读配置、跑节拍、冒气泡，以及菜单用的开/关。

为什么 20 秒而不是每秒：槽位去重键是 ``"YYYY-MM-DD HH:MM"``，
20 秒一拍足以在分钟内命中一次，而常驻开销只有报时服务的同级。
"""
from __future__ import annotations

import logging
import random
from datetime import datetime

from PySide6.QtCore import QTimer

from . import daily_summary as daily_summary_mod
from . import quiet_service
from . import timed_reminder

logger = logging.getLogger("dsh-pet-standalone")

TICK_INTERVAL_MS = 20_000
_STATE_ATTR = "_timed_reminder_state"
_TIMER_ATTR = "_timed_reminder_timer"


def state(host) -> dict:
    current = getattr(host, _STATE_ATTR, None)
    if not isinstance(current, dict):
        current = {"dedupe": {}}
        setattr(host, _STATE_ATTR, current)
    return current


def _cfg_value(host, key, default=None):
    cfg = getattr(host, "cfg", None)
    try:
        return cfg.get(key, default) if cfg is not None else default
    except Exception:
        return default


def enabled(host) -> bool:
    return bool(_cfg_value(host, "timed_on", True))


def rules(host) -> list[dict]:
    return timed_reminder.clean_timed_rules(_cfg_value(host, "timed_rules", None))


def is_running(host) -> bool:
    timer = getattr(host, _TIMER_ATTR, None)
    return bool(timer is not None and timer.isActive())


def ensure_started(host) -> None:
    """按配置启停节拍（幂等）：关掉开关且没在跑时什么也不做。"""
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is None:
        # 无主 QTimer：宿主可能是测试替身（不是 QObject），带 parent 会 TypeError；
        # 引用挂在 host 属性上，生命周期与窗口一致（与本仓库既有服务同口径）。
        timer = QTimer()
        timer.setInterval(TICK_INTERVAL_MS)
        timer.timeout.connect(lambda: tick(host))
        setattr(host, _TIMER_ATTR, timer)
    if not enabled(host):
        return
    if not timer.isActive():
        timer.start()


def stop_timer(host) -> None:
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is not None:
        timer.stop()


def toggle(host) -> bool:
    """菜单/设置里的开与关；返回切换后的状态。"""
    cfg = getattr(host, "cfg", None)
    new_value = not enabled(host)
    if cfg is not None:
        cfg.set("timed_on", new_value)
        saver = getattr(cfg, "save", None)
        if callable(saver):
            saver()
    if new_value:
        ensure_started(host)
        _present(host, f"好呀，人家按着 {len(rules(host))} 条规则守着时间啦")
    else:
        stop_timer(host)
        _present(host, "定时提醒先关掉了哦")
    return new_value


def tick(host, now: datetime | None = None) -> None:
    """一拍：到点就说；同分钟只触发一次（键在 timed_reminder 里）。"""
    if not enabled(host):
        return
    fired, new_state = timed_reminder.due_rule_ids(
        rules(host), state(host)["dedupe"], now or datetime.now(),
    )
    state(host)["dedupe"] = new_state
    for rule in fired:
        _fire(host, rule)


def _fire(host, rule: dict) -> None:
    lines = timed_reminder.clean_lines(rule.get("lines"))
    if not lines:
        return
    text = random.choice(lines)
    if rule.get("summary"):
        head = quiet_service.summary_text(host)
        if head and head != daily_summary_mod.EMPTY_SUMMARY:
            text = f"{head}。{text}"
    _present(host, text, duration_ms=5200)
    logger.info("定时提醒触发 rule=%s", rule.get("id"))


def _present(host, text: str, *, duration_ms: int = 4200) -> None:
    from . import window_alerts

    try:
        window_alerts.show_alert(
            host, text, duration_ms=duration_ms, sticky=False,
            alert_type="timed_reminder", priority=3,
        )
    except Exception:
        logger.exception("定时提醒气泡展示失败")
