# -*- coding: utf-8 -*-
"""ACTS 末帧收口（F8）：墙钟到点 ≠ 末帧已上屏。

实机口径（三条臂，241/481 帧素材）：
- ``ctl-act24-fs-24`` 帧序列臂：库 duration = 241/24 = 10.042s，播放器定时器
  取整到 42ms（> 名义帧长 41.67ms）→ 末帧要到 241×42ms = 10.122s 才交付，
  墙钟到点时还缺 239-240 两帧；
- ``ctl-act24-webm-22`` webm 臂：再叠加 WebM 冷启动首帧延迟（实机 60-166ms，
  折算 2 帧）→ 缺 237-240 四帧；
- ``ctl-act48-fs-23``：481 帧 @48fps，定时器 21ms > 名义 20.83ms → 缺末三帧。

旧行为在 ``elapsed >= duration`` 那一 tick 直接掷下一个动作并换绑 clip，
把还没交付的末帧连同 clip 一起换掉——末帧永远没机会上屏，实测就是上述
缺帧窗口。本文件的假 clip 按同一节拍交付源帧，断言「到点不换绑、末帧交付
后至少一个 tick 才换、末帧/结束标记都不来时宽限内收口、被打断则绝不迟到
收口」。

纪律（AGENTS.md 时序测试）：不起真实 QTimer、不 sleep——tick 推进与帧交付
全部由测试显式驱动，时长为模拟时钟累加；假 clip 只提供
currentFrameNumber/frameCount + frameChanged/finished 信号。
"""
from __future__ import annotations

import os
from collections import deque

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import QObject, QPointF, QRect, Signal
from PySide6.QtGui import QImage

from pet.pet_sprite import PetSprite
from pet.sprite_behavior import (
    _ACTS_TAIL_GRACE_MAX_S,
    _ACTS_TAIL_GRACE_MIN_S,
    STATE_ACTS,
    STATE_CLICK,
    STATE_DRAG,
    STATE_IDLE,
    BehaviorController,
)

BOUNDS = QRect(0, 0, 2000, 1000)
#: overlay tick 间隔（tick_driver.TickDriver.tick_interval_ms 封顶 16ms）
TICK_S = 0.016
FRAME_S_24 = 1.0 / 24          # 名义帧长（源 fps 24）
INTERVAL_24 = 0.042            # 播放器定时器口径 round(1000/24)
INTERVAL_48 = 0.021            # round(1000/48)
ACT = "act241"


# ---------------------------------------------------------------- 假 clip / 假 library
class FakeClip(QObject):
    """帧交付节拍由测试驱动的假 clip（接口对齐 WebMClip/FrameSeqClip）。

    ``currentFrameNumber``/``frameCount`` 是产品代码读的两个公开接口，
    ``deliver``/``finish`` 只在测试里调用——模拟播放器定时器与解码线程
    在 GUI 事件循环里交替交付帧与结束标记。
    """

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, name, frames, frame_s=FRAME_S_24):
        super().__init__()
        self.name = name
        self.frames = frames
        self.frame_s = frame_s
        self.image = QImage(64, 36, QImage.Format.Format_ARGB32)
        self.image.fill(0xFF336699)
        self._index = 0
        self.start_count = 0

    # 播放器公开接口（库 frames()/duration() 也走这里）
    def frameCount(self):
        return self.frames

    def duration(self):
        return self.frames * self.frame_s

    def currentFrameNumber(self):
        return self._index

    def currentImage(self):
        return self.image

    def start(self):
        self.start_count += 1
        return True

    def stop(self):
        return None

    # 测试侧帧交付
    def deliver(self, index):
        self._index = index
        self.frameChanged.emit(index)

    def finish(self):
        self.finished.emit()


class NoFrameApiClip(FakeClip):
    """接口缺失的旧 clip 替身（帧号/帧数都读不到 → 必须回退旧墙钟语义）。"""

    frameCount = None
    currentFrameNumber = None


class FakeLibrary:
    """轻量库协议：movie/clip/frames/duration（与 MovieLibrary 同名同义）。"""

    def __init__(self, *, idles, turns, moves, clicks, acts, act_frames,
                 act_frame_s=FRAME_S_24, clip_cls=FakeClip, drag=None):
        self.idles = list(idles)
        self.turns = list(turns)
        self.moves = list(moves)
        self.clicks = list(clicks)
        self.acts = list(acts)
        self.drag = drag
        self._clips = {}
        for name in self.idles + self.turns + self.moves + self.clicks:
            self._clips[name] = FakeClip(name, 24)
        for name in self.acts:
            self._clips[name] = clip_cls(name, act_frames, act_frame_s)
        if drag:
            self._clips[drag] = FakeClip(drag, 24)
        self.move_strides = {}
        self.move_curves = {}

    def movie(self, name, **_kw):
        return self._clips[name]

    def clip(self, name):
        return self._clips[name]

    def frames(self, name):
        return self._clips[name].frameCount()

    def duration(self, name):
        return self._clips[name].duration()


class ScriptedRng:
    """确定性随机源：队列耗尽后回落到稳妥默认值（0.0 → 待机/直接起步）。"""

    def __init__(self, rolls=(), ints=(), choices=()):
        self.rolls = deque(rolls)
        self.ints = deque(ints)
        self.choices = deque(choices)

    def random(self):
        return self.rolls.popleft() if self.rolls else 0.0

    def randint(self, a, b):
        return self.ints.popleft() if self.ints else a

    def choice(self, seq):
        if self.choices and self.choices[0] in seq:
            return self.choices.popleft()
        return seq[0]


# ---------------------------------------------------------------- 播放节拍模拟
class ActFeed:
    """ACTS 播放节拍：源帧 idx 在 ``lag + (idx+1)×interval`` 时刻交付。

    interval = 播放器定时器取整口径（24fps→42ms、48fps→21ms，均 ≥ 名义帧长）；
    lag = 首个交付延迟（帧序列 ~2.5ms 可忽略 → 0；WebM 冷启动 60-166ms →
    按 2 个帧间隔折算）。末帧之后一个帧间隔发 finished（两种播放器同款）。
    """

    def __init__(self, clip, frames, *, interval, lag=0.0, emit_finish=True):
        self.clip = clip
        self.frames = frames
        self.interval = interval
        self.lag = lag
        self.last_frame = frames - 1
        self.emit_finish = emit_finish
        self.finished = False
        self.delivered = -1
        self.t0 = None

    def start(self, t):
        self.t0 = t
        self.delivered = -1

    def due(self, t):
        if self.t0 is None:
            return -1
        n = int((t - self.t0 - self.lag) / self.interval) - 1
        return max(-1, min(n, self.last_frame))

    def poll(self, t):
        """交付所有到期帧；末帧之后一个帧间隔发结束标记。"""
        if self.t0 is None:
            return
        n = self.due(t)
        while self.delivered < n:
            self.delivered += 1
            self.clip.deliver(self.delivered)
        if (self.emit_finish and not self.finished
                and self.delivered >= self.last_frame
                and t - self.t0 >= self.lag + (self.delivered + 2) * self.interval):
            self.finished = True
            self.clip.finish()


class Runner:
    """确定性 tick 循环：先交付到期帧（播放器定时器）再 tick（overlay 定时器）。

    ``clock_scale`` = 真实钟/仿真钟的比值（默认 1.0 = 同步）。> 1 等价于
    TickDriver 的 dt 被钳到上限（tick_driver.py:433，真实流逝 > 传入的 dt）：
    clip 的帧按真实钟交付、状态机的 ``elapsed`` 按仿真钟累加，本文件用它把
    「dt 钳制」这一档做成确定性现场（见 ``test_acts_tail_under_tick_dt_clamp``）。
    """

    def __init__(self, controller, sprite, feed, dt=TICK_S, clock_scale=1.0):
        self.c = controller
        self.sprite = sprite
        self.feed = feed
        self.dt = dt
        self.clock_scale = clock_scale
        self.t = 0.0
        self.ticks = 0

    def step(self):
        self.t += self.dt * self.clock_scale
        self.feed.poll(self.t)
        self.c.tick([self.sprite], self.dt)
        self.sprite.advance(self.dt)
        self.ticks += 1

    def run_until(self, pred, limit=6000):
        while not pred() and self.ticks < limit:
            self.step()
        assert pred(), "run_until 未在预算内满足条件"

    def run_to_acts_end(self, st, duration):
        """跑到墙钟到点那一 tick（此刻末帧通常还在路上）。

        终止条件带上「已不在 ACTS」：旧代码在到点那一 tick 就换动画，
        elapsed 随之归零，只等 elapsed ≥ duration 会空转到超时——
        这条断言就是本刀的红点。
        """
        self.run_until(lambda: st.elapsed >= duration
                       or self.c.state_of(self.sprite) != STATE_ACTS)
        assert self.c.state_of(self.sprite) == STATE_ACTS, (
            f"墙钟到点那一 tick 就换了动画（此时末帧 {self.feed.delivered + 1}/"
            f"{self.feed.frames} 还没交付）")


def _scene(*, frames=241, frame_s=FRAME_S_24, interval=INTERVAL_24, lag=0.0,
           clip_cls=FakeClip, emit_finish=True):
    """搭一台「待机播完 → 掷中 acts 桶」的 ACTS 现场，返回全部句柄。"""
    lib = FakeLibrary(
        idles=["idle1"], turns=["turn1"], moves=[], clicks=["click1"], acts=[ACT],
        act_frames=frames, act_frame_s=frame_s, clip_cls=clip_cls)
    sprite = PetSprite(lib, pos=QPointF(800, 400), facing="left", scale=0.5)
    # rolls: 0.5 ∈ acts 桶（进 ACTS）；随后 0.0 → 收口回待机
    c = BehaviorController(BOUNDS, rng=ScriptedRng(rolls=(0.5, 0.0)))
    c.predict_enabled = False
    clip = lib.clip(ACT)
    feed = ActFeed(clip, frames, interval=interval, lag=lag, emit_finish=emit_finish)
    runner = Runner(c, sprite, feed)
    runner.run_until(lambda: c.state_of(sprite) == STATE_ACTS)
    feed.start(runner.t)
    st = c._states[sprite]
    assert st.elapsed == 0.0 and st.anim == ACT
    return lib, sprite, c, clip, feed, runner


# 三条实机臂的节拍与缺帧窗口
CORE_SCENES = [
    ("ctl-act24-fs-24", 241, FRAME_S_24, INTERVAL_24, 0.0),
    ("ctl-act24-webm-22", 241, FRAME_S_24, INTERVAL_24, 2 * INTERVAL_24),
    ("ctl-act48-fs-23", 481, 1.0 / 48, INTERVAL_48, 0.0),
]


# ---------------------------------------------------------------- 核心：到点不换绑
@pytest.mark.parametrize("label,frames,frame_s,interval,lag", CORE_SCENES,
                         ids=[s[0] for s in CORE_SCENES])
def test_acts_keeps_clip_until_final_frame_delivered(label, frames, frame_s,
                                                     interval, lag):
    """墙钟到点时末帧尚未交付 → 绝不换动画（旧代码在此 rebind，红点）。"""
    lib, sprite, c, clip, feed, runner = _scene(
        frames=frames, frame_s=frame_s, interval=interval, lag=lag)
    st = c._states[sprite]
    duration = lib.duration(ACT)
    runner.run_to_acts_end(st, duration)

    # 前提：到点那一刻末帧确实还在路上（实机缺 2-4 帧）
    assert feed.delivered < frames - 1
    # 产品行为：仍绑着原 clip、没掷下一个动作
    assert c.state_of(sprite) == STATE_ACTS
    assert sprite._clip_name == ACT
    assert clip.start_count == 1

    # 末帧交付后：下一个 tick 收口（至少一个 tick 的显示机会），不拖过宽限
    final_tick = None
    while c.state_of(sprite) == STATE_ACTS and runner.ticks < 3000:
        before = feed.delivered
        runner.step()
        if before < frames - 1 <= feed.delivered:
            final_tick = runner.ticks
    assert final_tick is not None, "末帧交付后仍不收口（状态机挂起）"
    assert c.state_of(sprite) == STATE_IDLE          # roll 0.0 → 回待机
    assert final_tick < runner.ticks <= final_tick + 2
    assert feed.delivered == frames - 1              # 全圈帧数一帧不少地播完
    assert clip.start_count == 1                     # 末帧段既不重播也不换绑


def test_acts_final_frame_callback_never_rebinds_inside_callback():
    """帧回调栈里绝不换 clip（换绑会在旧 clip 的 emit 栈里重入解码）。"""
    lib, sprite, c, clip, feed, runner = _scene(lag=2 * INTERVAL_24, frames=241)
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))
    feed.last_frame = -1                             # 停掉节拍器，只留手动交付
    feed.emit_finish = False

    clip.deliver(240)                                # 末帧（回调栈内）

    assert c.state_of(sprite) == STATE_ACTS          # 回调里不切换
    assert clip.start_count == 1
    runner.step()                                    # 本 tick 只登记观测
    assert c.state_of(sprite) == STATE_ACTS
    runner.step()                                    # 下一个 tick 才收口（显示机会）
    assert c.state_of(sprite) == STATE_IDLE


# ---------------------------------------------------------------- 结束标记
def test_acts_finished_completes_tail_without_last_frames():
    """末帧始终不交付但结束标记到了 → 下一个 tick 收口，不等满宽限。"""
    lib, sprite, c, clip, feed, runner = _scene(lag=2 * INTERVAL_24, frames=241)
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))
    feed.last_frame = 239                            # 第 240 帧永不交付
    ticks_before = runner.ticks

    clip.finish()                                    # 末帧之后一个帧间隔的结束标记

    runner.step()
    assert c.state_of(sprite) == STATE_IDLE
    assert runner.ticks - ticks_before <= 2          # 不等宽限（0.35s ≈ 22 tick）
    # tick 与 finished 两条路径不得各收口一次（重复切换）
    idle_bindings = lib.clip("idle1").start_count
    for _ in range(3):
        runner.step()
    assert lib.clip("idle1").start_count == idle_bindings
    assert clip.start_count == 1


# ---------------------------------------------------------------- 宽限与回退
def test_acts_grace_bounds_wait_when_last_frame_never_arrives():
    """末帧与结束标记都不到（预取断链）→ 宽限到期必须收口，绝不挂起。"""
    lib, sprite, c, clip, feed, runner = _scene(lag=2 * INTERVAL_24, frames=241)
    feed.last_frame = 238                            # 末三帧永不交付
    feed.emit_finish = False
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))
    t_tail = runner.t

    runner.run_until(lambda: runner.t - t_tail >= _ACTS_TAIL_GRACE_MIN_S - 2 * TICK_S)
    assert c.state_of(sprite) == STATE_ACTS          # 宽限内不提前收口

    runner.run_until(lambda: c.state_of(sprite) != STATE_ACTS)
    waited = runner.t - t_tail
    assert _ACTS_TAIL_GRACE_MIN_S - TICK_S <= waited <= _ACTS_TAIL_GRACE_MIN_S + 3 * TICK_S
    assert c.state_of(sprite) == STATE_IDLE


def test_acts_grace_capped_for_slow_clip():
    """慢素材（帧间隔 0.5s、4×帧间隔 = 2s）也绝不越过硬上限 0.5s。"""
    lib, sprite, c, clip, feed, runner = _scene(
        frames=6, frame_s=0.5, interval=0.5, lag=0.0, emit_finish=False)
    feed.last_frame = 4                              # 第 6 帧永不交付
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))
    t_tail = runner.t

    runner.run_until(lambda: c.state_of(sprite) != STATE_ACTS)
    waited = runner.t - t_tail
    assert _ACTS_TAIL_GRACE_MAX_S - TICK_S <= waited    # 没有被上限抹成 0 等待
    assert waited <= _ACTS_TAIL_GRACE_MAX_S + 3 * TICK_S
    assert c.state_of(sprite) == STATE_IDLE


@pytest.mark.parametrize("clip_cls", [NoFrameApiClip, FakeClip],
                         ids=["no-frame-api", "frames-never-delivered"])
def test_acts_without_usable_frame_api_keeps_wallclock_switch(clip_cls):
    """帧接口缺失 / 从未交付过帧 → 旧墙钟语义逐 tick 一致（零额外等待）。

    这条正是既有 test_sprite_behavior.py 假库（clip 帧号恒为 0、库无 frames()）
    仍必须原样通过的原因。
    """
    lib, sprite, c, clip, feed, runner = _scene(lag=0.0, clip_cls=clip_cls)
    feed.last_frame = -1                             # 本圈一帧都不交付
    st = c._states[sprite]
    duration = lib.duration(ACT)
    runner.run_until(lambda: st.elapsed + TICK_S > duration)
    assert c.state_of(sprite) == STATE_ACTS

    runner.step()                                    # 跨过到点那一 tick

    assert c.state_of(sprite) == STATE_IDLE


# ------------------------------------- 残余：首帧迟到量随负载漂移（r7/r8 形状）
#: 实机两轮残余（``ACT-ARCH2X2-63-REPORT.md`` §7）的入场首帧延迟是 384ms
#: （241 帧 @24fps）/ 318ms（481 帧 @48fps），而旧宽限是不看缺口的常数
#: 350ms，实测迟到量 424ms / 377ms——余量被负载吃掉就丢末帧。下列用例把
#: 「迟到量落在固定下限与硬上限之间」「宽限随实测缺口放大」「迟到量超出硬
#: 上限」三档做成确定性现场（滞后按帧间隔折算成首帧交付延迟）。
RESIDUAL_SCENES = [
    # 241 帧 @24fps（r7-new24 形状）：滞后 8 个帧间隔 = 336ms → 到点还缺 10 帧、
    # 剩余等待 ≈ 410ms（常数下限 350ms 覆盖不到，硬上限 0.5s 覆盖得到）
    ("r7-new24-shape", 241, FRAME_S_24, INTERVAL_24, 8 * INTERVAL_24, 8),
    # 481 帧 @48fps（r8-new48 形状）：滞后 16 个帧间隔 = 336ms → 到点还缺 20 帧、
    # 剩余等待 ≈ 416ms
    ("r8-new48-shape", 481, 1.0 / 48, INTERVAL_48, 16 * INTERVAL_48, 8),
]


def _window_at_open(lag, *, frames=241, frame_s=FRAME_S_24, interval=INTERVAL_24):
    """跑到「墙钟到点、末帧等待窗口刚开」那一 tick，返回现场与状态。"""
    lib, sprite, c, clip, feed, runner = _scene(
        frames=frames, frame_s=frame_s, interval=interval, lag=lag)
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))
    return lib, sprite, c, clip, feed, runner, st


@pytest.mark.parametrize("label,frames,frame_s,interval,lag,min_deficit",
                         RESIDUAL_SCENES, ids=[s[0] for s in RESIDUAL_SCENES])
def test_acts_tail_covers_late_first_frame_beyond_min_grace(
        label, frames, frame_s, interval, lag, min_deficit):
    """首帧迟到量超出固定下限时，宽限必须随实测缺口放大（r7/r8 残余）。

    旧实现给的是不看缺口的 350ms，于是首帧迟到 384ms / 318ms 那两轮在到点
    350ms 后就换绑，239-240 / 480 号帧没上屏；本用例要求这两档形状下首圈
    一帧不少地播完，且收口仍落在硬上限之内。
    """
    lib, sprite, c, clip, feed, runner = _scene(
        frames=frames, frame_s=frame_s, interval=interval, lag=lag)
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))

    # 前提：到点那一刻缺得比常数下限能覆盖的更多（实机两轮缺 11 / 18 帧）
    assert frames - 1 - feed.delivered > min_deficit
    # 宽限按实测缺口放大到下限之上、硬上限之内（旧实现恒为下限）
    assert _ACTS_TAIL_GRACE_MIN_S < st.acts_tail_grace <= _ACTS_TAIL_GRACE_MAX_S

    t_open = runner.t
    runner.run_until(lambda: c.state_of(sprite) != STATE_ACTS)

    assert feed.delivered == frames - 1              # 首圈一帧不少（旧实现丢 2/1 帧）
    assert clip.start_count == 1                     # 末帧段既不重播也不换绑
    assert c.state_of(sprite) == STATE_IDLE          # roll 0.0 → 回待机
    assert runner.t - t_open <= _ACTS_TAIL_GRACE_MAX_S + 3 * TICK_S


def test_acts_tail_budget_is_deficit_driven_not_a_constant():
    """宽限 = 实测缺口 × 名义帧间隔（夹进 [下限, 硬上限]），不是素材无关的常数。

    缺口小（到点还缺 4 帧 ≈ 0.17s）→ 落回下限；缺口大（缺 10 帧 ≈ 0.52s）→
    顶到硬上限。旧实现对两种缺口给的是同一个数值——r7/r8 的残余正源于此。
    """
    _, _, _, _, _, _, st_small = _window_at_open(2 * INTERVAL_24)
    _, _, _, _, _, _, st_large = _window_at_open(8 * INTERVAL_24)

    assert st_small.acts_tail_grace == _ACTS_TAIL_GRACE_MIN_S
    assert st_large.acts_tail_grace == _ACTS_TAIL_GRACE_MAX_S


def test_acts_tail_beyond_ceiling_fails_bounded_not_hanging():
    """迟到量超出硬上限：有界收口、丢掉尾巴，但绝不挂起/重播（登记的风险）。

    本刀**不声称**覆盖这一档：宽限按缺口放大后仍被硬上限截断（滞后 20 个帧
    间隔 = 840ms → 剩余等待 ≈ 0.92s > 0.5s）。此时必须与旧墙钟语义一致地
    在有界时间内收口——宁可丢尾巴几帧，也不让 ACTS 状态把动画链挂住。
    """
    lib, sprite, c, clip, feed, runner = _scene(lag=20 * INTERVAL_24, frames=241)
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))
    t_open = runner.t

    assert st.acts_tail_grace == _ACTS_TAIL_GRACE_MAX_S   # 缺口大 → 顶到硬上限

    runner.run_until(lambda: c.state_of(sprite) != STATE_ACTS)
    assert runner.t - t_open <= _ACTS_TAIL_GRACE_MAX_S + 3 * TICK_S   # 有界
    assert c.state_of(sprite) == STATE_IDLE
    assert clip.start_count == 1                          # 不重播、不换绑
    assert feed.delivered < 240                           # 尾巴确实没等到（有界的代价）


def test_acts_tail_under_tick_dt_clamp_still_delivers_full_loop():
    """dt 钳制（真实流逝 > 仿真 dt）只会让窗口更晚开，不会把尾巴挤掉。

    残余不能归因于 dt 钳制：``elapsed`` 由被钳的 dt 累加、永远 ≤ 真实流逝，
    故窗口在真实时间上只会更晚开启，clip 按真实钟交付得更靠前。现场取真实钟
    1.5× 仿真钟（TickDriver 的 50ms 上限方向），到点那一刻末帧已在手里。
    """
    lib, sprite, c, clip, feed, runner = _scene(lag=8 * INTERVAL_24, frames=241)
    runner.clock_scale = 1.5                         # dt 被钳：真实钟跑在仿真钟前
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))

    assert feed.delivered == 240                     # 钳制下末帧早已交付（不是丢失）
    runner.step()                                    # 显示机会 + 1 个 tick → 收口
    assert c.state_of(sprite) == STATE_IDLE
    assert clip.start_count == 1


# ---------------------------------------------------------------- 打断
def test_acts_tail_interrupted_by_click_never_rebinds_late():
    """末帧等待期被点击打断：等待窗口作废，绝不出现迟到的 ACTS 收口。"""
    lib, sprite, c, clip, feed, runner = _scene(lag=2 * INTERVAL_24, frames=241)
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))
    assert c.state_of(sprite) == STATE_ACTS

    assert c.on_sprite_clicked(sprite) is True
    assert c.state_of(sprite) == STATE_CLICK
    assert st.acts_tail_grace is None                # 待收口窗口已作废
    assert lib.clip("click1").start_count == 1

    runner.run_until(lambda: c.state_of(sprite) != STATE_CLICK)
    assert c.state_of(sprite) == STATE_IDLE          # 点击链照常收口回待机
    for _ in range(40):                              # 越过原宽限窗口
        runner.step()
    assert c.state_of(sprite) == STATE_IDLE          # 没有第二次切换
    assert clip.start_count == 1                     # act clip 未被重播/换绑


def test_acts_tail_interrupted_by_drag_never_rebinds_late():
    """末帧等待期拖拽接管：同样作废等待窗口，松手回待机不再收口 ACTS。"""
    lib, sprite, c, clip, feed, runner = _scene(lag=2 * INTERVAL_24, frames=241)
    st = c._states[sprite]
    runner.run_to_acts_end(st, lib.duration(ACT))

    sprite.on_press(QPointF(10, 10))                 # overlay 接线点：按下
    sprite.begin_drag()                              # 过阈值升级（真拖拽序列）
    c.on_drag_started(sprite)
    assert c.state_of(sprite) == STATE_DRAG
    assert st.acts_tail_grace is None

    for _ in range(40):                              # 越过原宽限窗口
        runner.step()
    assert c.state_of(sprite) == STATE_DRAG          # 拖拽期间绝不收口
    assert clip.start_count == 1

    sprite.on_release(QPointF(12, 12))               # 原地放下（非甩出）
    c.on_drag_released(sprite)
    assert c.state_of(sprite) == STATE_IDLE


# ------------------------------------------- finished 早于墙钟到点（单圈 ACTS 绝不续播）
def _finished_before_duration(frames=241, *, run_to=0.5):
    """搭一台「ACTS 真播一半 → 墙钟还没到点但 clip 已 finished」的现场。

    ``st.elapsed`` 由 tick 的 dt 累加，TickDriver 的 dt 上限与 GUI 卡顿都会
    让它落后真实时间：单圈 clip 按真实时间播完（finished 到达）时，
    ``st.elapsed`` 可能还没到 duration。
    """
    lib, sprite, c, clip, feed, runner = _scene(lag=0.0, frames=frames)
    st = c._states[sprite]
    runner.run_until(lambda: st.elapsed >= run_to)   # 先真的播一段（保证有帧交付）
    feed.last_frame = -1                             # 停节拍器：只留手动结束标记
    feed.emit_finish = False
    st.elapsed = st.duration - 0.05                  # 注入：墙钟还没到点
    return lib, sprite, c, clip, feed, runner, st


def test_acts_premature_finished_never_restarts_clip():
    """单圈 ACTS 的 finished 早于墙钟到点：绝不重播整圈（旧代码闪回一次）。

    旧实现把 ACTS 交给 _REARM_STATES 的通用续圈分支（剩余时长 > 0 →
    ``sprite.restart_clip()``），而 ACTS 计划本就只有一圈——clip 已按真实时间
    播完，再 start() 就把整个动作从第 0 帧重播一遍（视觉闪回 + 白付一次解码）。
    """
    lib, sprite, c, clip, feed, runner, st = _finished_before_duration()
    assert clip.currentFrameNumber() > 0             # 本圈确实交付过帧
    assert st.duration - st.elapsed > 0.0            # 与 DRAG/MOVE 续圈同条件

    clip.finish()                                    # 单圈已播完（结束标记）

    assert clip.start_count == 1                     # ← 旧代码在这里 = 2（RED）

    # 到点开窗后 1 个 tick 内收口：finished 已提供「末帧显示过一个帧间隔」证据，
    # 不消耗 0.35s 宽限
    window_tick = None
    while c.state_of(sprite) == STATE_ACTS and runner.ticks < 3000:
        runner.step()
        if window_tick is None and st.acts_tail_grace is not None:
            window_tick = runner.ticks
    assert c.state_of(sprite) == STATE_IDLE
    assert window_tick is not None, "到点后未开启末帧等待窗口（状态机挂起）"
    assert runner.ticks - window_tick <= 1
    assert clip.start_count == 1                     # 全程不重播


def test_acts_premature_finished_without_any_frame_still_collects():
    """finished 提前到且本圈一帧都没交付（异常 clip）：不重播，也不挂起。"""
    lib, sprite, c, clip, feed, runner = _scene(lag=0.0, frames=241)
    st = c._states[sprite]
    feed.last_frame = -1                             # 一帧都不交付
    feed.emit_finish = False
    st.elapsed = st.duration - 0.05
    ticks_before = runner.ticks

    clip.finish()

    assert clip.start_count == 1                     # 不重播（旧代码 = 2）
    assert clip.currentFrameNumber() == 0            # 确实没有任何帧证据
    runner.run_until(lambda: c.state_of(sprite) != STATE_ACTS, limit=200)
    assert c.state_of(sprite) == STATE_IDLE          # 到点即收口
    assert runner.ticks - ticks_before <= 6          # 0.05s + 1 tick，不等宽限


def test_acts_premature_finished_evidence_cleared_on_interrupt():
    """提前到达的 finished 证据属于本次绑定：打断即清零，绝不留下迟到收口。"""
    lib, sprite, c, clip, feed, runner, st = _finished_before_duration()
    clip.finish()
    assert clip.start_count == 1
    assert st.acts_clip_finished is True             # 证据已登记（待 tick 收口）

    assert c.on_sprite_clicked(sprite) is True       # 点击打断

    assert st.acts_clip_finished is False            # 证据随绑定作废
    assert st.acts_tail_grace is None
    runner.run_until(lambda: c.state_of(sprite) != STATE_CLICK)
    assert c.state_of(sprite) == STATE_IDLE          # 点击链照常收口
    for _ in range(40):                              # 越过原到点/宽限窗口
        runner.step()
    assert c.state_of(sprite) == STATE_IDLE
    assert clip.start_count == 1                     # act clip 未被重播


def test_drag_finished_still_rearms_when_time_remains():
    """范围守卫：剩余时长 > 0 时 DRAG 照旧原地续圈（ACTS 的改动不外溢）。

    MOVE / THROWN 的续圈语义由 test_sprite_behavior.py 的
    ``test_multi_loop_move_finished_rearms_clip`` /
    ``test_thrown_binds_drag_clip_and_landing_returns_idle`` 守住（同一批次跑）。
    """
    lib = FakeLibrary(idles=["idle1"], turns=["turn1"], moves=[], clicks=["click1"],
                      acts=[ACT], act_frames=241, drag="hang")
    sprite = PetSprite(lib, pos=QPointF(800, 400), facing="left", scale=0.5)
    c = BehaviorController(BOUNDS, rng=ScriptedRng(rolls=(0.99,)))
    c.predict_enabled = False
    c.tick([sprite], TICK_S)

    sprite.on_press(QPointF(10, 10))                 # overlay 接线点：按下
    sprite.begin_drag()                              # 过阈值升级（真拖拽序列）
    c.on_drag_started(sprite)
    st = c._states[sprite]
    assert c.state_of(sprite) == STATE_DRAG
    assert st.duration - st.elapsed > 0.0            # 圈内（剩余时长 > 0）

    lib.clip("hang").finished.emit()                 # 悬空动画过圈末

    assert lib.clip("hang").start_count == 2         # 续圈：既有语义原样保留
