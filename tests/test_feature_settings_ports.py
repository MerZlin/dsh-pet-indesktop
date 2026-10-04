"""Real settings widgets with bounded host ports, no Config handed to feature UI."""

import json
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from pet.config import Config
from tests.screen_fakes import MemoryVault


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    from features.screen_understanding.host.contribution_settings import ScreenContributionSettings
    from pet.screen_understanding.host_binding import bind_settings

    app = QApplication.instance() or QApplication([])
    backend = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    cfg = Config(tmp_path / "config.json")
    cfg.data["proactive_screen"]["runtime_mode"] = "in_process"
    cfg.data["proactive_screen"]["change_threshold"] = 13
    cfg.save()
    context = bind_settings(cfg)
    widget = ScreenContributionSettings(context)
    yield cfg, context, widget, backend
    widget.dispose()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)


def test_feature_ui_receives_only_its_ports(mounted):
    cfg, context, widget, backend = mounted
    for obj in (context, context.vision, context.preferences, context.memory):
        assert not any(hasattr(obj, name) for name in ("cfg", "data", "dir", "path"))
    assert not hasattr(widget.strategy, "config")
    assert not backend.items  # mounting is not credential access or a request
    snapshot = context.preferences.read()
    snapshot["whitelist"].append("not-saved.exe")
    assert "not-saved.exe" not in context.preferences.read()["whitelist"]
    with pytest.raises(PermissionError, match="preference_not_granted"):
        context.preferences.stage({"chat": {"enabled": True}})
    assert not backend.items


def test_strategy_stage_preserves_unowned_fields_and_password_draft(mounted):
    cfg, context, widget, backend = mounted
    if sys.platform != "win32":
        assert widget.strategy is None
        return
    before = cfg.path.read_bytes()
    widget.vision.key_edit.setText("TEST-NOT-SAVED")
    widget.strategy.pro_enabled_check.setChecked(True)
    widget.strategy.pro_whitelist_edit.setPlainText("editor.exe")
    widget.save_strategy()
    assert cfg.get("proactive_screen")["enabled"] is True
    assert cfg.path.read_bytes() == before  # outer Apply still owns flush
    assert context.preferences.flush()
    saved = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert saved["proactive_screen"]["runtime_mode"] == "in_process"
    assert saved["proactive_screen"]["change_threshold"] == 13
    assert "TEST-NOT-SAVED" not in cfg.path.read_text(encoding="utf-8")
    assert widget.vision.key_edit.text() == "TEST-NOT-SAVED"
    assert not backend.items


def test_confirm_save_keeps_draft_if_host_flush_fails(mounted, monkeypatch):
    cfg, context, widget, backend = mounted
    if widget.strategy is None:
        return
    widget.strategy.pro_enabled_check.setChecked(True)
    monkeypatch.setattr(cfg, "save", lambda: False)
    assert widget.dirty()
    assert widget.confirm_save() is False
    assert widget.dirty()
    monkeypatch.setattr(cfg, "save", lambda: True)
    assert widget.confirm_save() is True
    assert not widget.dirty()


def test_memory_clear_uses_only_bound_document(mounted, monkeypatch):
    cfg, context, widget, backend = mounted
    if widget.strategy is None:
        return
    context.memory.write({"entries": [{"time": 1, "process": "editor", "activity": "coding"}]})
    unrelated = cfg.dir / "unrelated.json"
    unrelated.write_text("unchanged", encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    widget.strategy._on_pro_clear_memory()
    assert context.memory.read().get("entries", []) == []
    assert unrelated.read_text(encoding="utf-8") == "unchanged"
    timer = widget.strategy.findChild(QTimer)
    timer.start(3000)
    widget.vision.key_edit.setText("TEST-DISCARD")
    widget.dispose()
    assert not timer.isActive()
    assert widget.vision.key_edit.text() == ""


def test_explicit_discard_restores_editor_without_writing_or_secret_retention(mounted):
    cfg, context, widget, backend = mounted
    before = cfg.path.read_bytes()
    original = widget.vision.url.text()
    widget.vision.url.setText("https://test-generated.invalid/changed")
    widget.vision.key_edit.setText("GENERATED-NOT-SAVED")
    if widget.strategy:
        widget.strategy.pro_enabled_check.setChecked(not widget.strategy.pro_enabled_check.isChecked())
    assert widget.dirty()
    assert widget.discard_changes()
    assert not widget.dirty()
    assert widget.vision.url.text() == original
    assert not widget.vision.key_edit.text()
    assert cfg.path.read_bytes() == before and not backend.items
