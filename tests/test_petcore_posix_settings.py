from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def posix_capabilities(monkeypatch):
    from petcore.platform import desktop
    from petcore.ui.settings import pages_core
    original = desktop.capabilities
    capabilities = lambda **_: original(platform="linux", qpa="xcb", environ={"DISPLAY": ":0"})
    monkeypatch.setattr(desktop, "capabilities", capabilities)
    monkeypatch.setattr(pages_core, "desktop_capabilities", capabilities)


def test_unsupported_metrics_are_disabled_without_losing_saved_selection(app, tmp_path, posix_capabilities):
    from petcore.config import Config
    from petcore.ui.settings.pages_core import AppearancePage
    config = Config(tmp_path, {"island_metrics": ["cpu", "gpu", "fps"]})
    page = AppearancePage(SimpleNamespace(config=config, root=tmp_path, window=None))
    try:
        for key in ("gpu", "fps"):
            assert page.island_metric_boxes[key].isChecked()
            assert not page.island_metric_boxes[key].isEnabled()
        assert not page.fps_permission_button.isEnabled()
        page.island_metric_boxes["mem"].setChecked(True)
        assert config.get("island_metrics") == ["cpu", "mem", "gpu", "fps"]
    finally:
        page.close()


def test_unsupported_voice_controls_preserve_cross_platform_configuration(app, tmp_path, posix_capabilities, monkeypatch):
    from petcore.config import Config
    from petcore.media import audio_output
    from petcore.ui.settings.page_voice import VoicePage
    monkeypatch.setattr(audio_output, "list_outputs", lambda: [])
    config = Config(tmp_path, {"asr_enabled": True, "asr_device": 9, "asr_device_name": "原有设备", "speech_backend": "sapi"})
    page = VoicePage(SimpleNamespace(config=config, root=tmp_path))
    try:
        assert not page.asr_enabled.isEnabled()
        assert not page.asr_device.isEnabled()
        assert not page.preview_button.isEnabled()
        sapi = page.backend_combo.findData("sapi")
        assert not page.backend_combo.model().item(sapi).isEnabled()
        page.volume_slider.setValue(65)
        assert config.get("asr_device") == 9
        assert config.get("asr_device_name") == "原有设备"
        assert config.get("asr_enabled") is True
        assert config.get("speech_backend") == "sapi"
    finally:
        page.close()


def test_chat_record_buttons_follow_microphone_capability(app, posix_capabilities):
    from petcore.ui.chat.widgets import ChatComposer
    from petcore.ui.chat.quick import QuickChatBubble
    widgets = [ChatComposer(), QuickChatBubble()]
    try:
        for widget in widgets:
            assert not widget.record_button.isEnabled()
            assert "Windows" in widget.record_button.toolTip()
    finally:
        for widget in widgets:
            widget.close()
