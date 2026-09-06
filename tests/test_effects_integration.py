# -*- coding: utf-8 -*-
"""黄金回旋/边缘探头在右键菜单与设置页中的集成测试。"""
from __future__ import annotations

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMenu

from pet.config import Config
from pet.context_menu import populate_context_menu
from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow
from pet.window_optional_services import WindowFeatureGateMixin


def _qapp():
    return QApplication.instance() or QApplication([])


class _Config:
    def __init__(self):
        self.values = {
            "context_menu_template": "modern",
            "context_menu_layout": None,
            "context_menu_appearance": {"theme": "light"},
            "character": "shenshen",
            "on_top": True,
            "edge_probe_enabled": False,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        return None


class _Pet:
    def __init__(self, template="modern"):
        self.cfg = _Config()
        self.cfg.values["context_menu_template"] = template
        self.on_open_chat = lambda self: None
        self.on_look_screen = lambda self: None
        self.on_show_balance = lambda self, parent=None: None
        self.on_check_update = lambda self: None
        self.on_open_modern_settings = lambda self: None
        self.on_spawn_pet = lambda self: None
        self.idles = ["idle"]
        self.turns = moves = clicks = acts = []
        self.playback_speed = self.scale = 1.0
        self.drag_physics = self.no_move = self.mouse_through = False
        self.golden_spins = 0
        self.edge_toggles = []

    def icon_pixmap(self, size=64):
        pixmap = QPixmap(size, size)
        pixmap.fill()
        return pixmap

    def trigger_golden_spin(self):
        self.golden_spins += 1

    def set_edge_probe_enabled(self, enabled):
        self.edge_toggles.append(bool(enabled))
        self.cfg.values["edge_probe_enabled"] = bool(enabled)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _find_action(menu, text):
    return next(action for action in menu.actions() if action.text() == text)


def test_modern_menu_has_golden_spin_and_edge_probe_in_pet_controls():
    app = _qapp()
    menu = QMenu()
    pet = _Pet(template="modern")
    populate_context_menu(menu, pet)
    controls = next(action.menu() for action in menu.actions() if action.text() == "桌宠控制")
    golden = _find_action(controls, "黄金回旋")
    edge = _find_action(controls, "边缘探头")
    assert edge.isCheckable()
    assert not edge.isChecked()
    golden.trigger()
    assert pet.golden_spins == 1
    edge.setChecked(True)
    assert pet.edge_toggles == [True]
    menu.close()
    app.processEvents()


def test_legacy_menu_has_golden_spin_and_edge_probe():
    app = _qapp()
    menu = QMenu()
    pet = _Pet(template="legacy")
    populate_context_menu(menu, pet)
    assert _find_action(menu, "黄金回旋") is not None
    edge = _find_action(menu, "边缘探头")
    assert edge.isCheckable()
    assert not edge.isChecked()
    edge.setChecked(True)
    assert pet.edge_toggles == [True]
    menu.close()
    app.processEvents()


def test_settings_dialog_has_effect_toggles_and_writes_config(tmp_path):
    app = _qapp()
    cfg = Config(tmp_path)
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    assert dialog.golden_spin_click_check is not None
    assert dialog.golden_spin_direct_check is not None
    assert dialog.edge_probe_check is not None
    direct_row = dialog.findChild(SettingRow, "settingRow_golden_spin_direct")
    assert direct_row is not None
    # 子开关只在“点击触发黄金回旋”开启后显示。
    assert dialog.golden_spin_click_check.isChecked() is False
    assert direct_row.isHidden() is True
    dialog.golden_spin_click_check.setChecked(True)
    assert direct_row.isHidden() is False
    dialog.golden_spin_direct_check.setChecked(True)
    dialog.edge_probe_check.setChecked(True)
    assert dialog._write_config() is True
    assert cfg.get("golden_spin_on_click") is True
    assert cfg.get("golden_spin_direct") is True
    assert cfg.get("edge_probe_enabled") is True
    dialog.reject()
    app.processEvents()


class _ActiveProbe:
    active = True
    clicked = 0

    def on_clicked(self):
        self.clicked += 1


class _FilterHost(WindowFeatureGateMixin):
    idles = ["idle"]
    turns = ["turn"]

    def __init__(self):
        self._edge_probe = _ActiveProbe()
        self._golden_spin = None

    def _pick(self, pool):
        return pool[0]


def test_effect_filter_switch_restricts_probe_session_to_idle_turn():
    from pet.window_optional_services import WindowFeatureGateMixin

    host = _FilterHost()
    assert host._effects_filter_switch("turn") == "turn"
    assert host._effects_filter_switch("idle") == "idle"
    assert host._effects_filter_switch("click") == "idle"
    assert host._effects_consume_click() is True
    assert host._edge_probe.clicked == 1
    assert host._effects_probe_active() is True
    assert host._effects_skip_turn_facing() is True


def test_real_window_paint_with_active_rotation_does_not_raise(tmp_path):
    """回归：黄金回旋角度非零时 paintEvent/_sync_mask 的 begin/end 必须配对。

    之前 end_rotation 参数不匹配会在实际靠边/旋转时抛 TypeError，并留下未结束
    QPainter 导致 QPaintDevice 崩溃。这里直接让真实 PetWindow 带旋转角度 grab。
    """
    from tests.test_collision_window import FakeLibrary

    from pet.window import PetWindow

    app = _qapp()
    cfg = Config(tmp_path)
    cfg.set("auto_hide_fullscreen", False)
    cfg.set("collision_enabled", False)
    win = PetWindow(FakeLibrary(), cfg)
    try:
        spin = win._golden_spin
        spin._active = True
        spin._angle_deg = 35.0
        win.grab()  # 触发 paintEvent，若 begin/end 不配对这里会抛错/崩溃
        spin._active = False
        spin._angle_deg = 0.0
        win.grab()
    finally:
        win.close()
        app.processEvents()


def test_real_window_direct_golden_spin_on_click_accumulates(tmp_path):
    """点击直连模式：真实 PetWindow 点击不播动画，直接回旋并累计圈数。"""
    from tests.test_collision_window import FakeLibrary

    from pet.window import PetWindow

    app = _qapp()
    cfg = Config(tmp_path)
    cfg.set("auto_hide_fullscreen", False)
    cfg.set("collision_enabled", False)
    cfg.set("golden_spin_on_click", True)
    cfg.set("golden_spin_direct", True)
    win = PetWindow(FakeLibrary(), cfg)
    try:
        spin = win._golden_spin
        win._on_click()
        assert spin.active
        assert spin.queued_turns == 1
        # 旋转中再点一次：累计下一圈，不打断当前旋转。
        win._on_click()
        assert spin.active
        assert spin.queued_turns == 2
        # 真实 paint/mask 路径带旋转调用不应抛错（角度是否已推进取决于定时器调度）。
        win.grab()
        spin.cancel()
    finally:
        win.close()
        app.processEvents()
