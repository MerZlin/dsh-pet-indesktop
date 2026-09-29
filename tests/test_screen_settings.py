import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from pet.config import Config
from pet.screen_understanding.config import VisionConfigService
from tests.screen_fakes import MemoryVault


@pytest.fixture
def screen_ui(tmp_path, monkeypatch):
    from pet.screen_understanding.settings import ScreenSettingsPage

    app = QApplication.instance() or QApplication([])
    backend = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    cfg = Config(tmp_path / "config.json")
    cfg.data["chat"]["enabled"] = False
    cfg.save()
    page = ScreenSettingsPage(cfg)
    yield page, cfg, backend, app
    page.close()
    page.deleteLater()
    app.processEvents()


def test_open_editor_without_touching_credentials(screen_ui):
    page, cfg, backend, _ = screen_ui
    assert not backend.items
    assert VisionConfigService(cfg).settings().profiles == {}
    assert page.key_edit.echoMode() == page.key_edit.EchoMode.Password
    assert page.status.text()


def test_save_manual_profile_independently(screen_ui):
    page, cfg, backend, _ = screen_ui
    page.mode.setCurrentData("manual")
    page.profile_id.setText("my-vision")
    page.url.setText("https://vision.example")
    page.model.setText("vision-model")
    page.key_edit.setText("TEST-VISION-KEY")
    page.save_button.click()
    result = VisionConfigService(cfg).resolve("manual")
    assert result.ready
    assert result.request.api_key == "TEST-VISION-KEY"
    assert not VisionConfigService(cfg).resolve("automatic").ready
    assert not page.key_edit.text()
    assert "TEST-VISION-KEY" not in cfg.path.read_text(encoding="utf-8")
    assert not cfg.data["chat"]["enabled"]


def test_cancel_migration_does_not_write(screen_ui, monkeypatch):
    page, cfg, backend, _ = screen_ui
    before = cfg.path.read_bytes()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    page.migrate_button.click()
    assert cfg.path.read_bytes() == before
    assert not backend.items


def test_storage_failure_stays_pending(screen_ui):
    page, cfg, backend, _ = screen_ui
    backend.fail = True
    page.profile_id.setText("manual")
    page.url.setText("https://vision.example")
    page.model.setText("model")
    page.key_edit.setText("TEST-VISION-KEY")
    page.save_button.click()
    assert not VisionConfigService(cfg).settings().profiles
    assert "TEST-VISION-KEY" not in page.status.text()
    assert "失败" in page.status.text()


@pytest.mark.parametrize("include_ai", [False, True])
def test_modern_settings_owns_vision_in_automation(screen_ui, include_ai):
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.settings_widgets import SettingRow

    _, cfg, _, app = screen_ui
    dlg = ModernSettingsDialog(cfg, include_ai=include_ai)
    try:
        assert not hasattr(dlg.screen_settings_page.service, "cfg")
        assert dlg.screen_settings_page.service.settings() == VisionConfigService(cfg).settings()
        assert "look_screen" in dlg.menu_available_actions
        assert dlg.findChild(SettingRow, "settingRow_screen_model") is not None
        assert dlg.findChild(SettingRow, "settingRow_vision_model") is None
        dlg._search_settings("vision_model")
        assert dlg.sidebar.currentRow() >= 0
    finally:
        dlg.deleteLater()
        app.processEvents()


def test_preview_explains_both_effective_profiles_without_credentials(screen_ui, monkeypatch):
    page, cfg, backend, _ = screen_ui
    cfg.data["chat"]["providers"] = {
        "p": {
            "base_url": "https://vision.example",
            "model": "vision-model",
            "timeout": 45,
            "temperature": 0.4,
            "max_tokens": 999,
            "verify_ssl": False,
            "api_key_ref": "provider/p",
        }
    }
    cfg.data["chat"]["active_provider"] = "p"
    cfg.save()
    backend.items[("dsh-pet-standalone", "provider/p")] = "TEST-SECRET"
    captured = []

    def question(parent, title, text, *args):
        captured.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    page.migrate_button.click()
    assert len(captured) == 1
    for value in ("自动", "手动", "/v1/chat/completions", "45", "0.4", "999", "TLS", "可复制"):
        assert value in captured[0]
    assert "TEST-SECRET" not in captured[0]
    assert not page.service.settings().profiles


def test_corrupt_namespace_disables_editor_and_preserves_core_save(screen_ui):
    import json

    page, cfg, _, _ = screen_ui
    doc = json.loads(cfg.path.read_text(encoding="utf-8"))
    doc.setdefault("plugins", {})["official.screen-understanding"] = []
    cfg.path.write_text(json.dumps(doc), encoding="utf-8")
    before = cfg.path.read_bytes()
    page.reload()
    assert not page.save_button.isEnabled()
    assert not page.migrate_button.isEnabled()
    assert not cfg.save()
    assert cfg.path.read_bytes() == before


@pytest.mark.parametrize("width", [720, 1100])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_independent_editor_layout_keyboard_and_no_side_effects(screen_ui, width, theme):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea

    from pet.modern_settings_dialog import ModernSettingsDialog

    _, cfg, backend, app = screen_ui
    before = cfg.path.read_bytes()
    dlg = ModernSettingsDialog(cfg, include_ai=False)
    try:
        dlg.menu_theme_select.setCurrentData(theme)
        dlg.resize(width, 700)
        dlg.show()
        dlg._search_settings("screen_model")
        app.processEvents()
        page = dlg.pages.currentWidget()
        scroll = page.findChild(QScrollArea, "settingsScroll")
        editor = dlg.screen_settings_page
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0
        for control in (editor.model, editor.key_edit, editor.save_button, editor.migrate_button):
            scroll.ensureWidgetVisible(control)
            app.processEvents()
            x = control.mapTo(scroll.viewport(), QPoint(0, 0)).x()
            assert control.isVisible()
            assert x >= 0
            assert x + control.width() <= scroll.viewport().width()
            assert control.accessibleName()
        scroll.ensureWidgetVisible(editor.model)
        editor.model.setFocus()
        QTest.keyClicks(editor.model, "test-vision")
        assert editor.model.text() == "test-vision"
        QTest.keyClick(editor.model, Qt.Key.Key_Tab)
        assert app.focusWidget() is not editor.model
        assert not backend.items
        assert cfg.path.read_bytes() == before
    finally:
        dlg.hide()
        dlg.deleteLater()
        app.processEvents()
