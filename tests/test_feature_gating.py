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
    from pet.app import AppShell

    app = _qapp()
    cfg = _disabled_config(tmp_path)
    shell = AppShell(app, cfg, enable_chat=False)
    # 本分支架构差异：碰撞会话为每窗自持（批5.2 P1-1，恒建），broker 已由
    # 进程内 fan-out hub 取代（批5.3）；上游断言的 collision/broker 门控不适用。
    # todo 服务走 Phase 1 懒门控（挂 AppShell 进程级）。
    assert shell.todo_service is None


def test_petapp_enabled_default_services_still_constructed(tmp_path):
    """默认配置（待办开）保持既有构造行为。"""
    from pet.app import AppShell

    app = _qapp()
    shell = AppShell(app, Config(tmp_path), enable_chat=False)
    assert shell.todo_service is not None
    # 碰撞会话每窗自持（恒建）；共享解码 hub 进程级（默认 enabled 取决于 flag）。
    assert shell.instance.collision_ipc is not None
    assert shell._decode_hub is not None


def test_petapp_start_disabled_services_stay_stopped(tmp_path, monkeypatch):
    import pet.app as app_mod
    from pet.app import AppShell

    app = _qapp()
    monkeypatch.setattr(app_mod.QTimer, "singleShot", lambda *a, **k: None)
    cfg = _disabled_config(tmp_path)
    shell = AppShell(app, cfg, enable_chat=False)
    shell.instance._create_ui = lambda cid: None
    shell.instance._apply_spawn_offset = lambda: None
    shell._apply_balance_timer = lambda: None
    shell.instance.collision_ipc = type("FakeCollision", (), {"start": lambda self: None})()
    shell.start()
    assert shell.todo_service is None


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


def test_petwindow_agent_link_enabled_at_startup_constructs_manager(tmp_path):
    """回归（#99）：agent_link 已开启时，启动路径必须创建 AgentLinkManager。

    此前 __init__ 收尾只调 _install_effect_services()，没人调
    sync_optional_services()/apply_config()——重启后已开启的 Agent 联动
    必须手工展开一次联动菜单或开关一次设置对话框才生效。
    """
    from tests.test_collision_window import FakeLibrary

    app = _qapp()
    cfg = _disabled_config(tmp_path)
    cfg.set("agent_link", {"dsh": True})
    win = PetWindow(FakeLibrary(), cfg)
    try:
        assert win.agent_link_manager is not None
    finally:
        mgr = win.agent_link_manager
        if mgr is not None:
            mgr.shutdown()
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
