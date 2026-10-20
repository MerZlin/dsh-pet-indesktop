# -*- coding: utf-8 -*-
"""IDLE 圈末：墙钟到点 ≠ 本圈播完（同 clip 重绑必须落在软停 + 续圈上）。

实机口径（``.scratch/arch-ab/hsrearm-diag-20260926/REPORT.md``）：单宠 idle 每
10.044s 在 frame 237/241 触发一次重绑——行为 tick 的墙钟到点（``elapsed ≥
duration``）先于圈末到达，此刻 ``_natural_end_pending=False``、
``_reader_parked=True``、``_boundary_marker_pending=True``，于是 ``bind_clip``
里旧 clip 的 ``stop()`` 在 park 的第一道判据上被拒 → 硬停杀 ffmpeg → ``start()``
换代 spawn 新进程（3 次全同、re-arm 调用数 0），且每圈末 238-240 三帧从未交付
（诊断只证到「未交付 / 无绘制机会」；present 未证，见该 REPORT §5）。

修法在行为层（webm_clip 的 park 判据一字不动）：IDLE 到点后不再立刻掷骰换绑，
而是等**圈末证据**在 tick 内收口——有 ``finished`` 契约的 clip 等 finished 登记
（``_reader_parked`` 由 reader 线程写、GUI 侧读，跨线程无同步点，按帧号抢先换绑
可能仍落硬停换代），只有确无该接口的有限 clip 才退到「末帧 + 一次绘制机会」；
两者都由宽限兜底。等到的是真正的圈末，随之而来的同 clip 重绑就落在软停 +
re-arm 上（``stop()`` 的两道判据都真），既不杀进程也不截尾巴帧。

证据链（``_process_frame`` 与 ``_loop_boundary``，webm_clip.py:2626-2629 /
2407-2488）：末帧交付即置 ``_natural_end_pending``，reader 在标记入队后置
``_reader_parked`` 驻留等续圈（宽限 1.0s）——两者在「末帧已交付 + 一个帧间隔的
finished 登记」那一刻都已成立，缺的只是把换绑推迟到那一刻。

纪律（AGENTS.md 时序测试）：offscreen；tick 推进与帧交付全部由测试显式驱动
（确定性替身 + 播放器节拍模拟），不启动真实 QTimer、不 sleep、不赌时序。真实
WebMClip 那条集成用例复用 ``tests/test_webm_clip_loop.py`` 的假 ffmpeg 夹具
（放行额度逐帧产出、假 Popen），断言 park→re-arm 不换代——不靠假 clip 自证。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import QObject, QPointF, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet.pet_sprite import PetSprite
from pet.sprite_behavior import (
    _IDLE_TAIL_GRACE_S,
    STATE_ACTS,
    STATE_CLICK,
    STATE_DRAG,
    STATE_IDLE,
    STATE_MOVE,
    BehaviorController,
)
from tests.test_sprite_behavior import BOUNDS, ScriptedRng
from tests.test_webm_clip_loop import (
    _close_all,
    _consume_until,
    _install_fake_ffmpeg,
    _make_clip,
    _wait_parked,
)

#: 确定性用例要 QApplication 实例（QPixmap/QImage 构建），只取副作用的模块级创建；
#: 真实 WebMClip 的集成用例按 tests/test_webm_clip_loop.py 的习惯走 ``app`` fixture。
_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def app():
    """与 tests/test_webm_clip_loop.py 同款：真实 WebMClip 用例要 QApplication。"""
    return QApplication.instance() or QApplication([])


#: overlay tick 间隔（tick_driver.TickDriver.tick_interval_ms 封顶 16ms）
TICK_S = 0.016
FRAME_S_24 = 1.0 / 24          # 名义帧长（源 fps 24）
INTERVAL_24 = 0.042            # 播放器定时器口径 round(1000/24)
INTERVAL_48 = 0.021            # round(1000/48)


# ---------------------------------------------------------------- 确定性替身
class _Playback(QObject):
    """播放器位（接口对齐 WebMClip / FrameSeqClip / GifClip）。

    交付链路照抄 webm_clip：末帧交付即「圈末自然结束」（``_process_frame`` →
    ``_natural_end_pending``），reader 在结束标记入队后驻留圈边界（``parked``
    = ``_reader_parked``）等续圈；``stop()`` 只在「已自然结束 + 仍在驻留」时软停
    （保进程，start() 走 re-arm 同一代），否则硬停并让下一次 start() 换代。
    本替身只把这两个判据如实记账，供测试断言「到点不硬停」；真实 WebMClip 的
    同一断言由文件末尾的集成用例给出。
    """

    frameChanged = Signal(int)

    def __init__(self, name, frames, frame_s=FRAME_S_24):
        super().__init__()
        self.name = name
        self.frames = frames
        self.frame_s = frame_s
        self.image = QImage(64, 36, QImage.Format.Format_ARGB32)
        self.image.fill(0xFF336699)
        self.index = -1              # -1 = 尚未交付任何帧
        self.generation = 0          # fresh start 次数（= 新 ffmpeg/reader 代）
        self.start_count = 0
        self.hard_stops = 0          # 中途 stop：terminate 进程、退役 reader
        self.rearms = 0              # 圈末软停后的 start()：同一代续圈
        self.parked = False          # reader 驻留在圈边界（= _reader_parked）
        self.natural_end = False     # 圈末已到（= _natural_end_pending）
        self.soft_parked = False

    # 播放器公开接口（库的 frames()/duration() 也走这里）
    def frameCount(self):
        return self.frames

    def duration(self):
        return self.frames * self.frame_s

    def currentFrameNumber(self):
        return self.index

    def currentImage(self):
        return self.image

    def start(self):
        self.start_count += 1
        if self.soft_parked:
            self.rearms += 1
            self.soft_parked = False
            self.natural_end = False
            self.index = 0
            return True
        self.generation += 1
        self.natural_end = False
        self.parked = False
        self.index = 0
        return True

    def stop(self):
        if self.natural_end and self.parked:
            self.soft_parked = True          # 圈末软停：保留进程等续圈
            return
        self.hard_stops += 1                 # 硬停：进程被杀、换代
        self.parked = False

    # 测试侧驱动
    def deliver(self, index):
        """播放器定时器交付源帧 index（消费侧，= webm_clip 的 _process_frame）。

        末帧交付即「圈末自然结束」（webm_clip.py:2626-2629 置
        ``_natural_end_pending``）；同一刻 reader 视为已驻留圈边界
        （webm_clip.py:2467-2488：reader 读帧走在消费侧之前——实机诊断在显示
        frame 237/241 时 ``_reader_parked`` 已为 True、结束标记已在队列里）。
        """
        self.index = index
        if index >= self.frames - 1:
            self.natural_end = True
            self.parked = True
        self.frameChanged.emit(index)

    def reach_loop_end(self):
        """圈末结束标记被消费（= webm_clip._poll 取到 None，2370-2380）。

        此刻置 ``_natural_end_pending`` 并发 ``finished``；有该信号的播放器在
        末帧之后一个帧间隔才走到这里，缺信号的旧替身只留下 natural_end/parked。
        """
        self.natural_end = True
        self.parked = True
        signal = getattr(self, "finished", None)
        if signal is not None:
            signal.emit()


class ParkClip(_Playback):
    """带 finished 的替身（WebMClip/FrameSeqClip/GifClip 都有该信号）。"""

    finished = Signal()


class NoSignalClip(_Playback):
    """缺 finished 信号的旧替身（``PetSprite.bind_clip`` 静默跳过连接）。"""


class NoFrameApiClip(ParkClip):
    """接口缺失的旧 clip 替身（帧号/帧数都读不到 → 必须回退旧墙钟语义）。"""

    frameCount = None
    currentFrameNumber = None


class ClipLibrary:
    """轻量库协议：movie/clip/frames/duration + 各素材池属性。"""

    def __init__(self, clips, *, idles=(), turns=(), moves=(), clicks=(),
                 acts=(), drag=None, strides=None):
        self._clips = dict(clips)
        self.idles = list(idles)
        self.turns = list(turns)
        self.moves = list(moves)
        self.clicks = list(clicks)
        self.acts = list(acts)
        self.drag = drag
        self.move_strides = dict(strides or {})
        self.move_curves = {}

    def movie(self, name, **_kw):
        return self._clips[name]

    def clip(self, name):
        return self._clips[name]

    def frames(self, name):
        fn = getattr(self._clips[name], "frameCount", None)
        return int(fn() or 0) if callable(fn) else 0

    def duration(self, name):
        return self._clips[name].duration()


class IdleFeed:
    """播放器节拍模拟：源帧 idx 在 ``t0 + lag + (idx+1)×interval`` 交付。

    interval = 播放器定时器取整口径（24fps→42ms、48fps→21ms，均 ≥ 名义帧长）；
    lag = 首个交付延迟折算（帧序列 ~2.5ms 可忽略 → 0；WebM 冷启动实测 60-166ms
    → 2 个帧间隔）。末帧之后一个帧间隔交付圈末（结束标记 + finished + 驻留）。
    节拍按 clip 分别记账：换绑到另一条 clip 时旧 clip 停在原地（它已被 stop）。

    ``upper`` = 只交付到该帧号（默认末帧）——模拟末帧在链路上丢失、圈末照常到
    达那一档；``end_marker=False`` = 只交帧、**永不交付圈末**（finished 丢失：
    只剩帧号证据，用来钉住「有 finished 契约的 clip 绝不因帧到末尾就换绑」）；
    ``frozen`` = 彻底停表（预取断链：帧停在中途、永不 finished）。
    """

    def __init__(self, *, interval, lag=0.0, upper=None, end_marker=True):
        self.interval = interval
        self.lag = lag
        self.upper = upper
        self.end_marker = end_marker
        self.frozen = False
        self._state: dict = {}

    def _slot(self, clip, t):
        slot = self._state.get(clip)
        if slot is None:
            slot = self._state[clip] = {
                "starts": clip.start_count, "t0": t,
                "count": -1, "index": -1, "ended": False}
        elif slot["starts"] != clip.start_count:
            slot.update(starts=clip.start_count, t0=t,
                        count=-1, index=-1, ended=False)
        return slot

    def delivered(self, clip):
        """该 clip 最近一次交付的源帧号（-1 = 一帧都没交）。"""
        return self._state.get(clip, {}).get("index", -1)

    def freeze(self):
        """预取断链：不再交付任何帧、永不发圈末。"""
        self.frozen = True

    def poll(self, t, clip):
        if clip is None or self.frozen:
            return
        slot = self._slot(clip, t)
        if clip.start_count == 0:
            return
        last = clip.frames - 1 if self.upper is None else min(self.upper, clip.frames - 1)
        due = min(int((t - slot["t0"] - self.lag) / self.interval) - 1, last)
        while slot["count"] < due:
            slot["count"] += 1
            slot["index"] = slot["count"]
            clip.deliver(slot["index"])
        if (self.end_marker and not slot["ended"] and slot["count"] >= last
                and t - slot["t0"] >= self.lag + (slot["count"] + 2) * self.interval):
            slot["ended"] = True
            clip.reach_loop_end()


class Runner:
    """确定性 tick 循环：先交付到期帧（播放器定时器）再 tick（overlay 定时器）。"""

    def __init__(self, controller, sprite, feed, dt=TICK_S):
        self.c = controller
        self.sprite = sprite
        self.feed = feed
        self.dt = dt
        self.t = 0.0
        self.ticks = 0

    def step(self):
        self.t += self.dt
        self.feed.poll(self.t, self.sprite._clip)
        self.c.tick([self.sprite], self.dt)
        self.sprite.advance(self.dt)
        self.ticks += 1

    def run_until(self, pred, limit=6000):
        """跑到谓词成立；``limit`` = 本次调用的 tick 预算（相对，不是全局上限）。"""
        deadline = self.ticks + limit
        while not pred() and self.ticks < deadline:
            self.step()
        assert pred(), "run_until 未在预算内满足条件"

    def run_to_idle_expiry(self, st, duration, clip, lib):
        """跑到墙钟到点那一 tick（此刻本圈通常还差 2-4 帧没交付）。

        终止条件带上「已重绑」（任一 clip 被 start）：旧代码在到点那一 tick
        就掷骰换绑、elapsed 随之归零，只等 elapsed ≥ duration 会空转到超时——
        这条断言就是红点。
        """
        base = _total_starts(lib)
        self.run_until(lambda: st.elapsed >= duration or _total_starts(lib) > base)
        assert _total_starts(lib) == base, (
            f"墙钟到点那一 tick 就换绑了（此时末帧 {self.feed.delivered(clip) + 1}/"
            f"{clip.frames} 还没交付）")


# ---------------------------------------------------------------- 场景搭建
def _total_starts(lib) -> int:
    """库里所有 clip 的 start() 累计次数（= 有没有发生换绑/续圈的全局观测）。"""
    return sum(clip.start_count for clip in lib._clips.values())


def _idle_scene(*, frames=241, frame_s=FRAME_S_24, interval=INTERVAL_24, lag=0.0,
                clip_cls=ParkClip, idle_names=("idle1",), clicks=("click1",),
                drag="hang", moves=(), acts=(), strides=None, end_marker=True,
                rolls=(0.0,), ints=(), choices=(), facing="left", dt=TICK_S):
    """搭一台单宠 idle 现场：首 tick 进待机并绑 ``idle_names[0]``。"""
    clips = {name: clip_cls(name, frames, frame_s) for name in idle_names}
    for name in clicks:
        clips[name] = ParkClip(name, 24)
    if drag:
        clips[drag] = ParkClip(drag, 24)
    for name in moves:
        clips[name] = ParkClip(name, 48)
    for name in acts:
        clips[name] = clip_cls(name, frames, frame_s)
    lib = ClipLibrary(clips, idles=list(idle_names), clicks=list(clicks), drag=drag,
                      moves=list(moves), acts=list(acts), strides=strides)
    sprite = PetSprite(lib, pos=QPointF(800, 400), facing=facing, scale=0.5)
    c = BehaviorController(
        BOUNDS, rng=ScriptedRng(rolls=rolls, ints=ints, choices=choices))
    c.predict_enabled = False
    feed = IdleFeed(interval=interval, lag=lag, end_marker=end_marker)
    runner = Runner(c, sprite, feed, dt=dt)
    runner.step()                     # 首 tick：进待机 + 起播
    assert c.state_of(sprite) == STATE_IDLE
    clip = clips[idle_names[0]]
    assert clip.start_count == 1
    return lib, sprite, c, clip, feed, runner


# 三条实机形状的节拍（241/481 帧素材，帧序列与 webm 冷启动两档）
CORE_SCENES = [
    ("idle241-24fps-frameseq", 241, FRAME_S_24, INTERVAL_24, 0.0),
    ("idle241-24fps-webm-cold", 241, FRAME_S_24, INTERVAL_24, 2 * INTERVAL_24),
    ("idle481-48fps-frameseq", 481, 1.0 / 48, INTERVAL_48, 0.0),
    ("idle481-48fps-webm-cold", 481, 1.0 / 48, INTERVAL_48, 2 * INTERVAL_48),
]


# ---------------------------------------------------------------- 核心：到点不换绑
@pytest.mark.parametrize("label,frames,frame_s,interval,lag", CORE_SCENES,
                         ids=[s[0] for s in CORE_SCENES])
def test_idle_expiry_keeps_clip_until_loop_end_then_rearms(label, frames, frame_s,
                                                           interval, lag):
    """墙钟到点时本圈还在播 → 绝不重绑；圈末证据到达才收口，且落在软停+续圈上。"""
    lib, sprite, c, clip, feed, runner = _idle_scene(
        frames=frames, frame_s=frame_s, interval=interval, lag=lag)
    st = c._states[sprite]
    duration = clip.duration()

    runner.run_to_idle_expiry(st, duration, clip, lib)   # 旧实现在这里就换绑（红点）
    assert feed.delivered(clip) < frames - 1          # 前提：末帧确实还在路上
    assert clip.start_count == 1
    assert clip.hard_stops == 0

    # 末帧交付前每一 tick 都不得离场
    while feed.delivered(clip) < frames - 1:
        runner.step()
        assert c.state_of(sprite) == STATE_IDLE, (
            f"末帧还在路上就换绑了（{feed.delivered(clip) + 1}/{frames}）")
        assert clip.start_count == 1
    t_final = runner.t

    runner.run_until(lambda: clip.start_count >= 2, limit=200)
    # 末帧之后一个帧间隔才发 finished（24fps ≈ 3 tick）——收口落在证据上，不等满宽限
    assert runner.t - t_final <= interval + 3 * TICK_S
    assert feed.delivered(clip) == frames - 1         # 首圈一帧不少（旧实现停在 238）
    assert clip.hard_stops == 0                       # 不走硬停（旧实现 = 1）
    assert clip.generation == 1                       # 同一代：没 spawn 新 ffmpeg
    assert clip.rearms == 1                           # 走的是 re-arm 续圈
    assert c.state_of(sprite) == STATE_IDLE


def test_idle_finished_evidence_completes_tail_without_last_frame():
    """末帧在链路上丢失（永不交付）但圈末标记到了 → 靠 finished 证据收口。

    丢帧路径在 reader 队列满时真实存在（``_stamp_source_indices`` 丢帧但时间线
    槽位照常推进）：此时帧号证据永远等不到，只能靠结束标记。
    """
    lib, sprite, c, clip, feed, runner = _idle_scene(frames=241)
    feed.upper = 238                                  # 第 240 帧永不交付
    st = c._states[sprite]

    runner.run_to_idle_expiry(st, clip.duration(), clip, lib)
    assert feed.delivered(clip) < clip.frames - 1     # 前提：末帧已丢，帧号证据失效
    t_open = runner.t
    assert st.idle_tail_grace is not None             # 窗口已开（到点不换绑）

    runner.run_until(lambda: clip.start_count >= 2, limit=400)
    assert runner.t - t_open <= 2 * INTERVAL_24 + 3 * TICK_S   # 不等满 0.5s 宽限
    assert feed.delivered(clip) == 238                # 只交到丢帧的前一帧
    assert clip.hard_stops == 0                       # 圈末已到 → 同样是软停 + 续圈
    assert clip.rearms == 1
    assert c.state_of(sprite) == STATE_IDLE


def test_idle_last_frame_never_rebinds_before_finished_registers():
    """**负控**：有 finished 契约的 clip 到末帧后哪怕再 tick 若干次也绝不换绑。

    末帧已交付 ≠ reader 已驻留圈边界：``_reader_parked`` 由 reader 线程写、
    GUI 侧读，两者之间没有同步点。按帧号抢先换绑 → ``stop()`` 可能恰在驻留
    置位之前判定，落回硬停换代（实机那种每圈一次 ffmpeg churn）。故有
    finished 契约时只认 finished 登记（或宽限到期）。
    """
    lib, sprite, c, clip, feed, runner = _idle_scene(frames=241, end_marker=False)
    st = c._states[sprite]
    duration = clip.duration()

    runner.run_to_idle_expiry(st, duration, clip, lib)
    runner.run_until(lambda: feed.delivered(clip) >= clip.frames - 1)
    assert clip.natural_end is True and clip.parked is True   # 末帧已交付、标记已入队

    # 末帧之后若干 tick（>2）都没有 finished 登记 → 绝不换绑
    t_final = runner.t
    runner.run_until(lambda: runner.t - t_final >= 2 * INTERVAL_24 + 2 * TICK_S,
                     limit=200)
    assert clip.start_count == 1, "末帧到了就抢先换绑（finished 还没登记）"
    assert st.idle_clip_finished is False
    assert clip.hard_stops == 0 and c.state_of(sprite) == STATE_IDLE

    # finished 登记后：下一个 tick 才切（显示机会 + 真圈末）
    clip.reach_loop_end()
    assert st.idle_clip_finished is True
    before = runner.ticks
    runner.run_until(lambda: clip.start_count >= 2, limit=50)
    assert runner.ticks - before <= 2
    assert clip.hard_stops == 0 and clip.rearms == 1          # 软停 + 续圈（非硬停）
    assert c.state_of(sprite) == STATE_IDLE


def test_idle_last_frame_without_finished_exits_on_bounded_grace():
    """末帧已交付但 finished 始终不到（信号丢失）→ 宽限到期有界换绑，不挂起。

    这一档不是"抢先换绑"的借口：换绑仍要等宽限（`_IDLE_TAIL_GRACE_S`）走完，
    代价与旧墙钟语义的下限相同，且此时圈末已到（标记已交付）→ 仍是软停 + 续圈。
    """
    lib, sprite, c, clip, feed, runner = _idle_scene(frames=241, end_marker=False)
    st = c._states[sprite]

    runner.run_to_idle_expiry(st, clip.duration(), clip, lib)
    t_open = runner.t                                 # 窗口开：宽限从这里起算
    assert st.idle_tail_grace is not None
    runner.run_until(lambda: feed.delivered(clip) >= clip.frames - 1)
    assert clip.start_count == 1                      # 末帧到了仍不换绑
    assert c.state_of(sprite) == STATE_IDLE

    # 宽限未满之前绝不换绑
    runner.run_until(lambda: runner.t - t_open >= _IDLE_TAIL_GRACE_S - 3 * TICK_S,
                     limit=200)
    assert clip.start_count == 1
    assert c.state_of(sprite) == STATE_IDLE

    # 宽限到期 → 有界换绑，且圈末已到故仍走软停 + 续圈
    runner.run_until(lambda: clip.start_count >= 2, limit=100)
    assert 0.0 <= (runner.t - t_open) - _IDLE_TAIL_GRACE_S <= 3 * TICK_S
    assert clip.hard_stops == 0 and clip.rearms == 1
    assert c.state_of(sprite) == STATE_IDLE


# ---------------------------------------------------------------- 旧替身兼容
@pytest.mark.parametrize("clip_cls", [NoFrameApiClip, ParkClip],
                         ids=["no-frame-api", "frames-never-delivered"])
def test_idle_without_frame_witness_keeps_wallclock_roll(clip_cls):
    """帧接口缺失 / 本圈一帧未交付 → 旧墙钟语义逐 tick 一致（零额外等待）。

    这条正是既有 ``test_sprite_behavior.py`` 假库（clip 帧号恒为 0）与
    ``animation_gap`` 族仍必须原样通过的原因，也是「不许把所有 idle 都拖进
    等帧」的边界：拿不到任何帧证据时立即按旧口径掷骰。
    """
    lib, sprite, c, clip, feed, runner = _idle_scene(clip_cls=clip_cls)
    feed.freeze()                                     # 一帧都不交付
    st = c._states[sprite]
    duration = clip.duration()

    runner.run_until(lambda: st.elapsed + TICK_S > duration)
    assert c.state_of(sprite) == STATE_IDLE
    assert clip.start_count == 1

    runner.step()                                     # 跨过到点那一 tick
    assert clip.start_count == 2                      # 到点即掷骰换绑
    assert clip.hard_stops == 1                       # 无圈末证据：该换绑只能硬停（旧代价）
    assert c.state_of(sprite) == STATE_IDLE


# ---------------------------------------------------------------- 缺信号 / 有界退出
def test_idle_finite_clip_without_finished_signal_exits_on_last_frame():
    """缺 finished 信号的有限素材：末帧交付并有过一次绘制机会即收口，不拖满宽限。

    这一档只能靠帧号证据，故收口略早于圈末标记（软停判据此时不真 → 硬停，
    代价与改动前同档）；关键是**有界**——绝不因为等不到信号把掷骰链挂住。
    出货的三个播放器（WebMClip / FrameSeqClip / GifClip）都有该信号。
    """
    lib, sprite, c, clip, feed, runner = _idle_scene(clip_cls=NoSignalClip)
    assert getattr(clip, "finished", None) is None    # 信号确实缺（接不上）
    st = c._states[sprite]

    runner.run_until(lambda: feed.delivered(clip) >= clip.frames - 1
                     or clip.start_count >= 2)
    assert c.state_of(sprite) == STATE_IDLE
    assert clip.start_count == 1                      # 末帧那一 tick 只登记观测

    runner.step()                                     # 下一个 tick 收口（显示机会）
    assert clip.start_count == 2
    assert feed.delivered(clip) == clip.frames - 1    # 全圈帧一帧不少
    assert c.state_of(sprite) == STATE_IDLE


def test_idle_stalled_frames_exit_bounded_and_never_hang():
    """帧停在中途且永不 finished（预取断链）→ 宽限到期有界收口，绝不挂起。"""
    lib, sprite, c, clip, feed, runner = _idle_scene(frames=241)
    runner.run_until(lambda: feed.delivered(clip) >= 120)
    feed.freeze()
    st = c._states[sprite]
    duration = clip.duration()

    base = _total_starts(lib)
    runner.run_until(lambda: st.elapsed >= duration or _total_starts(lib) > base,
                     limit=4000)
    t_open = runner.t
    assert c.state_of(sprite) == STATE_IDLE
    assert _total_starts(lib) == base                 # 到点不换绑（帧号证据 > 0）

    runner.run_until(lambda: clip.start_count >= 2, limit=400)
    waited = runner.t - t_open
    assert _IDLE_TAIL_GRACE_S - TICK_S <= waited <= _IDLE_TAIL_GRACE_S + 3 * TICK_S
    assert feed.delivered(clip) == 120                # 尾巴确实没等到（有界的代价）
    assert clip.hard_stops == 1                       # 无圈末证据 → 该档只能硬停
    assert c.state_of(sprite) == STATE_IDLE


# ---------------------------------------------------------------- 换绑目标
def test_idle_roll_to_other_idle_clip_switches_and_parks_old():
    """圈末收口后掷中另一条待机素材：照常换绑，旧 clip 也停在圈末（非硬停）。"""
    lib, sprite, c, clip, feed, runner = _idle_scene(
        idle_names=("idle1", "idle2"), choices=("idle1", "idle2"))
    other = lib.clip("idle2")
    st = c._states[sprite]

    runner.run_to_idle_expiry(st, clip.duration(), clip, lib)
    runner.run_until(lambda: other.start_count >= 1, limit=400)

    assert sprite._clip_name == "idle2"               # 换绑照常发生（闸门不吞换绑）
    assert c.state_of(sprite) == STATE_IDLE
    assert other.generation == 1                      # 新 clip fresh start
    assert feed.delivered(clip) == clip.frames - 1    # 旧 clip 首圈一帧不少
    assert clip.soft_parked is True                   # 旧 clip 停在圈末驻留
    assert clip.hard_stops == 0


def test_idle_roll_into_move_still_starts_move_and_parks_old():
    """掷中移动桶仍照常起步（MOVE 语义不因圈末闸门退化），旧 idle 停在圈末。"""
    lib, sprite, c, clip, feed, runner = _idle_scene(
        facing="right", moves=("walk",), strides={"walk": 120},
        rolls=(0.99,), ints=(100, 0), choices=(1,))
    walk = lib.clip("walk")
    st = c._states[sprite]

    runner.run_to_idle_expiry(st, clip.duration(), clip, lib)
    runner.run_until(lambda: st.state == STATE_MOVE, limit=400)

    assert st.anim == "walk"
    assert walk.start_count == 1
    assert sprite.velocity.x() != 0.0                 # 移动照常起步
    assert clip.soft_parked is True                   # 旧 idle 停在圈末（没被硬停）
    assert clip.hard_stops == 0


def test_acts_tail_still_gated_with_idle_tail_in_place():
    """同一条链上的 ACTS 末帧收口不退化：到点仍等末帧，末帧后照常掷骰回待机。"""
    lib, sprite, c, clip, feed, runner = _idle_scene(acts=("act1",))
    act = lib.clip("act1")
    assert c.play_once(sprite, "act1") is True
    st = c._states[sprite]
    assert c.state_of(sprite) == STATE_ACTS
    # 换绑即作废 IDLE 末帧证据（代次守卫）：新一圈 ACTS 不受旧 clip 观测影响
    assert st.idle_tail_grace is None and st.idle_clip_finished is False

    runner.run_until(lambda: st.elapsed >= st.duration or st.state != STATE_ACTS)
    assert c.state_of(sprite) == STATE_ACTS           # 到点仍不换绑
    assert feed.delivered(act) < act.frames - 1
    while feed.delivered(act) < act.frames - 1:
        runner.step()
        assert c.state_of(sprite) == STATE_ACTS
    t_final = runner.t

    runner.run_until(lambda: c.state_of(sprite) != STATE_ACTS, limit=200)
    assert runner.t - t_final <= INTERVAL_24 + 3 * TICK_S
    assert c.state_of(sprite) == STATE_IDLE           # roll 0.0 → 回待机
    assert act.start_count == 1                       # 单圈动作不重播


# ---------------------------------------------------------------- 打断
def test_idle_tail_interrupted_by_click_never_rolls_late():
    """末帧等待期被点击打断：窗口作废、立即换绑，旧 clip 的 finished 不污染新绑定。"""
    lib, sprite, c, clip, feed, runner = _idle_scene(frames=241)
    st = c._states[sprite]
    runner.run_to_idle_expiry(st, clip.duration(), clip, lib)
    assert st.idle_tail_grace is not None             # 等待窗口已开

    assert c.on_sprite_clicked(sprite) is True
    assert c.state_of(sprite) == STATE_CLICK          # 即时打断
    assert st.idle_tail_grace is None                 # 窗口随换绑作废
    assert st.idle_clip_finished is False
    assert lib.clip("click1").start_count == 1

    clip.reach_loop_end()                             # 旧 clip 的圈末信号（已断开）
    assert st.idle_clip_finished is False             # 旧 finished 绝不落进新绑定
    assert c.state_of(sprite) == STATE_CLICK          # 也不提前收口

    runner.run_until(lambda: c.state_of(sprite) != STATE_CLICK, limit=400)
    assert c.state_of(sprite) == STATE_IDLE           # 点击链照常收口回待机
    for _ in range(40):                               # 越过原宽限窗口：无第二次切换
        runner.step()
        assert c.state_of(sprite) == STATE_IDLE
    assert clip.hard_stops == 1                       # 只有打断那一次硬停，无迟到换绑


def test_idle_tail_interrupted_by_drag_never_rolls():
    """末帧等待期拖拽接管：立即进 DRAG、窗口作废，接管期间绝不掷骰换绑。"""
    lib, sprite, c, clip, feed, runner = _idle_scene(frames=241)
    st = c._states[sprite]
    runner.run_to_idle_expiry(st, clip.duration(), clip, lib)
    assert st.idle_tail_grace is not None

    sprite.on_press(QPointF(10, 10))                  # overlay 接线点：按下
    sprite.begin_drag()                               # 过阈值升级（真拖拽序列）
    c.on_drag_started(sprite)
    assert c.state_of(sprite) == STATE_DRAG           # 即时打断
    assert st.idle_tail_grace is None
    assert st.idle_clip_finished is False

    for _ in range(40):                               # 越过原宽限窗口
        runner.step()
        assert c.state_of(sprite) == STATE_DRAG
    assert clip.start_count == 1                      # idle clip 未被重播/换绑

    sprite.on_release(QPointF(12, 12))                # 原地放下（非甩出）
    c.on_drag_released(sprite)
    assert c.state_of(sprite) == STATE_IDLE


# -------------------------------------------- 真实 WebMClip：软停 → re-arm 不换代
class RealClipLibrary:
    """把一条真实 WebMClip 当作 idle1 的轻量库（movie/duration/frames 同名同义）。"""

    def __init__(self, clip, idle="idle1"):
        self._clip = clip
        self._idle = idle
        self.idles = [idle]
        self.turns: list = []
        self.moves: list = []
        self.clicks: list = []
        self.acts: list = []
        self.drag = None
        self.move_strides: dict = {}
        self.move_curves: dict = {}

    def movie(self, name, **_kw):
        assert name == self._idle, name
        return self._clip

    def duration(self, name):
        assert name == self._idle, name
        return self._clip.duration()

    def frames(self, name):
        assert name == self._idle, name
        return self._clip.frameCount()


def test_real_webmclip_idle_loop_end_parks_and_rearms_without_new_process(
        app, monkeypatch, tmp_path):
    """同 clip 在真正圈末重绑：软停 + re-arm——不杀进程、不换代、不新起 ffmpeg。

    复用既有 webm 生命周期夹具（假 ffmpeg 流按放行额度逐帧产出 + 假 Popen），
    与实机诊断逐项对应：墙钟到点时末帧还在路上 → 不重绑；末帧交付 + reader
    驻留圈边界 → 下一个 tick 收口 → ``bind_clip`` 的 ``stop()`` 落在软停、
    ``start()`` 走 re-arm（同一 reader 线程 / 同一 ffmpeg 进程 / 退役池为空）。
    """
    clip = _make_clip(tmp_path, frame_count=3)        # 真实 WebMClip，3 帧 @24fps
    spawns: list = []
    _install_fake_ffmpeg(monkeypatch, clip, spawns)
    srcs: list = []
    finished: list = []
    clip.frameChanged.connect(srcs.append)
    clip.finished.connect(lambda: finished.append(True))
    lib = RealClipLibrary(clip)
    sprite = PetSprite(lib, pos=QPointF(800, 400), facing="left", scale=0.5)
    c = BehaviorController(BOUNDS, rng=ScriptedRng(rolls=(0.0, 0.0)))
    c.predict_enabled = False
    try:
        c.tick([sprite], 0.05)                        # 进待机并起播（真实 WebMClip.start）
        assert sprite._clip_name == "idle1"
        assert clip._reader_ready.wait(5.0)
        proc, gen = spawns[0][0], spawns[0][1]
        first_thread = clip._thread
        assert len(spawns) == 1 and clip.duration() > 0.0

        # 交付前两帧（末帧还在路上），把行为墙钟推过 clip 时长
        for expected in (0, 1):
            gen.release()
            assert _consume_until(clip, lambda e=expected: clip.currentFrameNumber() == e), \
                f"srcs={srcs}"
        st = c._states[sprite]
        for _ in range(3):
            c.tick([sprite], 0.05)
        assert len(spawns) == 1, "墙钟到点就重绑换代了（旧实现在此 spawn 第二个 ffmpeg）"
        assert clip._retired == [], "墙钟到点就走了硬停（退役 reader）"
        assert st.elapsed >= clip.duration()          # 墙钟已到点
        assert clip.currentFrameNumber() == 1         # 末帧仍在路上

        # 末帧已交付 + reader 驻留圈边界（= 实机 _reader_parked=True / 标记已入队）
        gen.release()
        assert _consume_until(clip, lambda: clip.currentFrameNumber() == 2), f"srcs={srcs}"
        assert _wait_parked(clip)
        assert clip._natural_end_pending is True      # 末帧交付即置位（_process_frame）

        # 负控：末帧已交付但 finished 还没登记 → 再 tick 两次也绝不重绑换代
        c.tick([sprite], 0.05)
        c.tick([sprite], 0.05)
        assert len(spawns) == 1, "末帧到了就抢先换绑（finished 还没登记）"
        assert clip._retired == []
        assert clip._reader_parked is True, "reader 不应已被换绑唤醒"
        assert st.idle_clip_finished is False
        assert sprite._clip_name == "idle1"

        # 结束标记被消费 → finished 登记（此刻 park 的两道判据都已就位）
        assert _consume_until(clip, lambda: len(finished) == 1), f"srcs={srcs}"
        assert st.idle_clip_finished is True
        assert clip._natural_end_pending is True and clip._reader_parked is True
        assert clip._running is False                 # 圈末标记路径已停表

        # 收口：同一 clip 重绑 → stop() 软停、start() re-arm，一切不换代
        c.tick([sprite], 0.05)
        assert c.state_of(sprite) == STATE_IDLE
        assert len(spawns) == 1, "圈末重绑又起了第二个 ffmpeg"
        assert clip._thread is first_thread, "圈末重绑换了 reader 线程"
        assert clip._reader_proc is proc, "圈末重绑换了 ffmpeg 进程"
        assert clip._retired == []                    # 没走硬停
        assert clip._soft_parked is False and clip._running is True   # re-arm 续圈中

        # 第二圈：同一进程继续出帧，帧号回绕 0/1/2
        for _ in range(3):
            gen.release()
        assert _consume_until(clip, lambda: srcs[-3:] == [0, 1, 2]), f"srcs={srcs}"
    finally:
        _close_all(spawns)
        clip.cleanup()
        app.processEvents()
    assert proc.poll() is not None, "cleanup 后 ffmpeg 进程必须被杀"
