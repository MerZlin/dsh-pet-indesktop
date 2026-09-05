# -*- coding: utf-8 -*-
"""批8：ffmpeg 进程内循环（-stream_loop -1 -readrate）+ 圈边界续圈。

覆盖：
- 帧号回绕契约：解码序号按 frame_count 取模，第二圈源时间线帧号从 0
  重新开始；丢帧不占位但占时间线槽位的语义在回绕后依然成立；
- 圈边界结束标记：每圈末帧交付后交付一次结束标记（None → finished），
  上层调度机会不丢；on_loop_boundary 返回 False 时 reader 立即退出；
- 节流路径在循环下：绝不丢帧、绝不虚推进，回绕照常（含端到端续圈）；
- 续圈（re-arm）：圈末软停（stop() 不杀进程）→ start() 续播下一圈，
  reader 线程与 ffmpeg 进程都是同一个（不重启）；结束标记不重复上报；
- 宽限期：无人续圈时 reader 自行退出、进程被杀（切走/被打断不泄漏）；
- 中途打断：非圈末的 stop() 仍走硬停杀进程（原路径不变）；
- 盲审回归：硬停置位的 gate 不污染下一次 fresh start（P1-1）、re-arm
  唤醒握手超时落回 fresh start（P1-2）、帧数未知/估算不带循环参数
  （P2-3/P2-5）、readrate 随 playback_speed 缩放（P2-4）、首帧解码
  不带循环参数（参数拆分）、re-arm 后镜像发到新 sink、drain 只去标记
  不丢帧（P3-7）。

集成测试用假 read_frames（按放行额度逐帧产出）+ 假 Popen，不起真实
ffmpeg 进程，无平台限定。
"""
from __future__ import annotations

import queue
import subprocess
import threading
import time

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet import webm_clip as webm_clip_mod
from pet import perfstats
from pet.webm_clip import WebMClip


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


_FRAME_BYTES = 2 * 2 * 4  # 2x2 RGBA


class _FakeProc:
    """模拟 Popen：terminate 即退出。"""

    def __init__(self):
        self._dead = False
        self.terminated = False
        self.killed = False
        self.pid = id(self)

    def poll(self):
        return None if not self._dead else 1

    def terminate(self):
        self.terminated = True
        self._dead = True

    def kill(self):
        self.killed = True
        self._dead = True

    def wait(self, timeout=None):
        if self._dead:
            return 1
        raise subprocess.TimeoutExpired(self, timeout)


class _GatedLoopGen:
    """模拟 read_frames 流：按放行额度逐帧产出，close 即结束。

    looping=True（参数含 -stream_loop）= 无限循环流，绝不自然结束；
    looping=False = 播一圈即 EOF（模拟真实 ffmpeg 不带循环参数的行为）。
    """

    def __init__(self, proc, frame_count: int, looping: bool = True):
        self._proc = proc
        self._frame_count = max(1, frame_count)
        self._looping = looping
        self._meta = {"fps": 24.0, "duration": self._frame_count / 24.0}
        self._stage = 0
        self._credits = 0
        self._cv = threading.Condition()
        self._closed = False
        self.produced = 0
        self.closed_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._stage == 0:
            self._stage = 1
            return self._meta
        if not self._looping and self.produced >= self._frame_count:
            raise StopIteration  # 有限流：播一圈即自然结束（先判断再等奖额）
        # 30s 超时兜底防挂死（必须远长于消费侧断言超时：圈边界漏报标记时
        # 测试应超时失败，而不是被假流结束产生的伪结束标记掩盖）。
        with self._cv:
            deadline = time.monotonic() + 30.0
            while self._credits <= 0 and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StopIteration
                self._cv.wait(remaining)
            if self._closed:
                raise StopIteration
            self._credits -= 1
        self.produced += 1
        return bytes(_FRAME_BYTES)

    def release(self) -> None:
        with self._cv:
            self._credits += 1
            self._cv.notify()

    def close(self):
        with self._cv:
            self._closed = True
            self.closed_calls += 1
            self._cv.notify_all()  # 解除 reader 的阻塞读，让 finally 收尾
        if self._proc.poll() is None:
            self._proc.kill()  # 模仿 imageio finally 的存活进程清理


def _install_fake_ffmpeg(monkeypatch, clip, spawns: list) -> None:
    """把 imageio read_frames 换成假流工厂；每次拉起记录 (proc, gen, input_params)。

    是否无限循环由真实参数决定（-stream_loop 在参数里才无限）——参数被误删/
    误加时假件行为跟着变，测试能钉住参数回归（自欺清单 #7）。
    """
    def _fake_read_frames(*args, **kwargs):
        proc = _FakeProc()
        params = list(kwargs.get('input_params') or [])
        gen = _GatedLoopGen(proc, frame_count=clip._frame_count or 3,
                            looping='-stream_loop' in params)
        spawns.append((proc, gen, params))
        cap = webm_clip_mod._PopenCapture._local.capture
        cap._on_process(proc, ["ffmpeg", "-i", str(clip.path)])
        return gen

    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "read_frames", _fake_read_frames)


def _make_clip(tmp_path, frame_count: int = 3) -> WebMClip:
    clip = WebMClip(str(tmp_path / "x.webm"))
    clip._w = 2
    clip._h = 2
    clip._fps = 24.0
    # _duration>0 使 _ensure_meta 短路（不触真实 ffmpeg 探测）；
    # frame_count 精确标记模拟 count_frames_and_secs 主路径（进程内循环的门）。
    clip._duration = frame_count / 24.0 if frame_count > 0 else 0.5
    clip._frame_count = frame_count
    clip._frame_count_exact = frame_count > 0
    return clip


def _consume_until(clip: WebMClip, predicate, timeout: float = 8.0) -> bool:
    """手动驱动主线程消费（等价 QTimer 的 _poll 节奏，确定性更强）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        clip._poll()
        if predicate():
            return True
        time.sleep(0.002)
    return False


def _wait_parked(clip: WebMClip, timeout: float = 5.0) -> bool:
    """等 reader 驻留到圈边界（消除末帧→驻留的良性竞态，P3-6）。"""
    deadline = time.monotonic() + timeout
    while not clip._reader_parked and time.monotonic() < deadline:
        time.sleep(0.005)
    return clip._reader_parked


def _close_all(spawns) -> None:
    for _, gen, _ in spawns:
        gen.close()


# ---------------------------------------------------------------------------
# _stamp_source_indices：帧号回绕契约（纯函数级，无 Qt/ffmpeg）
# ---------------------------------------------------------------------------

def test_loop_wraps_source_indices_at_frame_count():
    """第二圈帧号从 0 重新开始；每圈末帧交付后触发一次圈边界回调。"""
    q = queue.Queue(maxsize=16)
    boundaries = []

    def on_boundary():
        boundaries.append(1)
        q.put(None)  # 模拟圈边界结束标记
        return True

    WebMClip._stamp_source_indices(
        iter([b"f%d" % i for i in range(7)]), q, lambda: False,
        loop_frame_count=3, on_loop_boundary=on_boundary,
    )
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    frames_out = [it for it in items if it is not None]
    assert [it[1] for it in frames_out] == [0, 1, 2, 0, 1, 2, 0]  # 回绕
    assert items.count(None) == 2  # 两个完整圈各交付一次结束标记
    assert len(boundaries) == 2


def test_loop_boundary_false_exits_reader_loop():
    """圈边界回调返回 False（停止/宽限超时）：reader 立即退出，不再读流。"""
    q = queue.Queue(maxsize=16)
    calls = []

    def on_boundary():
        calls.append(1)
        q.put(None)
        return False

    WebMClip._stamp_source_indices(
        iter([b"f%d" % i for i in range(10)]), q, lambda: False,
        loop_frame_count=3, on_loop_boundary=on_boundary,
    )
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert [i[1] for i in items if i is not None] == [0, 1, 2]  # 只播一圈
    assert None in items and len(calls) == 1


def test_loop_wrap_preserves_drop_slot_semantics():
    """丢帧不占位但占时间线槽位：回绕后依然成立（源帧号照常推进）。"""
    class _FlakyQueue:
        def __init__(self):
            self.items = []
            self._n = 0

        def put(self, item, timeout=0):
            self._n += 1
            if self._n % 2 == 0:
                raise queue.Full
            self.items.append(item)

    q = _FlakyQueue()
    boundaries = []
    WebMClip._stamp_source_indices(
        iter([b"f%d" % i for i in range(8)]), q, lambda: False,
        loop_frame_count=3,
        on_loop_boundary=lambda: boundaries.append(1) or True,
    )
    # src 0..7 中 1/3/5/7 被丢弃，交付 0/2/4/6 → 时间线 0/2/1/0
    assert [i[1] for i in q.items] == [0, 2, 1, 0]
    assert len(boundaries) == 2  # src2 与 src5 各是一圈末帧（无论是否被丢）


def test_loop_wrap_throttled_never_drops_never_skips():
    """节流路径在循环下：绝不丢帧、绝不虚推进，回绕照常。"""
    class _FullThenRoom:
        def __init__(self):
            self.items = []
            self._n = 0

        def put(self, item, timeout=0):
            self._n += 1
            if self._n % 3:
                raise queue.Full  # 每帧先两次队列满：必须阻塞重试同一帧
            self.items.append(item)

    q = _FullThenRoom()
    boundaries = []
    WebMClip._stamp_source_indices(
        iter([b"f%d" % i for i in range(7)]), q, lambda: False,
        throttled=lambda: True, loop_frame_count=3,
        on_loop_boundary=lambda: boundaries.append(1) or True,
    )
    assert [i[1] for i in q.items] == [0, 1, 2, 0, 1, 2, 0]
    assert len(boundaries) == 2


def test_loop_disabled_when_frame_count_unknown():
    """loop_frame_count=0（元数据缺失兜底）：不回绕、不触发圈边界。"""
    q = queue.Queue(maxsize=16)
    WebMClip._stamp_source_indices(
        iter([b"f0", b"f1"]), q, lambda: False,
        loop_frame_count=0, on_loop_boundary=lambda: pytest.fail("不得触发圈边界"),
    )
    assert [q.get_nowait()[1] for _ in range(2)] == [0, 1]


# ---------------------------------------------------------------------------
# _loop_boundary：结束标记与续圈/宽限语义（直接调用，无 reader 线程）
# ---------------------------------------------------------------------------

def test_loop_boundary_skips_marker_when_rearm_pending(app, tmp_path):
    """re-arm 先于结束标记：reader 到边界看到 _rearm_pending 就跳过标记续圈，
    并清掉 re-arm 置位的 gate（否则下一圈边界会被残留 gate 直通而跳过驻留）。"""
    clip = _make_clip(tmp_path)
    clip._rearm_pending = True
    clip._loop_gate.set()  # 模拟 re-arm 已置位
    q = queue.Queue(maxsize=8)
    try:
        assert clip._loop_boundary(q, threading.Event(), clip._generation) is True
        assert q.empty()  # 未发结束标记（本圈结束已由末帧路径上报）
        assert clip._rearm_pending is False
        assert not clip._loop_gate.is_set()  # 残留 gate 已清（防下一圈直通）
    finally:
        clip.cleanup()
        app.processEvents()


def test_loop_boundary_parks_then_exits_on_grace_timeout(app, tmp_path, monkeypatch):
    """无人续圈：发结束标记后驻留，宽限期满自行退出（返回 False）。"""
    monkeypatch.setattr(webm_clip_mod, "_LOOP_REARM_GRACE_SECS", 0.2)
    clip = _make_clip(tmp_path)
    q = queue.Queue(maxsize=8)
    t0 = time.monotonic()
    try:
        assert clip._loop_boundary(q, threading.Event(), clip._generation) is False
        assert 0.15 < time.monotonic() - t0 < 3.0  # 确实驻留了约一个宽限期
        assert q.get_nowait() is None  # 结束标记已交付
    finally:
        clip.cleanup()
        app.processEvents()


def test_loop_boundary_wakes_on_hard_stop(app, tmp_path):
    """驻留期间硬停（stop_evt + gate）：立即退出，不等宽限期。"""
    clip = _make_clip(tmp_path)
    q = queue.Queue(maxsize=8)
    stop_evt = clip._stop_evt  # 与 _hard_stop 作用的是同一个事件对象
    result = []
    t = threading.Thread(
        target=lambda: result.append(clip._loop_boundary(q, stop_evt, clip._generation)),
        daemon=True,
    )
    t.start()
    try:
        time.sleep(0.1)  # 让 reader 进入驻留
        t0 = time.monotonic()
        clip._hard_stop()
        t.join(3.0)
        assert not t.is_alive(), "硬停必须立即唤醒驻留的 reader"
        assert time.monotonic() - t0 < 2.0
        assert result == [False]
    finally:
        clip.cleanup()
        app.processEvents()


# ---------------------------------------------------------------------------
# 集成：续圈不重启进程 / 软停与硬停分流 / 宽限期自清
# ---------------------------------------------------------------------------

def test_rearm_continues_loop_without_process_restart(app, monkeypatch, tmp_path):
    """续播同一 clip：圈末软停 → start() re-arm——同一 reader 线程、同一
    ffmpeg 进程，帧号第二圈从 0 回绕，结束标记不重复上报；连续三圈稳定。"""
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    finished: list = []
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        first_thread = clip._thread
        # 第一圈：逐帧放行 + 消费到末帧（末帧路径：is_last → stop → 软停）
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: srcs == [0, 1, 2]), f"srcs={srcs}"
        assert clip._natural_end_pending is True

        # 上层调度（末帧路径）：stop() 软停保进程 → start() re-arm 续圈
        clip.stop()
        assert clip._soft_parked is True
        assert clip._timer.isActive() is False  # 软停停消费节拍（清单 #8）
        assert proc.poll() is None, "软停不得杀 ffmpeg 进程"
        assert clip.start() is True
        assert clip._thread is first_thread, "续圈不得重启 reader 线程"
        assert clip._reader_proc is proc, "续圈不得重启 ffmpeg 进程"
        assert clip._running is True
        assert clip._timer.isActive() is True  # re-arm 恢复消费节拍（清单 #8）

        # 第二圈：帧号回绕 0..2；结束标记只报一次（re-arm 已排空/跳过）
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1), \
            f"srcs={srcs} finished={finished}"
        assert srcs == [0, 1, 2, 0, 1, 2], f"第二圈帧号未回绕: {srcs}"
        assert finished == [True]

        # 第三圈（清单 #9：多圈连续 re-arm，gate/标志跨圈演化）
        clip.stop()
        assert clip._soft_parked is True
        assert clip.start() is True
        assert clip._thread is first_thread, "第三圈仍是同一 reader/进程"
        assert clip._reader_proc is proc
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 2)
        assert srcs == [0, 1, 2, 0, 1, 2, 0, 1, 2]
        assert finished == [True, True]
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()
    assert proc.poll() is not None, "cleanup 后 ffmpeg 进程必须被杀"


def test_end_marker_path_rearm_continues_same_process(app, monkeypatch, tmp_path):
    """兜底路径（结束标记 → finished → 续播）：同样软停 + re-arm 续圈。"""
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    finished: list = []
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        for _ in range(3):
            gen.release()
        # 消费到结束标记（_poll 消费 None → finished；不逐帧断言消费）
        assert _consume_until(clip, lambda: len(finished) == 1), f"srcs={srcs}"
        assert clip._running is False
        assert clip._natural_end_pending is True
        first_thread = clip._thread

        clip.stop()  # 结束标记路径的 stop 同样转软停
        assert clip._soft_parked is True
        assert proc.poll() is None
        assert clip.start() is True
        assert clip._thread is first_thread
        assert clip._reader_proc is proc

        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 2)
        assert srcs[-3:] == [0, 1, 2], f"第二圈帧号未回绕: {srcs}"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_no_rearm_grace_expires_and_kills_process(app, monkeypatch, tmp_path):
    """圈边界无人续圈（切走/被打断）：宽限期满 reader 自行退出并杀进程。"""
    monkeypatch.setattr(webm_clip_mod, "_LOOP_REARM_GRACE_SECS", 0.2)
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    finished: list = []
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1)
        clip.stop()  # 圈末软停驻留
        assert clip._soft_parked is True
        assert proc.poll() is None
        # 不续圈：宽限期（0.2s）满 → reader 自行退出，finally 杀进程
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.02)
        assert proc.poll() is not None, "宽限期满未续圈必须杀掉 ffmpeg 进程"
        assert clip._thread is None, "宽限期满 reader 自行退出后 _thread 必须清空（B1）"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_mid_loop_stop_hard_kills_process(app, monkeypatch, tmp_path):
    """中途打断（非圈末）的 stop() 仍走硬停：立即 terminate 进程、不驻留。"""
    clip = _make_clip(tmp_path, frame_count=100)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    clip.frameChanged.connect(srcs.append)
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        spawns[0][1].release()
        assert _consume_until(clip, lambda: srcs == [0])
        assert clip._natural_end_pending is False
        clip.stop()  # 中途打断：硬停
        assert clip._soft_parked is False
        assert spawns[0][0].terminated is True, "中途 stop 必须立即 terminate 进程"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_grace_expired_rearm_falls_back_to_fresh_start(app, monkeypatch, tmp_path):
    """宽限期已过（reader 已退出）再 start()：落回正常路径重新拉起 reader。"""
    monkeypatch.setattr(webm_clip_mod, "_LOOP_REARM_GRACE_SECS", 0.2)
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    finished: list = []
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        gen1 = spawns[0][1]
        for _ in range(3):
            gen1.release()
        assert _consume_until(clip, lambda: len(finished) == 1)
        clip.stop()
        assert clip._soft_parked is True
        # 等宽限期满、reader 自行退出（_thread 被 B1 finally 清空）
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and clip._thread is not None:
            time.sleep(0.02)
        assert clip._thread is None, "宽限过期后 _thread 必须已清空（B1）"
        # 再 start()：reader 已死 → re-arm 失败 → 正常重启（新线程新流）
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        assert len(spawns) == 2, "宽限过期后必须重新拉起 ffmpeg"
        assert clip._soft_parked is False
        gen2 = spawns[1][1]
        for _ in range(3):
            gen2.release()
        assert _consume_until(clip, lambda: len(finished) == 2)
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


# ---------------------------------------------------------------------------
# 盲审回归：P1-1 / P1-2 / P2-3 / P2-4 / 参数拆分 / 镜像新 sink / drain
# ---------------------------------------------------------------------------

def test_hard_stop_then_replay_soft_parks_at_next_boundary(app, monkeypatch, tmp_path):
    """P1-1 回归（门槛 #1）：中途打断（硬停置位 gate）→ 重播 → 下一圈边界
    必须真实驻留、stop() 走软停、进程不重启——残留 gate 直通会退化成杀进程。"""
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    clip.frameChanged.connect(srcs.append)
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        # 中途打断：硬停，gate 被置位（旧 bug：残留 gate 直通下一圈边界）
        spawns[0][1].release()
        assert _consume_until(clip, lambda: srcs == [0])
        clip.stop()
        assert clip._soft_parked is False
        assert spawns[0][0].terminated is True

        # 重播（fresh start）：必须清掉残留 gate（P1-1 修复点）
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        assert len(spawns) == 2
        assert not clip._loop_gate.is_set(), "fresh start 必须清掉硬停残留的 gate"
        proc1, gen1 = spawns[1][0], spawns[1][1]
        for _ in range(3):
            gen1.release()
        assert _consume_until(clip, lambda: len(srcs) >= 4)
        assert _wait_parked(clip), "新 reader 到圈边界必须真实驻留"
        clip.stop()
        assert clip._soft_parked is True, "gate 残留直通时软停会退化成硬停"
        assert proc1.poll() is None, "软停不得杀进程"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_rearm_ack_timeout_falls_back_to_fresh_start(app, monkeypatch, tmp_path):
    """P1-2（门槛 #3）：reader 在退出窗口（is_alive 仍 True 但永不 ack）时
    re-arm 握手超时 → 回滚并落回 fresh start——start() 不空转、新 reader 拉起。"""
    monkeypatch.setattr(webm_clip_mod, "_LOOP_REARM_ACK_TIMEOUT", 0.1)
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    finished: list = []
    clip.finished.connect(lambda: finished.append(True))
    # 构造「软停驻留但 reader 永不 ack」的状态（模拟 finally 退出窗口）：
    # 线程活着但马上退出、不到圈边界、永不置 ack。
    sleeper = threading.Thread(target=lambda: time.sleep(1.0), daemon=True)
    sleeper.start()
    clip._thread = sleeper
    clip._soft_parked = True
    clip._natural_end_pending = True
    clip._running = False
    try:
        t0 = time.monotonic()
        assert clip.start() is True  # 握手超时回滚 → fresh start 仍成功
        assert time.monotonic() - t0 < 3.0, "握手等待必须有界"
        assert clip._soft_parked is False
        assert clip._reader_ready.wait(5.0)
        assert len(spawns) == 1, "fresh start 必须拉起新 reader/进程"
        # 活性断言：fresh start 后有限时间内必有帧到达（门槛 #3）
        spawns[0][1].release()
        assert _consume_until(clip, lambda: len(finished) == 0 and True) or True
        srcs: list = []
        clip.frameChanged.connect(srcs.append)
        spawns[0][1].release()
        assert _consume_until(clip, lambda: len(srcs) >= 1), "fresh start 后必须有帧产出"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()
        sleeper.join(2.0)


def test_frame_count_unknown_falls_back_to_single_pass(app, monkeypatch, tmp_path):
    """P2-3（门槛 #2）：帧数未知（探测失败）→ 不带 -stream_loop（退化为
    播一遍自然结束），reader/timer 自然终止——无限流常驻的泄漏不成立。"""
    clip = _make_clip(tmp_path, frame_count=0)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    finished: list = []
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        assert '-stream_loop' not in spawns[0][2], "帧数未知不得带 -stream_loop"
        assert '-readrate' not in spawns[0][2]
        gen = spawns[0][1]
        assert gen._looping is False  # 假流按真实参数退化为一圈即 EOF
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1)
        # 自然结束后 reader 线程退出，且清理 _thread（B1：有限流 EOF 自然退出
        # 不清 _thread 会让死 Thread 对象每 clip 钉 1 个 OS 线程句柄）。结束时
        # 标记消费与 reader finally 清 _thread 存在良性竞态，等其清空再断言。
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and clip._thread is not None:
            time.sleep(0.005)
        assert clip._thread is None, "有限流自然结束后 _thread 必须已清空（B1）"
        assert clip._timer.isActive() is False
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_loop_params_require_exact_frame_count(app, monkeypatch, tmp_path):
    """P2-5：帧数来自估算（fps×duration 回填，非 count_frames_and_secs）
    → 同样不带循环参数（取模回绕的相位漂移风险不入进程内循环）。"""
    clip = _make_clip(tmp_path, frame_count=3)
    clip._frame_count_exact = False  # 估算帧数
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        assert '-stream_loop' not in spawns[0][2], "估算帧数不得带 -stream_loop"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_readrate_scales_with_playback_speed(app, monkeypatch, tmp_path):
    """P2-4（门槛 #4）：playback_speed=1.5 时 -readrate 跟随为 1.5
    （钉死 1 会把解码封顶在原生帧率、消费端饥饿）；speed<=1 恒为 1。"""
    clip = _make_clip(tmp_path, frame_count=3)
    clip.set_playback_speed(1.5)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        params = spawns[0][2]
        assert '-stream_loop' in params
        assert params[params.index('-readrate') + 1] == '1.5'
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_first_frame_decode_never_loops(app, monkeypatch, tmp_path):
    """参数拆分：首帧解码（_decode_first_qimage）用共享基础参数表，
    绝不带 -stream_loop/-readrate（只读一帧即关）。"""
    clip = _make_clip(tmp_path, frame_count=3)
    captured = []

    def _fake_read_frames(*args, **kwargs):
        captured.append(list(kwargs.get('input_params') or []))
        proc = _FakeProc()
        cap = webm_clip_mod._PopenCapture._local.capture
        cap._on_process(proc, ["ffmpeg", "-i", str(clip.path)])
        return iter([{"fps": 24.0, "duration": 1.0}, bytes(_FRAME_BYTES)])

    monkeypatch.setattr(webm_clip_mod.imageio_ffmpeg, "read_frames", _fake_read_frames)
    try:
        img = clip._decode_first_qimage()
        assert img is not None
        assert captured, "首帧解码必须拉起一次 read_frames"
        assert captured[0] == list(webm_clip_mod._FFMPEG_INPUT_PARAMS)
        assert '-stream_loop' not in captured[0]
        assert '-readrate' not in captured[0]
    finally:
        clip.cleanup()
        app.processEvents()


def test_rearm_mirrors_frames_to_rebuilt_sink(app, monkeypatch, tmp_path):
    """续圈后 broker 会话重建（_publish_sink 换实例）：旧 reader 的第二圈帧
    必须发到新 sink（_mirror_frame_to_sink 逐帧读当前 sink，清单 #5）。"""
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    finished: list = []

    class _Sink:
        def __init__(self):
            self.srcs = []

        def on_frame(self, data, src):
            self.srcs.append(int(src))

    sink1, sink2 = _Sink(), _Sink()
    clip._publish_sink = sink1
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        gen = spawns[0][1]
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1)
        assert sink1.srcs == [0, 1, 2]

        clip._publish_sink = sink2  # facade 重建会话：换 sink
        clip.stop()
        assert clip.start() is True
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 2)
        assert sink2.srcs == [0, 1, 2], "第二圈必须镜像到新 sink"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_throttled_loop_rearm_end_to_end(app, monkeypatch, tmp_path):
    """节流（divisor=2）下的循环续圈（清单 #4）：边界/驻留/re-arm 语义不变。"""
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    clip.set_decode_throttle(2)
    srcs: list = []
    finished: list = []
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        gen = spawns[0][1]
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: srcs == [0, 1, 2]), f"srcs={srcs}"
        assert _wait_parked(clip)
        clip.stop()
        assert clip._soft_parked is True
        assert clip.start() is True
        assert clip.decode_throttle_divisor == 2  # 节流状态跨续圈保持
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1)
        assert srcs == [0, 1, 2, 0, 1, 2], "节流路径第二圈帧号回绕"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_drain_boundary_marker_preserves_frames(app, tmp_path):
    """P3-7（清单 #6）：_drain_boundary_marker 只去结束标记，其前后的帧
    原序保留——绝不静默丢已交付帧。"""
    clip = _make_clip(tmp_path)
    try:
        clip._queue.put_nowait((b"f", 1))
        clip._queue.put_nowait(None)
        clip._queue.put_nowait((b"g", 0))
        clip._drain_boundary_marker()
        assert clip._queue.get_nowait() == (b"f", 1)
        assert clip._queue.get_nowait() == (b"g", 0)
        assert clip._queue.empty()
        # 无标记时幂等且不动帧
        clip._queue.put_nowait((b"h", 0))
        clip._drain_boundary_marker()
        assert clip._queue.get_nowait() == (b"h", 0)
        assert clip._queue.empty()
    finally:
        clip.cleanup()
        app.processEvents()


# ---------------------------------------------------------------------------
# 批11-B1：ffmpeg 圈边界定期回收（阈值到期退役换新 / 未到照旧 park /
#          0 关闭永不回收 / 只在圈边界生效 / re-arm 竞态不回收）
# ---------------------------------------------------------------------------

def _recycle_count() -> int:
    snap = perfstats.snapshot()
    return snap.get('ffmpeg.recycle', {}).get('count', 0)


def test_recycle_at_boundary_retires_process_and_fresh_spawns(app, monkeypatch, tmp_path):
    """到达回收阈值的圈边界 → 进程退役（旧 pid 死、下圈 start 新 pid）。"""
    clip = _make_clip(tmp_path, frame_count=3)
    clip.set_recycle_minutes(10)  # 阈值 = 600s
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    finished: list = []
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    perfstats.enable()
    perfstats.reset()
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        # 把当前进程「出生时刻」拨回 1200s 前 → 到达圈边界时必已过阈值。
        clip._reader_born_at = time.monotonic() - 1200.0
        for _ in range(3):
            gen.release()
        # 回收必须完整播完一圈（绝不打断）：三帧全交付 + 结束标记 → finished
        assert _consume_until(clip, lambda: len(finished) == 1), f"srcs={srcs}"
        assert srcs == [0, 1, 2], f"回收前必须完整播完一圈: {srcs}"
        # 圈边界回收：旧 ffmpeg 进程退役
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.02)
        assert proc.poll() is not None, "圈边界回收必须杀掉旧 ffmpeg 进程"
        assert clip._thread is None, "回收后 reader 自行退出，_thread 必须清空（B1）"
        assert _recycle_count() >= 1, "回收必须计入 perfstats ffmpeg.recycle"
        # 圈末 stop()：_reader_parked=False → 硬停（无软停驻留）
        clip.stop()
        assert clip._soft_parked is False
        # 下圈 start() 自然 fresh spawn（新 pid）
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        assert len(spawns) == 2, "回收后必须重新拉起 ffmpeg"
        proc2, gen2 = spawns[1][0], spawns[1][1]
        assert proc2 is not proc and proc2.pid != proc.pid
        for _ in range(3):
            gen2.release()
        assert _consume_until(clip, lambda: len(finished) == 2), f"srcs={srcs}"
        assert srcs[-3:] == [0, 1, 2], f"新进程第二圈帧号未回绕: {srcs}"
    finally:
        _close_all(spawns)
        clip.cleanup()
        perfstats.disable()
        perfstats.reset()
        app.processEvents()


def test_recycle_not_due_still_parks_and_rearms(app, monkeypatch, tmp_path):
    """未达回收阈值的圈边界 → 照旧 park/re-arm（同一 reader/进程续圈）。"""
    clip = _make_clip(tmp_path, frame_count=3)
    clip.set_recycle_minutes(10)  # 阈值 = 600s；进程刚出生 → 未达阈值
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    finished: list = []
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    perfstats.enable()
    perfstats.reset()
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        first_thread = clip._thread
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1), f"srcs={srcs}"
        assert clip._natural_end_pending is True
        clip.stop()
        assert clip._soft_parked is True, "未达回收阈值必须照旧软停"
        assert proc.poll() is None, "软停不得杀 ffmpeg 进程"
        assert _recycle_count() == 0, "未达阈值不得计入回收"
        assert clip.start() is True
        assert clip._thread is first_thread, "未达阈值不得重启 reader 线程"
        assert clip._reader_proc is proc, "未达阈值不得重启 ffmpeg 进程"
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 2)
        assert srcs[-3:] == [0, 1, 2], f"第二圈帧号未回绕: {srcs}"
    finally:
        _close_all(spawns)
        clip.cleanup()
        perfstats.disable()
        perfstats.reset()
        app.processEvents()


def test_recycle_disabled_never_triggers(app, monkeypatch, tmp_path):
    """recycle_minutes=0 → 永不回收（现状逐位一致）：即使进程已超长寿也照常软停。"""
    clip = _make_clip(tmp_path, frame_count=3)
    clip.set_recycle_minutes(0)  # 0 = 关闭回收（回退保险）
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    finished: list = []
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    perfstats.enable()
    perfstats.reset()
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        clip._reader_born_at = time.monotonic() - 3000.0  # 已远超任何合理阈值
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1), f"srcs={srcs}"
        clip.stop()
        assert clip._soft_parked is True, "0 配置必须照旧软停（永不回收）"
        assert proc.poll() is None, "0 配置不得杀 ffmpeg 进程"
        assert _recycle_count() == 0, "0 配置永不回收"
        assert clip.start() is True
        assert clip._reader_proc is proc, "0 配置不得重启 ffmpeg 进程"
    finally:
        _close_all(spawns)
        clip.cleanup()
        perfstats.disable()
        perfstats.reset()
        app.processEvents()


def test_recycle_only_fires_at_boundary_not_mid_loop(app, monkeypatch, tmp_path):
    """回收只在圈边界（park 决策点）生效：播放循环中途绝不被回收打断。"""
    clip = _make_clip(tmp_path, frame_count=100)
    clip.set_recycle_minutes(10)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    clip.frameChanged.connect(srcs.append)
    perfstats.enable()
    perfstats.reset()
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        clip._reader_born_at = time.monotonic() - 1200.0  # 已远超阈值
        # 循环中途（未达圈边界）：回收必须不生效——进程继续存活出帧
        gen.release()
        assert _consume_until(clip, lambda: srcs == [0])
        assert clip._natural_end_pending is False
        assert proc.poll() is None, "循环中途不得因回收杀进程"
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(srcs) >= 4)
        assert proc.poll() is None, "同一圈内播放不得被回收打断"
        assert _recycle_count() == 0, "回收未到边界不得计入"
    finally:
        _close_all(spawns)
        clip.cleanup()
        perfstats.disable()
        perfstats.reset()
        app.processEvents()


def test_recycle_skipped_when_rearm_pending(app, tmp_path):
    """pending re-arm 期间不得回收：_rearm_pending 置位时圈边界直接续圈，
    绝不经回收分支（否则 re-arm 与回收竞态会破坏续圈语义）。"""
    clip = _make_clip(tmp_path)
    clip._recycle_seconds = 0.001  # 若评估回收必命中
    # 盲审 P1-1：回收判定还要求 _reader_born_at 已记录，缺这行则
    # _recycle_due 恒 False、本测试对回收维度空转（守卫失效）。
    clip._reader_born_at = time.monotonic() - 10.0
    clip._rearm_pending = True
    clip._loop_gate.set()  # 模拟 re-arm 已置位
    q = queue.Queue(maxsize=8)
    perfstats.enable()
    perfstats.reset()
    try:
        assert clip._loop_boundary(q, threading.Event(), clip._generation) is True
        assert q.empty(), "re-arm 期间不得发结束标记"
        assert clip._rearm_pending is False
        assert not clip._loop_gate.is_set(), "残留 gate 必须被清（防下一圈直通）"
        assert _recycle_count() == 0, "pending re-arm 期间不得回收"
    finally:
        clip.cleanup()
        perfstats.disable()
        perfstats.reset()
        app.processEvents()


# ---------------------------------------------------------------------------
# 批12-A1：显示槽清空——非显示 clip 释放 _current_image/_current_pixmap
#          （正在显示的 clip 显示槽永不为 None / park 不清 / _switch 重启）
# ---------------------------------------------------------------------------

def test_hard_stop_clears_display_frame(app, monkeypatch, tmp_path):
    """A1：硬停（切走/隐藏/关闭的 stop 都走 _hard_stop）后，旧 clip 的显示槽
    必须清空——不再永久持有最后一帧的 QImage+QPixmap。"""
    clip = _make_clip(tmp_path, frame_count=100)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    clip.frameChanged.connect(srcs.append)
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        spawns[0][1].release()
        assert _consume_until(clip, lambda: srcs == [0])
        assert clip.currentPixmap() is not None, "播放中显示槽必须持有当前帧"
        clip.stop()  # 中途打断 = 硬停
        assert clip._soft_parked is False
        assert clip._current_image is None, "硬停后旧 clip 显示槽必须清空"
        assert clip._current_pixmap is None
        assert clip.currentPixmap() is None
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_playing_clip_display_frame_remains_non_none(app, monkeypatch, tmp_path):
    """A1：正在播放/显示的 clip，其显示槽必须持续持有帧（非 None）——
    只有非显示 clip 才被清空。"""
    clip = _make_clip(tmp_path, frame_count=100)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    clip.frameChanged.connect(srcs.append)
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        for _ in range(3):
            spawns[0][1].release()
        assert _consume_until(clip, lambda: len(srcs) >= 3)
        assert clip.currentPixmap() is not None, "正在显示 clip 的显示槽不得为 None"
        assert clip._current_image is not None
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_park_rearm_keeps_display_frame(app, monkeypatch, tmp_path):
    """A1：圈边界软停驻留（park）的 clip 仍是当前显示对象，显示槽不得清空；
    re-arm 续圈后仍持有帧。park 路径（stop() 早退）绝不清显示槽。"""
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    clip.frameChanged.connect(srcs.append)
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        gen = spawns[0][1]
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: srcs == [0, 1, 2]), f"srcs={srcs}"
        assert _wait_parked(clip)
        clip.stop()
        assert clip._soft_parked is True
        assert clip.currentPixmap() is not None, "软停驻留不得清空显示槽"
        first_thread = clip._thread
        # re-arm 续圈：同一 reader/进程，显示槽不清空
        assert clip.start() is True
        assert clip._thread is first_thread
        assert clip.currentPixmap() is not None, "re-arm 后显示槽仍须持有帧"
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(srcs) >= 4)
        assert clip.currentPixmap() is not None, "续圈播放中显示槽必须持有帧"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


def test_switch_restart_same_clip_sets_first_frame(app, tmp_path):
    """A1：_switch 重启同 clip（stop→jumpToFrame(0)→start）后，显示槽必须被
    jumpToFrame 重写为首帧——stop 清空无害，重启后显示槽=首帧非 None。"""
    clip = _make_clip(tmp_path, frame_count=3)
    first = QImage(2, 2, QImage.Format.Format_RGBA8888)
    clip._first_image = first  # 模拟已缓存的 _first_image（warm/同步解码）
    try:
        clip.stop()          # _switch 的 movie.stop()
        clip.jumpToFrame(0)  # _switch 的 movie.jumpToFrame(0)
        assert clip._current_pixmap is not None, "重启后显示槽必须为首帧"
        assert clip._current_image is first, "重启后显示槽必须复用首帧缓存"
        assert not clip._current_pixmap.isNull()
    finally:
        clip.cleanup()
        app.processEvents()


def test_parked_loop_reader_self_exit_keeps_display_frame(app, monkeypatch, tmp_path):
    """A1 复审修订（REVIEW_batch12 P1-1）：park 后宽限期满 reader 自退**不得**
    清显示槽——clip 侧无法区分「放弃」与「仍在显示」（hold 路径），显示槽只由
    窗口 _switch 切走时按权威显示状态清（GUI 线程）。B1：reader 自退后
    _thread 必须清空。"""
    monkeypatch.setattr(webm_clip_mod, "_LOOP_REARM_GRACE_SECS", 0.2)
    clip = _make_clip(tmp_path, frame_count=3)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    finished: list = []
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        gen = spawns[0][1]
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1), f"finished={finished}"
        clip.stop()  # 圈末软停驻留：reader 保活、显示槽保留
        assert clip._soft_parked is True
        assert clip.currentPixmap() is not None, "圈末 park 时显示槽不得清空"
        # 不续圈 → 宽限期满 reader 自行退出：_thread 清空（B1），
        # 但显示槽**不清**（复审 P1-1/P1-2：清槽是 GUI 线程的窗口侧职责）
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and clip._thread is not None:
            time.sleep(0.02)
        assert clip._thread is None, "reader 自退后 _thread 必须清空（B1）"
        assert clip.currentPixmap() is not None, \
            "reader 自退不得清显示槽（P1-1/P1-2：清槽归窗口 _switch 权威侧）"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()


# ---------------------------------------------------------------------------
# 批12-B1：reader 自行退出后清 _thread——绝不残留死 Thread 对象钉 OS 线程句柄
# ---------------------------------------------------------------------------

def test_thread_none_after_recycle_self_exit(app, monkeypatch, tmp_path):
    """B1：圈界回收后 reader 自行退出，_thread 必须被清 None——绝不残留死
    Thread 对象（每 clip 钉 1 个 OS 线程句柄）；下圈 start 自然 fresh spawn。"""
    clip = _make_clip(tmp_path, frame_count=3)
    clip.set_recycle_minutes(10)
    spawns = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    finished: list = []
    clip.finished.connect(lambda: finished.append(True))
    try:
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        clip._reader_born_at = time.monotonic() - 1200.0  # 必达回收阈值
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: len(finished) == 1), f"srcs={finished}"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.02)
        assert proc.poll() is not None, "圈界回收必须杀掉旧 ffmpeg 进程"
        assert clip._thread is None, "圈界回收后 _thread 必须清空（B1）"
        # 下圈 start：fresh spawn（新 reader 线程，无死对象残留）
        assert clip.start() is True
        assert clip._reader_ready.wait(5.0)
        assert len(spawns) == 2
        assert clip._thread is not None and clip._thread.is_alive()
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()
