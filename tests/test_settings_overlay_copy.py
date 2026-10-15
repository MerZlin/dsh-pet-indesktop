# -*- coding: utf-8 -*-
"""设置页文案 × overlay（默认拓扑）实际行为的聚焦回归（批 B8）。

三个缺口（证据：.scratch/windows-parity-20260926-a/audit-20260927-swarm/raw.txt）：

- T1「省电模式」（``idle_low_fps_enabled``）：文案声称「动画按半帧率呈现
  （24fps 素材 → 12fps 效果）」。overlay 下可见帧率由素材 clip 自持定时器驱动
  （``frameseq_clip`` 按素材 fps），与该键无关；该键的实际作用只是关闭后台动画
  预热（``app.py``：``prewarm_enabled=not idle_low_fps_enabled``）。legacy 的
  PetWindow 确实是降帧（``window.py`` 的 ``_idle_reduction_active`` +
  ``_sync_movie_throttle``），故文案按拓扑分流，legacy 原文案不动。
- T2「直播捕获兼容」（``stream_capture_mode``）：文案曾声称 overlay 下「重启后
  生效」但 overlay 全仓无消费者（``overlay_window`` 恒 FramelessTool、无
  ``set_stream_capture_mode``）→ 当时改为置灰 + 如实说明。**T4 Phase A 复盘
  落地**：该键成为拓扑输入（``overlay_settings_command.is_overlay_topology``
  读主配置 ``stream_capture_mode``，进程内快照一次），勾选后重启真的走 legacy
  单窗路径并收拢多宠，故撤销置灰、文案回到 T4 口径（重启后生效 + 多宠收拢）；
  显式 ``PET_RENDER_TOPOLOGY=legacy`` 仍是运行期即时切换，逐字保留原提示。
- T3 窗口级键（置顶/鼠标穿透/不透明度/锁定位置/SHIFT 拖拽/光标隐藏穿透/全屏
  自动隐藏）在 overlay 一窗多宠下物理上只能整窗生效 → 每个 slot 设置页补一句
  「对所有桌宠生效」。**#271 起主宠页也在此列**：无 ``--instance`` 的空身份设置
  会话按 slot0 分流，主宠页就是 slot0 页（= 生产 ``--instance slot-0`` 路径），
  与子宠页同口径标注；只有 legacy 拓扑不加（每宠独立窗口，键确实只作用于自己）。

纪律：offscreen；同步直调，不 sleep；不 mock 产品对象。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

#: overlay 单合成窗下只能整窗生效的窗口级键（T3）。
WINDOW_SCOPE_IDS = (
    "on_top",
    "mouse_through",
    "pet_opacity",
    "lock_position",
    "shift_drag",
    "cursor_hidden_passthrough",
    "auto_hide_fullscreen",
)

SCOPE_NOTE = "对所有桌宠生效"
LEGACY_STREAM_CAPTURE_HINT = "让 OBS 等工具能够枚举并捕获桌宠窗口；OBS 捕获方式请选 Windows 10（1903 及以上），BitBlt 会是黑屏。"


def _dialog(tmp_path, monkeypatch, *, topology: str = "overlay", instance_id: str = "",
            standalone: bool = False):
    """按拓扑/身份构建设置页（照 test_stream_capture_copy 的既有构造方式）。

    平台固定 win32：T3 的 ``cursor_hidden_passthrough`` / ``auto_hide_fullscreen``
    与 T2 的 ``stream_capture`` 都只在 Windows 建控件。
    """
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    monkeypatch.setattr(settings_mod.sys, "platform", "win32")
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    monkeypatch.setenv("PET_RENDER_TOPOLOGY", topology)
    config = Config(base=tmp_path, instance_id=instance_id) if instance_id else Config(base=tmp_path)
    return settings_mod.ModernSettingsDialog(config, include_ai=False, standalone=standalone)


def _close(dialog):
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def _hint(dialog, setting_id: str) -> str:
    import pet.modern_settings_dialog as settings_mod

    row = dialog.findChild(settings_mod.SettingRow, f"settingRow_{setting_id}")
    assert row is not None, f"设置页必须有 {setting_id} 行"
    return row.hint_label.text()


# --------------------------------------------------------------------------
# T1 省电模式
# --------------------------------------------------------------------------
@pytest.fixture
def overlay_dialog(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    yield dialog
    _close(dialog)


def test_idle_low_fps_copy_matches_overlay_behaviour(overlay_dialog):
    """overlay：只描述真实作用（停预热），不得再声称降低呈现帧率。"""
    hint = _hint(overlay_dialog, "idle_low_fps")
    assert "预热" in hint
    assert "半帧率" not in hint and "12fps" not in hint
    assert "帧率不受影响" in hint


def test_idle_low_fps_copy_keeps_legacy_frame_reduction(tmp_path, monkeypatch):
    """legacy：PetWindow 仍按半帧率呈现，文案逐字保持原样。"""
    dialog = _dialog(tmp_path, monkeypatch, topology="legacy")
    try:
        hint = _hint(dialog, "idle_low_fps")
    finally:
        _close(dialog)
    assert "半帧率" in hint
    assert "停止后台动画预热" in hint


# --------------------------------------------------------------------------
# T2 直播捕获兼容
# --------------------------------------------------------------------------
def test_stream_capture_enabled_with_restart_level_copy_under_overlay(overlay_dialog):
    """overlay：可点 + T4 口径（重启后生效、多宠先收拢为主宠物、影响所有桌宠）。"""
    hint = _hint(overlay_dialog, "stream_capture")
    assert overlay_dialog.stream_capture_check.isEnabled() is True
    assert hint == (LEGACY_STREAM_CAPTURE_HINT
                    + "重启后生效；重启时多只桌宠会先收拢为主宠物。（影响所有桌宠）")
    assert "暂不支持" not in hint


def test_stream_capture_toggle_clickable_in_both_topologies(tmp_path, monkeypatch):
    """两个拓扑下点击都送达（T4 Phase A 撤销置灰；legacy 对照组同时证明非空转）。"""
    for name in ("overlay", "legacy"):
        (tmp_path / name).mkdir()
    overlay = _dialog(tmp_path / "overlay", monkeypatch, topology="overlay")
    legacy = _dialog(tmp_path / "legacy", monkeypatch, topology="legacy")
    try:
        overlay.show()
        legacy.show()
        app.processEvents()
        QTest.mouseClick(overlay.stream_capture_check, Qt.MouseButton.LeftButton)
        QTest.mouseClick(legacy.stream_capture_check, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert overlay.stream_capture_check.isChecked() is True
        assert legacy.stream_capture_check.isChecked() is True
    finally:
        _close(overlay)
        _close(legacy)


def test_stream_capture_unchanged_under_legacy(tmp_path, monkeypatch):
    """legacy：PetWindow 运行期可切窗旗标 → 控件可用、文案逐字不变。"""
    dialog = _dialog(tmp_path, monkeypatch, topology="legacy")
    try:
        hint = _hint(dialog, "stream_capture")
        enabled = dialog.stream_capture_check.isEnabled()
    finally:
        _close(dialog)
    assert enabled is True
    assert hint == LEGACY_STREAM_CAPTURE_HINT


# --------------------------------------------------------------------------
# T3 窗口级键的生效范围
# --------------------------------------------------------------------------
@pytest.mark.parametrize("setting_id", WINDOW_SCOPE_IDS)
def test_child_page_marks_window_scope_keys_as_global(tmp_path, monkeypatch, setting_id):
    """overlay + 子宠页：窗口级键补「对所有桌宠生效」，可访问说明同步。"""
    import pet.modern_settings_dialog as settings_mod

    dialog = _dialog(tmp_path, monkeypatch, instance_id="slot-1")
    try:
        hint = _hint(dialog, setting_id)
        row = dialog.findChild(settings_mod.SettingRow, f"settingRow_{setting_id}")
        description = row.control.accessibleDescription()
    finally:
        _close(dialog)
    assert SCOPE_NOTE in hint
    # SettingRow 在构造期把 hint 写进 accessibleDescription；改文案后必须同步，
    # 否则读屏仍播旧说明（test_menu_layout 的可访问性契约）。
    assert description == hint


@pytest.mark.parametrize("setting_id", WINDOW_SCOPE_IDS)
def test_main_page_shows_scope_note(tmp_path, monkeypatch, setting_id):
    """overlay + 主宠页：与子宠页同口径补「（对所有桌宠生效）」。

    #271 契约变更：无 ``--instance`` 的空身份设置会话按 slot0 分流——**主宠页就是
    slot0 页**，与生产 ``--instance slot-0`` 路径（D13）逐位同构。窗口级键在单合成
    窗里同样只能整窗生效，故主宠页不该再"静默地少一句说明"。旧契约（"空身份 = 全局
    视角页，不加注记"）已作废，这里与 ``test_child_page_marks_window_scope_keys_as_global``
    断言同一条注记；注记本体的措辞由产品常量 ``ALL_PETS_SCOPE_NOTE`` 提供。
    """
    from pet.settings_pet_controls import ALL_PETS_SCOPE_NOTE

    dialog = _dialog(tmp_path, monkeypatch)
    try:
        hint = _hint(dialog, setting_id)
    finally:
        _close(dialog)
    assert ALL_PETS_SCOPE_NOTE in hint


@pytest.mark.parametrize("setting_id", WINDOW_SCOPE_IDS)
def test_legacy_child_page_has_no_scope_note(tmp_path, monkeypatch, setting_id):
    """legacy + 子宠页：每宠独立进程/窗口，窗口级键只作用于自己，不误导。"""
    dialog = _dialog(tmp_path, monkeypatch, topology="legacy", instance_id="slot-1")
    try:
        hint = _hint(dialog, setting_id)
    finally:
        _close(dialog)
    assert SCOPE_NOTE not in hint


def test_standalone_settings_process_marks_scope_note(tmp_path, monkeypatch):
    """真实交付路径：独立设置进程（--settings --instance slot-1）同样带说明。"""
    dialog = _dialog(tmp_path, monkeypatch, instance_id="slot-1", standalone=True)
    try:
        hints = {key: _hint(dialog, key) for key in WINDOW_SCOPE_IDS}
    finally:
        _close(dialog)
    for key, hint in hints.items():
        assert SCOPE_NOTE in hint, key
