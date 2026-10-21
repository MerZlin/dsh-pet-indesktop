# -*- coding: utf-8 -*-
"""A02：岛对话气泡的身份真值 = **slot0 档案**（不是全局面）。

回归：两个 ``IslandChatBubble`` 构造点（``_show_island_chat`` / ``_island_feedback_bubble``）
此前都传 ``self.config``（全局 Config）。迁移已经把 slot0 的每宠键从 ``config.json`` 抽走，
全局面上那个 ``character`` 只是出厂默认的残留——于是"切完角色后第一次点岛"（或迁移后重启
第一次点岛）会进默认角色的会话/人设，而完整聊天窗与宠物快速对话走的是 slot0 档案。
``sync_chat_character`` 只修**已存在**的岛气泡，修不了首次构造。

规范 §二：共享会话的角色 = slot0 档案里选定的角色；slot0 关闭（留槽）不改变它；子宠切
自己的角色也不改变它。``AppShell.instance`` 的配置面正是那条 slot0 路由视图。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from pet import catalog  # noqa: E402
from pet import pet_profiles  # noqa: E402
from pet.app import AppShell  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.settings_slot_config import route_settings_config  # noqa: E402

app = QApplication.instance() or QApplication([])


class _SpyBubble(QObject):
    """IslandChatBubble 的最小替身：只记录构造参数与三次装配调用。

    ``role`` = 构造那一刻 ``config.get('character')`` 的取值：真气泡的角色（会话与人设）
    就是在构造期从身份配置面取一次的，暖缓存回归要看的正是这个快照（F01）。
    """

    show_pet_requested = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.role = config.get("character") if config is not None else None
        self.settings = None
        self.refreshed = 0
        self.feedback: list = []

    def refresh_session(self):
        self.refreshed += 1

    def show_for_island(self, island, activate=True, reply_text=None):
        self.shown = (island, activate, reply_text)

    def show_feedback(self, island, text, subtitle="", duration_ms=None):
        self.feedback.append((text, subtitle))

    def request_in_flight(self):
        return False


def _make_shell(base, monkeypatch, *, child_roles=()):
    """真 AppShell（``__new__`` 装配）：全局 Config + slot0 路由视图 + 可选的子宠。"""
    import pet.island.chat as island_chat_mod

    global_cfg = Config(base=base)
    config_dir = global_cfg.dir          # Config 会把 base 再拼一层 APP_DIR_NAME
    shell = AppShell.__new__(AppShell)
    shell.config = global_cfg
    shell.island = QWidget()          # 真实 Qt 对象（shiboken6.isValid 要过）
    shell.island_chat = None
    shell.enable_chat = True
    main_view = route_settings_config(Config(base=base, instance_id="slot-0"))
    shell._instances = [SimpleNamespace(config=main_view)]
    shell.instance = shell._instances[0]
    for index, role in enumerate(child_roles, start=1):
        pet_profiles.write_profile_settings(config_dir, index, {"character": role})
        child = route_settings_config(Config(base=base, instance_id=f"slot-{index}"))
        shell._instances.append(SimpleNamespace(config=child))
    # 岛的可用性闸门（岛启用/可见/hidden_chat）与本契约无关，直接放行。
    monkeypatch.setattr(shell, "_island_chat_available", lambda: True)
    monkeypatch.setattr(shell, "_aggregate_pet_visible", lambda: False)
    monkeypatch.setattr(island_chat_mod, "IslandChatBubble", _SpyBubble)
    return shell, global_cfg, main_view, config_dir


def test_island_chat_uses_the_slot0_profile_role_not_the_global_default(tmp_path, monkeypatch):
    """slot0 档案的角色不是默认值时，岛气泡必须用它（迁移后第一次点岛就要求对）。"""
    shell, global_cfg, main_view, config_dir = _make_shell(tmp_path, monkeypatch)
    try:
        assert global_cfg.get("character") == catalog.DEFAULT_CHARACTER, (
            "前提：全局面上那个 character 只是出厂默认残留")
        assert pet_profiles.write_profile_settings(
            config_dir, 0, {"character": "custom-role"}).status == "ok"

        shell._show_island_chat(activate=True)

        bubble = shell.island_chat
        assert bubble is not None
        assert bubble.config is main_view, "岛气泡的身份面必须是 slot0 路由视图"
        assert main_view.get("character") == "custom-role"
        assert bubble.refreshed == 1, "构造后仍要走 refresh_session（会话真值同源）"
    finally:
        shell.island.deleteLater()


def test_island_chat_ignores_child_pet_roles(tmp_path, monkeypatch):
    """子宠切自己的角色不改共享会话角色：岛气泡仍读 slot0 档案。"""
    shell, global_cfg, main_view, config_dir = _make_shell(
        tmp_path, monkeypatch, child_roles=("child-role",))
    try:
        assert pet_profiles.write_profile_settings(
            config_dir, 0, {"character": "slot0-role"}).status == "ok"

        shell._show_island_chat(activate=True)

        assert shell.island_chat.config is main_view
        assert shell.island_chat.config.get("character") == "slot0-role"
        child_view = shell._instances[1].config
        assert child_view.get("character") == "child-role", "前提：子宠确实是别的角色"
    finally:
        shell.island.deleteLater()


def test_island_chat_with_a_missing_slot0_profile_still_reads_slot0(tmp_path, monkeypatch):
    """slot0 档案缺席（槽留空/新装）时读的仍是 **slot0 的同源默认**，不是别宠或全局面。"""
    shell, global_cfg, main_view, config_dir = _make_shell(
        tmp_path, monkeypatch, child_roles=("child-role",))
    try:
        assert not pet_profiles.slot_profile_exists(config_dir, 0)

        shell._show_island_chat(activate=True)

        assert shell.island_chat.config is main_view
        assert shell.island_chat.config.get("character") == catalog.DEFAULT_CHARACTER
    finally:
        shell.island.deleteLater()


def test_island_feedback_bubble_uses_the_same_identity_view(tmp_path, monkeypatch):
    """第二个构造点（隐藏期间的反馈气泡改道）必须与第一个同源。"""
    shell, global_cfg, main_view, config_dir = _make_shell(tmp_path, monkeypatch)
    try:
        assert pet_profiles.write_profile_settings(
            config_dir, 0, {"character": "custom-role"}).status == "ok"

        assert shell._island_feedback_bubble("在想…", subtitle="进度") is True

        assert shell.island_chat.config is main_view
        assert shell.island_chat.feedback == [("在想…", "进度")]
    finally:
        shell.island.deleteLater()


def test_chat_identity_helper_falls_back_when_there_is_no_instance(tmp_path):
    """精简装配/测试替身（没有 instance）时回退全局面，不抛。"""
    shell = AppShell.__new__(AppShell)
    shell.config = Config(base=tmp_path)
    shell.instance = None
    assert shell._chat_identity_config() is shell.config


# ---------------------------------------------------------------- F01 暖缓存（2026-10-10 终审）
def _sync_instance(config):
    """只装配 ``sync_chat_character`` 真正碰到的字段（跑的是**真**方法）。"""
    from pet.app import PetInstance

    instance = PetInstance.__new__(PetInstance)
    instance.config = config
    instance.shell = SimpleNamespace(enable_chat=True, island_chat=None)
    instance.legacy_chat_window = None
    instance.modern_chat_window = None
    return instance


def test_role_switch_refreshes_the_slot0_view_before_the_first_island_chat(
        tmp_path, monkeypatch):
    """F01：slot0 读视图已缓存旧角色时，切角色后的**首次**岛聊天必须拿新角色。

    最小触发（终审给的链条，全在真实 API 上复现）：先开完整聊天窗——它按
    ``ChatWindow(self.config, self.config.get('character'))`` 构造，于是 slot0 路由
    视图把**旧角色**的档案快照缓存住了 → 关掉该面 → 用 slot0 右键切角色 → 此时岛
    气泡还没建 → 第一次点岛。``sync_chat_character`` 只同步已存在的聊天面，缓存里
    的旧角色不刷新，首次构造的岛气泡就进了旧角色的会话/人设。

    冷启动直读磁盘的用例（本文件前三条）覆盖不到这条暖路径，所以这里显式先读一次。
    """
    shell, global_cfg, main_view, config_dir = _make_shell(tmp_path, monkeypatch)
    try:
        assert pet_profiles.write_profile_settings(
            config_dir, 0, {"character": "old-role"}).status == "ok"
        # 完整聊天窗构造期的真实读取（app.py open_modern_chat：ChatWindow(self.config,
        # self.config.get('character'))）——这一次读就把旧角色钉进了视图缓存。
        assert main_view.get("character") == "old-role"

        # 切角色：写档案（OverlayShell.switch_character 的持久化那一步）
        assert pet_profiles.write_profile_settings(
            config_dir, 0, {"character": "new-role"}).status == "ok"
        _sync_instance(main_view).sync_chat_character("new-role")

        assert main_view.get("character") == "new-role", (
            "切角色后 slot0 读视图仍报旧角色 = 首次岛聊天拿到旧身份")
        shell._show_island_chat(activate=True)
        assert shell.island_chat.config is main_view
        assert shell.island_chat.role == "new-role", (
            "岛气泡构造期取到的角色必须是新角色")
    finally:
        shell.island.deleteLater()


def test_role_sync_refresh_does_not_wash_a_form_edit_baseline(tmp_path):
    """刷新的是**只读运行视图**：表单草稿与首读钉住的编辑代次都不许被洗掉。

    同一个 ``SlotRoutedConfig`` 类型既是应用的 slot0 读视图，也是设置页的表单视图
    （独立进程里各有一份）。刷新若顺手重钉代次基线，外部 reset 之后表单里那份旧
    控件值就会被放行落盘——那正是 pinned-token 契约要挡的事（见
    ``tests/test_settings_slot_config_route.py::test_form_edit_generation_is_pinned_*``）。
    """
    import pytest

    view = route_settings_config(Config(base=tmp_path, instance_id="slot-0"))
    config_dir = view._main.dir
    assert pet_profiles.write_profile_settings(
        config_dir, 0, {"scale": 0.8}).status == "ok"
    assert view.get("scale") == pytest.approx(0.8)      # 表单首读：钉住代次基线
    view.set("scale", 1.4)                              # 表单草稿（pending）

    snapshot = pet_profiles.read_slot_snapshot(config_dir, 0)
    assert snapshot.status == "ok"
    assert pet_profiles.reset_slot_profile(
        config_dir, 0, generation=snapshot.generation).status == "ok"

    _sync_instance(view).sync_chat_character("new-role")

    assert view.save() is False, "旧草稿不许带着刷新后看到的新代次落盘"
    assert pet_profiles.read_slot_profile(
        config_dir, 0).value("scale") == pytest.approx(pet_profiles.same_source_default("scale"))
    assert view.get("scale") == pytest.approx(1.4), "被拒时草稿必须还在"
