# -*- coding: utf-8 -*-
"""子项2 回归：FrameSeqClip 预取看门狗（修"画面经常卡住不动"）。

根因：异步预取 worker 所在共享线程失能时（退出收口后的孤儿 worker、线程事件
循环停摆），``_request`` 的 queued 调用永远不被处理，``_advance`` 就永远等不到
帧——``_awaiting`` 挂死 = 画面冻结（用户实机现象）。

本文件验收四条硬约束：
1. awaiting 滞留超阈值（``PREFETCH_STALL_MS``）→ 重发 ``_request`` + WARNING
   （含 clip 目录名/帧号/wanted/pending 大小）；
2. 连续超时 ``PREFETCH_STALL_LIMIT`` 次 → 同步加载兜底（播放链不断，不冻结）；
3. 恢复后静默（到货即清零，不再告警）；
4. 共享线程 isRunning 复查：线程死了重建并换挂新 worker，交付链自动接回。

纪律（AGENTS.md 时序测试）：看门狗钟经模块属性 ``_now`` 注入假钟，零 sleep；
异步交付用事件泵 + 宽预算（``_pump_until``，沿用 test_frameseq_clip）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet import frameseq_clip
from pet.frameseq_clip import (
    PREFETCH_STALL_LIMIT,
    PREFETCH_STALL_MS,
    FrameSeqClip,
)
from tests.test_frameseq_clip import _make_frames, _pump_until

app = QApplication.instance() or QApplication([])


class FakeClock:
    """可推进的假钟（替换 ``frameseq_clip._now``，不 sleep 赌时序）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _clip_with_clock(tmp_path, monkeypatch, count: int = 4):
    d = tmp_path / "clip"
    _make_frames(d, count=count)
    clip = FrameSeqClip(d)
    clock = FakeClock()
    monkeypatch.setattr(frameseq_clip, "_now", clock)
    return clip, clock, d


def _stub_request(clip, monkeypatch, sent: list):
    """替换 ``_request`` 但保留真实去重口径（wanted/pending），只记录请求流水。

    直接挂 ``sent.append`` 会绕过 ``_wanted`` 记账，看门狗"重发"就无从区分。
    """

    def fake_request(idx: int) -> None:
        if idx >= len(clip._frames) or idx in clip._pending or idx == clip._wanted:
            return
        clip._wanted = idx
        sent.append(idx)

    monkeypatch.setattr(clip, "_request", fake_request)


def test_watchdog_thresholds_are_the_documented_ones():
    """阈值/连续次数是实机调参口径（500ms / 3 次），改动必须重新评估。"""
    assert PREFETCH_STALL_MS == 500.0
    assert PREFETCH_STALL_LIMIT == 3


def test_watchdog_resends_request_and_warns_on_stall(tmp_path, monkeypatch, caplog):
    """滞留超阈值：重发预取请求 + WARNING（含目录名/帧号/wanted/pending）。"""
    clip, clock, d = _clip_with_clock(tmp_path, monkeypatch)
    sent: list[int] = []
    _stub_request(clip, monkeypatch, sent)  # worker 永不交付（保留真实去重口径）
    try:
        assert clip.start() is True
        assert sent == [0]
        clip._pending.clear()

        clip._advance()                      # 帧 1 未到货：登记 awaiting + 催取
        assert clip._awaiting == 1
        assert sent == [0, 1]

        clock.advance(0.4)
        clip._advance()                      # 未到阈值：静默且去重（不重发）
        assert "预取超时" not in caplog.text
        assert sent == [0, 1]

        clock.advance(0.2)                   # 累计 0.6s > 0.5s
        clip._advance()

        assert "预取超时" in caplog.text
        assert d.name in caplog.text         # clip 目录名
        assert "frame=1" in caplog.text      # 帧号
        assert "wanted=" in caplog.text and "pending=" in caplog.text
        assert sent == [0, 1, 1]             # 真的重发了
        assert clip._stall_count == 1
        assert clip._awaiting == 1           # 仍在等同一帧（不跳帧）
    finally:
        clip.close()


def test_watchdog_falls_back_to_sync_load_after_limit(tmp_path, monkeypatch, caplog):
    """连续 N 次超时 → 同步加载兜底：画面继续，播放链不断。"""
    clip, clock, _d = _clip_with_clock(tmp_path, monkeypatch)
    monkeypatch.setattr(clip, "_request", lambda _idx: None)  # worker 完全失能
    frames: list[int] = []
    clip.frameChanged.connect(frames.append)
    try:
        assert clip.start() is True
        clip._pending.clear()
        clip._advance()                      # 登记 awaiting=1（看门狗计时起点）
        for _ in range(PREFETCH_STALL_LIMIT):
            clock.advance(0.6)
            clip._advance()

        assert "降级同步加载" in caplog.text
        assert frames == [1]                 # 兜底帧真的上屏（不是空转）
        assert clip.currentImage() is not None
        assert clip._awaiting == -1          # 等待态收口
        assert clip._stall_count == 0        # 计数清零，下一轮重新计时
    finally:
        clip.close()


def test_watchdog_is_silent_after_recovery(tmp_path, monkeypatch, caplog):
    """恢复后不再告警：到货即清零，下一帧重新计时。"""
    clip, clock, _d = _clip_with_clock(tmp_path, monkeypatch)
    sent: list[int] = []
    _stub_request(clip, monkeypatch, sent)
    try:
        assert clip.start() is True
        clip._pending.clear()
        clip._advance()
        clock.advance(0.6)
        clip._advance()
        assert "预取超时" in caplog.text

        # 帧到货 = 恢复：等待态与连续超时计数一起清零
        clip._on_loaded(1, QImage(str(clip._frames[1])))
        assert clip._awaiting == -1
        assert clip._stall_count == 0

        caplog.clear()
        clock.advance(5.0)                   # 很久之后才轮到下一帧
        clip._advance()                      # 新的等待刚起步（计时在阈值内）
        assert "预取超时" not in caplog.text
    finally:
        clip.close()


def test_watchdog_revives_dead_shared_thread(tmp_path, monkeypatch):
    """共享线程失能复查：重建线程 + 换挂新 worker，交付链自动接回。"""
    clip, clock, _d = _clip_with_clock(tmp_path, monkeypatch)
    frames: list[int] = []
    clip.frameChanged.connect(frames.append)
    try:
        assert clip.start() is True
        dead = clip._prefetch_thread
        assert dead is not None and dead.isRunning()

        frameseq_clip._shutdown_shared_prefetch()   # 模拟退出收口/线程骤停
        assert not dead.isRunning()
        assert not clip._prefetch_thread_alive()

        clip._pending.clear()
        clip._advance()                      # 登记 awaiting=1（看门狗计时起点）
        clock.advance(0.6)
        clip._advance()                      # 看门狗发现线程已死 → 重建 + 重发

        assert clip._prefetch_thread in frameseq_clip._shared_threads
        assert clip._prefetch_thread.isRunning()
        assert clip._worker.thread() is clip._prefetch_thread
        assert len(clip._retired_workers) == 1
        assert clip._retired_workers[0] is not clip._worker

        _pump_until(lambda: 1 in frames, timeout_s=5.0)  # 新线程照常交付
        assert clip.currentFrameNumber() == 1
    finally:
        clip.close()
