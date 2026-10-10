"""Core API service window reuses the shared Qt layout/theme contract."""

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea

from pet.config import Config
from pet.settings_theme_qss import _settings_stylesheet
from tests.screen_fakes import MemoryVault


@pytest.mark.parametrize("width", [720, 1100])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_core_api_window_uses_shared_theme_and_no_horizontal_clipping(tmp_path, monkeypatch, width, theme):
    from pet.settings_api import _OPEN, open_api_settings

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: MemoryVault())
    cfg = Config(base=tmp_path)
    cfg.set("menu_theme", theme)
    assert cfg.save()
    open_api_settings(cfg)
    dialog = _OPEN[str(cfg.path)]
    try:
        dialog.resize(width, 700)
        app.processEvents()
        assert dialog.styleSheet() == _settings_stylesheet(theme)
        assert dialog.property("settingsDark") is (theme == "dark")
        scroll = dialog.findChild(QScrollArea)
        widget = scroll.widget()
        assert scroll.objectName() == "settingsScroll"
        assert widget.status.objectName() == "settingHint"
        assert scroll.horizontalScrollBar().maximum() == 0
        assert not widget.vision_url_edit.isVisible()
        for control in (widget.url_edit, widget.secret_edit, widget.save_button, widget.vision_secret_edit, widget.test_button):
            assert control.isVisible()
            assert control.mapTo(widget, control.rect().bottomRight()).x() < widget.width()
            assert control.accessibleName()
    finally:
        dialog.reject()
        app.processEvents()
