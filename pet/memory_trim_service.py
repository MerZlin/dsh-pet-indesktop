# -*- coding: utf-8 -*-
"""回收内存的服务壳：后台线程跑一轮 → 主线程冒泡。

为什么还要一层壳：``EmptyWorkingSet`` 要逐个进程开句柄，几十到几百毫秒不等，
放 GUI 线程会卡帧；而气泡必须在 GUI 线程弹。走本仓库既有的「后台线程写队列 →
主线程 tick 消费」老路（同 ``weather_service`` / ``voice_chime_service``）。
判定与统计在 ``pet/memory_trim.py``（纯逻辑、可离线单测）。
"""
from __future__ import annotations

import logging
import os
import threading

from PySide6.QtCore import QTimer

from . import memory_trim

logger = logging.getLogger("dsh-pet-standalone")

TICK_INTERVAL_MS = 300
_STATE_ATTR = "_memory_trim_state"
_TIMER_ATTR = "_memory_trim_timer"


def state(host) -> dict:
    current = getattr(host, _STATE_ATTR, None)
    if not isinstance(current, dict):
        current = {"busy": False, "queue": []}
        setattr(host, _STATE_ATTR, current)
    return current


def _cfg_value(host, key, default=None):
    cfg = getattr(host, "cfg", None)
    try:
        return cfg.get(key, default) if cfg is not None else default
    except Exception:
        return default


def skip_foreground(host) -> bool:
    """默认不动前台程序（被收掉后要重新读盘，用户会觉得卡）。"""
    return bool(_cfg_value(host, "mem_skip_foreground", True))


def min_bytes(host) -> int:
    floor = memory_trim.clean_min_mb(
        _cfg_value(host, "mem_min_size_mb", memory_trim.MEM_TRIM_MIN_MB)
    )
    return floor * 1024 * 1024


def _foreground_exe() -> str:
    """前台进程名（拿不到就空串）。**主线程**调用——vision 是 Qt 侧模块。"""
    try:
        from .vision import foreground_window_info

        info = foreground_window_info() or {}
        return str(info.get("process") or "").strip().lower()
    except Exception:
        return ""


def run_now(host) -> bool:
    """菜单「回收内存」：后台收一遍，回来报个数。已在收就只回一句。"""
    current = state(host)
    if current["busy"]:
        _present(host, "还在收拾呢，主人等人家一下下嘛")
        return True
    current["busy"] = True
    ensure_started(host)
    skip_exes = []
    if skip_foreground(host):
        name = _foreground_exe()
        if name:
            skip_exes.append(name)
    floor = min_bytes(host)

    def worker() -> None:
        try:
            got = memory_trim.trim_working_sets(
                skip_pids={os.getpid()}, skip_exes=skip_exes, min_bytes=floor,
            )
        except Exception as exc:          # 兜底：后台线程绝不把桌宠带崩
            logger.debug("回收内存失败", exc_info=True)
            got = {"error": str(exc)}
        state(host)["queue"].append(got)

    threading.Thread(target=worker, daemon=True, name="memory-trim").start()
    return True


def ensure_started(host) -> None:
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is None:
        # 无主 QTimer：宿主可能是测试替身（见 quiet_service 同款说明）
        timer = QTimer()
        timer.setInterval(TICK_INTERVAL_MS)
        timer.timeout.connect(lambda: tick(host))
        setattr(host, _TIMER_ATTR, timer)
    if not timer.isActive():
        timer.start()


def stop_timer(host) -> None:
    timer = getattr(host, _TIMER_ATTR, None)
    if timer is not None:
        timer.stop()


def tick(host) -> None:
    """主线程消费结果；队列空就停表，不常驻空转。"""
    queue = state(host)["queue"]
    while queue:
        got = queue.pop(0)
        state(host)["busy"] = False
        _present(host, memory_trim.summarize(got), duration_ms=4600)
    if not queue and not state(host)["busy"]:
        stop_timer(host)


def _present(host, text: str, *, duration_ms: int = 4200) -> None:
    from . import window_alerts

    try:
        window_alerts.show_alert(
            host, text, duration_ms=duration_ms, sticky=False,
            alert_type="memory_trim", priority=3,
        )
    except Exception:
        logger.exception("回收内存气泡展示失败")
