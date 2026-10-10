"""Management public seam, real Qt loop and installed third-party source."""
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog
from tests.test_content_dlc import _write_package


def pump(app, predicate, *, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        # Yield through the real Qt loop instead of monopolizing a CPU/GIL while
        # background metadata and queued lifecycle work are making progress.
        wait = QEventLoop()
        QTimer.singleShot(20, wait.quit)
        wait.exec()
    assert predicate()


def test_import_resource_from_existing_content_format_disabled_then_enable_delete(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path / 'data'), include_ai=False, standalone=True)
    source = _write_package(tmp_path / 'source')
    results = []
    dialog.mod_controller.result.connect(lambda key, outcome: results.append(outcome))
    try:
        assert dialog.mod_controller.import_source(source)
        pump(app, lambda: not dialog.mod_controller.busy)
        entry = next(row for row in dialog.mod_controller.entries() if row.id == 'test.character.demo')
        assert not entry.enabled
        assert dialog.mod_controller.import_source(source)
        pump(app, lambda: not dialog.mod_controller.busy)
        assert results[-1].message == '已存在，不重复导入'
        assert dialog.mod_controller.perform('enable', [entry.key])
        pump(app, lambda: not dialog.mod_controller.busy)
        assert dialog.mod_controller.resources._entry(entry.id).enabled
        assert dialog.mod_controller.perform('delete', [entry.key])
        pump(app, lambda: not dialog.mod_controller.busy)
        assert all(result.success for result in results), results
        assert source.is_dir() and not entry.path.exists()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_nonofficial_import_enable_mount_settings_and_preserve_unrelated_draft(tmp_path, monkeypatch):
    from pet.feature_management import FeatureManagementRuntime
    from scripts.build_mod_example import build_example
    from tests.test_feature_package_transactions import StubChecker
    app = QApplication.instance() or QApplication([])
    output = build_example(Path('examples/mods/hello-local'), tmp_path / 'package')
    dialog = ModernSettingsDialog(Config(base=tmp_path / 'data'), include_ai=False, standalone=True)
    # Only the external OS sandbox is substituted; source install, receipts,
    # factory loading, event loop, local grants and settings are real.
    original = FeatureManagementRuntime.submit_local_source
    def submit(manager, *args, **kwargs):
        manager.service.self_checker = StubChecker()
        return original(manager, *args, **kwargs)
    monkeypatch.setattr(FeatureManagementRuntime, 'submit_local_source', submit)
    unrelated = QLineEdit('未保存的其他设置', dialog)
    try:
        assert dialog.mod_controller.import_source(output)
        pump(app, lambda: not dialog.mod_controller.busy)
        rows = [row for row in dialog.mod_controller.entries() if row.id == 'demo.hello-local']
        assert len(rows) == 1 and not rows[0].enabled
        assert 'demo.hello-local' not in dialog._feature_components
        assert dialog.mod_controller.perform('enable', [rows[0].key])
        pump(app, lambda: not dialog.mod_controller.busy)
        component = dialog._feature_components['demo.hello-local']
        dialog._mod_settings.show('demo.hello-local')
        from pet.settings_widgets import SettingsSection
        section = dialog._mod_settings.panels['demo.hello-local'].findChild(SettingsSection)
        assert section.toggle.isChecked(), 'row Settings opens the actual editor in one click'
        from pet.feature_state_io import state_lock
        manager = dialog.feature_managers['demo.hello-local']
        with state_lock(manager.service.store.lock_path):
            busy = manager.ensure_loaded()
            assert busy.reason == 'state_lock_busy'
            assert manager._bootstrap_timer.isActive(), 'newly discovered mounts retry OS lock contention'
        pump(app, lambda: dialog.feature_host.enabled('demo.hello-local'))
        component.editor.setText('保留此草稿')
        dialog._mod_settings.refresh()
        assert dialog._feature_components['demo.hello-local'] is component
        assert component.editor.text() == '保留此草稿' and unrelated.text() == '未保存的其他设置'
        monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Cancel)
        assert dialog.mod_controller.perform('disable', [rows[0].key])
        pump(app, lambda: not dialog.mod_controller.busy)
        assert component.editor.text() == '保留此草稿' and dialog.feature_host.enabled('demo.hello-local')
        assert component.confirm_save()
        assert dialog.mod_controller.perform('disable', [rows[0].key])
        pump(app, lambda: not dialog.mod_controller.busy)
        assert not dialog.feature_host.enabled('demo.hello-local')
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_draft_blocked_result_finishes_operation_instead_of_spinning(tmp_path):
    from types import SimpleNamespace

    from pet.feature_package_transactions import OperationResult
    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path / 'data'), include_ai=False, standalone=True)
    controller = dialog.mod_controller
    outcomes = []
    controller.batch_finished.connect(outcomes.append)
    try:
        controller.busy = True
        controller._current = ('import_feature', SimpleNamespace(feature_id='demo.blocked'), None)
        controller._feature_finished('demo.blocked', OperationResult('awaiting_confirmation', phase='awaiting_confirmation', reason='draft_blocked', feature_id='demo.blocked'))
        app.processEvents()
        assert not controller.busy
        assert len(outcomes) == 1 and not outcomes[0][0][1].success
        assert 'draft_blocked' in outcomes[0][0][1].message
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_settings_deleted_before_initial_mod_refresh_has_no_late_callback(tmp_path, monkeypatch):
    """A queued first refresh must die with its Qt owner, not the next test/UI."""
    import sys

    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    app = QApplication.instance() or QApplication([])
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda kind, error, tb: errors.append(error))
    dialog = ModernSettingsDialog(Config(base=tmp_path / 'data'), include_ai=False, standalone=True)
    support = dialog._mod_settings
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(dialog)
    app.processEvents()
    assert not errors, f'queued callback survived dialog: {errors}'
    # Retaining a Python wrapper must not retain executable Qt callbacks.
    assert support is not None


def test_worker_exit_owner_change_retries_the_same_confirmed_plan(tmp_path):
    from types import SimpleNamespace

    from pet.feature_package_transactions import OperationResult
    from pet.mod_management import ModEntry
    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path / 'data'), include_ai=False, standalone=True)
    c = dialog.mod_controller
    dialog._mod_settings.closed = True
    entry = ModEntry('demo.retry', '功能扩展', '例子', '1.0.0', '', True, tmp_path)
    plan = SimpleNamespace(confirmation_token='ephemeral-not-persisted')
    calls = []
    c.managers[entry.id] = SimpleNamespace(service=SimpleNamespace(inspect=lambda: SimpleNamespace(pending_transaction=None, active=None)),
                                        submit=lambda *a, **kw: calls.append((a, kw)) or True)
    c._current = ('delete', entry, None)
    c.busy = True
    try:
        c._feature_finished(entry.id, OperationResult('awaiting_release', 'tx-owned', reason='lifecycle_owners_changed', plan=plan, feature_id=entry.id))
        pump(app, lambda: bool(calls) or not c.busy)
        assert calls and calls[0] == (('apply', plan), {'confirmation_token': plan.confirmation_token})
        assert c.busy, 'still waiting for the real retry result, not claiming accepted removal'
    finally:
        c.managers.pop(entry.id)
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_unaccepted_runtime_wait_is_not_reported_as_durable_deletion(tmp_path):
    from types import SimpleNamespace

    from pet.feature_package_transactions import OperationResult
    from pet.mod_management import ModEntry
    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path / 'data'), include_ai=False, standalone=True)
    c = dialog.mod_controller
    dialog._mod_settings.closed = True
    entry = ModEntry('demo.wait', '功能扩展', '例子', '1.0.0', '', True, tmp_path)
    c.managers[entry.id] = SimpleNamespace(service=SimpleNamespace(inspect=lambda: SimpleNamespace(pending_transaction=None, active=None)))
    c._current = ('delete', entry, None)
    c.busy = True
    results = []
    c.result.connect(lambda key, outcome: results.append(outcome))
    try:
        c._feature_finished(entry.id, OperationResult('awaiting_release', 'tx-owned', reason='runtime_unresponsive', feature_id=entry.id))
        assert results and not results[0].pending and '重试' in results[0].message
    finally:
        c.managers.pop(entry.id)
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
