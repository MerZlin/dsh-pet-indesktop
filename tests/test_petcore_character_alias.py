"""角色重命名（别名）：配置清洗、显示层、右键菜单入口、设置页入口与回滚。

别名是「用户级偏好」——同一角色 id 在所有宠上同名，所以它必须是全局键；
显示名统一在 `CoreHost._display_spec()` 收口，菜单/下拉都吃这一份。
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QWidget

from petcore.config import Config, ScopedConfig
from petcore.contracts.content import CharacterSpec


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _spec(character_id: str, name: str, root: Path) -> CharacterSpec:
    return CharacterSpec(
        id=character_id,
        name=name,
        root=root,
        media_kind="none",
        animations=(),
        owner="core",
    )


# ------------------------------------------------------------------ 配置层


def test_alias_key_is_a_global_default() -> None:
    from petcore.config import DEFAULTS, GLOBAL_CONFIG_KEYS

    assert DEFAULTS["character_aliases"] == {}
    # 全局键：子宠设置页写的就是主配置那一份。
    assert "character_aliases" in GLOBAL_CONFIG_KEYS


def test_alias_defaults_to_empty(tmp_path: Path) -> None:
    config = Config(tmp_path)
    assert config.character_alias("shenshen") == ""
    assert config.get("character_aliases") == {}


def test_set_and_read_alias_persists(tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set_character_alias("shenshen", "  小鲸  ")
    assert config.character_alias("shenshen") == "小鲸"
    assert config.get("character_aliases") == {"shenshen": "小鲸"}
    config.save()
    assert Config.load(tmp_path).character_alias("shenshen") == "小鲸"


def test_alias_is_truncated_to_24_characters(tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set_character_alias("shenshen", "鲸" * 40)
    alias = config.character_alias("shenshen")
    assert alias == "鲸" * 24
    assert len(alias) == 24, "按字符截断，中文名字不能被切成半个字"


def test_empty_alias_restores_the_default(tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set_character_alias("shenshen", "小鲸")
    config.set_character_alias("shenshen", "   ")
    assert config.character_alias("shenshen") == ""
    assert "shenshen" not in config.get("character_aliases")


def test_bad_alias_data_is_cleaned(tmp_path: Path) -> None:
    # 不是 dict：当空表，而且不炸。
    assert Config(tmp_path, {"character_aliases": "不是字典"}).get(
        "character_aliases"
    ) == {}
    assert Config(tmp_path, {"character_aliases": ["a", "b"]}).get(
        "character_aliases"
    ) == {}
    cleaned = Config(
        tmp_path,
        {
            "character_aliases": {
                "a": "名字",
                "b": 42,
                "": "空键",
                "c": "   ",
                "d": "两行\n名字",
                "e": "y" * 40,
                "f": "正常",
            }
        },
    ).get("character_aliases")
    assert cleaned == {"a": "名字", "e": "y" * 24, "f": "正常"}


def test_alias_writes_do_not_touch_other_keys(tmp_path: Path) -> None:
    config = Config(tmp_path, {"character": "other", "scale": 1.5})
    config.set_character_alias("shenshen", "小鲸")
    assert config.get("character") == "other"
    assert config.get("scale") == 1.5
    assert config.get("schema") == Config(tmp_path).get("schema")


def test_alias_ignores_an_empty_character_id(tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set_character_alias("", "无名")
    config.set_character_alias("   ", "无名")
    assert config.get("character_aliases") == {}


def test_alias_key_is_shared_by_child_pets(tmp_path: Path) -> None:
    """子宠设置页写的别名要落在主配置，主宠也读得到（同一角色同名）。"""
    global_config = Config(tmp_path)
    child = Config(tmp_path, {}, filename="config-slot-1.json")
    scoped = ScopedConfig(global_config, child)
    scoped.set_character_alias("shenshen", "小鲸")
    assert child.character_alias("shenshen") == "", "不许写进子宠自己的文件"
    assert global_config.character_alias("shenshen") == "小鲸"
    assert scoped.character_alias("shenshen") == "小鲸"


# ------------------------------------------------------------------ 显示层


class _Result:
    diagnostics: tuple = ()


@pytest.fixture
def host(app, tmp_path: Path, monkeypatch):
    from petcore.ui.app import CoreHost

    specs = [_spec("shenshen", "Shenshen", tmp_path), _spec("alice", "Alice", tmp_path)]
    # 只跳过真实素材扫描：CoreHost / PetManager 都是真的（同 click_listeners）。
    monkeypatch.setattr("petcore.ui.app._collect", lambda **_kw: (specs, _Result()))
    config = Config(tmp_path)
    return CoreHost(app, config, tmp_path, ())


def test_display_names_use_the_alias(host) -> None:
    assert [spec.name for spec in host.characters()] == ["Shenshen", "Alice"]
    host.set_character_alias("shenshen", "小鲸")
    assert [spec.name for spec in host.characters()] == ["小鲸", "Alice"]
    assert host.character_for("shenshen").name == "小鲸"
    assert host.character_for("missing").name == "小鲸", "未知 id 仍回落到第一个角色"
    assert host.character_display_name("shenshen") == "小鲸"
    assert host.character_default_name("shenshen") == "Shenshen"
    # 目录层那份 spec 不被改写：别名的撤销/切换不需要重新扫盘。
    assert host._characters[0].name == "Shenshen"  # noqa: SLF001


def test_alias_clears_back_to_the_directory_name(host) -> None:
    host.set_character_alias("alice", "爱丽丝")
    assert host.character_for("alice").name == "爱丽丝"
    host.set_character_alias("alice", "")
    assert host.character_for("alice").name == "Alice"


def test_non_ascii_alias_survives_the_round_trip(host, tmp_path: Path) -> None:
    host.set_character_alias("shenshen", "深海小鲸🐳")
    assert host.character_display_name("shenshen") == "深海小鲸🐳"
    assert Config.load(tmp_path).character_alias("shenshen") == "深海小鲸🐳"


def test_host_refreshes_the_open_settings_page(host) -> None:
    """菜单改名后，开着的设置页下拉要立刻换名字（不必等重开窗口）。"""
    calls: list[str] = []

    class _Page:
        def refresh_characters(self) -> None:
            calls.append("refresh")

    class _Dialog:
        _disposed = False

        def page_for_key(self, key):
            assert key == "general"
            return _Page()

    host.settings_dialog = _Dialog()
    host.set_character_alias("shenshen", "小鲸")
    host.refresh_character_names()
    assert calls == ["refresh"]
    # 没有设置窗时安静跳过。
    host.settings_dialog = None
    host.refresh_character_names()
    assert calls == ["refresh"]


def test_window_available_characters_use_the_alias(host) -> None:
    from petcore.ui.pet_manager import PetManager

    host.set_character_alias("alice", "爱丽丝")
    manager = PetManager(host, host.root, host.config)
    host.pets = manager
    window = manager.create_primary(host.character_for("shenshen"))
    host.window = window
    try:
        assert [spec.name for spec in window.available_characters()] == [
            "Shenshen",
            "爱丽丝",
        ]
    finally:
        window.shutdown()


# ------------------------------------------------------------------ 右键菜单


@pytest.fixture
def window(host):
    from petcore.ui.pet_manager import PetManager

    manager = PetManager(host, host.root, host.config)
    host.pets = manager
    window = manager.create_primary(host.character_for("shenshen"))
    host.window = window
    yield window
    window.shutdown()


def _character_menu(window) -> QMenu:
    from petcore.ui.menu_actions import core_items

    menu = QMenu()
    core_items(window)["character"].populate(menu)
    return menu


def test_character_menu_uses_aliases_and_offers_rename(window) -> None:
    window.host.set_character_alias("alice", "爱丽丝")
    menu = _character_menu(window)
    texts = [action.text() for action in menu.actions()]
    assert texts[:2] == ["Shenshen", "爱丽丝"]
    # 重命名动作挂在子菜单末尾，前面一条分隔线；不新增菜单项 id。
    assert menu.actions()[-1].text() == "重命名当前角色…"
    assert menu.actions()[-2].isSeparator()
    assert menu.actions()[-1].isCheckable() is False


def test_menu_rename_writes_alias_and_reports_it(window, monkeypatch) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: (calls.append((a, k)) or ("小鲸", True))),
    )
    menu = _character_menu(window)
    menu.actions()[-1].trigger()

    assert window.host.config.character_alias("shenshen") == "小鲸"
    assert window.bubble.label.text() == "角色名：小鲸"
    # 输入框预填的是当前显示名（目录名），提示里点名的是角色而不是 id。
    assert calls and calls[0][1]["text"] == "Shenshen"
    assert "Shenshen" in calls[0][0][2]


def test_menu_rename_cancel_changes_nothing(window, monkeypatch) -> None:
    window.host.set_character_alias("shenshen", "小鲸")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("别的名字", False)))
    menu = _character_menu(window)
    menu.actions()[-1].trigger()
    assert window.host.config.character_alias("shenshen") == "小鲸"
    assert "别的名字" not in window.bubble.label.text()


def test_menu_rename_empty_input_restores_the_default(window, monkeypatch) -> None:
    window.host.set_character_alias("shenshen", "小鲸")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("   ", True)))
    menu = _character_menu(window)
    menu.actions()[-1].trigger()
    assert window.host.config.character_alias("shenshen") == ""
    assert window.bubble.label.text() == "角色名：Shenshen"


def test_menu_rename_truncates_and_says_so(window, monkeypatch) -> None:
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("鲸" * 30, True))
    )
    menu = _character_menu(window)
    menu.actions()[-1].trigger()
    assert window.host.config.character_alias("shenshen") == "鲸" * 24
    # 长文案会被气泡折行：按去掉换行的全文断言，否则断在「24 个字」中间就假失败。
    assert "最多 24 个字" in window.bubble.label.text().replace("\n", "")


# ------------------------------------------------------------------ 设置页


class _FakeWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.said: list[str] = []

    def say(self, text, duration_ms=4000, **_kw):
        self.said.append(str(text))
        return True


class _AliasHost:
    """设置页要的最小宿主：真 Config + 两个角色 + 会记气泡的窗口。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = Config(root)
        self.window = _FakeWindow()
        self.features = None
        self.reloaded = False
        self._characters = [_spec("shenshen", "Shenshen", root), _spec("alice", "Alice", root)]

    def characters(self):
        # 与 CoreHost 同构：出口就是「别名 or 目录名」。
        from dataclasses import replace

        return [
            replace(spec, name=self.config.character_alias(spec.id) or spec.name)
            for spec in self._characters
        ]

    def character_for(self, character_id):
        for spec in self._characters:
            if spec.id == character_id:
                return spec
        return self._characters[0]

    def character_default_name(self, character_id):
        return self.character_for(character_id).name

    def set_character_alias(self, character_id, name):
        self.config.set_character_alias(character_id, name)

    def reload_mods(self):
        self.reloaded = True

    def open_data_dir(self):
        return None

    def quit_app(self):
        return None


@pytest.fixture
def page(app, tmp_path: Path):
    from petcore.ui.settings.pages_core import GeneralPage

    host = _AliasHost(tmp_path)
    host.config.set("character", "shenshen")
    page = GeneralPage(host)
    yield page
    page.deleteLater()


def test_general_page_has_a_rename_button(page) -> None:
    assert page.rename_character_button.text() == "重命名…"
    assert page.character_combo.count() == 2
    assert page.character_combo.itemText(0) == "Shenshen"


def test_page_rename_is_dirty_until_saved_then_persists(page, tmp_path: Path, monkeypatch) -> None:
    assert page.dirty() is False
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("小鲸", True)))
    page.rename_character_button.click()

    assert page.dirty() is True, "改完必须让保存按钮亮起来"
    assert page.character_combo.itemText(0) == "小鲸", "下拉项文本要立刻跟上"
    assert page.host.window.said == ["角色名：小鲸"]
    page.draft()
    page.confirm_save()
    assert page.host.config.character_alias("shenshen") == "小鲸"
    page.host.config.save()
    assert Config.load(tmp_path).character_alias("shenshen") == "小鲸"
    assert page.dirty() is False


def test_page_rename_cancel_and_discard_restore_the_snapshot(page, monkeypatch) -> None:
    # 取消输入框：什么都不改。
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("小鲸", False)))
    page.rename_character_button.click()
    assert page.dirty() is False
    assert page.host.config.character_alias("shenshen") == ""

    # 改了但点「取消」：回到打开时的名字（配置 + 下拉文本一起）。
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("小鲸", True)))
    page.rename_character_button.click()
    assert page.dirty() is True
    page.discard_changes()
    assert page.host.config.character_alias("shenshen") == ""
    assert page.character_combo.itemText(0) == "Shenshen"
    assert page.dirty() is False


def test_page_rename_refreshes_from_the_menu(page) -> None:
    """右键菜单改了名（立即落盘），已开着的设置页下拉与基线都要跟上。"""
    page.host.config.set_character_alias("shenshen", "小鲸")
    page.refresh_characters()
    assert page.character_combo.itemText(0) == "小鲸"
    assert page.dirty() is False


def test_page_rename_button_text_is_searchable(page) -> None:
    from petcore.ui.settings.base import page_search_texts

    assert any("重命名" in text for text in page_search_texts(page))


def test_child_pet_settings_page_writes_the_global_alias(app, tmp_path: Path, monkeypatch) -> None:
    """子宠设置页（ScopedConfig）写的别名必须落主配置：同一角色所有宠同名。"""
    from petcore.ui.settings.pages_core import GeneralPage

    host = _AliasHost(tmp_path)
    child = Config(tmp_path, {"character": "alice"}, filename="config-pet-1.json")
    host.config = ScopedConfig(host.config, child)
    host.window.config = child
    page = GeneralPage(host)
    try:
        monkeypatch.setattr(
            QInputDialog, "getText", staticmethod(lambda *a, **k: ("爱丽丝", True))
        )
        page._select_character("alice")  # noqa: SLF001 - 下拉选择的可编程入口
        page.rename_character_button.click()
        assert page.dirty() is True
        page.draft()
        page.confirm_save()
        host.config.save()
        assert child.character_alias("alice") == ""
        assert host.config.global_config.character_alias("alice") == "爱丽丝"
        assert Config.load(tmp_path).character_alias("alice") == "爱丽丝"
        assert page.dirty() is False
    finally:
        page.deleteLater()
