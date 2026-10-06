"""Small Core balance settings: explicit, masked and nonblocking OS credential writes."""

from __future__ import annotations

import json
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop
from PySide6.QtWidgets import QApplication, QLineEdit

from tests.test_core_balance_config import MemorySecrets, config


def pump(app, predicate):
    deadline = time.monotonic() + 20
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    assert predicate()


def test_balance_widget_is_masked_explicit_and_never_reads_secrets_on_open(tmp_path, monkeypatch):
    from pet.settings_balance import BalanceSettingsWidget

    app = QApplication.instance() or QApplication([])
    backend = MemorySecrets()
    read_secret = backend.get_password
    monkeypatch.setattr(backend, "get_password", lambda *_: pytest.fail("opening UI must not acquire secrets"))
    cfg = config(tmp_path, monkeypatch)
    widget = BalanceSettingsWidget(cfg, backend=backend)
    try:
        widget.show()
        app.processEvents()
        assert widget.secret_edit.echoMode() == QLineEdit.EchoMode.Password
        assert widget.secret_edit.accessibleName() and widget.endpoint_edit.accessibleName()
        monkeypatch.setattr(backend, "get_password", read_secret)
        widget.secret_edit.setText("generated-ui-only")
        assert not backend.items
        widget.save_button.click()
        pump(app, lambda: not widget.busy)
        assert "已保存" in widget.status_label.text()
        assert widget.secret_edit.text() == ""
        assert "generated-ui-only" not in cfg.path.read_text(encoding="utf-8")
    finally:
        widget.deleteLater()
        app.processEvents()


def test_balance_cas_error_retains_draft_and_does_not_claim_saved(tmp_path, monkeypatch):
    from pet.balance_config import BalanceConfiguration
    from pet.settings_balance import BalanceSettingsWidget

    app = QApplication.instance() or QApplication([])
    cfg = config(tmp_path, monkeypatch)
    backend = MemorySecrets()
    widget = BalanceSettingsWidget(cfg, backend=backend)
    latest = BalanceConfiguration(cfg, backend=backend)
    latest.save("https://api.deepseek.com", "generated-other", expected_revision=latest.revision)
    try:
        widget.secret_edit.setText("generated-stale")
        widget.save_button.click()
        pump(app, lambda: not widget.busy)
        assert "configuration_changed" in widget.status_label.text()
        assert "已保存" not in widget.status_label.text()
        assert widget.secret_edit.text() == "generated-stale"
        assert [json.loads(v)["secret"] for v in backend.items.values()] == ["generated-other"]
    finally:
        widget.deleteLater()
        app.processEvents()


def test_balance_close_during_write_retains_job_and_real_exit_evidence(tmp_path, monkeypatch):
    from pet.async_exit import application_exit_gate
    from pet.settings_balance import BalanceSettingsWidget

    app = QApplication.instance() or QApplication([])
    cfg = config(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    backend = MemorySecrets()

    def save(service, username, secret):
        entered.set()
        assert release.wait(20)
        backend.items[(service, username)] = secret

    monkeypatch.setattr(backend, "set_password", save)
    widget = BalanceSettingsWidget(cfg, backend=backend)
    widget.secret_edit.setText("generated-delayed")
    widget.save_button.click()
    job = widget.pending_job
    try:
        pump(app, entered.is_set)
        gate = application_exit_gate(app)
        assert any(b["owner"] == "core.balance" for b in gate.blockers)
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not job.ready()
        release.set()
        pump(app, lambda: job.ready())
        pump(app, lambda: not any(b["owner"] == "core.balance" for b in gate.blockers))
        assert [json.loads(v)["secret"] for v in backend.items.values()] == ["generated-delayed"]
    finally:
        release.set()
        pump(app, job.ready)


@pytest.mark.parametrize("width", [420, 800])
def test_balance_fields_fit_general_page_content_and_have_keyboard_path(tmp_path, monkeypatch, width):
    from pet.settings_balance import BalanceSettingsWidget

    app = QApplication.instance() or QApplication([])
    cfg = config(tmp_path, monkeypatch)
    widget = BalanceSettingsWidget(cfg, backend=MemorySecrets())
    try:
        widget.resize(width, 360)
        widget.show()
        app.processEvents()
        for field in (widget.endpoint_edit, widget.secret_edit, widget.save_button):
            assert field.isVisible()
            assert field.mapTo(widget, field.rect().bottomRight()).x() < widget.width()
        assert widget.endpoint_edit.nextInFocusChain() is widget.secret_edit
    finally:
        widget.deleteLater()
        app.processEvents()


def test_general_mount_keeps_balance_independent_of_ai(tmp_path, monkeypatch):
    from pet.modern_settings_dialog import ModernSettingsDialog, SettingRow

    app = QApplication.instance() or QApplication([])
    from pet import feature_distribution

    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    cfg = config(tmp_path, monkeypatch)
    dialog = ModernSettingsDialog(cfg, include_ai=False)
    try:
        assert dialog.findChild(SettingRow, "settingRow_balance_credentials") is not None
        assert dialog.balance_refresh_spin is not None
        assert dialog.balance_settings_widget is not None
    finally:
        for manager in dialog.feature_managers.values():
            manager.close()
        dialog.deleteLater()
        app.processEvents()
