# -*- coding: utf-8 -*-
"""内存取证计数器（诊断专用，默认整体关闭）。

背景：打包版零交互运行 10 分钟 PrivateMemorySize64 从 253MB 涨到 273MB
（≈2MB/min），而句柄数 / GDI 对象 / 线程数全平、tracemalloc 双快照的
Python 堆也全平（47.6→47.7MB）——增长发生在 Qt/C++ 原生堆。要定位是哪条
链路（reader 换代 / 帧队列 / 首帧缓存 / 渲染重建）在涨，必须让进程自己
按时间轴吐计数，再与外部采样的进程内存曲线对齐。

设计约束：
- **默认零开销**：所有计数点都是 ``if mem_debug.ENABLED:`` 短路，关闭时
  热路径只多一次模块属性读取；注册表/采集器在关闭时不建立、不遍历。
- **不碰 Qt 线程纪律**：ticker 是 daemon 线程（不用 QTimer），只读
  Python 侧计数与 ``queue`` 快照，不做任何 Qt GUI 调用；每 60s 写一行，
  进程被强杀时最后一行最多陈 60s（日志 handler 每条 flush）。
- **只观测不改行为**：本模块不持有 clip/proc/queue 的强引用（队列用
  weakref 登记，线程按注册表里的弱引用判活），绝不延长任何对象寿命。

启用：``DSPET_MEM_DEBUG=1 python -m pet``（或测试里 set_enabled(True)）。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import weakref

logger = logging.getLogger(__name__)

ENV_FLAG = 'DSPET_MEM_DEBUG'

# 热路径短路开关：所有计数点读它。关闭时整条取证链不存在。
ENABLED = False

_lock = threading.Lock()
_counts: dict[str, int] = {}
_collectors: list = []
# reader 登记表：label -> (weakref(queue), weakref(thread), frame_bytes)
_readers: dict[str, tuple] = {}
_started_at = 0.0
_ticker: threading.Thread | None = None
_interval_s = 60.0


def _env_flag() -> bool:
    return str(os.environ.get(ENV_FLAG, '')).strip().lower() in ('1', 'true', 'yes', 'on')


def bump(name: str, n: int = 1) -> None:
    """累加计数（仅在 ENABLED 下被调用）。"""
    with _lock:
        _counts[name] = _counts.get(name, 0) + n


def register_collector(fn) -> None:
    """登记采集器（无参可调用 → dict）；每行日志前调用一次。"""
    with _lock:
        _collectors.append(fn)


def note_reader(label: str, q, thread, frame_bytes: int) -> None:
    """登记一个 reader 的帧队列（weakref，不延长寿命；关闭时无操作）。"""
    if not ENABLED:
        return
    try:
        entry = (weakref.ref(q), weakref.ref(thread), int(frame_bytes))
    except TypeError:
        return
    with _lock:
        _readers[label] = entry


def forget_reader(label: str) -> None:
    if not ENABLED:
        return
    with _lock:
        _readers.pop(label, None)


def _queue_snapshot(q) -> tuple[int, int]:
    """(帧数, 字节数)：优先按真实条目长度求和，退化到 qsize×帧大小。"""
    try:
        items = list(q.queue)
    except Exception:
        items = None
    if items is not None:
        frames = 0
        nbytes = 0
        for item in items:
            if item is None:
                continue
            try:
                data = item[0]
                nbytes += len(data)
                frames += 1
            except Exception:
                continue
        return frames, nbytes
    try:
        return int(q.qsize()), 0
    except Exception:
        return 0, 0


def live_readers() -> list[tuple[str, int, int]]:
    """存活 reader 的 (label, 队列帧数, 队列字节数)；顺带清掉已死登记。"""
    dead = []
    out = []
    with _lock:
        entries = list(_readers.items())
    for label, (qref, tref, _fb) in entries:
        q = qref()
        t = tref()
        if q is None or t is None or not t.is_alive():
            dead.append(label)
            continue
        frames, nbytes = _queue_snapshot(q)
        out.append((label, frames, nbytes))
    if dead:
        with _lock:
            for label in dead:
                _readers.pop(label, None)
    return out


def snapshot() -> dict:
    """采集器 + 计数快照（每行日志前调用）。"""
    with _lock:
        data = dict(_counts)
        collectors = list(_collectors)
    for fn in collectors:
        try:
            extra = fn()
        except Exception:
            continue
        if isinstance(extra, dict):
            data.update(extra)
    try:
        data['py_blocks'] = sys.getallocatedblocks()
    except Exception:
        pass
    return data


def format_line(tag: str = 'MEM') -> str:
    snap = snapshot()
    elapsed = time.monotonic() - _started_at if _started_at else 0.0
    parts = [f'[{tag}]', f't={elapsed:.1f}s']
    for key in sorted(snap):
        parts.append(f'{key}={snap[key]}')
    return ' '.join(parts)


def _tick_loop() -> None:
    while True:
        time.sleep(_interval_s)
        try:
            logger.info(format_line())
        except Exception:
            pass


def set_enabled(on: bool, interval_s: float = 60.0) -> None:
    """开关取证（幂等）：置位后启动 ticker daemon 线程。"""
    global ENABLED, _started_at, _ticker, _interval_s
    ENABLED = bool(on)
    if not ENABLED:
        return
    _interval_s = max(5.0, float(interval_s))
    if _started_at == 0.0:
        _started_at = time.monotonic()
    if _ticker is None or not _ticker.is_alive():
        _ticker = threading.Thread(
            target=_tick_loop, daemon=True, name='mem-debug-ticker',
        )
        _ticker.start()
    logger.info(
        '内存取证已启用（%s=1，每 %.0fs 一行）——仅诊断，默认关闭',
        ENV_FLAG, _interval_s,
    )


def _reset_for_tests() -> None:
    """仅测试用：复位开关与计数（ticker 线程随进程存活，不影响后续用例）。"""
    global ENABLED, _started_at
    ENABLED = False
    _started_at = 0.0
    with _lock:
        _counts.clear()
        _collectors.clear()
        _readers.clear()


if _env_flag():
    set_enabled(True)
