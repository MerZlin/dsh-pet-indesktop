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

    rows = [dialog.findChild(QWidget, f"settingRow_{key}") for key in ("music_player_netease", "music_player_qqmusic", "music_player_detect")]
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
    monkeypatch.setattr(settings_music.music_players, "warm_cache_async", lambda *a, **k: False)
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
