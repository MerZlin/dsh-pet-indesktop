"""Actual Qt/import seams; generated profiles and an in-memory secret backend only."""

from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tests.test_core_uninstall_ui import pump
from tests.test_runtime_credential_import import authorized_fixture
from tests.test_runtime_data_import import fixture_import


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    old = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.setQuitOnLastWindowClosed(old)


def make_ui(importer, qapp):
    from pet.runtime_data_import_ui import DataImportDialog, DataImportRuntime

    runtime = DataImportRuntime(importer, qapp)
    dialog = DataImportDialog(runtime)
    dialog.show()
    return runtime, dialog


def test_dialog_preserves_inherited_qwidget_scroll_method(tmp_path, monkeypatch, qapp):
    _, _, importer = fixture_import(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    try:
        assert callable(dialog.scroll)
    finally:
        dialog.close()
        pump(qapp, lambda: not runtime.busy)


def test_real_preview_shows_source_mapping_and_writes_only_after_confirmation(tmp_path, monkeypatch, qapp):
    from pet import feature_state_io as io

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    threads = []
    write = io.atomic_write

    def observed(path, data):
        threads.append(threading.get_ident())
        return write(path, data)

    monkeypatch.setattr(io, "atomic_write", observed)
    runtime, dialog = make_ui(importer, qapp)
    assert runtime.submit("preflight", source=source)
    pump(qapp, lambda: not runtime.busy)
    assert dialog.plan is not None and dialog.confirm_button.isEnabled()
    text = dialog.detail_label.text().replace("\u200b", "")
    assert str(source) in text and str(layout.data_root) in text
    assert "feature-data/official.ai-chat/sessions-instance-A/dsh/session-A.json" in text
    assert dialog.plan.confirmation_digest in text
    assert not (layout.data_root / "config-instance-A.json").exists()
    dialog.confirm_button.click()
    pump(qapp, lambda: not runtime.busy)
    assert runtime.last_result.status == "completed"
    assert "已完成" in dialog.status_label.text()
    assert not dialog.confirm_button.isEnabled()
    assert threads and threading.get_ident() not in threads
    assert callable(dialog.result)
    assert (layout.data_root / "config-instance-A.json").is_file()
    dialog.close()


def test_secret_reference_transfer_needs_separate_visible_authorization(tmp_path, monkeypatch, qapp):
    layout, source, backend, importer = authorized_fixture(tmp_path, monkeypatch)
    reads = []
    original = backend.get_password
    monkeypatch.setattr(backend, "get_password", lambda *args: reads.append(args) or original(*args))
    runtime, dialog = make_ui(importer, qapp)
    assert runtime.submit("preflight", source=source)
    pump(qapp, lambda: not runtime.busy)
    assert dialog.credential_check.isVisible() and not dialog.credential_check.isChecked()
    assert not dialog.confirm_button.isEnabled()
    assert not reads
    text = dialog.detail_label.text().replace("\u200b", "")
    assert "official.ai-chat" in text and "official.screen-understanding" in text
    assert "GENERATED-AI-SECRET" not in text
    dialog.credential_check.setChecked(True)
    assert dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()
    pump(qapp, lambda: not runtime.busy)
    assert runtime.last_result.status == "completed" and reads
    assert source.is_dir()
    dialog.close()


def test_source_edit_rejects_without_writing_user_data(tmp_path, monkeypatch, qapp):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    runtime.submit("preflight", source=source)
    pump(qapp, lambda: not runtime.busy)
    (source / "config-instance-A.json").write_text('{"edited":true}')
    dialog.confirm_button.click()
    pump(qapp, lambda: not runtime.busy)
    assert runtime.last_result.reason == "source_changed"
    assert "未宣称" in dialog.status_label.text()
    assert not (layout.data_root / "config-instance-A.json").exists()
    assert dialog.cancel_button.isEnabled()
    dialog.cancel_button.click()
    pump(qapp, lambda: not runtime.busy)
    assert dialog.plan is None
    dialog.close()


def test_waiting_for_natural_exit_retains_confirmed_plan_and_has_safe_retry(tmp_path, monkeypatch, qapp):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    runtime.submit("preflight", source=source)
    pump(qapp, lambda: not runtime.busy)
    with layout.acquire_session():
        dialog.confirm_button.click()
        pump(qapp, lambda: not runtime.busy)
        assert runtime.last_result.status == "awaiting_release"
        assert "自然退出" in dialog.status_label.text()
        assert not (layout.data_root / "config-instance-A.json").exists()
        assert dialog.retry_button.isEnabled()
    dialog.retry_button.click()
    pump(qapp, lambda: not runtime.busy)
    assert runtime.last_result.status == "completed"
    dialog.close()


def test_close_during_accepted_import_drains_without_cancel_or_late_qobject_use(tmp_path, monkeypatch, qapp):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    runtime.submit("preflight", source=source)
    pump(qapp, lambda: not runtime.busy)
    blocked, release = threading.Event(), threading.Event()

    def checkpoint(stage):
        if stage == "accepted":
            blocked.set()
            assert release.wait(30)

    monkeypatch.setattr(importer, "_checkpoint", checkpoint)
    dialog.confirm_button.click()
    try:
        pump(qapp, blocked.is_set)
        dialog.close()
        qapp.processEvents()
        assert runtime.closed and runtime.busy
        assert not runtime.submit("recover")
    finally:
        release.set()
    pump(qapp, lambda: not runtime.busy)
    assert (layout.data_root / "config-instance-A.json").is_file()
    assert not importer.pending.exists()


def test_close_during_preflight_cleans_only_its_unaccepted_snapshot_off_gui(tmp_path, monkeypatch, qapp):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    blocked, release = threading.Event(), threading.Event()
    preflight = importer.preflight

    def held(source):
        result = preflight(source)
        blocked.set()
        assert release.wait(30)
        return result

    monkeypatch.setattr(importer, "preflight", held)
    runtime.submit("preflight", source=source)
    try:
        pump(qapp, blocked.is_set)
        dialog.close()
    finally:
        release.set()
    pump(qapp, lambda: not runtime.busy)
    assert not list(importer.root.glob("import-*"))
    assert source.is_dir()
    assert not (layout.data_root / "config-instance-A.json").exists()


def test_recovery_is_explicit_and_does_not_offer_cancel_of_accepted_intent(tmp_path, monkeypatch, qapp):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    original = importer._checkpoint

    def fail(stage):
        if stage == "accepted":
            raise OSError("generated failure")

    monkeypatch.setattr(importer, "_checkpoint", fail)
    assert importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token).status == "recovery_required"
    runtime, dialog = make_ui(importer, qapp)
    assert importer.pending.exists()  # Opening never accepts or resumes an import.
    assert not dialog.cancel_button.isEnabled()
    monkeypatch.setattr(importer, "_checkpoint", original)
    dialog.retry_button.click()
    pump(qapp, lambda: not runtime.busy)
    assert runtime.last_result.status == "completed" and not importer.pending.exists()
    dialog.close()


@pytest.mark.parametrize("width", [720, 900, 1100])
@pytest.mark.parametrize("large", [False, True])
def test_dialog_wraps_has_keyboard_names_and_no_horizontal_overflow(tmp_path, monkeypatch, qapp, width, large):
    _, source, importer = fixture_import(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    if large:
        font = dialog.font()
        font.setPointSize(18)
        dialog.setFont(font)
    dialog.resize(width, 500)
    runtime.submit("preflight", source=source)
    pump(qapp, lambda: not runtime.busy)
    qapp.processEvents()
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    assert dialog.minimumSizeHint().width() <= width
    assert dialog.detail_label.wordWrap()
    for widget in (dialog.source_button, dialog.confirm_button, dialog.retry_button, dialog.cancel_button, dialog.close_button, dialog.credential_check):
        assert widget.accessibleName()
    dialog.close()
    pump(qapp, lambda: not runtime.busy)


@pytest.mark.parametrize("width", [720, 1100])
@pytest.mark.parametrize("large", [False, True])
def test_credential_consent_is_readable_with_long_english_copy(tmp_path, monkeypatch, qapp, width, large):
    _, source, _, importer = authorized_fixture(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    dialog.resize(width, 500)
    if large:
        font = dialog.font()
        font.setPointSize(18)
        dialog.setFont(font)
    runtime.submit("preflight", source=source)
    pump(qapp, lambda: not runtime.busy)
    dialog.credential_check.setText("Authorize credential reference transfer")
    dialog.credential_hint.setText(
        "Only transfer references explicitly bound to the selected source, owner, profile and instance. Never store secrets in the portable directory, journal, snapshot or backup. The source remains unchanged."
    )
    qapp.processEvents()
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    assert dialog.minimumSizeHint().width() <= width
    assert dialog.credential_hint.wordWrap()
    assert dialog.credential_check.isVisible()
    assert not dialog.confirm_button.isEnabled()
    dialog.close()
    pump(qapp, lambda: not runtime.busy)


def test_cancel_retry_never_reaccepts_previously_confirmed_preview(tmp_path, monkeypatch, qapp):
    from pet import feature_state_io as io

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    runtime.submit("preflight", source=source)
    pump(qapp, lambda: not runtime.busy)
    with layout.acquire_session():
        dialog.confirm_button.click()
        pump(qapp, lambda: not runtime.busy)
        assert runtime.last_result.status == "awaiting_release"
        assert not importer.pending.exists()
        with io.open_kernel_lock(importer.management_lock):
            dialog.cancel_button.click()
            pump(qapp, lambda: not runtime.busy)
            assert runtime.last_result.status == "awaiting_release"
            assert runtime.last_result.reason == "import_management_busy"
            assert not dialog.confirm_button.isEnabled()
            assert dialog.retry_button.isEnabled()
    dialog.retry_button.click()
    pump(qapp, lambda: not runtime.busy)
    assert runtime.last_result.reason == "preflight_cancelled"
    assert not (layout.data_root / "config-instance-A.json").exists()
    assert not importer.pending.exists()
    assert not list(importer.root.glob("import-*"))
    dialog.close()


def test_close_preview_while_core_is_running_reclaims_owned_snapshot(tmp_path, monkeypatch, qapp):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    runtime, dialog = make_ui(importer, qapp)
    with layout.acquire_session():
        runtime.submit("preflight", source=source)
        pump(qapp, lambda: not runtime.busy)
        dialog.close()
        pump(qapp, lambda: not runtime.busy)
        assert runtime.last_result.reason == "preflight_cancelled"
        assert not list(importer.root.glob("import-*"))
    assert source.is_dir()
    assert not (layout.data_root / "config-instance-A.json").exists()
