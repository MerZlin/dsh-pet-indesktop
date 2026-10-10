"""Manager widget calls the real asynchronous service seam, never load receipts."""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.plugins.feature_host import FeatureHost
from tests.test_feature_package_transactions import StubChecker, _package


def _pump(app, predicate):
    deadline = time.monotonic() + 20
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    assert predicate()


def test_builtin_explains_core_ownership_and_cannot_offer_physical_actions(tmp_path):
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget

    app = QApplication.instance() or QApplication([])
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), role="settings", management_only=True)
    widget = FeatureManagementWidget(manager)
    try:
        widget.show()
        app.processEvents()
        assert "Core" in widget.status_label.text()
        assert not widget.install_button.isEnabled() and not widget.uninstall_button.isEnabled()
        assert widget.accessibleName() and widget.status_label.accessibleName()
    finally:
        widget.close()
        widget.deleteLater()
        manager.close()
        app.processEvents()


def test_directory_preflight_is_async_confirm_bound_and_pending_is_not_success(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), role="settings", management_only=True)
    source, verifier, _ = _package(tmp_path / "source")
    manager.service.verifier = verifier
    manager.service.self_checker = StubChecker()  # Only OS sandbox seam; separately native-validated.
    widget = FeatureManagementWidget(manager)
    widget.show()
    try:
        _pump(app, lambda: not manager.busy and widget.inspection is not None)
        assert widget.begin_source(source)
        _pump(app, lambda: not manager.busy and widget.plan is not None)
        assert widget.plan.target_version == "1.2.3" and widget.confirm_button.isEnabled()
        assert "保留" in widget.summary_label.text()
        widget.confirm_button.click()
        _pump(app, lambda: not manager.busy and widget.last_operation is not None and widget.last_operation.status == "awaiting_startup_confirmation")
        assert "启动确认" in widget.status_label.text() and "安装成功" not in widget.status_label.text()
        assert manager.startup is None
        assert manager.service.store.read().state.pending_transaction
        assert not widget.cancel_button.isEnabled()
    finally:
        widget.close()
        widget.deleteLater()
        manager.close()
        app.processEvents()


def test_manager_deep_link_lives_in_existing_general_domain_and_search(tmp_path):
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.settings_widgets import SETTINGS_DOMAIN_NAV, SettingRow

    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path), include_ai=False, standalone=True, initial_page="extensions")
    try:
        dialog.resize(720, 700)
        dialog.show()
        app.processEvents()
        assert dialog.select_page("extensions")
        assert dialog.sidebar.currentItem().text() == "常规"
        assert dialog.findChild(SettingRow, "settingRow_local_package_import") is not None
        assert tuple(dialog.sidebar.item(i).text() for i in range(dialog.sidebar.count())) == tuple(
            item[0] for item in SETTINGS_DOMAIN_NAV if item[0] != "AI 与对话"
        )
        assert dialog._screen_component is None  # management-only entry never loads feature UI/Worker.
        dialog._search_settings("扩展")
        assert dialog._search_matches
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_retry_before_acceptance_reuses_confirmed_plan_not_empty_recovery(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget
    from pet.feature_package_transactions import RuntimePreparation

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), role="settings", management_only=True)
    source, verifier, _ = _package(tmp_path / "source")
    manager.service.verifier, manager.service.self_checker = verifier, StubChecker()

    class Draft:
        dirty = True

        def prepare(self, request):
            return RuntimePreparation("awaiting_release", "lifecycle_busy") if self.dirty else RuntimePreparation()

    draft = Draft()
    manager.service.runtime = draft
    widget = FeatureManagementWidget(manager)
    widget.show()
    try:
        _pump(app, lambda: not manager.busy and widget.inspection is not None)
        assert widget.begin_source(source)
        _pump(app, lambda: not manager.busy and widget.plan is not None)
        operation = widget.plan.operation_id
        widget.confirm_button.click()
        _pump(app, lambda: not manager.busy and widget.last_operation is not None and widget.last_operation.status == "awaiting_release")
        assert manager.service.store.read().state.pending_transaction is None
        assert not widget.confirm_button.isEnabled()
        assert widget.retry_button.isEnabled()
        draft.dirty = False
        widget.retry_button.click()
        _pump(app, lambda: not manager.busy and widget.last_operation.status == "awaiting_startup_confirmation")
        assert manager.service.store.read().state.pending_transaction == operation
    finally:
        widget.close()
        widget.deleteLater()
        manager.close()
        app.processEvents()


def test_clean_settings_component_does_not_block_lifecycle_as_method_object(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_lifecycle import LifecyclePrepareRequest, _Reply
    from pet.modern_settings_dialog import ModernSettingsDialog

    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path), include_ai=False, standalone=True, initial_page="extensions")

    class Component:
        def dirty(self):
            return False

    try:
        service = dialog.feature_management.service
        service._state(initialize=True)
        dialog._screen_component = Component()
        reply = _Reply(LifecyclePrepareRequest("fixture-operation", service.store.read().state.revision, "uninstall", ()))
        dialog.feature_management.endpoint._prepare(reply)
        assert reply.result.status == "ready"
    finally:
        dialog._screen_component = None
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("width", [720, 900, 1100])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("language", ["zh", "long-en"])
@pytest.mark.parametrize("font_pixels", [13, 18])
def test_management_layout_language_theme_font_matrix(tmp_path, width, theme, language, font_pixels):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea

    from pet.modern_settings_dialog import ModernSettingsDialog
    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path), include_ai=False, standalone=True, initial_page="extensions")
    try:
        dialog.menu_theme_select.setCurrentIndex(dialog.menu_theme_select.findData(theme))
        dialog.setStyleSheet(dialog.styleSheet() + f"\n#modCenter QLabel, #modCenter QPushButton {{font-size:{font_pixels}px;}}")
        dialog.resize(width, 760)
        dialog.show()
        widget = dialog.mod_center
        if language == "long-en":
            widget.summary.setText("Waiting for the previously loaded generation to be naturally released. No application will be forcibly closed. " + "a" * 64)
            widget.import_directory.setText("Import package directory")
            widget.import_zip.setText("Import package ZIP")
        _pump(app, lambda: widget.width() > 0)
        for _ in range(4):
            app.processEvents()
        assert widget.summary.fontMetrics().height() >= font_pixels
        assert widget.summary.textFormat() == Qt.TextFormat.PlainText
        for area in dialog.findChildren(QScrollArea):
            if area.isVisible():
                assert area.horizontalScrollBar().maximum() == 0
        buttons = [widget.import_zip, widget.import_directory, *widget.filters.values()]
        for button in buttons:
            assert button.accessibleName()
            assert button.mapTo(widget, button.rect().topRight()).x() < widget.width()
        dialog.select_page("extensions")
        assert dialog.local_package_zip_button.hasFocus()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_deleted_dialog_ignores_late_background_result(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent

    from pet import feature_distribution
    from pet.modern_settings_dialog import ModernSettingsDialog

    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path), include_ai=False, standalone=True, initial_page="extensions")
    manager = dialog.feature_management
    gate, started, finished = threading.Event(), threading.Event(), threading.Event()
    original = manager.service.inspect

    def blocked():
        started.set()
        assert gate.wait(20)
        try:
            return original()
        finally:
            finished.set()

    monkeypatch.setattr(manager.service, "inspect", blocked)
    assert manager.submit("inspect")
    _pump(app, started.is_set)
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gate.set()
    assert finished.wait(20)
    app.processEvents()
    assert manager._closed.is_set()


@pytest.mark.parametrize("choice, expected, saved, discarded", [("Save", True, 1, 0), ("Discard", True, 0, 1), ("Cancel", False, 0, 0)])
def test_draft_decision_is_owned_by_explicit_settings_ui(tmp_path, monkeypatch, choice, expected, saved, discarded):
    from PySide6.QtWidgets import QMessageBox

    from pet.modern_settings_dialog import ModernSettingsDialog

    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path), include_ai=False, standalone=True, initial_page="extensions")

    class Component:
        saves = discards = 0

        def dirty(self):
            return True

        def confirm_save(self):
            self.saves += 1
            return True

        def discard_changes(self):
            self.discards += 1
            return True

    component = Component()
    dialog._screen_component = component
    monkeypatch.setattr(QMessageBox, "question", lambda *args: getattr(QMessageBox.StandardButton, choice))
    try:
        assert dialog._prepare_screen_revocation() is expected
        assert (component.saves, component.discards) == (saved, discarded)
    finally:
        dialog._screen_component = None
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_management_only_page_can_explicitly_open_real_instance_settings(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget
    from tests.test_feature_package_transactions import _installed

    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    app = QApplication.instance() or QApplication([])
    manager = attach_feature_management(Config(base=tmp_path / "profile"), FeatureHost(), role="settings", management_only=True)
    manager.service = _installed(tmp_path / "installed")
    widget = FeatureManagementWidget(manager)
    widget.show()
    try:
        _pump(app, lambda: widget.inspection is not None and not manager.busy)
        assert not manager.host.owners()
        assert widget.settings_button.isEnabled()
    finally:
        widget.close()
        widget.deleteLater()
        manager.close()
        app.processEvents()


def test_breakable_plain_text_preserves_canonical_confirmation_and_accessibility():
    from PySide6.QtCore import Qt

    from pet.feature_management_ui import _BreakableLabel

    app = QApplication.instance() or QApplication([])
    original = "source " + "f" * 64 + " <not-html> C:\\generated\\" + "long" * 20
    label = _BreakableLabel(original, None)
    try:
        label.setTextFormat(Qt.TextFormat.PlainText)
        assert label.text().replace("\u200b", "") == original
        assert label.toolTip() == original and label.accessibleDescription() == original
        assert "\u200b" in label.text()
    finally:
        label.deleteLater()
        app.processEvents()


def test_retry_after_kernel_lock_contention_reuses_the_confirmed_operation(tmp_path, monkeypatch):
    from pet import feature_distribution, feature_state_io
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), role="settings", management_only=True)
    source, verifier, _ = _package(tmp_path / "source")
    manager.service.verifier, manager.service.self_checker = verifier, StubChecker()
    manager.service.runtime = None
    widget = FeatureManagementWidget(manager)
    widget.show()
    try:
        _pump(app, lambda: not manager.busy and widget.inspection is not None)
        assert widget.begin_source(source)
        _pump(app, lambda: not manager.busy and widget.plan is not None)
        confirmed_plan = widget.plan
        with feature_state_io.open_kernel_lock(manager.service.management_lock_path):
            widget.confirm_button.click()
            _pump(app, lambda: not manager.busy and widget.last_operation is not None and widget.last_operation.status == "failed")
        assert widget.last_operation.reason == "management_lock_busy"
        assert manager.service.store.read().state.pending_transaction is None
        assert widget.retry_button.isEnabled()
        assert widget.retry_plan is confirmed_plan
        _pump(app, lambda: not manager.busy)
        widget.retry_button.click()
        _pump(app, lambda: not manager.busy and widget.last_operation.status == "awaiting_startup_confirmation")
        assert manager.service.store.read().state.pending_transaction == confirmed_plan.operation_id
    finally:
        widget.close()
        widget.deleteLater()
        manager.close()
        app.processEvents()


@pytest.mark.parametrize("reason", ["management_lock_busy", "leases_lock_busy", "state_lock_busy"])
def test_lock_failure_replaces_confirmation_and_survives_widget_recreation(tmp_path, monkeypatch, reason):
    from contextlib import ExitStack

    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    from pet import feature_distribution
    from pet import feature_state_io as io
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), role="settings", management_only=True)
    source, verifier, _ = _package(tmp_path / "source")
    manager.service.verifier, manager.service.self_checker = verifier, StubChecker()
    widget = FeatureManagementWidget(manager)
    replacement = None
    widget.show()
    try:
        _pump(app, lambda: not manager.busy and widget.inspection is not None)
        assert widget.begin_source(source)
        _pump(app, lambda: not manager.busy and widget.plan is not None)
        operation = widget.plan.operation_id
        with ExitStack() as held:
            resource = reason.removesuffix("_lock_busy")
            if resource == "management":
                held.enter_context(io.open_kernel_lock(manager.service.management_lock_path))
            elif resource == "state":
                held.enter_context(io.open_kernel_lock(manager.service.store.lock_path))
            else:
                # Do not obscure the final lease guard with an earlier occupancy error.
                original = manager.service.runtime

                class HoldAfterPrepare:
                    def prepare(self, request):
                        result = original.prepare(request)
                        held.enter_context(io.open_kernel_lock(manager.service.leases.leases_lock_path))
                        return result

                manager.service.runtime = HoldAfterPrepare()
            widget.confirm_button.click()
            _pump(app, lambda: not manager.busy and widget.last_operation is not None and widget.last_operation.status == "failed")
            assert widget.last_operation.reason == reason
            assert widget.retry_button.isEnabled()
            assert "确认摘要" not in widget.summary_label.text()
            assert "安全重试" in widget.summary_label.text()
        if resource == "leases":
            manager.service.runtime = original
        _pump(app, lambda: not manager.busy)
        widget.close()
        widget.deleteLater()
        # processEvents() alone does not guarantee DeferredDelete outside an
        # exec() loop. Prove actual destruction before testing a replacement;
        # never leave two closing receivers attached to the live manager.
        QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(widget)
        replacement = FeatureManagementWidget(manager)
        replacement.show()
        _pump(app, lambda: not manager.busy and replacement.inspection is not None)
        assert replacement.last_operation is not None
        assert replacement.last_operation.operation_id == operation
        assert reason in replacement.status_label.text()
        assert replacement.retry_button.isEnabled()
        assert replacement.retry_plan.confirmation_token
        replacement.retry_button.click()
        _pump(app, lambda: not manager.busy and replacement.last_operation.status == "awaiting_startup_confirmation")
        assert manager.service.store.read().state.pending_transaction == operation
    finally:
        manager.close()  # Stop producers before deleting these owned receivers.
        for owned in (replacement, widget):
            if owned is not None and shiboken6.isValid(owned):
                owned.close()
                owned.deleteLater()
                QCoreApplication.sendPostedEvents(owned, QEvent.Type.DeferredDelete)
        manager.deleteLater()
        QCoreApplication.sendPostedEvents(manager, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(manager)
        app.processEvents()


def test_uninstall_preview_does_not_claim_signature_validation_or_reenable_rollback(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget
    from tests.test_feature_package_transactions import _confirm, _startup

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), role="settings", management_only=True)
    source, verifier, _ = _package(tmp_path / "source")
    manager.service.verifier, manager.service.self_checker = verifier, StubChecker()
    # Metadata fixture setup has no loaded runtime; user operations below stay async.
    runtime = manager.service.runtime
    manager.service.runtime = None
    installed = _confirm(manager.service, manager.service.preflight_install(source))
    assert installed.status == "awaiting_startup_confirmation"
    _startup(manager.service, installed.operation_id)
    manager.service.runtime = runtime
    widget = FeatureManagementWidget(manager)
    widget.show()
    try:
        _pump(app, lambda: not manager.busy and widget.inspection is not None)
        widget.uninstall_button.click()
        _pump(app, lambda: not manager.busy and widget.plan is not None)
        text = widget.summary_label.text()
        assert "全部安装版本" in text and "1.2.3" in text
        assert "不重新执行包代码或声称验签" in text
        assert "已接受卸载不能取消并重新启用" in text
        assert "回滚目标：1.2.3" not in text
        assert "官方签名与兼容性已验证" not in text
    finally:
        widget.close()
        widget.deleteLater()
        manager.close()
        app.processEvents()


def test_real_queued_uninstall_can_revoke_before_lock_failure_then_safely_resume(tmp_path, monkeypatch):
    from contextlib import ExitStack

    from pet import feature_distribution
    from pet import feature_state_io as io
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget
    from pet.feature_version_lease import FeatureVersionSelection
    from pet.plugins.feature_host import FeatureDefinition
    from tests.test_feature_package_transactions import _confirm, _startup

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    host = FeatureHost()
    manager = attach_feature_management(Config(base=tmp_path), host, role="settings", management_only=True)
    source, verifier, _ = _package(tmp_path / "source")
    service = manager.service
    service.verifier, service.self_checker = verifier, StubChecker()
    runtime = service.runtime
    service.runtime = None
    installed = _confirm(service, service.preflight_install(source))
    assert _startup(service, installed.operation_id).status == "completed"
    service.runtime = runtime
    selection = FeatureVersionSelection.from_resolution(service.store.resolve_verified(verifier))
    pin = service.leases.acquire_host(selection)
    owner = "official.screen-understanding"
    host.provide(FeatureDefinition(owner, (), lambda: None))
    stopped = []
    owner_thread = threading.get_ident()
    host.bind_execution(owner, lambda: stopped.append(threading.get_ident()), lambda: None)
    widget = FeatureManagementWidget(manager)
    widget.show()
    try:
        _pump(app, lambda: not manager.busy and widget.inspection is not None)
        widget.uninstall_button.click()
        _pump(app, lambda: not manager.busy and widget.plan is not None)
        operation = widget.plan.operation_id
        before = service.store.read().state.document()
        with ExitStack() as held:

            class HoldAfterPrepare:
                def prepare(self, request):
                    result = runtime.prepare(request)
                    assert result.status == "ready"
                    held.enter_context(io.open_kernel_lock(service.leases.leases_lock_path))
                    return result

            service.runtime = HoldAfterPrepare()
            widget.confirm_button.click()
            _pump(app, lambda: not manager.busy and widget.last_operation.status == "failed")
        assert widget.last_operation.reason == "leases_lock_busy"
        assert host.state(owner) == "absent" and stopped == [owner_thread]
        assert service.store.read().state.document() == before
        assert service._load(operation)["accepted"] is False
        assert service.versions_root.joinpath("1.2.3").is_dir()
        service.runtime = runtime
        widget.retry_button.click()
        _pump(app, lambda: not manager.busy and widget.last_operation.status == "awaiting_release")
        assert widget.last_operation.reason == "version_in_use"
        state = service.store.read().state
        assert state.pending_transaction == operation and not state.enabled
        assert service.versions_root.joinpath("1.2.3").is_dir()
        assert not widget.cancel_button.isEnabled()
        pin.close()  # Only this generated fixture's pin, never a user process.
        widget.retry_button.click()
        _pump(app, lambda: not manager.busy and widget.last_operation.status == "completed")
        assert service.store.read().status == "uninstalled"
        assert not service.versions_root.joinpath("1.2.3").exists()
    finally:
        service.runtime = runtime
        pin.close()
        widget.close()
        widget.deleteLater()
        manager.close()
        app.processEvents()


def test_single_list_keeps_independent_managers_and_closes_all_observers(tmp_path):
    from pet.modern_settings_dialog import ModernSettingsDialog
    from pet.official_features import AI_OWNER, SCREEN_OWNER
    from pet.settings_widgets import SettingRow

    app = QApplication.instance() or QApplication([])
    dialog = ModernSettingsDialog(Config(base=tmp_path), include_ai=False, standalone=True, initial_page="extensions")
    managers = None
    try:
        dialog.show()
        app.processEvents()
        managers = dialog.feature_managers
        assert set(managers) == {AI_OWNER, SCREEN_OWNER}
        assert not dialog.feature_management_widgets  # no obsolete transaction cards
        assert not dialog.feature_host.owners(), "management-only must not import either factory"
        assert dialog.findChild(SettingRow, "settingRow_local_package_import") is not None
        assert dialog.mod_controller.managers is managers
        dialog._search_settings("扩展")
        assert dialog._search_matches
        assert dialog.sidebar.currentItem().text() == "常规"
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
    assert managers and all(manager._closed.is_set() for manager in managers.values())


def test_owner_card_ignores_cross_package_result_and_screen_draft(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QWidget

    from pet import feature_distribution
    from pet.feature_management import attach_official_management, close_official_management
    from pet.feature_management_ui import FeatureManagementWidget
    from pet.feature_package_transactions import Inspection, OperationResult
    from pet.official_features import AI_OWNER, SCREEN_OWNER

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    host = FeatureHost()
    managers = attach_official_management(Config(base=tmp_path), host, role="settings", management_only=True)
    parent = QWidget()
    parent._screen_component = SimpleNamespace(dirty=lambda: True)
    screen = FeatureManagementWidget(managers[SCREEN_OWNER], parent)
    ai = FeatureManagementWidget(managers[AI_OWNER], parent)
    try:
        _pump(app, lambda: all(not manager.busy for manager in managers.values()))
        previous = screen.inspection
        ai_result = Inspection("ready", 1, "1.0.0", None, True, None, (), (), feature_id=AI_OWNER)
        screen._result(ai_result)
        screen._result(OperationResult("completed", feature_id=AI_OWNER))
        assert screen.inspection is previous and screen.last_operation is None
        ai._result(ai_result)
        ai._update_actions()
        screen._update_actions()
        assert screen.draft_button.isEnabled()
        assert not ai.draft_button.isEnabled(), "screen drafts cannot authorize an AI operation"
        assert ai.settings_button.accessibleDescription().startswith("AI 对话")
    finally:
        close_official_management(host)
        parent.close()
        parent.deleteLater()
        app.processEvents()


def test_probe_cleanup_warning_is_separate_wrapped_and_not_install_failure(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_management import attach_feature_management
    from pet.feature_management_ui import FeatureManagementWidget
    from pet.feature_probe_materials import ProbeMaterialCleanup

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), management_only=True)
    widget = FeatureManagementWidget(manager)
    try:
        widget.resize(720, 700)
        widget.show()
        _pump(app, lambda: widget.inspection is not None and not manager.busy)
        before = widget.status_label.text()
        manager.probe_cleanup = (ProbeMaterialCleanup("recovery_required", "probe-" + "a" * 32, "probe_materials_io_error"),)
        manager.probe_cleanup_changed.emit(manager.probe_cleanup)
        app.processEvents()
        assert widget.status_label.text() == before
        assert widget.cleanup_label.isVisible() and widget.cleanup_label.wordWrap()
        assert "不会改变功能启停" in widget.cleanup_label.text()
        assert "probe_materials_io_error" in widget.cleanup_label.toolTip()
        assert widget.cleanup_label.accessibleDescription() == widget.cleanup_label.toolTip()
        assert widget.cleanup_label.accessibleName()
        manager.probe_cleanup_changed.emit((ProbeMaterialCleanup("completed", "probe-" + "a" * 32),))
        app.processEvents()
        assert not widget.cleanup_label.isVisible()
    finally:
        widget.close()
        widget.deleteLater()
        manager.close()
        app.processEvents()
