# -*- coding: utf-8 -*-
"""批5.2a：子系统上移 + 托盘聚合（单进程多窗共享）的机器可验测试。

覆盖 DISPATCH_batch52a 验收 ①：
- agent_link 上移：flag 开时单 manager 扇出——两窗各收到呈现事件（全体跳舞）；
- 托盘聚合：flag 开时单托盘 + 每窗子菜单存在，动作路由正确（显示/隐藏、切换角色、退出这只）；
- 灵动岛单击 toggle **全部**窗（按聚合可见态同步 set_pet_visible）；
- flag 关逐位一致：共享子系统不实例化（每窗各自 manager，既有 spawn 测试族全绿）。

flag 关的逐位一致由既有 tests/test_single_process_spawn.py 族保证（本批不弱化、
只在 __init__ 注入 None → 每窗各自建）。
"""
from __future__ import annotations

import os
import uuid

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import pet.app as app_mod
import pet.slot_manager as slot_manager_mod
from pet import catalog
from pet.app import AppShell, PetInstance
from pet.config import Config
from pet.multi_window_shared import SharedProactiveWatcher


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _FakeLib:
    def pause_warm(self):
        pass

    def resume_warm(self):
        pass


class _RecordWin:
    """记录调用的多窗替身：记录呈现事件，供代理扇出断言。"""

    def __init__(self, visible=True):
        self.is_shown = visible
        self.bubbles: list[str] = []
        self.anims: list[str] = []
        self.activities = 0
        self.link_provider = None
        self.cfg = None
        self._single_process_spawn = True
        self._bubble_busy_until = 0.0
        # 非空 dict（代理以 `if c:` 判真）
        self.cats = {"idle": "idle", "acts": ["写代码"], "moves": [], "turns": []}
        self._dragging = False
        self._physics_mode = None
        self._click_effect_phase = 0
        self.mouse_through = False

    # ---- 代理/呈现接口 ----
    def set_link_next_provider(self, p):
        self.link_provider = p

    def show_bubble(self, text, duration_ms=4500):
        self.bubbles.append(text)

    def request_link_anim(self, name):
        self.anims.append(name)

    def request_link_idle(self):
        pass

    def mark_activity(self):
        self.activities += 1

    def clear_pending_link_anim(self):
        pass

    def hold_bubble(self, seconds):
        self._bubble_busy_until = seconds

    def on_look_synced(self, user_text, reply):
        pass

    def isVisible(self):
        return self.is_shown

    def hide(self, notify=True):
        self.is_shown = False

    def show(self):
        self.is_shown = True

    def deleteLater(self):
        pass

    # ---- 托盘构建所需 ----
    def icon_pixmap(self, _size=None):
        return QPixmap(2, 2)

    def set_mouse_through(self, on):
        pass

    def hide_speech_bubble(self):
        pass


class _FakeIsland:
    def __init__(self):
        self.pet_visible = None

    def set_pet_visible(self, visible):
        self.pet_visible = bool(visible)

    def hide(self):
        pass

    def show(self):
        pass

    def refresh_from_config(self):
        pass


def _make_flag_on_shell(tmp_path):
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", True)
    config.save()
    slot_id, slot_handle = slot_manager_mod.acquire_pet_slot(config.dir, preferred_slot=0)
    shell = AppShell(QApplication.instance(), config, enable_chat=True,
                     slot_handle=slot_handle, slot_id=slot_id)
    return shell, config, slot_handle


def _make_primary_record_win(shell, config):
    win = _RecordWin()
    win.cfg = config
    shell.instance.win = win
    return win


def _make_second_record_win(shell, tmp_path, monkeypatch):
    """经 spawn_in_process_window 生成第二实例（独立 slot/Config），窗口为 _RecordWin。"""
    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _RecordWin()
        win.cfg = self.config
        win._single_process_spawn = self.shell._single_process_spawn
        self.win = win
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)
    monkeypatch.setattr(app_mod.PetInstance, "_apply_spawn_offset", lambda self: None)
    second = shell.spawn_in_process_window(1)
    return second


def _stop_sessions(*insts):
    for inst in insts:
        try:
            inst.collision_ipc.stop()
        except Exception:
            pass


def test_flag_on_agent_link_single_manager_fans_out(tmp_path, app, monkeypatch):
    """§③.1 / 验收①：flag 开时同一份 AgentLinkManager 服务两窗，呈现扇出到两窗。"""
    shell, config, primary_handle = _make_flag_on_shell(tmp_path)
    try:
        primary_win = _make_primary_record_win(shell, config)
        second = _make_second_record_win(shell, tmp_path, monkeypatch)
        second_win = second.win

        # 一份共享 manager（对象身份一致）
        assert shell._shared is not None
        mgr = shell._shared.agent_link
        assert len(shell.instances) == 2

        # 触发一次联动气泡呈现 → 两窗都收到
        mgr.presentation.show_link_bubble("全体跳舞", important=True, duration_ms=2000)
        assert "全体跳舞" in primary_win.bubbles
        assert "全体跳舞" in second_win.bubbles

        # 「全体跳舞」动画扇出：state_applied 忙状态 → request_link_anim 到两可见窗
        primary_win.bubbles.clear()
        second_win.bubbles.clear()
        mgr._on_agent_state("dsh", "working", gen=0)
        assert primary_win.anims, "主窗应收到联动动画"
        assert second_win.anims, "第二窗应收到联动动画（全体跳舞）"

        # 主窗隐藏后：呈现不再扇出到隐藏窗
        second_win.hide()
        second_win.bubbles.clear()
        mgr.presentation.show_link_bubble("只看主窗", important=True, duration_ms=2000)
        assert "只看主窗" in primary_win.bubbles
        assert "只看主窗" not in second_win.bubbles, "隐藏窗不接收呈现事件"
    finally:
        _stop_sessions(*getattr(shell, "instances", []))
        if getattr(shell, "_shared", None) is not None:
            shell._shared.stop_all()
        slot_manager_mod._unlock_file(primary_handle)


def test_flag_off_shared_subsystems_none(tmp_path, app):
    """验收④：flag 关不实例化共享子系统（每窗各自建，逐位一致）。"""
    config = Config(tmp_path)
    config.set("experimental_single_process_spawn", False)
    config.save()
    shell = AppShell(QApplication.instance(), config, enable_chat=True)
    assert shell._single_process_spawn is False
    assert shell._shared is None
    assert shell.instance.win is None


def test_flag_on_tray_per_window_submenu_exists_and_routes(tmp_path, app, monkeypatch):
    """§③.3 / 验收②：单托盘 + 每窗子菜单存在，动作路由正确。"""
    shell, config, primary_handle = _make_flag_on_shell(tmp_path)
    try:
        primary_win = _make_primary_record_win(shell, config)
        second = _make_second_record_win(shell, tmp_path, monkeypatch)

        tray = shell._build_tray(primary_win)
        menu = tray.contextMenu()
        submenus = {}
        for act in menu.actions():
            sub = act.menu()
            if sub is not None and act.text().startswith("桌宠 "):
                submenus[act.text()] = sub
        # 每窗一个子菜单
        assert submenus, f"应存在每窗子菜单，实际 actions: {[a.text() for a in menu.actions()]}"
        assert any("slot-0" in t for t in submenus), f"主窗子菜单缺失: {list(submenus)}"
        assert any("slot-1" in t for t in submenus), f"第二窗子菜单缺失: {list(submenus)}"

        # 每窗子菜单含 显示/隐藏、切换角色、退出这只
        for text, sub in submenus.items():
            labels = [a.text() for a in sub.actions()]
            assert "显示 / 隐藏" in labels, f"{text} 缺显示/隐藏: {labels}"
            assert "退出这只" in labels, f"{text} 缺退出这只: {labels}"
            char_menu = next((a.menu() for a in sub.actions() if a.text() == "切换角色"), None)
            assert char_menu is not None, f"{text} 缺切换角色子菜单"

        # 动作路由：点第二窗子菜单的「显示 / 隐藏」→ 切换第二窗可见性
        target_sub = submenus[next(t for t in submenus if "slot-1" in t)]
        toggle_act = next(a for a in target_sub.actions() if a.text() == "显示 / 隐藏")
        assert second.win.is_shown is True
        toggle_act.trigger()
        assert second.win.is_shown is False, "「显示 / 隐藏」应切第二窗可见性"
        toggle_act.trigger()
        assert second.win.is_shown is True

        # 动作路由：点第二窗子菜单的「退出这只」→ _on_window_exit_requested(second)
        exited = []
        monkeypatch.setattr(shell, "_on_window_exit_requested",
                            lambda inst: exited.append(inst))
        exit_act = next(a for a in target_sub.actions() if a.text() == "退出这只")
        exit_act.trigger()
        assert exited == [second], "「退出这只」应路由到本窗实例"

        tray.hide()
    finally:
        _stop_sessions(*getattr(shell, "instances", []))
        if getattr(shell, "_shared", None) is not None:
            shell._shared.stop_all()
        slot_manager_mod._unlock_file(primary_handle)


def test_flag_on_island_toggle_all_windows(tmp_path, app, monkeypatch):
    """§③.4 / 验收③：灵动岛单击 toggle 全部窗，并按聚合可见态同步 set_pet_visible。"""
    shell, config, primary_handle = _make_flag_on_shell(tmp_path)
    try:
        primary_win = _make_primary_record_win(shell, config)
        second = _make_second_record_win(shell, tmp_path, monkeypatch)
        second_win = second.win

        island = _FakeIsland()
        shell.island = island
        # 启用灵动岛，让 _sync_dynamic_island 走向聚合可见态分支
        config.set("dynamic_island", {"enabled": True})
        config.save()

        # 初始：两窗都可见 → 单击 → 全部隐藏，island 同步为 False
        assert primary_win.is_shown and second_win.is_shown
        shell._toggle_pet_from_island()
        assert primary_win.is_shown is False
        assert second_win.is_shown is False
        assert island.pet_visible is False

        # 再单击 → 全部显示，island 同步为 True
        shell._toggle_pet_from_island()
        assert primary_win.is_shown is True
        assert second_win.is_shown is True
        assert island.pet_visible is True

        # 聚合可见态：只隐藏第二窗 → 主窗仍可见 → island 仍为可见
        second_win.hide()
        shell._sync_dynamic_island()
        assert island.pet_visible is True, "任一窗可见即聚合可见"
    finally:
        _stop_sessions(*getattr(shell, "instances", []))
        if getattr(shell, "_shared", None) is not None:
            shell._shared.stop_all()
        slot_manager_mod._unlock_file(primary_handle)


def test_flag_on_shared_proactive_broadcasts_bubble(tmp_path, app, monkeypatch):
    """§③.2：共享 proactive watcher 单一实例（限流器全局）且气泡广播到各可见窗。"""
    shell, config, primary_handle = _make_flag_on_shell(tmp_path)
    try:
        primary_win = _make_primary_record_win(shell, config)
        second = _make_second_record_win(shell, tmp_path, monkeypatch)
        second_win = second.win

        # 共享 proactive watcher：单一实例，限流器绑定主窗 config 目录（全局语义，R8）
        assert isinstance(shell._shared.proactive, SharedProactiveWatcher)
        assert shell._shared.proactive.limiter.state_path == \
            (config.dir / "proactive_screen_state.json")

        # 「我看」先兆气泡广播到两可见窗
        shell._shared.proactive._bridge._forward_bubble("hello", 1000)
        assert "hello" in primary_win.bubbles
        assert "hello" in second_win.bubbles
    finally:
        _stop_sessions(*getattr(shell, "instances", []))
        if getattr(shell, "_shared", None) is not None:
            shell._shared.stop_all()
        slot_manager_mod._unlock_file(primary_handle)


def test_flag_on_hidden_notify_text_non_primary(tmp_path, app, monkeypatch):
    """§③.4：隐藏提示文案对非主窗改为指引托盘子菜单（P2-5 消除误导）。"""
    shell, config, primary_handle = _make_flag_on_shell(tmp_path)
    try:
        primary_win = _make_primary_record_win(shell, config)
        second = _make_second_record_win(shell, tmp_path, monkeypatch)

        shown = []
        tray = type("_FakeTray", (), {
            "tray": None,
            "showMessage": lambda self, *a, **k: shown.append((a, k)),
        })()
        shell.tray = tray

        # 非主窗隐藏：文案指向托盘子菜单
        second._notify_pet_hidden()
        assert shown, "非主窗隐藏应弹托盘提示"
        msg = shown[-1][0][1]
        assert "显示 / 隐藏" in msg, f"非主窗提示应指向托盘子菜单: {msg}"

        # 主窗隐藏：恢复旧文案
        shown.clear()
        primary_win.cfg = config
        shell.instance._notify_pet_hidden()
        msg = shown[-1][0][1]
        assert "显示 / 隐藏" not in msg, f"主窗提示保持原样: {msg}"
        assert "点击托盘图标" in msg
    finally:
        _stop_sessions(*getattr(shell, "instances", []))
        if getattr(shell, "_shared", None) is not None:
            shell._shared.stop_all()
        slot_manager_mod._unlock_file(primary_handle)


# ---------------------------------------------------------------- P1 复审回归
def test_new_window_receives_link_provider_after_real_build(tmp_path, app, monkeypatch):
    """P1-1 回归（真实 _build_window 路径，不再整体 monkeypatch）：新窗必须在
    建窗完成后拿到共享联动 provider——`_wire_shared_subsystems` 若早于
    `self.win = win` 执行，扇出遍历不到新窗，provider 永远缺位。"""
    from tests.test_predictive_prewarm import FakeLibrary

    shell, config, handle = _make_flag_on_shell(tmp_path)
    config.set("click_sound_enabled", False)
    config.set("collision_sound_enabled", False)
    win = None
    try:
        win = shell.instance._build_window("shenshen", lib=FakeLibrary())
        assert getattr(win, "_link_next_provider", None) is not None, (
            "真实建窗路径下新窗必须拿到联动 provider（P1-1 时序回归）")
    finally:
        if win is not None:
            win.close()
            win.deleteLater()
        slot_manager_mod._unlock_file(handle)
        app.processEvents()


def test_shared_fullscreen_broadcast_respects_per_window_config(tmp_path, app):
    """P1-2 回归：共享全屏广播必须按每窗 auto_hide_fullscreen 过滤——
    关掉该功能的窗不得被无关广播隐藏。"""
    from tests.test_predictive_prewarm import FakeLibrary

    shell, config, handle = _make_flag_on_shell(tmp_path)
    config.set("click_sound_enabled", False)
    config.set("collision_sound_enabled", False)
    win1 = win2 = None
    try:
        win1 = shell.instance._build_window("shenshen", lib=FakeLibrary())
        sec = PetInstance(shell, Config(base=tmp_path, instance_id="slot-1"),
                          enable_chat=True)
        sec.config.set("click_sound_enabled", False)
        sec.config.set("collision_sound_enabled", False)
        shell._instances.append(sec)
        win2 = sec._build_window("shenshen", lib=FakeLibrary(), build_tray=False)
        win2.set_auto_hide_fullscreen(False)

        shell._on_shared_fullscreen(True)
        app.processEvents()

        assert not win1.isVisible(), "开启自动隐藏的窗应被全屏广播隐藏"
        assert win2.isVisible(), "关闭 auto_hide_fullscreen 的窗不得被广播误隐藏（P1-2）"

        shell._on_shared_fullscreen(False)
        app.processEvents()
        assert win1.isVisible(), "全屏结束后被隐藏的窗应恢复"
        assert win2.isVisible()
    finally:
        for w in (win1, win2):
            if w is not None:
                w.close()
                w.deleteLater()
        _stop_sessions(shell.instance, sec)
        slot_manager_mod._unlock_file(handle)
        if sec.slot_handle is not None:
            slot_manager_mod._unlock_file(sec.slot_handle)
        app.processEvents()
