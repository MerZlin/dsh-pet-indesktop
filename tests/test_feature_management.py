"""Production management attachment uses build policy, not user-selected trust."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.plugins.feature_host import FeatureHost


def test_builtin_manager_is_explicit_and_never_initializes_transactions_or_worker(tmp_path):
    from pet.feature_management import attach_feature_management

    app = QApplication.instance() or QApplication([])
    config = Config(base=tmp_path)
    host = FeatureHost()
    manager = attach_feature_management(config, host, role="settings", management_only=True)
    assert manager.builtin and manager.service is None and manager.startup is None
    assert attach_feature_management(config, host) is manager
    assert not (config.dir / "plugins").exists()
    manager.close()


def test_nonbuiltin_unconfigured_build_is_fail_closed_not_unsigned_or_source_fallback(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_management import attach_feature_management

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    config = Config(base=tmp_path)
    host = FeatureHost()
    manager = attach_feature_management(config, host, role="settings", management_only=True)
    try:
        assert not manager.builtin and manager.service is not None and manager.startup is None
        assert not manager.service.verifier.allow_developer_unsigned
        assert not manager.service.verifier.trust_anchors
        assert not manager.service.self_checker.available
        assert host.state("official.screen-understanding") == "absent"
    finally:
        manager.close()


def test_corrupt_frozen_helper_fails_closed_without_crashing_manager(tmp_path, monkeypatch):
    import sys

    from pet import feature_build_policy, feature_distribution
    from pet.feature_management import attach_feature_management

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "missing-owned-core"), raising=False)
    monkeypatch.setattr(feature_build_policy, "PROBE_BUNDLE_MANIFEST_SHA256", "a" * 64)
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), management_only=True)
    try:
        assert not manager.service.self_checker.available
        assert manager.last_result.status == "rejected"
        assert manager.last_result.reason == "trusted_probe_unavailable"
        assert manager.host.state("official.screen-understanding") == "absent"
    finally:
        manager.close()


def test_state_monitor_starts_after_synchronous_settings_bootstrap_and_not_after_close(tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop

    from pet import feature_distribution
    from pet.feature_management import attach_feature_management

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(), role="settings", management_only=True)
    try:
        # The first read otherwise competes with settings' verified resolution
        # during this same synchronous constructor and produces lock_busy.
        assert not manager.monitor.running
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        assert manager.monitor.running
    finally:
        manager.close()
    closed = attach_feature_management(Config(base=tmp_path / "closed"), FeatureHost(), management_only=True)
    closed.close()
    app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    assert not closed.monitor.running


def test_core_retries_transient_pending_recovery_without_settings_or_user_action(tmp_path, monkeypatch):
    import json
    import time
    from dataclasses import replace

    from pet import __version__, feature_build_policy, feature_distribution
    from pet.feature_management import attach_feature_management
    from pet.feature_package_transactions import FeaturePackageTransactionService
    from pet.feature_state_io import open_kernel_lock
    from tests.test_feature_package_transactions import _KEY, StubChecker, _confirm, _package

    app = QApplication.instance() or QApplication([])
    config = Config(base=tmp_path / "generated-profile")
    source, policy, manifest = _package(tmp_path / "source")
    manifest["core_requires"] = ">=4.0.0,<6.0.0"
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (source / "manifest.json").write_bytes(raw)
    (source / "manifest.sig").write_bytes(_KEY.sign(raw))
    policy = replace(policy, core_version=__version__)
    service = FeaturePackageTransactionService(config.dir, policy, self_checker=StubChecker())
    pending = _confirm(service, service.preflight_install(source))
    assert pending.status == "awaiting_startup_confirmation"
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    monkeypatch.setattr(feature_build_policy, "OFFICIAL_FEATURE_TRUST_ANCHORS", tuple((k, v.hex()) for k, v in policy.trust_anchors.items()))
    host = FeatureHost()
    with open_kernel_lock(service.management_lock_path):
        manager = attach_feature_management(config, host, role="core")
        assert manager.last_result.reason == "management_lock_busy"
        assert manager.startup.binding is None
    try:
        deadline = time.monotonic() + 12
        while service.store.read().state.pending_transaction and time.monotonic() < deadline:
            app.processEvents()
            # Event loop waits, not a sleep guessing a particular thread ordering.
            from PySide6.QtCore import QEventLoop, QTimer

            wait = QEventLoop()
            QTimer.singleShot(20, wait.quit)
            wait.exec()
        assert service.store.read().state.pending_transaction is None
        assert host.enabled("official.screen-understanding")
        assert manager.startup.binding.handle.process_pin is not None
        assert manager.role == "core"
    finally:
        manager.close()


def test_management_runtimes_are_independent_for_two_official_owners(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_management import attach_feature_management

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False, raising=False)
    cfg, host = Config(base=tmp_path), FeatureHost()
    ai = attach_feature_management(cfg, host, feature_id="official.ai-chat", role="settings", management_only=True)
    screen = attach_feature_management(cfg, host, role="settings", management_only=True)
    try:
        assert ai is not screen
        assert ai.feature_id == ai.service.store.feature_id == ai.last_result.feature_id == "official.ai-chat"
        assert screen.feature_id == "official.screen-understanding"
        assert ai.service.store.root != screen.service.store.root
        assert ai.service.verifier.feature_id == "official.ai-chat"
        assert ai is attach_feature_management(cfg, host, feature_id="official.ai-chat")
        assert screen is host.management_runtime
        assert ai.startup is None and screen.startup is None
    finally:
        ai.close()
        screen.close()


def test_ai_context_is_scoped_without_desktop_or_worker(tmp_path):
    from pet.feature_host_bindings import bind_ai_context

    cfg = Config(base=tmp_path)
    context = bind_ai_context(cfg)
    assert context.owner == "official.ai-chat"
    assert context.desktop is None and context.window is None
    assert context.worker_launch_factory is None and context.allow_in_process
    context.preferences.stage({"enabled": False})
    assert cfg.get("chat", {})["enabled"] is False
    import pytest

    with pytest.raises(PermissionError):
        context.preferences.stage({"character": "other"})
    from pet.credentials import CredentialError

    with pytest.raises(CredentialError, match="legacy_import_authorization_required"):
        context.legacy_secret_reader("not-an-authorized-reference")


def test_official_bootstrap_and_close_own_both_runtimes(tmp_path, monkeypatch):
    from pet import feature_distribution
    from pet.feature_management import attach_official_management, close_official_management

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    monkeypatch.setattr(feature_distribution, "BUILTIN_AI", False)
    host = FeatureHost()
    runtimes = attach_official_management(Config(base=tmp_path), host, role="core")
    assert set(runtimes) == {"official.screen-understanding", "official.ai-chat"}
    assert all(m.role == "core" and m.startup is not None for m in runtimes.values())
    assert runtimes["official.screen-understanding"] is host.management_runtime
    close_official_management(host)
    app.processEvents()
    assert all(m._closed.is_set() and not m.monitor.running and not m._bootstrap_timer.isActive() for m in runtimes.values())
    close_official_management(host)


def _frozen_maintenance_manager(tmp_path, monkeypatch, collect):
    import sys

    from pet import feature_build_policy, feature_distribution
    from pet.feature_management import attach_feature_management
    from pet.feature_probe_adapter import WindowsFeatureProbeSandbox
    from tests.test_feature_probe_adapter import _fixture

    sandbox, request, calls, options, bundle = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(feature_distribution, "BUILTIN_SCREEN", False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle.root.parent), raising=False)
    monkeypatch.setattr(feature_build_policy, "PROBE_BUNDLE_DIRECTORY", bundle.root.name)
    monkeypatch.setattr(feature_build_policy, "PROBE_BUNDLE_MANIFEST_SHA256", bundle.manifest_digest)
    monkeypatch.setattr(WindowsFeatureProbeSandbox, "collect_garbage", collect)
    return attach_feature_management(Config(base=tmp_path / "generated-profile"), FeatureHost(), management_only=True)


def test_startup_probe_recovery_is_queued_off_gui_and_does_not_change_installation(tmp_path, monkeypatch):
    import threading
    import time

    from pet.feature_probe_materials import ProbeMaterialCleanup

    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    threads = []

    def collect(sandbox):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(20)
        return (ProbeMaterialCleanup("recovery_required", "probe-" + "a" * 32, "probe_materials_evidence"),)

    manager = _frozen_maintenance_manager(tmp_path, monkeypatch, collect)
    try:
        assert not entered.is_set(), "constructor performs probe cleanup on GUI thread"
        deadline = time.monotonic() + 20
        while not entered.is_set() and time.monotonic() < deadline:
            app.processEvents()
        assert entered.is_set(), "production startup never recovers owned probe materials"
        assert threads == [threads[0]] and threads[0] != threading.get_ident()
        release.set()
        while not getattr(manager, "probe_cleanup", ()) and time.monotonic() < deadline:
            app.processEvents()
        assert manager.probe_cleanup[0].reason == "probe_materials_evidence"
        assert manager.service.inspect().active is None
        assert manager.last_result.status == "idempotent"
    finally:
        release.set()
        manager.close()
        app.processEvents()


def test_safe_retry_includes_probe_cleanup_warning_without_falsifying_transaction(tmp_path, monkeypatch):
    import time

    from pet.feature_probe_materials import ProbeMaterialCleanup

    app = QApplication.instance() or QApplication([])
    calls = []

    def collect(sandbox):
        calls.append(sandbox.run_parent)
        return (ProbeMaterialCleanup("recovery_required", "probe-" + "a" * 32, "probe_materials_io_error"),)

    manager = _frozen_maintenance_manager(tmp_path, monkeypatch, collect)
    try:
        assert manager.submit("gc")
        deadline = time.monotonic() + 20
        while manager.busy and time.monotonic() < deadline:
            app.processEvents()
        assert not manager.busy and calls
        assert manager.last_result.status in ("completed", "idempotent")
        assert manager.last_result.details["probe_cleanup"][0]["reason"] == "probe_materials_io_error"
        assert manager.last_result.feature_id == "official.screen-understanding"
    finally:
        manager.close()
        app.processEvents()
