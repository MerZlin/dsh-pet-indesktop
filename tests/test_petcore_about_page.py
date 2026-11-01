from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import CharacterSpec
from petcore.ui.app import CoreHost
from petcore.update import UpdateResult
from petcore.ui.menu_actions import core_items
from petcore.ui.settings.page_about import AboutPage
from petcore.ui.settings.page_about_thanks import ThanksPanel
from petcore.ui.settings.page_thanks import ThanksPage


class FakeHost:
    def __init__(self, root: Path):
        self.root = root
        self.config = Config(root)
        self.load_result = None
        self.mod_dirs = ()
        self.pets = SimpleNamespace(each=lambda: [])
        self._character = CharacterSpec(
            id="fallback", name="Fallback", root=root, media_kind="none", animations=()
        )

    def characters(self):
        return [self._character]

    def open_data_dir(self):
        return None

    def reload_mods(self):
        return None


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_about_is_a_settings_page_not_a_window(app, tmp_path: Path) -> None:
    import petcore.ui

    assert not (Path(petcore.ui.__file__).parent / "about.py").exists()
    assert not hasattr(CoreHost, "show_about")
    page = AboutPage(FakeHost(tmp_path))
    try:
        assert page.title == "关于与诊断"
        assert page.tab_container is not None
        assert page.tab_container.labels() == ("版本与诊断",)
        # 致谢已提成侧栏独立页（ThanksPage 才是它的宿主），关于页不再内嵌它。
        assert not hasattr(page, "thanks_panel")
        assert "关于与诊断" in page.snapshot_view.toPlainText() or "version" in page.snapshot_view.toPlainText()
        assert '"pets"' in page.snapshot_view.toPlainText()
        page._copy_report()
        assert "version" in QApplication.clipboard().text()
    finally:
        page.deleteLater()

    thanks = ThanksPage(FakeHost(tmp_path))
    try:
        assert thanks.title == "致谢"
        assert isinstance(thanks.panel, ThanksPanel)
    finally:
        thanks.dispose()
        thanks.deleteLater()


def test_about_menu_item_is_gone() -> None:
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

        def open_settings(self, initial_page=None):
            return None

        def quit_app(self):
            return None

    items = core_items(StubWindow())
    assert "about" not in items


def test_about_update_download_can_be_cancelled(app, tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    host.cancelled = False
    host.update_status = lambda: UpdateResult(False, "downloading", "正在下载更新…")
    host.cancel_update = lambda: setattr(host, "cancelled", True)
    page = AboutPage(host)
    try:
        assert page.cancel_update_button.isEnabled() is True
        page.cancel_update_button.click()
        assert host.cancelled is True
    finally:
        page.deleteLater()
