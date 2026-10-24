# -*- coding: utf-8 -*-
"""气泡鼠标穿透（``bubble_click_through``，issue #367）：意愿、生效判据与样式重放。

背景：气泡是独立置顶 Tool 窗口，落在其矩形内的点击今天会被原生窗口吃掉——
``WA_TransparentForMouseEvents`` 只是 Qt 不处理事件，不会穿透到其它应用。本项
给气泡单独一个 OS 级穿透开关（``WS_EX_TRANSPARENT``，复用 ``platform_win`` 的
``_set_windows_click_through``，按 HWND 生效），与整宠「鼠标穿透」相互独立。

本文件守护的行为契约：
1. 默认关 = 从不碰原生样式（与旧版逐位一致）；
2. 生效判据 = 意愿 ∧ 非交互气泡（审批/提问按钮在场）∧ 非捕获兼容子模式；
3. 交互气泡弹出期间临时撤销、收起后按意愿恢复；
4. ``setWindowFlags`` 重建原生窗口会丢扩展样式位 ⇒ ``showEvent`` 必须重放；
5. 非 Windows 无此原语：完全不碰原生窗口，设置行也不创建；
6. 设置行：默认关、保存 round trip、搜「气泡穿透」可达。

原生调用一律走记录器替换（只 mock OS 边界，Qt 事件用真实事件循环语义）。
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import pet.speech_bubble as speech_bubble  # noqa: E402
import pet.modern_settings_dialog as settings_mod  # noqa: E402
from pet.config import Config  # noqa: E402
from pet.modern_settings_dialog import SettingRow  # noqa: E402
from pet.speech_bubble import PetSpeechBubble  # noqa: E402

app = QApplication.instance() or QApplication([])

ROW_KEY = "bubble_click_through"


def _anchor() -> QRect:
    return QRect(420, 460, 220, 260)


@pytest.fixture()
def native(monkeypatch) -> list[tuple[int, bool]]:
    """记录 ``_set_windows_click_through`` 调用，并把平台模拟成 win32。

    三平台 CI 腿共用同一套断言：平台分支在调用点读 ``sys.platform``，测试里
    替换即可（模块级常量 ``_MAC`` 等与本功能无关）。
    """
    calls: list[tuple[int, bool]] = []

    def _record(hwnd, enabled, user32=None):
        calls.append((int(hwnd), bool(enabled)))
        return True

    monkeypatch.setattr(speech_bubble, "_set_windows_click_through", _record)
    monkeypatch.setattr(sys, "platform", "win32")
    return calls


# ------------------------------------------------------------ 气泡 seam
def test_default_off_never_touches_native_style(native):
    bubble = PetSpeechBubble()
    bubble.show_text("今天的风很舒服。", _anchor())
    bubble.hide()
    bubble.show()
    assert native == [], "默认关 = 从不写原生样式（与旧版逐位一致）"


def test_set_click_through_applies_and_toggles(native):
    bubble = PetSpeechBubble()
    bubble.show_text("今天的风很舒服。", _anchor())
    assert native == []
    bubble.set_click_through(True)
    assert [enabled for _, enabled in native] == [True]
    hwnd_on = native[-1][0]
    bubble.set_click_through(True)  # 意愿不变：不重复碰原生窗口
    assert len(native) == 1
    bubble.set_click_through(False)  # 显式关闭：把样式收回去
    assert [enabled for _, enabled in native] == [True, False]
    assert native[-1][0] == hwnd_on


def test_intent_set_while_hidden_applies_on_first_show(native):
    """启动期宿主先下发意愿、气泡尚未显示：等首次 showEvent 才写样式。"""
    bubble = PetSpeechBubble()
    bubble.set_click_through(True)
    assert native == []
    bubble.show_text("今天的风很舒服。", _anchor())
    assert [enabled for _, enabled in native] == [True]


def test_interactive_buttons_temporarily_revoke_and_restore(native):
    bubble = PetSpeechBubble()
    bubble.show_text("今天的风很舒服。", _anchor())
    bubble.set_click_through(True)
    native.clear()
    bubble.show_text("要开窗吗？", _anchor(), buttons=[("同意", lambda: None), ("拒绝", lambda: None)])
    assert native and all(enabled is False for _, enabled in native), "审批按钮在场时必须撤销穿透（按钮要能点）"
    bubble.dismiss()
    assert native[-1][1] is True, "收起后按意愿恢复穿透"


def test_show_event_replays_after_native_rebuild(native):
    """setWindowFlags 重建原生窗口后扩展样式归零，showEvent 必须重放。

    offscreen 平台 winId 恒定，重放不能依赖句柄变化检测——开启期间每次
    可见化都无条件收敛一次（helper 自带"样式相同只读不写"的幂等）。
    """
    bubble = PetSpeechBubble()
    bubble.show_text("今天的风很舒服。", _anchor())
    bubble.set_click_through(True)
    native.clear()
    bubble.set_host_on_top(False)  # _apply_window_flags → setWindowFlags → 重建
    bubble.set_host_on_top(True)
    assert [enabled for _, enabled in native] == [True, True], "每次重建后的 show 都要重放一次"
    native.clear()
    bubble.hide()  # 同一窗口再显示：同样收敛一次（幂等在 Win32 原语里）
    bubble.show()
    assert [enabled for _, enabled in native] == [True]


def test_capture_compat_child_has_no_independent_hwnd(native):
    """捕获兼容模式气泡是主窗子控件（无独立 HWND）：整个开关不生效。"""
    host = QWidget()
    host.show()  # 宿主可见，子控件才真正可见——中段断言检验的是「子模式跳过」而不是「不可见跳过」
    bubble = PetSpeechBubble()
    bubble.show_text("今天的风很舒服。", _anchor())
    bubble.set_click_through(True)
    native.clear()
    bubble.set_capture_compat(True, host)
    bubble.show_text("捕获模式", _anchor())
    bubble.hide()
    bubble.show()
    assert native == [], "子模式下没有可写的 HWND，一次都不写（也不能把穿透写到主窗上）"
    bubble.set_capture_compat(False)  # 恢复独立窗口：重建后 showEvent 重放
    assert native and all(enabled is True for _, enabled in native), "退出子模式后的每次可见化都收敛回穿透态"


def test_non_windows_platform_never_touches_native(monkeypatch):
    calls: list[tuple[int, bool]] = []

    def _record(hwnd, enabled, user32=None):
        calls.append((int(hwnd), bool(enabled)))
        return True

    monkeypatch.setattr(speech_bubble, "_set_windows_click_through", _record)
    monkeypatch.setattr(sys, "platform", "linux")
    bubble = PetSpeechBubble()
    bubble.show_text("今天的风很舒服。", _anchor())
    bubble.set_click_through(True)
    bubble.hide()
    bubble.show()
    assert calls == [], "WS_EX_TRANSPARENT 是 Windows 原生样式，其它平台零调用"


# ------------------------------------------------------------ overlay 注入 seam
class _StubBubble:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def set_click_through(self, on) -> None:
        self.calls.append(bool(on))


def test_overlay_helper_applies_click_through_from_config():
    from pet.overlay_shell import OverlayShell

    def _shell(cfg):
        return SimpleNamespace(_config=cfg)

    stub = _StubBubble()
    OverlayShell._apply_bubble_click_through(_shell({"bubble_click_through": True}), stub, {"bubble_click_through": True})
    OverlayShell._apply_bubble_click_through(_shell({}), stub, {})
    assert stub.calls == [True, False], "跟随该 sprite 的配置来源：开=注入 True，缺省=注入 False"
    # 测试替身气泡不实现 setter：静默跳过（与 _apply_bubble_text_scale 同口径）
    OverlayShell._apply_bubble_click_through(_shell({}), object(), {})


# ------------------------------------------------------------ 设置行
def _dialog(tmp_path, monkeypatch, *, platform="win32"):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    return settings_mod.ModernSettingsDialog(Config(base=tmp_path), include_ai=True)


def _row(dialog) -> SettingRow:
    row = dialog.findChild(SettingRow, f"settingRow_{ROW_KEY}")
    assert row is not None, "互动域「输入」组必须有「气泡鼠标穿透」行"
    return row


def test_settings_row_defaults_off_and_saves_round_trip(tmp_path, monkeypatch):
    cfg = Config(base=tmp_path)
    assert cfg.get(ROW_KEY) is False, "出厂默认必须是关"
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        row = _row(dialog)
        assert row.control.isChecked() is False, "行初值跟着配置（默认关）"
        row.control.setChecked(True)
        assert dialog._write_config() is True
    finally:
        dialog.close()
    # 设置页的配置面套着 SlotRoutedConfig，落盘走自己的路径——断言用新
    # Config 读回（同 test_settings_cross_screen 的口径），不信任旧内存对象。
    assert Config(base=tmp_path).get(ROW_KEY) is True, "勾选必须落盘"
    dialog2 = _dialog(tmp_path, monkeypatch)
    try:
        assert _row(dialog2).control.isChecked() is True, "重开对话框初值跟盘上配置"
    finally:
        dialog2.close()


def test_settings_row_not_created_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dialog = _dialog(tmp_path, monkeypatch, platform="linux")
    try:
        assert getattr(dialog, "bubble_through_check", "missing") is None
        assert dialog.findChild(SettingRow, f"settingRow_{ROW_KEY}") is None, "永久不支持的能力不创建控件"
        assert dialog._write_config() is True, "缺控件不影响保存"
    finally:
        dialog.close()


def test_settings_row_is_reachable_by_search(tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, monkeypatch)
    try:
        for query in ("气泡穿透", "穿透到下层"):
            dialog.search_edit.setText("")
            app.processEvents()
            dialog.search_edit.setText(query)
            app.processEvents()
            matched = [row.objectName() for row in dialog._search_matches]
            assert f"settingRow_{ROW_KEY}" in matched, f"搜「{query}」应当命中这一行"
    finally:
        dialog.close()


# ------------------------------------------------------------ 配置注册
def test_config_default_and_bool_normalization(tmp_path):
    cfg = Config(base=tmp_path)
    assert cfg.get(ROW_KEY) is False, "新键默认关（升级用户呈现不变）"
    cfg.save()  # 出厂默认先落盘，才有文件可注入脏值
    raw = json.loads(cfg.path.read_text(encoding="utf-8"))
    raw[ROW_KEY] = "false"  # 盘上脏值（字符串）不得被当成真
    cfg.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert Config(base=tmp_path).get(ROW_KEY) is False
    cfg.set(ROW_KEY, True)
    cfg.save()
    assert Config(base=tmp_path).get(ROW_KEY) is True
