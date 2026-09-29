"""Real official factory receives Core ports, including in an isolated package."""

import os
import subprocess
import sys
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from pet.config import Config
from pet.official_features import SCREEN_OWNER
from tests.screen_fakes import MemoryVault


@pytest.fixture
def bound(tmp_path, monkeypatch):
    from pet.feature_host_bindings import bind_screen_context
    from pet.official_features import default_feature_host

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("pet.credentials.secure_backend", lambda: MemoryVault())
    cfg = Config(tmp_path / "config.json")
    cfg.save()
    window = QWidget()
    window.feature_host = default_feature_host()
    window.show_bubble = lambda *a, **kw: None
    context = bind_screen_context(cfg, window=window)
    yield app, cfg, window, context
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


def test_real_factory_uses_bound_ports_and_existing_storage(bound):
    from features.screen_understanding.host.factory import create_host

    _, cfg, window, context = bound
    definition = create_host()
    assert definition.owner == SCREEN_OWNER
    runtime = definition.runtime_factory(context, worker_mode="in_process")
    try:
        assert not hasattr(runtime, "win") and not hasattr(runtime, "cfg")
        assert runtime.context.vision.config is context.configuration
        assert runtime.context.vision.vault is context.credentials
        assert runtime.limiter.consume_budget()
        assert (cfg.dir / "proactive_screen_state.json").is_file()
        assert runtime.context.window_state().window_id == f"window:{id(window)}"
    finally:
        runtime.dispose()


def test_external_context_has_no_source_fallback(bound):
    from features.screen_understanding.host.factory import create_host

    _, _, _, context = bound
    calls = []
    context = replace(context, allow_in_process=False, worker_launch_factory=lambda: calls.append(1))
    runtime = create_host().runtime_factory(context, worker_mode="auto")
    try:
        assert runtime.context.allow_in_process is False
        runtime._on_worker_failed("test_fault")
        assert not runtime._worker_fallback
        assert calls == []  # No eager worker for an unconfigured feature.
    finally:
        runtime.dispose()


def test_factory_rejects_raw_config(bound):
    from features.screen_understanding.host.factory import create_host

    _, cfg, _, _ = bound
    with pytest.raises(TypeError, match="bound"):
        create_host().runtime_factory(cfg)


def test_settings_factory_does_not_receive_global_config(bound):
    from features.screen_understanding.host.factory import create_host

    _, _, _, context = bound
    settings = create_host().settings_factory(context)
    try:
        assert settings.context.vision.config is context.configuration
        assert not hasattr(settings.context, "cfg")
    finally:
        settings.dispose()


def test_no_screen_source_import_for_core_modules():
    code = r"""
import importlib.abc
import sys
class RejectScreen(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('features.screen_understanding', 'pet.screen_understanding')) or fullname in ('pet.vision', 'pet.proactive', 'pet.workers.proactive_screen_adapter', 'pet.workers.proactive_screen_worker'):
            raise AssertionError('screen import: ' + fullname)
sys.meta_path.insert(0, RejectScreen())
from pet import feature_distribution
feature_distribution.BUILTIN_SCREEN = False
from pet.official_features import default_feature_host
assert default_feature_host().owners() == ()
import pet.window
import pet.multi_window_shared
import pet.modern_settings_dialog
import pet.app
print('CORE_WITHOUT_SCREEN_IMPORT_OK')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CORE_WITHOUT_SCREEN_IMPORT_OK" in result.stdout


@pytest.mark.parametrize("shared", [False, True])
def test_real_core_windows_without_screen_package(tmp_path, shared):
    code = r"""
import importlib.abc
import runpy
import sys
class RejectScreen(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('features.screen_understanding', 'pet.screen_understanding')) or fullname in ('pet.vision', 'pet.proactive', 'pet.proactive_memory', 'pet.proactive_limiter', 'pet.workers.proactive_screen_adapter', 'pet.workers.proactive_screen_worker'):
            raise AssertionError('screen import: ' + fullname)
sys.meta_path.insert(0, RejectScreen())
from pet import feature_distribution
feature_distribution.BUILTIN_SCREEN = False
from pet.official_features import default_feature_host
assert default_feature_host().owners() == ()
runpy.run_module('tests.desktop_query_core_probe', run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), str(int(shared))],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"query_probe": "ok"' in result.stdout
    assert '"worker_starts": 0' in result.stdout


def test_verified_canonical_factory_uses_version_namespace_and_ports(bound, tmp_path):
    import hashlib
    import json
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from pet.plugins.feature_packages import FeaturePackageLoader, FeaturePackageVerifier

    root = tmp_path / "package"
    root.mkdir()
    files = {}
    source = Path(__file__).resolve().parents[1] / "features/screen_understanding"
    for section in ("host", "common"):
        for item in sorted((source / section).rglob("*.py")):
            relative = item.relative_to(source).as_posix()
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = item.read_bytes()
            target.write_bytes(data)
            files[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    (root / "worker").mkdir()
    (root / "worker/worker.exe").write_bytes(b"not launched by this test")
    data = (root / "worker/worker.exe").read_bytes()
    files["worker/worker.exe"] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    manifest = dict(
        id=SCREEN_OWNER,
        version="1.0.0",
        api_version="1",
        core_requires=">=4.2.1,<6.0.0",
        platforms=[sys.platform],
        capabilities=["screen.capture"],
        factory="screen-understanding/v1",
        worker={"path": "worker/worker.exe", "args": []},
        files=files,
    )
    raw = json.dumps(manifest).encode()
    key = Ed25519PrivateKey.generate()
    (root / "manifest.json").write_bytes(raw)
    (root / "manifest.sig").write_bytes(key.sign(raw))
    verifier = FeaturePackageVerifier(
        core_version="4.2.1",
        api_version="1",
        allowed_capabilities={"screen.capture"},
        trust_anchors={"isolated-test": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
    )
    loader = FeaturePackageLoader(verifier)
    descriptor = verifier.verify(root)
    before = list(sys.path)
    handle = loader.load_host(descriptor)
    try:
        definition = handle.factory()
        _, _, _, context = bound
        runtime = definition.runtime_factory(replace(context, allow_in_process=False))
        try:
            assert type(runtime).__module__.startswith("_pet_official_screen_")
            assert runtime.context.vision.config is context.configuration
            assert runtime.context.allow_in_process is False
            assert sys.path == before
        finally:
            runtime.dispose()
    finally:
        handle.close()
    assert not loader.lease_counts(descriptor).can_remove  # Loaded Python code remains pinned.


def test_external_host_idle_gate_uses_platform_port_without_worker_import(bound, monkeypatch):
    import builtins
    from types import SimpleNamespace

    from features.screen_understanding.host.factory import runtime_context
    from features.screen_understanding.host.runtime import ProactiveScreenWatcher
    from pet.desktop_query import QueryResult

    _, _, _, context = bound
    queries = []
    desktop = SimpleNamespace(idle_time=lambda: (queries.append(1), QueryResult("ok", 20.0))[1])
    runtime = ProactiveScreenWatcher(runtime_context(replace(context, desktop=desktop, allow_in_process=False)), worker_mode="auto")
    original_import = builtins.__import__

    def reject_worker(name, *args, **kwargs):
        if "worker" in name and (name.endswith("vision") or name == "worker"):
            raise AssertionError("host must not import worker for idle query")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_worker)
    monkeypatch.setattr(runtime._vision_config, "resolve", lambda mode: SimpleNamespace(ready=True))
    context.preferences.stage({"enabled": True, "whitelist": ["code.exe"], "require_idle": True, "min_idle_seconds": 10})
    bound[2].show()
    monkeypatch.setattr(runtime, "_authorized", lambda: True)
    # Keep other gates deterministic; this checks the host's real idle method.
    try:
        assert runtime._automatic_allowed_now() is True
        assert queries == [1]
    finally:
        runtime.dispose()
