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
