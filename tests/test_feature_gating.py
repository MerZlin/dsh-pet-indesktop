# -*- coding: utf-8 -*-
"""Phase 1 开关式加载门控测试。

验证目标：可选功能关闭时不构造/启动对应服务对象；启用时才懒装配。
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.window import PetWindow


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _disabled_config(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("collision_enabled", False)
    cfg.set("decode_broker_enabled", True)  # 依赖碰撞通道，碰撞关时仍不应创建 broker
    cfg.set("todo_reminder_enabled", False)
    cfg.set("click_sound_enabled", False)
    island = dict(cfg.get("dynamic_island", {}))
    island["enabled"] = False
    cfg.set("dynamic_island", island)
    cfg.set("auto_hide_fullscreen", False)
    return cfg


def test_petapp_disabled_optional_services_not_constructed(tmp_path):
    from pet.app import PetApp

    app = _qapp()
    cfg = _disabled_config(tmp_path)
    owner = PetApp(app, cfg, enable_chat=False)
    assert owner.collision_ipc is None
    assert owner.broker_facade is None
    assert owner.todo_service is None


def test_petapp_enabled_default_services_still_constructed(tmp_path):
    """默认配置（碰撞/待办开）保持既有 PetApp 构造行为。"""
    from pet.app import PetApp

    app = _qapp()
    owner = PetApp(app, Config(tmp_path), enable_chat=False)
    assert owner.collision_ipc is not None
    assert owner.todo_service is not None
    # broker 默认关，不应凭空创建 facade。
    assert owner.broker_facade is None


def test_petapp_start_disabled_services_stay_stopped(tmp_path, monkeypatch):
    import pet.app as app_mod
    from pet.app import PetApp

    app = _qapp()
    monkeypatch.setattr(app_mod.QTimer, "singleShot", lambda *a, **k: None)
    cfg = _disabled_config(tmp_path)
    owner = PetApp(app, cfg, enable_chat=False)
    owner._create_ui = lambda cid: None
    owner._apply_spawn_offset = lambda: None
    owner._apply_balance_timer = lambda: None
    owner.start()
    assert owner.collision_ipc is None
    assert owner.todo_service is None


def test_petwindow_disabled_optional_services_not_constructed(tmp_path):
    from tests.test_collision_window import FakeLibrary

    app = _qapp()
    cfg = _disabled_config(tmp_path)
    win = PetWindow(FakeLibrary(), cfg)
    try:
        assert win.proactive_watcher is None
        assert win.agent_link_manager is None
    finally:
        win.close()
        app.processEvents()


def test_petwindow_lazy_ensure_creates_optional_services(tmp_path):
    from tests.test_collision_window import FakeLibrary

    app = _qapp()
    cfg = _disabled_config(tmp_path)
    win = PetWindow(FakeLibrary(), cfg)
    try:
        assert win.proactive_watcher is None
        assert win.agent_link_manager is None
        assert win._ensure_proactive_watcher() is not None
        assert win._ensure_agent_link_manager() is not None
    finally:
        win.close()
        app.processEvents()


def test_petwindow_proactive_toggle_creates_watcher(tmp_path, monkeypatch):
    from tests.test_collision_window import FakeLibrary

    app = _qapp()
    cfg = _disabled_config(tmp_path)
    win = PetWindow(FakeLibrary(), cfg)
    try:
        bubbles = []
        monkeypatch.setattr(win, "show_bubble", lambda text, duration_ms=3200: bubbles.append(text))
        assert win.proactive_watcher is None
        win._toggle_proactive_enabled(True)
        assert win.proactive_watcher is not None
    finally:
        win.close()
        app.processEvents()
