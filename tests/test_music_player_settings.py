# -*- coding: utf-8 -*-
"""设置页「音乐播放器路径」（pet/settings_music.py）的聚焦回归。

产品契约：
- 两行路径写回 ``music_player_paths``（空 = 删掉该键，回到自动搜索）；
- 路径变化时清掉播放器路径缓存，让右键菜单下次拿到确定态；
- 「自动检测」在后台预热 + 只读缓存轮询回填，**绝不在 GUI 线程扫盘**；
- 冷缓存时继续等、超时也会收尾（不留一个永远转的定时器）；
- 找不到时给出可见说明（而不是静默什么都不发生）。
"""

from __future__ import annotations

import pytest

from pet import music_players, settings_music
from pet.settings_widgets import ToggleSwitch, _line_edit

pytest.importorskip("PySide6")


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Messages:
    """捕获 QMessageBox.information 调用（不弹真窗口）。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def information(self, parent, title, text, *args, **kwargs):
        self.calls.append((str(title), str(text)))
        return None


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    import pet.modern_settings_dialog as settings_mod
    from pet.config import Config

    app = _qapp()
    monkeypatch.setattr(settings_mod.autostart_mod, "is_enabled", lambda: False)
    dlg = settings_mod.ModernSettingsDialog(Config(tmp_path), include_ai=True)
    yield dlg
    dlg.close()
    app.processEvents()


def _pickers(dialog):
    return (
        dialog.music_player_netease_picker,
        dialog.music_player_qqmusic_picker,
    )


def test_rows_sit_in_the_pet_domain_music_group(dialog):
    """两行 + 检测按钮落在「桌宠 · 音乐关联」组里（掉进「待分类」就等于没做）。"""
    from PySide6.QtWidgets import QWidget

    rows = [
        dialog.findChild(QWidget, f"settingRow_{key}")
        for key in ("music_player_netease", "music_player_qqmusic", "music_player_detect")
    ]
    assert all(row is not None for row in rows)

    page = None
    for index in range(dialog.pages.count()):
        candidate = dialog.pages.widget(index)
        if candidate.isAncestorOf(rows[0]):
            page = candidate
            break
    assert page is not None
    assert dialog.sidebar.item(dialog.pages.indexOf(page)).text() == "桌宠"
    assert "待分类（开发期）" not in [row.objectName() for row in rows]


def test_saving_paths_persists_and_clears_cache(dialog, monkeypatch):
    cleared: list[bool] = []
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: cleared.append(True))

    netease, qqmusic = _pickers(dialog)
    netease.setText(r"D:\CloudMusic\cloudmusic.exe")
    qqmusic.setText(r"D:\QQ音乐\QQMusic\QQMusic.exe")
    dialog._save()

    assert dialog.config.get("music_player_paths") == {
        "netease": r"D:\CloudMusic\cloudmusic.exe",
        "qqmusic": r"D:\QQ音乐\QQMusic\QQMusic.exe",
    }
    assert cleared == [True], "路径变了必须清缓存，否则菜单还拿着旧的负缓存"


def test_empty_paths_drop_the_key_and_restore_auto_search(dialog, monkeypatch):
    from pet.config import Config
    from pet import settings_music as module

    dialog.config.set("music_player_paths", {"netease": "D:/old/cloudmusic.exe"})
    dialog.config.save()
    cleared: list[bool] = []
    monkeypatch.setattr(module.music_players, "clear_cache", lambda: cleared.append(True))

    for picker in _pickers(dialog):
        picker.setText("")
    dialog._save()

    assert dialog.config.get("music_player_paths") == {}
    assert cleared == [True]
    assert Config(base=dialog.config.dir).get("music_player_paths") == {}


def test_unchanged_paths_do_not_touch_the_cache(dialog, monkeypatch):
    """路径没变就不要清缓存（避免每次保存都让菜单重新扫盘）。"""
    monkeypatch.setattr(
        settings_music.music_players, "warm_cache_async", lambda *a, **k: False
    )
    cleared: list[bool] = []

    netease, _qqmusic = _pickers(dialog)
    netease.setText(r"D:\CloudMusic\cloudmusic.exe")
    dialog.config.set("music_player_paths", {"netease": r"D:\CloudMusic\cloudmusic.exe"})

    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: cleared.append(True))
    dialog._save()

    assert cleared == []


def test_detect_fills_pickers_and_stops_the_timer(dialog, monkeypatch):
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    warmed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        settings_music.music_players,
        "warm_cache_async",
        lambda key, manual="": warmed.append((key, manual)) or True,
    )

    found = {"netease": r"D:\CloudMusic\cloudmusic.exe", "qqmusic": None}

    def lookup(key, manual=""):
        path = found.get(key)
        return (music_players.CACHED_FOUND, path) if path else (music_players.CACHED_MISSING, None)

    settings_music.start_detect(dialog, lookup=lookup)

    netease, qqmusic = _pickers(dialog)
    assert netease.text() == r"D:\CloudMusic\cloudmusic.exe"
    assert qqmusic.text() == ""
    assert sorted(warmed) == [("netease", ""), ("qqmusic", "")], "两个播放器都要触发一次后台搜索"
    assert dialog._music_detect_timer.isActive() is False, "出结果后必须停掉轮询"
    assert dialog.music_player_detect_btn.isEnabled() is True
    assert dialog.music_player_detect_btn.text() == "自动检测"

    title, text = messages.calls[-1]
    assert title == "自动检测播放器"
    assert "网易云音乐" in text and "QQ音乐：未找到" in text


def test_detect_keeps_waiting_while_cache_is_cold(dialog, monkeypatch):
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: True)

    def cold(key, manual=""):
        return music_players.CACHED_COLD, None

    settings_music.start_detect(dialog, lookup=cold)

    assert dialog._music_detect_timer.isActive() is True, "还在扫盘时应继续轮询"
    assert messages.calls == [], "没结果就不该弹结论"
    assert dialog.music_player_detect_btn.text() == "检测中…"

    dialog._music_detect_timer.stop()


def test_detect_timeout_finishes_and_reports(dialog, monkeypatch):
    """超时兜底：不会因为一个永远 cold 的 key 留下永远转的定时器。"""
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: True)

    def cold(key, manual=""):
        return music_players.CACHED_COLD, None

    dialog._music_detect_lookup = cold
    dialog._music_detect_results = {}
    dialog._music_detect_deadline = 0.0
    settings_music.poll_detect(dialog)

    assert messages.calls and "未找到" in messages.calls[-1][1]
    assert dialog.music_player_detect_btn.isEnabled() is True


def test_detected_path_is_written_to_config_on_save(dialog, monkeypatch):
    """检测回填 → 保存 → 配置里真的有了（端到端最小链路）。"""
    messages = _Messages()
    monkeypatch.setattr(settings_music, "QMessageBox", messages)
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: True)
    monkeypatch.setattr(settings_music.music_players, "clear_cache", lambda: None)

    def lookup(key, manual=""):
        if key == "netease":
            return music_players.CACHED_FOUND, r"D:\CloudMusic\cloudmusic.exe"
        return music_players.CACHED_MISSING, None

    settings_music.start_detect(dialog, lookup=lookup)
    dialog._save()

    assert dialog.config.get("music_player_paths") == {"netease": r"D:\CloudMusic\cloudmusic.exe"}


# ------------------------------------------------- 浏览器媒体会话开关：平台契约
#
# SMTC 是 Windows 专属能力。非 Windows 上这个开关既没有效果也不该出现在设置页
# （SETTINGS-CHANGE-GATES §4「永久不支持的能力不创建控件」），所以控件与设置行
# 都只在 Windows 创建——与 cursor_hidden_passthrough 的处置一致。


def test_browser_media_row_is_absent_on_non_windows(monkeypatch):
    """非 Windows 平台：即使控件存在，设置行也不出现（平台闸门优先）。"""
    import sys

    from pet.settings_widgets import ToggleSwitch

    _qapp()
    host = type("H", (), {"music_browser_media_check": ToggleSwitch()})()

    monkeypatch.setattr(sys, "platform", "linux")
    assert settings_music.browser_media_supported() is False
    assert settings_music.browser_media_rows(host) == []

    monkeypatch.setattr(sys, "platform", "darwin")
    assert settings_music.browser_media_rows(host) == []

    monkeypatch.setattr(sys, "platform", "win32")
    assert settings_music.browser_media_supported() is True
    rows = settings_music.browser_media_rows(host)
    assert [row.objectName() for row in rows] == ["settingRow_music_browser_media"]


def test_browser_media_row_is_absent_when_widget_missing():
    """控件没建（非 Windows 的实际形态）时行也不建：不留"只有行没有控件"的残骸。"""
    _qapp()
    host = type("H", (), {"music_browser_media_check": None})()

    assert settings_music.browser_media_rows(host) == []


def test_browser_media_widget_is_windows_only(monkeypatch, tmp_path):
    """控件本体同样只在 Windows 创建（设置页构造路径）。"""
    import sys

    from pet import settings_pet_controls
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_pet_controls.sys, "platform", "linux")
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    assert dialog.music_browser_media_check is None, "非 Windows 不该建这个控件"
    # 缺控件时保存路径要安全跳过（不许抛，也不许把它写成 False）
    dialog._save()
    dialog.reject()
    app.processEvents()


# ------------------------------------------------------------------ #228
#
# foobar2000 歌词（窗口标题源）。平台闸门与 browser_media 同规：控件与设置行都只在
# Windows 创建，非 Windows 连控件都不建（免得出现"控件在、行不在"的游离开关）。


def test_foobar_lyrics_rows_are_absent_on_non_windows(monkeypatch):
    """非 Windows 平台：即使控件存在，两行也不出现（平台闸门优先）。"""
    import sys

    from pet.settings_widgets import ToggleSwitch

    _qapp()
    host = type(
        "H",
        (),
        {
            "foobar_lyrics_check": ToggleSwitch(),
            "foobar_title_format_edit": _line_edit(""),
        },
    )()

    for platform in ("linux", "darwin"):
        monkeypatch.setattr(sys, "platform", platform)
        assert settings_music.foobar_lyrics_supported() is False
        assert settings_music.foobar_lyrics_rows(host) == []

    monkeypatch.setattr(sys, "platform", "win32")
    assert settings_music.foobar_lyrics_supported() is True
    rows = settings_music.foobar_lyrics_rows(host)
    assert [row.objectName() for row in rows] == [
        "settingRow_foobar_lyrics",
        "settingRow_foobar_title_format",
    ]


def test_foobar_lyrics_rows_are_absent_when_widgets_missing():
    """控件没建（非 Windows 的实际形态）时行也不建——不留孤儿行。"""
    _qapp()
    host = type("H", (), {"foobar_lyrics_check": None, "foobar_title_format_edit": None})()
    assert settings_music.foobar_lyrics_rows(host) == []


def test_foobar_lyrics_widgets_are_windows_only(monkeypatch, tmp_path):
    """控件本体同样只在 Windows 创建；缺控件时保存路径安全跳过。"""
    import sys

    from pet import settings_pet_controls
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_pet_controls.sys, "platform", "linux")
    dialog = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    assert dialog.foobar_lyrics_check is None, "非 Windows 不该建这个控件"
    assert dialog.foobar_title_format_edit is None
    dialog._save()
    dialog.reject()
    app.processEvents()


def test_save_source_settings_writes_both_switches_and_format(tmp_path):
    """保存：两个来源开关与格式串一起写回，格式串去掉首尾空白。"""
    from pet.config import Config

    _qapp()
    cfg = Config(tmp_path)
    host = type(
        "H",
        (),
        {
            "config": cfg,
            "music_browser_media_check": ToggleSwitch(),
            "foobar_lyrics_check": ToggleSwitch(),
            "foobar_title_format_edit": _line_edit("  %artist% - %title%  "),
        },
    )()
    host.music_browser_media_check.setChecked(True)
    host.foobar_lyrics_check.setChecked(True)

    settings_music.save_source_settings(host)

    assert cfg.get("music_browser_media_enabled") is True
    assert cfg.get("foobar_lyrics_enabled") is True
    assert cfg.get("foobar_title_format") == "%artist% - %title%"


def test_save_source_settings_skips_missing_widgets(tmp_path):
    """控件不存在（非 Windows）时**整键跳过**：不许把它写成 False 覆盖用户配置。"""
    from pet.config import Config

    _qapp()
    cfg = Config(tmp_path)
    cfg.set("foobar_lyrics_enabled", True)
    host = type(
        "H",
        (),
        {
            "config": cfg,
            "music_browser_media_check": None,
            "foobar_lyrics_check": None,
            "foobar_title_format_edit": None,
        },
    )()

    settings_music.save_source_settings(host)

    assert cfg.get("foobar_lyrics_enabled") is True, "缺控件不该把开关写成 False"


def test_foobar_controls_are_editable_on_the_child_page(monkeypatch, tmp_path):
    """子宠页：foobar 两键可编辑，保存落**主配置**（歌词源解析读的就是它）。

    这两个键不在 ``PER_SLOT_SETTING_KEYS`` 里（子宠页曾经置灰：写了 slot 文件、
    运行期读主配置 = 死开关）。独立设置进程现在按 key 分流到主配置，故恢复可用。
    """
    import json

    from pet import settings_music
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_music.sys, "platform", "win32")   # 两键仅在 Windows 建
    master = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    child = ModernSettingsDialog(Config(base=tmp_path, instance_id="slot-1"),
                                 include_ai=False)
    try:
        assert master.foobar_lyrics_check is not None, "用例前置：Windows 下应建出控件"
        for control in (master.foobar_lyrics_check, master.foobar_title_format_edit,
                        child.foobar_lyrics_check, child.foobar_title_format_edit):
            assert control is not None
            assert control.isEnabled() is True
            assert control.toolTip() != "仅主桌宠可设置"

        child.foobar_lyrics_check.setChecked(True)
        child.foobar_title_format_edit.setText("%artist% - %title%")
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，本用例改的键
        # 都是全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["foobar_lyrics_enabled"] is True, "子宠页改的键必须真正生效"
        assert on_disk["foobar_title_format"] == "%artist% - %title%"
    finally:
        for dialog in (master, child):
            dialog.close()
            dialog.deleteLater()
        app.processEvents()


def test_browser_media_control_is_editable_on_the_child_page(monkeypatch, tmp_path):
    """子宠页：「浏览器媒体会话参与歌词」与 foobar 同组同规——可编辑 + 落主配置。"""
    import json

    from pet import settings_music
    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    monkeypatch.setattr(settings_music.sys, "platform", "win32")   # 控件仅在 Windows 建
    master = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    child = ModernSettingsDialog(Config(base=tmp_path, instance_id="slot-1"),
                                 include_ai=False)
    try:
        assert master.music_browser_media_check is not None, "用例前置：Windows 下应建出控件"
        assert master.music_browser_media_check.isEnabled() is True
        assert child.music_browser_media_check.isEnabled() is True
        assert child.music_browser_media_check.toolTip() != "仅主桌宠可设置"

        child.music_browser_media_check.setChecked(True)
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，本用例改的键
        # 都是全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["music_browser_media_enabled"] is True
    finally:
        for dialog in (master, child):
            dialog.close()
            dialog.deleteLater()
        app.processEvents()


def test_music_player_pickers_are_editable_on_the_child_page(tmp_path):
    """子宠页：播放器路径两键与「自动检测」可编辑，保存落主配置（右键菜单读它）。"""
    import json

    from pet.config import Config
    from pet.modern_settings_dialog import ModernSettingsDialog

    app = _qapp()
    master = ModernSettingsDialog(Config(tmp_path), include_ai=False)
    child = ModernSettingsDialog(Config(base=tmp_path, instance_id="slot-1"),
                                 include_ai=False)
    try:
        for picker in _pickers(master) + _pickers(child):
            assert picker.isEnabled() is True
            assert picker.toolTip() != "仅主桌宠可设置"
        assert master.music_player_detect_btn.isEnabled() is True
        assert child.music_player_detect_btn.isEnabled() is True

        _pickers(child)[0].setText(r"D:\CloudMusic\cloudmusic.exe")
        assert child._write_config() is True
        # #271：空身份（主宠页）会话的配置面指向 config-slot-0.json，本用例改的键
        # 都是全局键、落主配置——读盘要用主配置路径。
        on_disk = json.loads(master.config._main.path.read_text(encoding="utf-8"))
        assert on_disk["music_player_paths"] == {
            "netease": r"D:\CloudMusic\cloudmusic.exe"
        }
    finally:
        for dialog in (master, child):
            dialog.close()
            dialog.deleteLater()
        app.processEvents()
