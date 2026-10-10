"""R01: regressions at real Qt/config and delivery public seams; generated data only."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_saved_ai_configuration_is_seen_by_existing_runtime(tmp_path, app):
    from features.ai_chat.host.config import AiConfiguration
    from tests.test_ai_host_contracts import binding

    cfg, host, context = binding(tmp_path)
    runtime = host.runtime(context)
    editor = AiConfiguration(context)
    settings = editor.chat_settings()
    provider = settings.active_config
    provider.model = "generated-new-model"
    provider.base_url = "http://127.0.0.1:34999"
    provider.api_key_ref = editor.save_provider_secret(provider, "generated-only-key")
    editor.set_chat_settings(settings)
    editor.save()
    try:
        assert runtime.config.chat_settings().active_config.model == "generated-new-model"
    finally:
        runtime.close()
        app.processEvents()


def test_auto_off_refresh_does_not_cancel_manual_screen_request(tmp_path, app, monkeypatch):
    from tests.screen_fakes import MemoryVault
    from tests.test_proactive_watcher_worker import FakeAdapter, _watcher

    monkeypatch.setattr("pet.credentials.secure_backend", lambda: MemoryVault())
    # Keep a single fake OS store across the real configuration operations.
    backend = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    watcher = _watcher(tmp_path)
    watcher.cfg.set("proactive_screen", {**watcher.cfg.get("proactive_screen"), "enabled": False})
    watcher.worker_mode = "auto"
    adapter = FakeAdapter()
    watcher._worker_adapter = adapter
    watcher._worker_fallback = False
    results = []
    try:
        assert watcher.request_manual_look(watcher._vision_config.resolve("manual").request, "", "", lambda text, url, error: results.append((text, error)))
        generation = watcher._generation
        request = watcher._manual_request_id
        watcher.apply_config()
        app.processEvents()
        assert watcher._manual_request_id == request
        assert watcher._generation == generation
        assert results == []
        assert adapter.active
    finally:
        watcher.dispose()
        app.processEvents()


def test_production_worker_closure_includes_identity_dependency():
    from scripts.build_screen_worker import REQUIRED_MODULES, WORKER_SOURCES

    assert "pet/official_features.py" in WORKER_SOURCES
    assert "pet.official_features" in REQUIRED_MODULES
    assert "pet.feature_install_state" in REQUIRED_MODULES


def test_core_only_uninstall_does_not_load_config_or_dlc(tmp_path, monkeypatch, app):
    from pet import core_maintenance, runtime_layout

    exe = tmp_path / "core" / "dsh-pet-core-webm.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"generated")
    retained = tmp_path / "data" / "plugins" / "third-party.example" / "staging"
    retained.mkdir(parents=True)
    marker = retained / "unknown-original.zip"
    marker.write_bytes(b"retain")
    layout = SimpleNamespace(executable=exe)
    monkeypatch.setattr(runtime_layout, "initialize_for_current_build", lambda **kw: layout)
    monkeypatch.setattr(runtime_layout, "current_layout", lambda: layout)
    monkeypatch.setattr("sys.executable", str(exe))

    def forbidden(*args, **kwargs):
        raise AssertionError("Core uninstall must not load Config or DLC transactions")

    monkeypatch.setattr("PySide6.QtWidgets.QApplication", lambda *args: app)
    monkeypatch.setattr("pet.config.Config", forbidden)
    monkeypatch.setattr(core_maintenance, "finish_core_removal", lambda *a, **kw: 0)
    assert core_maintenance.run_uninstall() == 0
    assert marker.read_bytes() == b"retain"


def test_external_worker_fault_allows_explicit_verified_retry(tmp_path, app, monkeypatch):
    from tests.screen_fakes import MemoryVault
    from tests.test_proactive_watcher_worker import FakeAdapter, _watcher

    backend = MemoryVault()
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: backend)
    watcher = _watcher(tmp_path)
    watcher.context = replace(watcher.context, allow_in_process=False)
    watcher.worker_mode = "auto"
    watcher._worker_fallback = False
    adapter = FakeAdapter()
    watcher._worker_adapter = adapter
    watcher._execution_fault = True
    adapter.state, adapter.active, adapter.ready = "stopped", False, False
    calls, results = [], []

    def start(config):
        calls.append(config)
        adapter.state, adapter.active = "handshaking", True
        return True

    adapter.start = start
    try:
        provider = watcher._vision_config.resolve("manual").request
        assert watcher.request_manual_look(provider, "", "", lambda *args: results.append(args))
        assert calls and not results
        assert not watcher._execution_fault
        assert not watcher._worker_fallback
        assert watcher._pending_manual_look is not None
    finally:
        watcher.dispose()
        app.processEvents()


def test_worker_diagnostic_redacts_untrusted_stderr_but_keeps_missing_module(tmp_path, app, monkeypatch, caplog):
    import logging

    from tests.screen_fakes import MemoryVault
    from tests.test_proactive_watcher_worker import _watcher

    monkeypatch.setattr("pet.credentials.secure_backend", lambda: MemoryVault())
    watcher = _watcher(tmp_path)
    try:
        with caplog.at_level(logging.DEBUG):
            watcher._on_worker_diagnostic("stderr", {"line": "Authorization: GENERATED-PRIVATE-KEY"})
            watcher._on_worker_diagnostic("stderr", {"line": "ModuleNotFoundError: No module named 'pet.official_features'"})
        assert "GENERATED-PRIVATE-KEY" not in caplog.text
        assert "pet.official_features" in caplog.text
    finally:
        watcher.dispose()


@pytest.mark.parametrize("source", ["setup", "external"])
@pytest.mark.parametrize("state", ["staging", "pending_uninstall", "broken_ledger"])
def test_core_only_removal_preserves_every_dlc_state(tmp_path, monkeypatch, app, source, state):
    from pet import core_maintenance, runtime_layout

    exe = tmp_path / "core" / "dsh-pet-core-webm.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"generated")
    retained = tmp_path / "data"
    retained.mkdir()
    files = [retained / name for name in (source + ".zip", state + ".json", "config.json", "personal.txt")]
    for file in files:
        file.write_bytes((source + state).encode())
    before = {p.name: p.read_bytes() for p in files}
    layout = SimpleNamespace(executable=exe)
    monkeypatch.setattr(runtime_layout, "current_layout", lambda: layout)
    scopes = []
    monkeypatch.setattr("pet.agent_link.DshMonitor.uninstall_bridge", lambda **kw: scopes.append(kw["scope_root"]) or True)
    monkeypatch.setattr("pet.core_registration_cleanup.remove_owned_autostart", lambda *a, **kw: SimpleNamespace(status="completed"))
    assert core_maintenance.finish_core_removal(core_maintenance.CoreRemovalPermit(layout), executable=exe) == 0
    assert scopes == [exe.parent / "_internal" / "integrations" / "dsh-pet-bridge"]
    assert {p.name: p.read_bytes() for p in files} == before


@pytest.mark.parametrize("owner,version", [("official.ai-chat", "1.0.3"), ("official.screen-understanding", "1.0.3")])
def test_new_release_material_requires_central_api_core(tmp_path, owner, version):
    from scripts.feature_release_materials import prepare_unsigned_feature

    selected = "ai_chat" if owner == "official.ai-chat" else "screen_understanding"
    repo = tmp_path / "repo"
    host = repo / "features" / selected / "host"
    host.mkdir(parents=True)
    (host / "factory.py").write_text("# generated fixture only\n")
    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "proactive-screen-worker.exe").write_bytes(b"MZ generated fixture")
    manifest = prepare_unsigned_feature(
        repo,
        tmp_path / "package",
        owner,
        key_id="local",
        version=version,
        owned_root=tmp_path,
        worker_bundle=worker if selected == "screen_understanding" else None,
    )
    assert manifest["version"] == version
    assert manifest["core_requires"] == (">=4.2.3,<6.0.0" if owner == "official.ai-chat" else ">=4.2.4,<6.0.0")


def test_both_local_package_paths_use_new_versions_and_minimum_core(tmp_path):
    import json

    from scripts.build_screen_delivery import assemble_ai_package, assemble_package

    repo = tmp_path / "repo"
    for selected in ("ai_chat", "screen_understanding"):
        host = repo / "features" / selected / "host"
        host.mkdir(parents=True)
        (host / "factory.py").write_text("# generated fixture only\n")
    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "proactive-screen-worker.exe").write_bytes(b"MZ generated fixture")
    ai = assemble_ai_package(repo, tmp_path / "ai")
    screen = assemble_package(repo, tmp_path / "screen", worker)
    for package, version, core in ((ai, "1.0.4", "4.2.5"), (screen, "1.0.4", "4.2.5")):
        manifest = json.loads((package / "manifest.json").read_text())
        assert manifest["version"] == version
        assert manifest["core_requires"] == f">={core},<6.0.0"


def test_frozen_acceptance_matrix_targets_repair_versions():
    from scripts.validate_phase5a_delivery import OWNERS

    assert OWNERS["official.ai-chat"][0] == "1.0.3"
    assert OWNERS["official.screen-understanding"][0] == "1.0.3"
