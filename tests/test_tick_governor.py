# -*- coding: utf-8 -*-
"""M-1 TickGovernor 闲置降档回归（QT_QPA_PLATFORM=offscreen 可跑）。

覆盖（REVIEW_VERDICT.md M-1）：
- 档位语义：运动/近期活动 ⇒ T0；仅动画 ⇒ T1；全静 ⇒ T2；不可见 ⇒ T3；
- 升档同步立即、降档 800ms 滞回（连续静默才降）；
- overlay 集成：静默后 QTimer 间隔/TimerType 随档切换；set_velocity 与
  位移经 sprite 回调同步唤醒回 T0；切档后首 tick 不吃历史流逝；
- T3 心跳不跑仿真（仿真段钩子不被调用）。

注（M-2）：档位状态机随 tick 时钟迁到统一驱动器
（``pet.tick_driver.TickDriver``），集成断言的挂点从 overlay 内部属性面
平移到 ``overlay.tick_driver``——语义逐条等价。

纪律（AGENTS.md 时序测试）：governor 用注入假钟；驱动器侧同步直调
_on_tick/_sync_tier，不启动真实 QTimer、不固定 sleep。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet.overlay_window import OverlayWindow
from pet.pet_sprite import PetSprite
from pet.tick_governor import (
    ACTIVE_HOLD_MS,
    DOWNGRADE_HOLD_MS,
    TIER_ACTIVE,
    TIER_IDLE_ANIM,
    TIER_IDLE_STILL,
    TIER_INTERVAL_MS,
    TIER_OCCLUDED,
    TickGovernor,
)

app = QApplication.instance() or QApplication([])


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------- governor 单元
def test_motion_forces_active():
    g = TickGovernor()
    assert g.evaluate(any_motion=True, animating=False, visible=True) == TIER_ACTIVE


def test_idle_ladder_with_downgrade_hold():
    clock = FakeClock()
    g = TickGovernor(clock=clock)
    # 起步 T0；无运动无动画 → 目标 T2，但滞回期内不降
    assert g.evaluate(any_motion=False, animating=False, visible=True) == TIER_ACTIVE
    clock.advance(DOWNGRADE_HOLD_MS / 1000.0 - 0.1)
    assert g.evaluate(any_motion=False, animating=False, visible=True) == TIER_ACTIVE
    clock.advance(0.2)
    assert g.evaluate(any_motion=False, animating=False, visible=True) == TIER_IDLE_STILL


def test_animating_holds_tier1():
    clock = FakeClock()
    g = TickGovernor(clock=clock)
    g.evaluate(any_motion=False, animating=True, visible=True)   # 启动静默计时
    clock.advance(DOWNGRADE_HOLD_MS / 1000.0 + 0.1)
    assert g.evaluate(any_motion=False, animating=True, visible=True) == TIER_IDLE_ANIM


def test_upgrade_is_immediate():
    clock = FakeClock()
    g = TickGovernor(clock=clock)
    clock.advance(10.0)
    g.evaluate(any_motion=False, animating=False, visible=True)   # 启动静默计时
    clock.advance(DOWNGRADE_HOLD_MS / 1000.0 + 0.1)
    assert g.evaluate(any_motion=False, animating=False, visible=True) == TIER_IDLE_STILL
    g.notify_kinetic()     # 一次运动信号
    assert g.evaluate(any_motion=False, animating=False, visible=True) == TIER_ACTIVE


def test_kinetic_tail_holds_active():
    clock = FakeClock()
    g = TickGovernor(clock=clock)
    g.notify_kinetic()
    clock.advance(ACTIVE_HOLD_MS / 1000.0 - 0.05)
    assert g.evaluate(any_motion=False, animating=False, visible=True) == TIER_ACTIVE
    clock.advance(0.1)
    g.evaluate(any_motion=False, animating=False, visible=True)   # 启动静默计时
    clock.advance(DOWNGRADE_HOLD_MS / 1000.0 + 0.1)
    assert g.evaluate(any_motion=False, animating=False, visible=True) == TIER_IDLE_STILL


def test_occluded_when_hidden():
    clock = FakeClock()
    g = TickGovernor(clock=clock)
    g.evaluate(any_motion=False, animating=False, visible=False)  # 启动静默计时
    clock.advance(DOWNGRADE_HOLD_MS / 1000.0 + 0.1)
    assert g.evaluate(any_motion=False, animating=False, visible=False) == TIER_OCCLUDED


def test_downgrade_hold_resets_on_activity():
    clock = FakeClock()
    g = TickGovernor(clock=clock)
    g.evaluate(any_motion=False, animating=False, visible=True)
    clock.advance(0.5)  # 滞回期过半
    g.notify_kinetic()  # 活动打断
    g.evaluate(any_motion=False, animating=False, visible=True)
    clock.advance(0.5)  # 重新计时不足 800ms
    assert g.evaluate(any_motion=False, animating=False, visible=True) == TIER_ACTIVE


# ---------------------------------------------------------------- 驱动器集成（M-2 拆分的挂点）
class FakeClip(QObject):
    frameChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.frame = 0
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(Qt.GlobalColor.transparent)

    def currentFrameNumber(self):
        return self.frame

    def currentImage(self):
        return self.image

    def frameCount(self):
        return 1

    def start(self):
        return True

    def stop(self):
        pass


class FakeLibrary:
    def __init__(self, clip):
        self._clip = clip
        self.no_mirror: set[str] = set()

    def movie(self, name, **_kw):
        return self._clip


def _make_overlay_with_sprite():
    overlay = OverlayWindow()
    sprite = PetSprite(FakeLibrary(FakeClip()), pos=QPointF(100, 100), scale=0.5)
    sprite.bind_clip("idle")
    overlay.add_sprite(sprite)
    return overlay, sprite


def _quiesce_to_tier(driver, clock, tier):
    """把驱动器静默推进到目标档：先越过活动尾巴 → 启动静默计时 →
    越过降档滞回期 → 再评估。"""
    clock.advance(ACTIVE_HOLD_MS / 1000.0 + 0.1)
    driver._sync_tier()
    clock.advance(DOWNGRADE_HOLD_MS / 1000.0 + 0.1)
    driver._sync_tier()
    assert driver.applied_tier == tier


def test_driver_downshifts_after_quiet(monkeypatch):
    """M-1 档位应用到 QTimer：静默后间隔/TimerType 随档切换。

    M-2 后档位状态机随 tick 时钟迁到统一驱动器（overlay.tick_driver），
    断言点从 overlay 内部属性面平移到驱动器（语义等价）。
    """
    overlay, _sprite = _make_overlay_with_sprite()
    driver = overlay.tick_driver
    # 注入假钟：governor 时间快进越过滞回期；offscreen 未 show → 补可见
    clock = FakeClock()
    monkeypatch.setattr(driver._governor, "_clock", clock)
    monkeypatch.setattr(overlay, "isVisible", lambda: True)
    driver._last_frame_notify = None      # 无动画活性 → 目标 T2
    driver._governor.notify_kinetic()
    driver._sync_tier()
    assert driver.applied_tier == TIER_ACTIVE
    assert driver.timer.timerType() == Qt.TimerType.PreciseTimer

    _quiesce_to_tier(driver, clock, TIER_IDLE_STILL)
    assert driver.timer.interval() == TIER_INTERVAL_MS[TIER_IDLE_STILL]
    assert driver.timer.timerType() == Qt.TimerType.CoarseTimer


def test_overlay_velocity_write_wakes_immediately(monkeypatch):
    overlay, sprite = _make_overlay_with_sprite()
    driver = overlay.tick_driver
    clock = FakeClock()
    monkeypatch.setattr(driver._governor, "_clock", clock)
    monkeypatch.setattr(overlay, "isVisible", lambda: True)
    driver._last_frame_notify = None
    _quiesce_to_tier(driver, clock, TIER_IDLE_STILL)

    sprite.set_velocity(QPointF(120, 0))  # 行为掷骰起步 → 同步回 T0

    assert driver.applied_tier == TIER_ACTIVE
    assert driver.timer.timerType() == Qt.TimerType.PreciseTimer


def test_overlay_set_pos_wakes_immediately(monkeypatch):
    overlay, sprite = _make_overlay_with_sprite()
    driver = overlay.tick_driver
    clock = FakeClock()
    monkeypatch.setattr(driver._governor, "_clock", clock)
    monkeypatch.setattr(overlay, "isVisible", lambda: True)
    driver._last_frame_notify = None
    _quiesce_to_tier(driver, clock, TIER_IDLE_STILL)

    sprite.set_pos(sprite.pos + QPointF(30, 0))  # 物理/拖拽位移 → 同步回 T0

    assert driver.applied_tier == TIER_ACTIVE


def test_occluded_heartbeat_skips_simulation(monkeypatch):
    overlay, _sprite = _make_overlay_with_sprite()
    driver = overlay.tick_driver
    clock = FakeClock()
    monkeypatch.setattr(driver._governor, "_clock", clock)
    driver._last_frame_notify = None
    monkeypatch.setattr(overlay, "isVisible", lambda: False)
    _quiesce_to_tier(driver, clock, TIER_OCCLUDED)  # 遮挡经滞回期降档

    calls = []
    overlay.before_sprites_advance = lambda dt: calls.append(dt)
    overlay._on_tick()
    assert calls == []  # T3 心跳不跑仿真
