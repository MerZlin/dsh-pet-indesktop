from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea

from petcore.config import Config
from petcore.ui.settings.base import page_search_texts, reveal_text
from petcore.ui.settings.dialog import SettingsDialog
from petcore.ui.settings.page_api import ApiSettingsPage


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _FakeHost:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = Config(root)


def _page(tmp_path: Path, monkeypatch) -> ApiSettingsPage:
    monkeypatch.setitem(sys.modules, "keyring", None)
    return ApiSettingsPage(_FakeHost(tmp_path))


def test_api_page_has_services_and_local_guide_tabs(
    app, tmp_path: Path, monkeypatch
) -> None:
    page = _page(tmp_path, monkeypatch)
    try:
        tabs = page.tab_container
        assert tabs is not None
        assert tabs.keys() == ("services", "local_guide")
        assert tabs.labels() == ("服务配置", "本地模型指南")
        assert tabs.currentKey() == "services"
        assert page.dirty() is False
    finally:
        page.dispose()
        page.deleteLater()
        app.processEvents()


def test_local_guide_contains_real_paths_and_launch_instructions(
    app, tmp_path: Path, monkeypatch
) -> None:
    page = _page(tmp_path, monkeypatch)
    try:
        assert page.local_guide_edit.isReadOnly() is True
        text = page.local_guide_edit.toPlainText()
        assert str(tmp_path) in text
        assert "api.json" in text
        assert "llama-server" in text
        assert "--reasoning off" in text
        assert "127.0.0.1:9931" in text
        assert "Ollama" in text
        assert "LM Studio" in text
        assert "vLLM" in text
        assert "GPT-SoVITS" in text
        assert "api_v2.py" in text
        assert "127.0.0.1:9880" in text
        assert "API 与账户" in text
        assert "AI 对话" in text
        assert "自定义 / 本地部署 TTS" in text
    finally:
        page.dispose()
        page.deleteLater()
        app.processEvents()


def test_copy_local_guide_writes_clipboard_and_keeps_page_clean(
    app, tmp_path: Path, monkeypatch
) -> None:
    page = _page(tmp_path, monkeypatch)
    try:
        QApplication.clipboard().clear()
        page.copy_local_guide_button.click()
        app.processEvents()
        copied = QApplication.clipboard().text()
        assert copied == page.local_guide_edit.toPlainText()
        assert "llama-server" in copied
        assert "已复制" in page.local_guide_status.text()
        assert str(len(copied)) in page.local_guide_status.text()
        assert page.dirty() is False
        assert not (tmp_path / "api.json").exists()
        assert not (tmp_path / "config.json").exists()
    finally:
        page.dispose()
        page.deleteLater()
        app.processEvents()


def test_search_reveals_local_guide_tab(
    app, tmp_path: Path, monkeypatch
) -> None:
    page = _page(tmp_path, monkeypatch)
    try:
        texts = page_search_texts(page)
        assert any("本地模型" in item for item in texts)
        assert any("llama.cpp" in item for item in texts)

        assert reveal_text(page, "本地模型指南") is True
        app.processEvents()
        assert page.tab_container is not None
        assert page.tab_container.currentKey() == "local_guide"

        page.tab_container.setCurrentKey("services")
        app.processEvents()
        assert reveal_text(page, "GGUF") is True
        app.processEvents()
        assert page.tab_container.currentKey() == "local_guide"
    finally:
        page.dispose()
        page.deleteLater()
        app.processEvents()


def test_local_guide_tab_fits_compact_720px_dialog(
    app, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_petcore_settings as fixtures

    dialog = SettingsDialog(fixtures.FakeHost(tmp_path), initial_page="api")
    try:
        dialog.resize(720, 500)
        dialog.show()
        app.processEvents()

        api = dialog.stack.currentWidget()
        assert isinstance(api, ApiSettingsPage)
        assert api.tab_container is not None

        api.tab_container.setCurrentKey("local_guide")
        app.processEvents()

        scroll = api.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0
        assert api.local_guide_edit.isVisibleTo(dialog)
        assert api.copy_local_guide_button.isVisibleTo(dialog)
        btn_right = api.copy_local_guide_button.mapTo(
            dialog, api.copy_local_guide_button.rect().topRight()
        ).x()
        edit_right = api.local_guide_edit.mapTo(
            dialog, api.local_guide_edit.rect().topRight()
        ).x()
        assert btn_right <= dialog.width()
        assert edit_right <= dialog.width()

        api.tab_container.setCurrentKey("services")
        app.processEvents()
        assert api.save_button.isEnabled()
    finally:
        dialog.close()
        app.processEvents()
