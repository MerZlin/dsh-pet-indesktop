from __future__ import annotations

import pytest

from petcore.ui.menu_actions import core_items
from petcore.ui.settings.coverage import (
    CORE_MENU_SETTINGS_COVERAGE,
    SETTINGS_EXEMPT,
    missing_settings_entries,
)
from petcore.ui.settings.dialog import _PAGE_KEYS


class StubWindow:
    state = type("State", (), {"scale": 0.72})()

    def toggle_hidden(self):
        return None

    def toggle_always_on_top(self):
        return None

    def toggle_click_through(self):
        return None

    def toggle_edge_probe(self):
        return None

    def return_corner(self):
        return None

    def move_to_primary(self):
        return None

    def reload_mods(self):
        return None

    def open_data_dir(self):
        return None

    def open_settings(self):
        return None

    def quit_from_menu(self):
        return None

    def show_about(self):
        return None

    def quit_app(self):
        return None


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def test_every_core_menu_item_has_a_settings_home():
    items = core_items(StubWindow())
    assert missing_settings_entries(items) == []
    assert set(CORE_MENU_SETTINGS_COVERAGE) | SETTINGS_EXEMPT == set(items)


def test_coverage_points_at_real_pages_and_rows():
    pages = set(_PAGE_KEYS.values())
    for menu_id, (page, row) in CORE_MENU_SETTINGS_COVERAGE.items():
        assert page in pages, menu_id
        assert row.strip(), menu_id


def test_coverage_rows_are_reachable_by_search(app, tmp_path):
    """落点必须真的能用搜索搜到（AUD-30）：只断言"字符串非空"抓不到写错页。"""
    from pathlib import Path

    from petcore.ui.settings.base import page_search_texts
    from petcore.ui.settings.dialog import SettingsDialog
    from tests.test_petcore_settings import _host

    dialog = SettingsDialog(_host(Path(tmp_path)))
    try:
        by_key = {entry.key: entry.page for entry in dialog._pages}  # noqa: SLF001
        for menu_id, (page_key, row) in CORE_MENU_SETTINGS_COVERAGE.items():
            page = by_key.get(page_key)
            assert page is not None, menu_id
            haystack = " ".join(page_search_texts(page))
            assert row in haystack, f"{menu_id}: 落点「{row}」不在 {page_key} 页"
    finally:
        dialog.close()


def test_search_matches_button_text(app, tmp_path):
    """按钮文案也要能搜到（AUD-29）：菜单写着「生成子宠」，设置里就该搜得到。"""
    from pathlib import Path

    from petcore.ui.settings.dialog import SettingsDialog
    from tests.test_petcore_settings import _host

    dialog = SettingsDialog(_host(Path(tmp_path)))
    try:
        dialog._apply_filter("生成子宠")  # noqa: SLF001
        visible = [
            dialog.nav.item(i).text()
            for i in range(dialog.nav.count())
            if not dialog.nav.item(i).isHidden()
        ]
        assert "多宠" in visible, visible
    finally:
        dialog.close()
