"""Human acceptance builds use real boundaries and a fixed non-driver entry."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from scripts.build_screen_delivery import prepare_core
from tests.test_feature_probe_windows import _bundle

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _restore_process_arguments(monkeypatch):
    monkeypatch.setattr(sys, "argv", list(sys.argv))


def _manual_root(tmp_path):
    root = tmp_path / "inputs"
    (root / "pet").mkdir(parents=True)
    (root / "pet/feature_build_policy.py").write_text("OFFICIAL_FEATURE_TRUST_ANCHORS = ()\nVALIDATION_BUILD = False\n", encoding="utf-8")
    (root / "pet/__init__.py").write_text("", encoding="utf-8")
    (root / "packaging").mkdir()
    (root / "packaging/phase4b_manual_entry.py").write_text('raise RuntimeError("preparation must not execute entry")\n', encoding="utf-8")
    (root / "features/screen_understanding/host").mkdir(parents=True)
    (root / "features/screen_understanding/common").mkdir()
    return root


def test_fixed_manual_snapshot_uses_real_boundaries_without_executing_entry(tmp_path):
    root = _manual_root(tmp_path)
    original = (root / "pet/feature_build_policy.py").read_bytes()
    bundle, checksum = _bundle(tmp_path)
    output = tmp_path / "out"
    manifest = prepare_core(
        root,
        output,
        chat=False,
        public_key="aa" * 32,
        entrypoint=root / "packaging/phase4b_manual_entry.py",
        probe_bundle=bundle,
        probe_manifest_sha256=checksum,
    )
    assert manifest["scope"] == "no-screen-core-manual-acceptance"
    assert manifest["manual_acceptance_only"] is True
    assert manifest["production_management"] is True
    assert not (output / "source/validation_boundaries.py").exists()
    policy = (output / "source/pet/feature_build_policy.py").read_text(encoding="utf-8")
    assert "manual-acceptance-only" in policy
    assert "MANUAL_ACCEPTANCE_BUILD = True" in policy
    assert "VALIDATION_BUILD = True" in policy
    assert "MANUAL_ACCEPTANCE_ONLY = True" in (output / "source/validation_config.py").read_text(encoding="utf-8")
    assert (root / "pet/feature_build_policy.py").read_bytes() == original


def test_manual_snapshot_rejects_tampered_helper_before_output(tmp_path):
    root = _manual_root(tmp_path)
    bundle, checksum = _bundle(tmp_path)
    (bundle / "probe.exe").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="bundle_integrity"):
        prepare_core(
            root,
            tmp_path / "out",
            chat=True,
            public_key="aa" * 32,
            entrypoint=root / "packaging/phase4b_manual_entry.py",
            probe_bundle=bundle,
            probe_manifest_sha256=checksum,
        )
    assert not (tmp_path / "out").exists()


def test_arbitrary_manual_entry_is_never_a_build_input(tmp_path):
    root = _manual_root(tmp_path)
    untrusted = root / "packaging/user_entry.py"
    untrusted.write_text("pass\n", encoding="utf-8")
    bundle, checksum = _bundle(tmp_path)
    with pytest.raises(ValueError, match="unexpected management entry"):
        prepare_core(root, tmp_path / "out", chat=False, public_key="aa" * 32, entrypoint=untrusted, probe_bundle=bundle, probe_manifest_sha256=checksum)
    assert not (tmp_path / "out").exists()


def _entry():
    spec = importlib.util.spec_from_file_location("owned_manual_entry", ROOT / "packaging/phase4b_manual_entry.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manual_entry_cannot_run_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(RuntimeError, match="frozen manual acceptance"):
        _entry().main(["owned", "--settings"])


def test_settings_entry_is_production_settings_not_automated_driver(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    config = types.ModuleType("validation_config")
    config.MANUAL_ACCEPTANCE_ONLY = True
    config.ENABLE_CHAT = False
    monkeypatch.setitem(sys.modules, "validation_config", config)
    from pet import feature_build_policy as policy

    monkeypatch.setattr(policy, "MANUAL_ACCEPTANCE_BUILD", True, raising=False)
    monkeypatch.setattr(policy, "VALIDATION_BUILD", True)
    monkeypatch.setattr(policy, "PROBE_BUNDLE_MANIFEST_SHA256", "aa" * 32)
    import pet.__main__ as normal

    seen = []
    monkeypatch.setattr(normal, "_run_settings", lambda: seen.append("settings") or 17)
    monkeypatch.setattr(entry, "protect_autostart", lambda: seen.append("autostart_guard"))
    assert entry.main(["owned", "--settings", "--settings-page", "extensions"]) == 17
    assert seen == ["autostart_guard", "settings"]
    assert "validation_boundaries" not in sys.modules


def test_manual_build_rejects_synthetic_worker_before_creating_output(tmp_path):
    from scripts import build_feature_management_manual as manual
    from scripts.build_screen_worker import prepare_build

    worker = tmp_path / "worker"
    prepare_build(ROOT, worker, synthetic=True)
    bundle, checksum = _bundle(tmp_path)
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="boundary does not match"):
        manual.main([str(output), "--worker-build", str(worker), "--probe-bundle", str(bundle), "--probe-manifest-sha256", checksum])
    assert not output.exists()


def test_manual_builder_never_serializes_private_key_or_installs_fake_boundaries():
    source = (ROOT / "scripts/build_feature_management_manual.py").read_text(encoding="utf-8")
    assert "private_bytes" not in source
    assert "synthetic=True" not in source
    assert "phase4b_validation_entry" not in source
    entry = (ROOT / "packaging/phase4b_manual_entry.py").read_text(encoding="utf-8")
    for replacement in ("validation_boundaries", "Synthetic", "local-http", "keyring.set_keyring"):
        assert replacement not in entry


def test_frozen_automated_configuration_cannot_enter_manual_ui(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    config = types.ModuleType("validation_config")
    config.MANUAL_ACCEPTANCE_ONLY = False
    monkeypatch.setitem(sys.modules, "validation_config", config)
    with pytest.raises(RuntimeError, match="manual acceptance policy"):
        _entry().main(["owned", "--settings"])


def test_manual_entry_excludes_system_autostart_without_touching_real_backends(monkeypatch):
    from pet import autostart

    calls = []
    for name in ("cleanup_stale_entries", "is_enabled", "enable", "disable", "set_enabled"):
        monkeypatch.setattr(autostart, name, lambda *args: calls.append(args) or True)
    _entry().protect_autostart()
    assert autostart.cleanup_stale_entries() == 0
    assert autostart.is_enabled() is False
    assert autostart.enable() is False
    assert autostart.disable() is False
    assert autostart.set_enabled(True) is False
    assert calls == []


def test_worker_entry_retains_production_handoff_before_gui_or_autostart(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    config = types.ModuleType("validation_config")
    config.MANUAL_ACCEPTANCE_ONLY = True
    monkeypatch.setitem(sys.modules, "validation_config", config)
    from pet import feature_build_policy as policy

    monkeypatch.setattr(policy, "MANUAL_ACCEPTANCE_BUILD", True, raising=False)
    monkeypatch.setattr(policy, "VALIDATION_BUILD", True)
    monkeypatch.setattr(policy, "PROBE_BUNDLE_MANIFEST_SHA256", "aa" * 32)
    import pet.__main__ as normal

    seen = []
    monkeypatch.setattr(normal, "_run_worker", lambda worker: seen.append(worker) or 78)
    monkeypatch.setattr(entry, "protect_autostart", lambda: pytest.fail("Worker must not touch GUI/system startup"))
    assert entry.main(["owned", "--worker", "screen"]) == 78
    assert seen == ["screen"]
