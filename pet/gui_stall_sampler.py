# -*- coding: utf-8 -*-
"""GUI 线程卡断现场采样器（仅观测模式启用，常态零开销）。

疑难卡顿（数百 ms 到数秒的 GUI 冻结）的事后归因：jank 看门狗只能在冻结
**结束后**报警，报不出冻结**期间** GUI 线程在干什么。本模块在观测模式
（perfstats.ENABLED）下起一个 daemon 采样线程，以 ~50ms 周期抓取主线程
Python 栈的顶部帧摘要存进环形缓冲；jank 看门狗发现 >_DUMP_THRESHOLD_S 的
空窗时，把覆盖该窗口的样本打到日志——样本里就是冻结期间的真实执行点。

开销：仅观测模式启用；每周期一次 sys._current_frames() 读取 + 顶部 6 帧
格式化，每秒约 20 次，量级可忽略。产品默认路径（perfstats 关闭）完全不
创建线程、不产生任何调用。
"""
from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
import weakref

log = logging.getLogger(__name__)

_SAMPLE_INTERVAL_S = 0.05      # 采样周期（20Hz，够覆盖 100ms+ 级卡顿）
_RING_CAPACITY = 240           # 环形缓冲长度（12s 现场）
_DUMP_THRESHOLD_S = 0.15       # 超过该 GUI 空窗即落样本（配合 jank 看门狗调用）
_TOP_FRAMES = 6                # 每次采样保留的顶部帧数


class _GuiStallSampler:
    def __init__(self, main_tid: int) -> None:
        self._main_tid = main_tid
        self._ring: list[tuple[float, str]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='pet-gui-stall-sampler')

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(_SAMPLE_INTERVAL_S):
            frame = sys._current_frames().get(self._main_tid)
            if frame is None:
                continue
            stack = traceback.extract_stack(frame, limit=_TOP_FRAMES)
            summary = ' <- '.join(
                f'{f.filename.rsplit("/", 1)[-1].rsplit(chr(92), 1)[-1]}:{f.lineno}:{f.name}'
                for f in stack)
            self._ring.append((time.monotonic(), summary))
            if len(self._ring) > _RING_CAPACITY:
                del self._ring[:len(self._ring) - _RING_CAPACITY]

    def dump_gap(self, gap_s: float, now: float) -> None:
        """jank 看门狗回调：把覆盖 (now-gap, now] 窗口的样本落日志。"""
        since = now - gap_s - 0.05
        samples = [s for s in self._ring if s[0] >= since]
        if not samples:
            return
        log.warning('GUI 冻结现场（%.0fms，%d 个采样点，旧→新）：', gap_s * 1000, len(samples))
        seen = None
        for ts, summary in samples:
            if summary != seen:  # 连续相同栈只打一条（冻结=栈不动，正好压缩）
                log.warning('  [t-%.0fms] %s', (now - ts) * 1000, summary)
                seen = summary

    def stop(self) -> None:
        self._stop.set()


_attached: dict[int, _GuiStallSampler] = {}


def attach(win) -> None:
    """给窗口的主线程挂采样器（幂等）；窗口消亡后经弱引用自清，无需 detach。"""
    tid = threading.main_thread().ident
    if tid is None or tid in _attached:
        return
    sampler = _GuiStallSampler(tid)
    sampler.start()
    _attached[tid] = sampler
    # 窗口销毁后停止采样线程（弱引用自清，不占 closeEvent 路径）
    ref = weakref.ref(win, lambda _r: _attached.pop(tid, _GuiStallSampler()).stop()
                      if tid in _attached else None)
    sampler._win_ref = ref


def dump_gap(gap_s: float) -> None:
    """供 jank 看门狗调用：把最近一次大空窗的现场样本落日志。"""
    sampler = _attached.get(threading.main_thread().ident)
    if sampler is not None:
        sampler.dump_gap(gap_s, time.monotonic())
