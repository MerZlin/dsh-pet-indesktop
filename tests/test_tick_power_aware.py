# -*- coding: utf-8 -*-
"""子项4 回归：AC 电源下的闲置升档（T1 = T0 间隔满速）。

根因（实机 M-1 T1=42ms≈24fps）：用户 170Hz 屏上"碰撞/非碰撞帧数没上去"，
而用户硬指标是流畅度 > 负载；42ms 是电池预算下的降载，AC 供电时没有必要。

验收三条：
1. ``GetSystemPowerStatus`` 边界按 ACLineStatus 判 AC/电池（打桩，不依赖真实
   供电）；POSIX 恒 AC 且不触碰 Win32 API；读数异常按 AC；
2. 5s 缓存轮询：TTL 内只查一次，过期后重查；
3. 档位语义：AC 下 T1 间隔 = T0 间隔（``tick_interval_ms`` V-6 公式，170Hz→6ms）
   + PreciseTimer；电池下 T1 保持 42ms≈24fps + CoarseTimer；T0/T2/T3 不变；
   AC↔电池切换（档位不变）在下一次 tick 复评时把间隔切过来。

纪律：电源读数在 ``_windows_ac_line_status`` 边界打桩（真 API 只按平台分支
调用）；时钟注入假钟验证 5s 缓存，不 sleep。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pet import tick_governor
from pet.tick_driver import TickDriver
from pet.tick_governor import (
    POWER_CACHE_TTL_S,
    TIER_ACTIVE,
    TIER_IDLE_ANIM,
    TIER_IDLE_STILL,
    TIER_INTERVAL_MS,
    TIER_OCCLUDED,
    TickGovernor,
)
from tests.test_tick_driver import FakeOverlay, FakeScreen, FakeSprite

app = QApplication.instance() or QApplication([])


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _driver(monkeypatch, rr: float = 170.0):
    """170Hz 屏 + 一个静止可见 sprite 的驱动器（T1 判定输入齐备）。

    tick 刷新率取**屏枚举**最高一档（单窗覆盖整个虚拟桌面），故这台"机器"上把
    枚举换成被测的那块屏。
    """
    from PySide6.QtGui import QGuiApplication

    screen = FakeScreen(rr)
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [screen]))
    clock = FakeClock()
    driver = TickDriver(clock=clock)
    driver.attach(FakeOverlay([FakeSprite()], screen=screen))
    return driver, clock


# ---------------------------------------------------------------- 电源读数（GetSystemPowerStatus 打桩）
def test_ac_line_status_maps_to_power_state(monkeypatch):
    """1=AC / 0=电池 / 255=未知 / 查询失败(None) → 未知按 AC（不误降档）。"""
    monkeypatch.setattr(tick_governor, "IS_WINDOWS", True)
    for ac_line, expected in ((1, True), (0, False), (255, True), (None, True)):
        monkeypatch.setattr(
            tick_governor, "_windows_ac_line_status", lambda v=ac_line: v)
        assert tick_governor.read_power_status() is expected


def test_posix_is_always_ac_and_skips_win32_api(monkeypatch):
    monkeypatch.setattr(tick_governor, "IS_WINDOWS", False)

    def _boom():
        raise AssertionError("非 Windows 不得触碰 GetSystemPowerStatus")

    monkeypatch.setattr(tick_governor, "_windows_ac_line_status", _boom)
    assert tick_governor._read_system_power_status() is None
    assert tick_governor.read_power_status() is True


def test_probe_exception_counts_as_ac():
    def _boom():
        raise RuntimeError("电源读数炸了")

    governor = TickGovernor(clock=FakeClock(), power_probe=_boom)

    assert governor.on_ac_power() is True


def test_ac_state_cached_for_5s():
    clock = FakeClock()
    calls: list[int] = []

    def _probe():
        calls.append(1)
        return False

    governor = TickGovernor(clock=clock, power_probe=_probe)
    assert governor.on_ac_power() is False      # 电池
    assert governor.on_ac_power() is False
    assert len(calls) == 1                      # TTL 内只查一次

    clock.advance(POWER_CACHE_TTL_S - 0.1)
    governor.on_ac_power()
    assert len(calls) == 1

    clock.advance(0.2)
    governor.on_ac_power()
    assert len(calls) == 2                      # 过期后重查


# ---------------------------------------------------------------- 档位语义
def test_ac_t1_runs_at_t0_interval(monkeypatch):
    """AC：T1 间隔 = T0 间隔（V-6 公式 170Hz→6ms），PreciseTimer 满速。"""
    driver, _clock = _driver(monkeypatch, 170.0)
    driver._governor._power_probe = lambda: True

    driver._apply_tier(TIER_IDLE_ANIM)

    assert driver.applied_tier == TIER_IDLE_ANIM
    assert driver.timer.interval() == TickDriver.tick_interval_ms(170.0) == 6
    assert driver.timer.timerType() == Qt.TimerType.PreciseTimer


def test_battery_t1_keeps_m1_24fps_semantics(monkeypatch):
    """电池：T1 保持 42ms≈24fps + CoarseTimer（M-1 原语义不变）。"""
    driver, _clock = _driver(monkeypatch, 170.0)
    driver._governor._power_probe = lambda: False

    driver._apply_tier(TIER_IDLE_ANIM)

    assert driver.timer.interval() == TIER_INTERVAL_MS[TIER_IDLE_ANIM] == 42
    assert driver.timer.timerType() == Qt.TimerType.CoarseTimer


def test_ac_does_not_touch_t0_t2_t3(monkeypatch):
    """T0/T2/T3 间隔与旧语义逐条一致（H 电源只管 T1）。"""
    driver, _clock = _driver(monkeypatch, 170.0)
    driver._governor._power_probe = lambda: True

    driver._apply_tier(TIER_ACTIVE)
    assert driver.timer.interval() == TickDriver.tick_interval_ms(170.0)
    assert driver.timer.timerType() == Qt.TimerType.PreciseTimer

    driver._apply_tier(TIER_IDLE_STILL)
    assert driver.timer.interval() == TIER_INTERVAL_MS[TIER_IDLE_STILL]
    assert driver.timer.timerType() == Qt.TimerType.CoarseTimer

    driver._apply_tier(TIER_OCCLUDED)
    assert driver.timer.interval() == TIER_INTERVAL_MS[TIER_OCCLUDED]
    assert driver.timer.timerType() == Qt.TimerType.CoarseTimer


def test_v6_interval_formula_unchanged():
    """V-6 的 T0 间隔公式不动（170Hz→6ms、90Hz→11ms、<75→16ms）。"""
    assert TickDriver.tick_interval_ms(170.0) == 6
    assert TickDriver.tick_interval_ms(90.0) == 11
    assert TickDriver.tick_interval_ms(60.0) == 16
    assert TickDriver.tick_interval_ms(None) == 16


def test_power_flip_reapplies_t1_interval_on_next_tick(monkeypatch):
    """档位不变、电源翻面：下一次 tick 复评把 T1 间隔切到满速（≤5s 感知）。"""
    from pet.tick_governor import DOWNGRADE_HOLD_MS

    driver, clock = _driver(monkeypatch, 170.0)
    state = {"ac": False}
    driver._governor._power_probe = lambda: state["ac"]

    # 真实降档路径进 T1（电池）：静默计时 → 越过滞回 → 动画在播 ⇒ T1 = 42ms
    driver.note_frame()
    driver._sync_tier()
    clock.advance(DOWNGRADE_HOLD_MS / 1000.0 + 0.1)
    driver.note_frame()                      # "动画在播" → 目标档 T1
    driver._sync_tier()
    assert driver.applied_tier == TIER_IDLE_ANIM
    assert driver.timer.interval() == 42

    state["ac"] = True
    clock.advance(POWER_CACHE_TTL_S + 0.1)   # 越过电源缓存 TTL
    driver.note_frame()
    driver._sync_tier()

    assert driver.applied_tier == TIER_IDLE_ANIM   # 档位没变
    assert driver.timer.interval() == TickDriver.tick_interval_ms(170.0)
    assert driver.timer.timerType() == Qt.TimerType.PreciseTimer

    state["ac"] = False
    clock.advance(POWER_CACHE_TTL_S + 0.1)
    driver.note_frame()
    driver._sync_tier()                      # 拔电：回落 24fps 降载

    assert driver.applied_tier == TIER_IDLE_ANIM
    assert driver.timer.interval() == 42
    assert driver.timer.timerType() == Qt.TimerType.CoarseTimer


def test_battery_t1_low_budget_when_downgraded_by_quiet(monkeypatch):
    """降档滞回语义不变：电池下静默到期照旧降到 T2（250ms 心跳）。"""
    from pet.tick_governor import ACTIVE_HOLD_MS, DOWNGRADE_HOLD_MS

    driver, clock = _driver(monkeypatch, 170.0)
    driver._governor._power_probe = lambda: False
    driver._last_frame_notify = None
    driver._governor.notify_kinetic()

    clock.advance(ACTIVE_HOLD_MS / 1000.0 + 0.1)
    driver._sync_tier()
    clock.advance(DOWNGRADE_HOLD_MS / 1000.0 + 0.1)
    driver._sync_tier()

    assert driver.applied_tier == TIER_IDLE_STILL
    assert driver.timer.interval() == TIER_INTERVAL_MS[TIER_IDLE_STILL]
