# -*- coding: utf-8 -*-
"""批 D 彩蛋：边缘探头状态被击飞时飞行整帧旋转跟随速度方向。

覆盖：arm 条件双向（碰撞取消才 arm）、角度四方向、低速碰边界恢复、
低速碰桌宠恢复、高速不恢复、_stop_physics 兜底、end 幂等与重 arm；
批 E 补：恢复阈值 240、飞行 8 秒硬上限兜底（速度降不下来也不卡死）。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QRect
from PySide6.QtWidgets import QApplication

from pet.edge_probe import PEEKING, EdgeProbeController
from pet.throw_egg import (
    THROW_EGG_MAX_FLIGHT_SECONDS,
    THROW_EGG_RECOVER_SPEED,
    ThrowEggController,
)


def _qapp():
    return QApplication.instance() or QApplication([])


class _Avail:
    def availableGeometry(self):
        return QRect(0, 0, 1000, 800)


class _FakeCfg:
    def get(self, key, default=None):
        return default


class _FakeWin(QObject):
    """最小窗口替身：只提供控制器需要的公开/半内部方法与 _throw_egg 装配槽位。"""

    def __init__(self):
        super().__init__()
        self._x = 0
        self._y = 100
        self._w = 400
        self._h = 300
        self._updates = 0
        self.cfg = _FakeCfg()
        self.anim = "idle"
        self.idles = ["idle"]
        self.turns = ["turn"]
        self._throw_egg = None

    def x(self):
        return self._x

    def y(self):
        return self._y

    def move(self, x, y):
        self._x = int(x)
        self._y = int(y)

    def update(self):
        self._updates += 1

    def screen_available(self, *_a):
        return _Avail()

    def character_local_region(self):
        return QRect(100, 0, 200, 200)

    def frameGeometry(self):
        return QRect(self._x, self._y, self._w, self._h)

    def _frame_draw_rect(self):
        return QRect(0, 0, self._w, self._h)

    def _switch(self, name):
        self.anim = name
        return True

    def _pick(self, pool):
        return pool[0]

    def _cancel_move(self):
        pass

    def _stop_physics(self):
        pass


def test_arm_activates_and_zeroes_angle():
    win = _FakeWin()
    egg = ThrowEggController(win)
    assert not egg.active
    egg.arm()
    assert egg.active
    assert egg.current_angle_deg() == 0.0
    assert win._updates > 0


def test_angle_follows_four_directions():
    """屏幕坐标 y 朝下：向右=90°、向下=180°、向上=0°、向左=270°。"""
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    egg.update(300.0, 0.0, False)
    assert egg.current_angle_deg() == pytest.approx(90.0)
    egg.update(0.0, 300.0, False)
    assert egg.current_angle_deg() == pytest.approx(180.0)
    egg.update(0.0, -300.0, False)
    assert egg.current_angle_deg() == pytest.approx(0.0)
    egg.update(-300.0, 0.0, False)
    assert egg.current_angle_deg() == pytest.approx(270.0)


def test_angle_debounces_below_speed_threshold():
    """速度低于阈值且未碰边界：保持当前角不更新（防抖）。"""
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    egg.update(300.0, 0.0, False)
    before = egg.current_angle_deg()
    egg.update(30.0, 0.0, False)
    assert egg.active
    assert egg.current_angle_deg() == before


def test_low_speed_touching_boundary_recovers():
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    # 高速贴边：不恢复，角度仍更新。
    egg.update(300.0, 0.0, True)
    assert egg.active
    assert egg.current_angle_deg() == pytest.approx(90.0)
    # 低速贴边：恢复正常姿态。
    egg.update(60.0, 0.0, True)
    assert not egg.active
    assert egg.current_angle_deg() == 0.0


def test_low_speed_pet_contact_recovers():
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    egg.on_pet_contact(60.0)
    assert not egg.active
    assert egg.current_angle_deg() == 0.0


def test_high_speed_contact_does_not_recover():
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    egg.on_pet_contact(300.0)
    assert egg.active
    egg.update(300.0, 0.0, True)
    assert egg.active
    assert egg.current_angle_deg() == pytest.approx(90.0)


def test_recover_speed_threshold_raised_to_240():
    """批 E：阈值 120→240，多只桌宠互撞时速度不再长期卡在阈值之上。"""
    assert THROW_EGG_RECOVER_SPEED == 240.0


def test_speed_above_old_threshold_recovers_at_boundary():
    """批 E 回归：200 px/s 贴边在旧阈值(120)下会卡住，新阈值(240)下应恢复。"""
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    egg.update(200.0, 0.0, True)
    assert not egg.active
    assert egg.current_angle_deg() == 0.0


def test_threshold_boundary_recovery_only_below_240():
    """阈值边界：241 px/s 贴边仍激活，239 px/s 贴边恢复。"""
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    egg.update(241.0, 0.0, True)
    assert egg.active
    egg.update(239.0, 0.0, True)
    assert not egg.active


def test_max_flight_seconds_backstop_ends_high_speed_flight(monkeypatch):
    """批 E：飞行超过 8 秒无条件 end（速度降不下来也不卡死），与是否贴边无关。"""
    import pet.throw_egg as throw_egg_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(throw_egg_mod.time, "monotonic", lambda: clock["t"])
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    clock["t"] += THROW_EGG_MAX_FLIGHT_SECONDS - 0.1
    egg.update(1000.0, 0.0, False)
    assert egg.active, "未到 8 秒硬上限：高速飞行应继续激活"
    clock["t"] += 0.2
    egg.update(1000.0, 0.0, False)
    assert not egg.active, "超过 8 秒硬上限：无条件恢复正常姿态"
    assert egg.current_angle_deg() == 0.0


def test_max_flight_timer_restarts_on_rearm(monkeypatch):
    """硬上限起点在 arm() 时记录：end 后重新 arm 重新计时。"""
    import pet.throw_egg as throw_egg_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(throw_egg_mod.time, "monotonic", lambda: clock["t"])
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    clock["t"] += THROW_EGG_MAX_FLIGHT_SECONDS + 1.0
    egg.update(1000.0, 0.0, False)
    assert not egg.active
    egg.arm()
    clock["t"] += 0.5
    egg.update(1000.0, 0.0, False)
    assert egg.active


def test_end_idempotent_and_rearm():
    win = _FakeWin()
    egg = ThrowEggController(win)
    egg.arm()
    egg.end()
    assert not egg.active
    assert egg.current_angle_deg() == 0.0
    # 幂等：再次 end 不报错、状态不变。
    egg.end()
    assert not egg.active
    assert egg.current_angle_deg() == 0.0
    # 结束后可重新 arm。
    egg.arm()
    assert egg.active


def test_update_on_inactive_is_noop():
    win = _FakeWin()
    updates_before = win._updates
    egg = ThrowEggController(win)
    egg.update(200.0, 0.0, False)
    egg.on_pet_contact(30.0)
    assert not egg.active
    assert win._updates == updates_before


def test_arm_wired_into_collision_throw_cancel():
    """arm 条件正向：探头激活被撞取消 → 彩蛋进入激活。"""
    _qapp()
    win = _FakeWin()
    egg = ThrowEggController(win)
    win._throw_egg = egg
    probe = EdgeProbeController(win)
    probe.enabled = True
    probe._mode = PEEKING  # 模拟探头处于激活会话
    probe.cancel("collision_throw", restore=False)
    assert egg.active
    assert probe._reentry_armed


def test_non_collision_cancel_does_not_arm():
    """arm 条件反向：非碰撞取消（如拖离）不激活彩蛋。"""
    _qapp()
    win = _FakeWin()
    egg = ThrowEggController(win)
    win._throw_egg = egg
    probe = EdgeProbeController(win)
    probe.enabled = True
    probe._mode = PEEKING
    probe.cancel("drag_away", restore=False)
    assert not egg.active
    assert not probe._reentry_armed


def test_stop_physics_backstop_ends_egg(tmp_path):
    """落地停稳兜底：window._stop_physics 无条件调用 throw_egg.end()。"""
    from tests.test_collision_window import FakeCollisionSession, FakeLibrary

    from pet.config import Config
    from pet.window import PetWindow

    app = _qapp()
    cfg = Config(str(tmp_path / "cfg.json"))
    cfg.set("collision_enabled", False)
    session = FakeCollisionSession("pet_egg_stop")
    win = PetWindow(FakeLibrary(), cfg, collision_session=session)
    win.resize(100, 100)
    win.show()
    app.processEvents()

    egg = win._throw_egg
    assert not egg.active
    egg.arm()
    assert egg.active
    win._stop_physics()
    assert not egg.active
    assert egg.current_angle_deg() == 0.0
    win.close()
