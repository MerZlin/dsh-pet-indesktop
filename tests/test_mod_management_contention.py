"""Real kernel contention at the public management/discovery seams."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.feature_management import attach_feature_management, discover_local_feature_ids
from pet.feature_state_io import open_kernel_lock
from pet.plugins.feature_host import FeatureHost
from scripts.build_mod_example import build_example
from tests.test_mod_center_integration import pump


def test_async_import_waits_for_transient_state_lock_without_finishing_as_failure(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = build_example(Path("examples/mods/hello-local"), tmp_path / "package")
    manager = attach_feature_management(Config(base=tmp_path / "data"), FeatureHost(),
                                        feature_id="demo.hello-local", role="settings", management_only=True)
    first_attempt = threading.Event()
    attempts, results = [], []
    original = manager.service.preflight_install
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        attempts.append(result)
        first_attempt.set()
        return result
    monkeypatch.setattr(manager.service, "preflight_install", observe)
    manager.result_ready.connect(results.append)
    from pet.feature_install_state import StateChange
    manager.service.store.commit(StateChange(versions={}), expected_revision=0, operation_id="initialize")
    try:
        with open_kernel_lock(manager.service.store.lock_path):
            assert manager.submit("import_disabled", source)
            assert first_attempt.wait(30)
            assert attempts[0].reason == "state_lock_busy"
        pump(app, lambda: not manager.busy)
        assert len(results) == 1 and results[0].status == "awaiting_confirmation", results
        assert results[0].plan.install_enabled is False
    finally:
        manager.close()
        app.processEvents()


def test_discovery_does_not_lose_committed_owner_during_state_read_lock(tmp_path):
    from pet.feature_install_state import FeatureInstallStateStore, StateChange
    config = Config(base=tmp_path)
    store = FeatureInstallStateStore(config.dir, feature_id="demo.hello-local")
    store.commit(StateChange({"1.0.0": "a" * 64}, "1.0.0", None, False),
                 expected_revision=0, operation_id="installed-for-discovery")
    with open_kernel_lock(store.lock_path):
        assert "demo.hello-local" in discover_local_feature_ids(config)
    assert "demo.hello-local" in discover_local_feature_ids(config)



def test_bootstrap_progress_is_not_an_explicit_command_completion(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = attach_feature_management(Config(base=tmp_path), FeatureHost(),
                                        feature_id="demo.hello-local", role="settings", management_only=True)
    from pet.feature_install_state import StateChange
    manager.service.store.commit(StateChange(versions={}), expected_revision=0, operation_id="initialize")
    results = []
    manager.result_ready.connect(results.append)
    try:
        with open_kernel_lock(manager.service.store.lock_path):
            manager.ensure_loaded()
            manager._retry_bootstrap()
            assert manager.last_result.reason in {"lock_busy", "state_lock_busy"}
            assert results == [], "background discovery must not complete an unrelated import/enable action"
    finally:
        manager.close()
        app.processEvents()


@pytest.mark.parametrize("boundary", ["initial_read", "initial_resolution", "refresh_resolution"])
def test_startup_preserves_real_state_contention_instead_of_faulting(tmp_path, monkeypatch, boundary):
    from pet.feature_host_bindings import bind_local_context
    from pet.feature_package_startup import ProductionFeatureStartup
    from tests.test_feature_package_transactions import StubChecker, _confirm

    app = QApplication.instance() or QApplication([])
    config = Config(base=tmp_path / "data")
    owner = "demo.hello-local"
    manager = attach_feature_management(config, FeatureHost(), feature_id=owner, management_only=True)
    source = build_example(Path("examples/mods/hello-local"), tmp_path / "package")
    service = manager.service
    service.runtime = None
    service.self_checker = StubChecker()
    assert _confirm(service, service.preflight_install(source)).status == "awaiting_startup_confirmation"
    assert manager.ensure_loaded().status == "completed"
    startup = manager.startup if boundary == "refresh_resolution" else ProductionFeatureStartup(
        service, FeatureHost(), runtime_directory=config.dir / "feature-runtime" / owner,
        context_factory=lambda: bind_local_context(config, owner))
    try:
        if boundary == "initial_read":
            with open_kernel_lock(service.store.lock_path):
                result = startup.load_current()
        else:
            original = service.store.resolve_verified
            def resolve_under_real_lock(*args, **kwargs):
                with open_kernel_lock(service.store.lock_path):
                    return original(*args, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(service.store, "resolve_verified", resolve_under_real_lock)
                result = startup.load_current()
        assert result.status == "awaiting_release", result
        assert result.reason == "state_lock_busy", result
        assert startup.host.state(owner) != "fault"
        # Release alone restores authorization; no reinstall or user restart.
        assert startup.load_current().status == "completed"
        assert startup.host.enabled(owner)
    finally:
        manager.close()
        app.processEvents()


def test_state_notification_retries_authorization_after_read_contention(tmp_path):
    from tests.test_feature_package_transactions import StubChecker, _confirm

    app = QApplication.instance() or QApplication([])
    config = Config(base=tmp_path / "data")
    owner = "demo.hello-local"
    manager = attach_feature_management(config, FeatureHost(), feature_id=owner, management_only=True)
    source = build_example(Path("examples/mods/hello-local"), tmp_path / "package")
    service = manager.service
    service.runtime = None
    service.self_checker = StubChecker()
    assert _confirm(service, service.preflight_install(source)).status == "awaiting_startup_confirmation"
    assert manager.ensure_loaded().status == "completed"
    state = service.store.read()
    try:
        with open_kernel_lock(service.store.lock_path):
            manager._state_update(state)
            assert not manager.host.enabled(owner)
            assert manager._bootstrap_timer.isActive(), "state notification must arrange its own retry"
        pump(app, lambda: manager.host.enabled(owner))
        assert manager._bootstrap_attempts == 0, "successful load ends this contention episode"
    finally:
        manager.close()
        app.processEvents()
