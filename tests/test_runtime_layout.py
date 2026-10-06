"""Public data-root contract; all fixtures stay under pytest's owned root."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

PRODUCT = "dsh-pet-core-webm"


def executable(tmp_path):
    path = tmp_path / "program" / "pet.exe"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"generated executable placeholder")
    return path


def marker(exe, **changes):
    doc = {"format_version": 1, "product_id": PRODUCT, "data": "data"}
    doc.update(changes)
    (exe.parent / "portable.json").write_text(json.dumps(doc), encoding="utf8")


def test_normal_layout_has_new_product_root_and_stable_identity(tmp_path):
    from pet.runtime_layout import RuntimeLayout

    exe = executable(tmp_path)
    layout = RuntimeLayout.discover(exe, appdata=tmp_path / "appdata")
    second = RuntimeLayout.discover(exe, appdata=tmp_path / "appdata")
    assert layout.mode == "installed" and layout.data_root == tmp_path / "appdata" / PRODUCT
    assert layout.data_root_id == second.data_root_id
    assert len(layout.data_root_id) == 32
    assert layout.data_root != exe.parent


def test_portable_identity_survives_move_but_ipc_path_does_not(tmp_path, monkeypatch):
    from pet import runtime_layout as api

    monkeypatch.setattr(api, "filesystem_name", lambda path: "NTFS")
    exe = executable(tmp_path)
    marker(exe)
    first = api.RuntimeLayout.discover(exe, appdata=tmp_path / "ignored")
    assert first.mode == "portable" and first.data_root == exe.parent / "data"
    first.data_root.joinpath("config.json").write_text('{"generated":true}')
    moved = tmp_path / "moved"
    exe.parent.rename(moved)
    second = api.RuntimeLayout.discover(moved / exe.name, appdata=tmp_path / "ignored")
    assert first.data_root_id == second.data_root_id
    assert first.credential_namespace("official.ai-chat", "slot-2") == second.credential_namespace("official.ai-chat", "slot-2")
    assert first.path_identity != second.path_identity
    assert not (tmp_path / "ignored").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"data": "../other"},
        {"data": "other"},
        {"data": "E:/outside"},
        {"format_version": True},
        {"format_version": 2},
        {"product_id": "old-product"},
        {"extra": True},
    ],
)
def test_invalid_portable_marker_never_falls_back(tmp_path, monkeypatch, change):
    from pet import runtime_layout as api

    exe = executable(tmp_path)
    marker(exe, **change)
    monkeypatch.setattr(api, "filesystem_name", lambda path: "NTFS")
    with pytest.raises(api.RuntimeLayoutError, match="portable_marker_invalid"):
        api.RuntimeLayout.discover(exe, appdata=tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("raw", [b"{", b"{}", b'{"format_version":1,"format_version":1}', b"x" * 4097])
def test_corrupt_or_duplicate_marker_is_not_absent(tmp_path, raw):
    from pet.runtime_layout import RuntimeLayout, RuntimeLayoutError

    exe = executable(tmp_path)
    exe.parent.joinpath("portable.json").write_bytes(raw)
    with pytest.raises(RuntimeLayoutError, match="portable_marker_invalid"):
        RuntimeLayout.discover(exe, appdata=tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("filesystem", ["exFAT", "FAT32", "ReFS", "unknown"])
def test_portable_unsupported_filesystem_is_explicit_error(tmp_path, monkeypatch, filesystem):
    from pet import runtime_layout as api

    exe = executable(tmp_path)
    marker(exe)
    monkeypatch.setattr(api, "filesystem_name", lambda path: filesystem)
    with pytest.raises(api.RuntimeLayoutError, match="portable_requires_ntfs"):
        api.RuntimeLayout.discover(exe, appdata=tmp_path / "forbidden")
    assert not exe.parent.joinpath("data").exists()


def test_corrupt_root_identity_does_not_generate_replacement(tmp_path):
    from pet.runtime_layout import RuntimeLayout, RuntimeLayoutError

    exe = executable(tmp_path)
    layout = RuntimeLayout.discover(exe, appdata=tmp_path / "appdata")
    identity = layout.data_root / "data-root.json"
    identity.write_bytes(b"generated corrupted evidence")
    with pytest.raises(RuntimeLayoutError, match="data_root_identity_invalid"):
        RuntimeLayout.discover(exe, appdata=tmp_path / "appdata")
    assert identity.read_bytes() == b"generated corrupted evidence"


def test_explicit_layout_config_uses_data_root_without_legacy_copy(tmp_path, monkeypatch):
    from pet.config import Config
    from pet.runtime_layout import RuntimeLayout

    exe = executable(tmp_path)
    layout = RuntimeLayout.discover(exe, appdata=tmp_path / "appdata")
    monkeypatch.setattr(Config, "_migrate_legacy_config", lambda *_: pytest.fail("new Core must not automatically import legacy data"))
    config = Config(layout=layout, instance_id="slot-2")
    assert config.dir == layout.data_root and config.path.name == "config-slot-2.json"
    assert config.runtime_layout is layout


def test_credentials_are_partitioned_by_root_owner_and_instance(tmp_path):
    from pet.runtime_layout import RuntimeLayout

    exe = executable(tmp_path)
    one = RuntimeLayout.discover(exe, appdata=tmp_path / "one")
    two = RuntimeLayout.discover(exe, appdata=tmp_path / "two")
    values = {
        one.credential_namespace("official.ai-chat", ""),
        one.credential_namespace("official.ai-chat", "slot-2"),
        one.credential_namespace("official.screen-understanding", ""),
        two.credential_namespace("official.ai-chat", ""),
    }
    assert len(values) == 4
    with pytest.raises(ValueError):
        one.credential_namespace("third.party", "")


@pytest.mark.skipif(os.name != "nt", reason="native Windows volume API")
def test_native_volume_probe_reports_ntfs_for_owned_fixture(tmp_path):
    from pet.runtime_layout import filesystem_name

    assert filesystem_name(tmp_path) == "NTFS"


def test_data_root_boundary_failure_is_not_attribute_error_or_fallback(tmp_path, monkeypatch):
    from pet import runtime_layout as api

    exe = executable(tmp_path)
    target = tmp_path / "appdata" / PRODUCT
    original = api.io.safe_path

    def check(path):
        if path == target:
            raise api.io.StateError("unsafe_path")
        return original(path)

    monkeypatch.setattr(api.io, "safe_path", check)
    with pytest.raises(api.RuntimeLayoutError, match="data_root_boundary_invalid"):
        api.RuntimeLayout.discover(exe, appdata=target.parent)
    assert not target.exists()


def test_shared_consumers_use_portable_root_not_appdata(tmp_path, monkeypatch):
    from pet import runtime_layout as api
    from pet.catalog import external_character_dirs
    from pet.config import Config, default_data_directory
    from pet.content.paths import app_data_root
    from pet.harness_launcher import _probe_cache_path

    exe = executable(tmp_path)
    marker(exe)
    monkeypatch.setattr(api, "filesystem_name", lambda path: "NTFS")
    layout = api.RuntimeLayout.discover(exe, appdata=tmp_path / "forbidden")
    monkeypatch.setattr(api, "_current_layout", layout)
    assert Config().dir == default_data_directory() == app_data_root() == layout.data_root
    assert _probe_cache_path().parent == layout.data_root
    dirs = external_character_dirs()
    assert layout.data_root / "characters" in dirs
    assert not any("dsh-pet-standalone" in path.parts for path in dirs)


def test_layout_failure_prevents_application_import(tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    from pet import runtime_layout as api

    exe = executable(tmp_path)
    exe.parent.joinpath("portable.json").write_bytes(b"generated malformed marker")
    variant = ModuleType("build_variant")
    variant.VARIANT = "core-webm"
    monkeypatch.setitem(sys.modules, "build_variant", variant)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(api, "_current_layout", None)
    with pytest.raises(api.RuntimeLayoutError, match="portable_marker_invalid"):
        api.initialize_for_current_build()
    assert api.current_layout() is None


def test_readonly_data_root_does_not_switch_to_appdata(tmp_path, monkeypatch):
    from pet import runtime_layout as api

    exe = executable(tmp_path)
    marker(exe)
    monkeypatch.setattr(api, "filesystem_name", lambda path: "NTFS")
    original = Path.open

    def opened(path, *args, **kwargs):
        if path.name.startswith(".layout-write-"):
            raise PermissionError("generated permission boundary failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(api.RuntimeLayoutError, match="data_root_not_writable"):
        api.RuntimeLayout.discover(exe, appdata=tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()


def test_screen_vault_binding_keeps_generated_secret_after_portable_move(tmp_path, monkeypatch):
    from pet import credentials
    from pet import runtime_layout as api
    from pet.config import Config
    from pet.feature_host_bindings import bind_screen_configuration
    from tests.test_feature_config_ports import MemoryVault

    backend = MemoryVault()
    monkeypatch.setattr(credentials, "secure_backend", lambda: backend)
    monkeypatch.setattr(api, "filesystem_name", lambda path: "NTFS")
    exe = executable(tmp_path / "first")
    marker(exe)
    layout = api.RuntimeLayout.discover(exe)
    cfg = Config(layout=layout, instance_id="one")
    _, vault, _ = bind_screen_configuration(cfg)
    ref = vault.save("fixture", "https://generated.invalid", "GENERATED-SECRET")
    old_parent = exe.parent
    new_parent = tmp_path / "moved"
    old_parent.rename(new_parent)
    moved = api.RuntimeLayout.discover(new_parent / exe.name)
    _, restored, _ = bind_screen_configuration(Config(layout=moved, instance_id="one"))
    assert restored.service == vault.service
    assert restored.acquire(ref, "fixture", "https://generated.invalid", "manual_look") == "GENERATED-SECRET"
    _, other, _ = bind_screen_configuration(Config(layout=moved, instance_id="two"))
    with pytest.raises(credentials.CredentialError, match="scope_denied"):
        other.acquire(ref, "fixture", "https://generated.invalid", "manual_look")
    assert "GENERATED-SECRET" not in "".join(p.read_text(encoding="utf-8") for p in moved.data_root.rglob("*.json"))


def test_ai_vault_operations_do_not_grant_screen_tasks():
    from pet.credentials import CredentialError, CredentialVaultPort
    from tests.test_feature_config_ports import MemoryVault

    vault = CredentialVaultPort("official.ai-chat", "fixture", backend=MemoryVault())
    ref = vault.save("fixture", "https://generated.invalid", "GENERATED-SECRET")
    assert vault.acquire(ref, "fixture", "https://generated.invalid", "chat.send") == "GENERATED-SECRET"
    assert vault.acquire(ref, "fixture", "https://generated.invalid", "files.interpret") == "GENERATED-SECRET"
    with pytest.raises(CredentialError, match="operation_denied"):
        vault.acquire(ref, "fixture", "https://generated.invalid", "analyze_frame")


def test_other_core_copy_cannot_access_root_during_uninstall(tmp_path):
    from pet import feature_state_io as io
    from pet.runtime_layout import RuntimeLayout, RuntimeLayoutError

    first = RuntimeLayout.discover(executable(tmp_path / "first-copy"), appdata=tmp_path / "appdata")
    second = RuntimeLayout.discover(executable(tmp_path / "second-copy"), appdata=tmp_path / "appdata")
    assert first.executable != second.executable and first.data_root_id == second.data_root_id
    with first.acquire_session():
        with pytest.raises(io.StateError, match="lock_busy"):
            io.open_kernel_lock(first.data_root / "core-removal.lock")
    with io.open_kernel_lock(first.data_root / "core-removal.lock"):
        with pytest.raises(RuntimeLayoutError, match="core_removal_in_progress"):
            second.acquire_session()
        with pytest.raises(RuntimeLayoutError, match="core_removal_in_progress"):
            RuntimeLayout.discover(second.executable, appdata=tmp_path / "appdata")
    with second.acquire_session():
        pass


def test_closed_maintenance_exemption_keeps_import_pending_guard(tmp_path, monkeypatch):
    import sys
    import types

    from pet import feature_state_io as io
    from pet import runtime_layout as api

    exe = tmp_path / "core" / "dsh-pet-core-webm.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"generated core placeholder")
    normal = api.RuntimeLayout.discover(exe, appdata=tmp_path / "appdata")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "argv", [str(exe), "--core-maintenance", "uninstall"])
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="core-webm"))
    monkeypatch.setattr(api, "_current_layout", None)
    monkeypatch.setattr(api, "_runtime_session", None)
    monkeypatch.setattr(api, "_runtime_removal_mode", False)
    monkeypatch.setattr(api, "shell_appdata", lambda: tmp_path / "appdata")
    with io.open_kernel_lock(normal.data_root / "core-removal.lock"):
        result = api.initialize_for_current_build(for_core_removal=True)
        assert result == normal
        assert api._runtime_session is not None
        api._runtime_session.close()
        monkeypatch.setattr(api, "_current_layout", None)
        monkeypatch.setattr(api, "_runtime_session", None)
        pending = normal.data_root / "data-import" / "pending.json"
        pending.parent.mkdir()
        pending.write_text('{"generated":"accepted import"}')
        with pytest.raises(api.RuntimeLayoutError, match="data_import_recovery_required"):
            api.initialize_for_current_build(for_core_removal=True)


@pytest.mark.parametrize("extra", [["--settings"], ["--worker", "screen"], ["--install-local-packages", "E:/generated"]])
def test_maintenance_gate_exemption_rejects_mixed_entry_before_data(tmp_path, monkeypatch, extra):
    import sys
    import types

    from pet import runtime_layout as api

    monkeypatch.setattr(api, "_current_layout", None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "dsh-pet-core-webm.exe"))
    monkeypatch.setattr(sys, "argv", [sys.executable, "--core-maintenance", "uninstall", *extra])
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="core-webm"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "must-not-create"))
    with pytest.raises(api.RuntimeLayoutError, match="core_removal_entry_required"):
        api.initialize_for_current_build(for_core_removal=True)
    assert not (tmp_path / "must-not-create").exists()


def test_maintenance_rejects_appdata_override_before_root_creation(tmp_path, monkeypatch):
    import sys
    import types

    from pet import runtime_layout as api

    exe = tmp_path / "core" / "dsh-pet-core-webm.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"generated placeholder")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "argv", [str(exe), "--core-maintenance", "uninstall"])
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="core-webm"))
    monkeypatch.setattr(api, "_current_layout", None)
    monkeypatch.setattr(api, "_runtime_session", None)
    monkeypatch.setattr(api, "_runtime_removal_mode", False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "overridden-appdata"))
    monkeypatch.setattr(api, "shell_appdata", lambda: tmp_path / "shell-appdata", raising=False)
    with pytest.raises(api.RuntimeLayoutError, match="core_removal_root_mismatch"):
        api.initialize_for_current_build(for_core_removal=True)
    assert not (tmp_path / "overridden-appdata").exists()
    assert not (tmp_path / "shell-appdata").exists()


def test_maintenance_requires_parent_root_barrier(tmp_path, monkeypatch):
    import sys
    import types

    from pet import runtime_layout as api

    exe = tmp_path / "core" / "dsh-pet-core-webm.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"generated placeholder")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "argv", [str(exe), "--core-maintenance", "uninstall"])
    monkeypatch.setitem(sys.modules, "build_variant", types.SimpleNamespace(VARIANT="core-webm"))
    monkeypatch.setattr(api, "_current_layout", None)
    monkeypatch.setattr(api, "_runtime_session", None)
    monkeypatch.setattr(api, "_runtime_removal_mode", False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(api, "shell_appdata", lambda: tmp_path / "appdata", raising=False)
    with pytest.raises(api.RuntimeLayoutError, match="core_removal_parent_missing"):
        api.initialize_for_current_build(for_core_removal=True)
    assert not (tmp_path / "appdata").exists()
