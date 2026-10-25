# -*- coding: utf-8 -*-
"""经典聊天窗读取角色别名（issue #370）：标题、头像与改名即时刷新。

回归背景：经典窗 ``_apply_character_theme`` 漏了 ``character_alias`` 查询
（现代窗有），标题永远是 manifest 名/目录名、头像永远是目录名首字母。
修复后经典窗与现代窗同一条优先级链：别名 → manifest 名 → 目录 id；头像
优先桌宠实时画面（``icon_pixmap``），拿不到回退**显示名**首字母。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pet.chat.legacy_widgets import ChatWindow as ClassicChatWindow  # noqa: E402
from pet.chat.widgets import ChatWindow as ModernChatWindow  # noqa: E402
from pet.config import Config  # noqa: E402

app = QApplication.instance() or QApplication([])

CID = "shenshen"
ALIAS = "小小萱"


def _config(tmp_path, alias: str = ALIAS) -> Config:
    cfg = Config(base=tmp_path)
    if alias:
        cfg.set_character_alias(CID, alias)
    return cfg


def _live_pixmap() -> QPixmap:
    pm = QPixmap(24, 24)
    pm.fill()
    return pm


# ------------------------------------------------------------ 标题
def test_classic_title_uses_alias(tmp_path):
    window = ClassicChatWindow(_config(tmp_path), CID)
    assert window.title_label.text() == f"{ALIAS} · AI 对话"


def test_classic_title_falls_back_to_display_name_without_alias(tmp_path):
    cfg = _config(tmp_path, alias="")
    window = ClassicChatWindow(cfg, CID)
    expected = f"{cfg.character_display_name(CID)} · AI 对话"
    assert window.title_label.text() == expected


def test_modern_header_name_uses_alias(tmp_path):
    window = ModernChatWindow(_config(tmp_path), CID)
    assert window.character_name == ALIAS
    assert window.brand_label.text() == f"{ALIAS} AI"


# ------------------------------------------------------------ 头像
def test_classic_avatar_falls_back_to_alias_initial(tmp_path):
    """无桌宠画面时：回退**显示名**首字母（别名字符），不是目录名首字母。"""
    window = ClassicChatWindow(_config(tmp_path), CID)
    assert window.avatar_label.text() == ALIAS[:1]
    assert window.avatar_label.pixmap() is None or window.avatar_label.pixmap().isNull()


def test_modern_avatar_falls_back_to_alias_initial(tmp_path):
    window = ModernChatWindow(_config(tmp_path), CID)
    assert window.avatar_label.text() == ALIAS[:1]


def test_classic_avatar_uses_pet_live_image(tmp_path):
    """有桌宠时：标题栏头像吃实时画面（icon_pixmap），不吃字母。"""
    window = ClassicChatWindow(_config(tmp_path), CID)
    stub_pet = SimpleNamespace(icon_pixmap=lambda size=64: _live_pixmap())
    window.set_pet_window(stub_pet)
    window.refresh_character_identity()
    pm = window.avatar_label.pixmap()
    assert pm is not None and not pm.isNull(), "桌宠在屏时头像必须是实时画面"
    assert window.avatar_label.text() == ""


def test_classic_avatar_initial_without_alias(tmp_path):
    cfg = _config(tmp_path, alias="")
    window = ClassicChatWindow(cfg, CID)
    expected = cfg.character_display_name(CID)[:1].upper() or "宠"
    assert window.avatar_label.text() == expected


# ------------------------------------------------------------ 改名即时刷新
def test_refresh_character_identity_updates_open_classic_window(tmp_path):
    """改名后已打开的经典窗：重读别名刷新标题与头像，无需关窗重开。"""
    cfg = _config(tmp_path, alias="")
    window = ClassicChatWindow(cfg, CID)
    before = window.title_label.text()
    assert ALIAS not in before
    cfg.set_character_alias(CID, ALIAS)
    window.refresh_character_identity()
    assert window.title_label.text() == f"{ALIAS} · AI 对话"
    assert window.avatar_label.text() == ALIAS[:1]


def test_refresh_character_identity_updates_open_modern_window(tmp_path):
    cfg = _config(tmp_path, alias="")
    window = ModernChatWindow(cfg, CID)
    cfg.set_character_alias(CID, ALIAS)
    window.refresh_character_identity()
    assert window.character_name == ALIAS
    assert window.brand_label.text() == f"{ALIAS} AI"


def test_legacy_rename_fires_callback(tmp_path, monkeypatch):
    """legacy 改名入口在 set_character_alias 之后必须通知（on_character_renamed）。"""
    from pet.library import MovieLibrary
    from pet.window import PetWindow

    cfg = _config(tmp_path, alias="")
    cfg.set("character", CID)
    win = PetWindow(MovieLibrary(character_id=CID), cfg)
    calls: list[str] = []
    win.on_character_renamed = lambda: calls.append("renamed")

    class _FakeDialog:
        @staticmethod
        def getText(*args, **kwargs):
            return ALIAS, True

    monkeypatch.setattr("pet.window.QInputDialog", _FakeDialog)
    win.rename_character()
    assert calls == ["renamed"], "改名后必须广播（供已打开的聊天窗刷新）"
    assert cfg.character_alias(CID) == ALIAS
