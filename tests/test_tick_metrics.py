# -*- coding: utf-8 -*-
"""子项3 回归：TickDriver tick 仪表（归因"偶发卡顿"）。

验收四条：
1. 档位转换记 INFO（旧档→新档 + 触发因）；电源切换单独一行 INFO；
2. 每 60s 一行 tick 间隔分位（p50/p99/max + 当前档 + 标称间隔），rolling 窗口；
3. 单次 tick_sim+advance 超 50ms 记 WARNING（含耗时与档）；
4. ``PET_TICK_METRICS=0`` 全关（开关经模块级 ``TICK_METRICS_ENABLED`` 打桩）；
   热路径零分配：间隔样本写预分配 array 环形缓冲，写入不换对象、不增长。

纪律：时钟注入假钟（不打真 QTimer、不 sleep 赌分位边界）；唯一真实等待是
慢 tick 用例里 >阈值的 ``time.sleep``（保证必然超，不会不及）。
"""
from __future__ import annotations

import logging
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import pet.tick_driver as tick_driver
from pet.tick_driver import (
    TICK_METRICS_REPORT_S,
    TICK_SLOW_MS,
    TickDriver,
    TickMetrics,
)
from pet.tick_governor import TIER_ACTIVE, TIER_IDLE_ANIM, TIER_IDLE_STILL
from tests.test_tick_driver import FakeOverlay, FakeScreen

app = QApplication.instance() or QApplication([])


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class SlowController:
    """单次 tick 固定耗掉 >TICK_SLOW_MS 的控制器（慢帧告警触发源）。"""

    def __init__(self, seconds: float = (TICK_SLOW_MS + 15.0) / 1000.0) -> None:
        self._seconds = seconds

    def tick(self, sprites, dt) -> None:
        time.sleep(self._seconds)


# ---------------------------------------------------------------- TickMetrics 单元
def test_report_line_has_percentiles_tier_and_period(caplog):
    """60s 一行：p50/p99/max + 当前档 + 标称；同一周期不重复打。"""
    clock = FakeClock()
    metrics = TickMetrics(clock=clock, window=8)
    for ms in (1.0, 2.0, 3.0, 4.0, 100.0):
        metrics.note_interval(ms)

    with caplog.at_level(logging.INFO, logger="pet.tick_driver"):
        metrics.maybe_report(TIER_IDLE_ANIM, 42)
        assert caplog.text == ""              # 未到周期：不打

        clock.advance(TICK_METRICS_REPORT_S)
        metrics.maybe_report(TIER_IDLE_ANIM, 42)
        assert "tick 间隔 p50=" in caplog.text
        assert "p99=" in caplog.text and "max=100.0ms" in caplog.text
        assert "tier=T1" in caplog.text and "标称=42ms" in caplog.text
        assert "n=5" in caplog.text

        caplog.clear()
        metrics.maybe_report(TIER_IDLE_ANIM, 42)
        assert caplog.text == ""              # 周期内只打一行


def test_interval_ring_is_preallocated_and_reused():
    """零分配热路径证据：定长 array 缓冲，写入不换对象、不增长。"""
    metrics = TickMetrics(clock=FakeClock(), window=4)
    buf = metrics._buf
    for i in range(20):
        metrics.note_interval(float(i))

    assert metrics._buf is buf                # 同一对象（无重建/无拷贝）
    assert len(metrics._buf) == 4             # 定长（rolling 覆盖）
    assert metrics._filled == 4
    assert list(metrics._buf) == [16.0, 17.0, 18.0, 19.0]


def test_slow_tick_warning_contains_cost_and_tier(caplog):
    metrics = TickMetrics(clock=FakeClock())
    with caplog.at_level(logging.WARNING, logger="pet.tick_driver"):
        metrics.note_slow_tick(TICK_SLOW_MS, TIER_ACTIVE)   # 未超：静默
        assert caplog.text == ""
        metrics.note_slow_tick(TICK_SLOW_MS + 25.5, TIER_IDLE_ANIM)

    assert "tick 慢帧" in caplog.text
    assert f"{TICK_SLOW_MS + 25.5:.1f}ms" in caplog.text     # 耗时
    assert "tier=T1" in caplog.text                          # 档位


# ---------------------------------------------------------------- 驱动器集成
def test_tier_reason_maps_governor_branches():
    """触发因与 governor 判定分支一一对应（日志归因的正确性前提）。"""
    driver = TickDriver(clock=FakeClock())    # 未 notify_kinetic：无活动尾巴
    assert driver._tier_reason(any_motion=True, animating=True, visible=True) == "motion"
    assert driver._tier_reason(any_motion=False, animating=True, visible=True) == "animating"
    assert driver._tier_reason(any_motion=False, animating=False, visible=True) == "quiet"
    assert driver._tier_reason(any_motion=False, animating=False, visible=False) == "hidden"

    driver._governor.notify_kinetic()         # 假钟未推进 → 活动尾巴仍内
    assert driver._tier_reason(any_motion=False, animating=False, visible=True) == "kinetic_tail"


def test_driver_logs_tier_transition_with_reason(caplog):
    driver = TickDriver(clock=FakeClock())
    driver.attach(FakeOverlay(screen=FakeScreen(170.0)))

    with caplog.at_level(logging.INFO, logger="pet.tick_driver"):
        driver._apply_tier(TIER_IDLE_STILL, reason="quiet")

    assert "tick 档位 T0→T2 触发因=quiet" in caplog.text
    assert "interval=250ms" in caplog.text


def test_driver_warns_on_slow_tick(caplog):
    """真控制器耗 65ms > 50ms：WARNING 记耗时与当时档位。"""
    driver = TickDriver(clock=FakeClock())
    driver.attach(FakeOverlay(screen=FakeScreen(170.0)))
    driver.set_controllers(behavior=SlowController())

    with caplog.at_level(logging.WARNING, logger="pet.tick_driver"):
        driver.on_tick(dt=0.006)

    assert "tick 慢帧" in caplog.text
    assert "tier=T0" in caplog.text


def test_driver_emits_interval_report_every_60s(monkeypatch, caplog):
    from PySide6.QtGui import QGuiApplication

    fast = FakeScreen(170.0)
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [fast]))
    clock = FakeClock()
    driver = TickDriver(clock=clock)
    driver.attach(FakeOverlay(screen=fast))

    with caplog.at_level(logging.INFO, logger="pet.tick_driver"):
        driver.on_tick(dt=0.006)              # 首个样本 = 基线（无间隔）
        clock.advance(0.006)
        driver.on_tick(dt=0.006)              # 记一个 6ms 样本
        clock.advance(TICK_METRICS_REPORT_S + 1.0)
        driver.on_tick(dt=0.006)              # 越过周期边界 → 打一行

    assert "tick 间隔 p50=" in caplog.text
    assert "tier=T0" in caplog.text
    assert "标称=6ms" in caplog.text          # 170Hz 屏的 V-6 间隔


def test_metrics_switch_off_silences_everything(monkeypatch, caplog):
    """PET_TICK_METRICS=0：不建仪表实例、慢帧/档位迁移/分位一行都不打。"""
    monkeypatch.setattr(tick_driver, "TICK_METRICS_ENABLED", False)
    clock = FakeClock()
    driver = TickDriver(clock=clock)
    assert driver.metrics is None             # 关闭时零实例（零分配）

    driver.attach(FakeOverlay(screen=FakeScreen(170.0)))
    driver.set_controllers(behavior=SlowController())
    with caplog.at_level(logging.DEBUG, logger="pet.tick_driver"):
        driver.on_tick(dt=0.006)
        clock.advance(TICK_METRICS_REPORT_S + 1.0)
        driver.on_tick(dt=0.006)
        driver._apply_tier(TIER_IDLE_STILL, reason="quiet")

    assert caplog.text == ""
