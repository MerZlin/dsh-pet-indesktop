"""Public seams for the local MOD center; no real user data or network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet.content import CharacterRegistry, ContentManager
from tests.test_content_dlc import _write_package
from tests.test_feature_package_transactions import _confirm, _package, _service


def test_feature_import_can_commit_disabled_without_transient_enable(tmp_path):
    source, verifier, _ = _package(tmp_path / "source")
    service = _service(tmp_path, source, verifier)
    result = service.preflight_install(source, enabled=False)
    assert result.plan is not None
    assert result.plan.install_enabled is False
    applied = _confirm(service, result)
    assert applied.status in {"completed", "awaiting_startup_confirmation"}
    state = service.store.read().state
    assert state.active == "1.2.3" and state.enabled is False
    assert service._load(result.operation_id)["plan"]["install_enabled"] is False


def test_resource_import_disabled_and_update_preserves_state(tmp_path):
    registry = CharacterRegistry(bundled_root=tmp_path / "bundled", installed_root=tmp_path / "data/characters", legacy_roots=[])
    manager = ContentManager(data_root=tmp_path / "data", registry=registry)
    first = _write_package(tmp_path / "first")
    installed = manager.install(first, allow_unsigned=True, enabled=False)
    assert not installed.activated
    assert registry.get("demo") is None
    assert manager.is_enabled("test.character.demo") is False
    second = _write_package(tmp_path / "second", version="1.0.1")
    manager.install(second, allow_unsigned=True, enabled=None)
    assert not manager.is_enabled("test.character.demo")
    assert registry.get("demo") is None
    manager.set_enabled("test.character.demo", True)
    assert registry.get("demo").manifest.version == "1.0.1"
    # A duplicate import does not undo the user's choice.
    manager.install(second, allow_unsigned=True, enabled=False)
    assert manager.is_enabled("test.character.demo")
    manager.set_enabled("test.character.demo", False)
    assert registry.get("demo") is None
    assert first.is_dir() and second.is_dir()


def test_resource_description_is_optional_and_plain_text(tmp_path):
    source = _write_package(tmp_path)
    manager = ContentManager(data_root=tmp_path / "data")
    assert manager.validate(source, allow_unsigned=True).manifest.description == ""
    p = source / "manifest.json"
    data = json.loads(p.read_text())
    data["description"] = "<b>不是 HTML</b>\n替换动作"
    p.write_text(json.dumps(data), encoding="utf-8")
    result = manager.validate(source, allow_unsigned=True)
    assert result.valid and result.manifest.description == data["description"]


@pytest.mark.parametrize("field,value", [("name", 42), ("description", {}), ("description", "x" * 4097)])
def test_feature_display_metadata_is_bounded(tmp_path, field, value):
    from pet.local_package_intents import LocalPackageRoute
    from pet.plugins.package_trust import PackageVerificationError
    source, _, manifest = _package(tmp_path / "source")
    manifest.update(format_version=2, key_id="local-user", execution_kind="host-worker")
    manifest[field] = value
    with pytest.raises(PackageVerificationError):
        LocalPackageRoute.from_manifest(source, json.dumps(manifest).encode())


def test_feature_optional_display_metadata_has_no_official_identity_branch(tmp_path):
    from pet.local_package_intents import LocalPackageRoute
    source, _, manifest = _package(tmp_path / "source")
    manifest.update(id="sample.offline", factory="sample-offline/v1", format_version=2, key_id="local-user", execution_kind="host-worker", name="离线示例", description="作者的纯文本说明")
    route = LocalPackageRoute.from_manifest(source, json.dumps(manifest).encode())
    assert route.feature_id == "sample.offline"
    assert route.manifest["description"] == "作者的纯文本说明"


def test_v1_definition_explicitly_opts_into_generic_mount():
    from pet.mod_api.v1 import FeatureDefinition
    definition = FeatureDefinition("sample.offline", (), lambda *_: None)
    assert definition.mount_contract == "mod/v1"
    from pet.plugins.feature_host import FeatureDefinition as LegacyDefinition
    assert LegacyDefinition("sample.legacy", (), lambda *_: None).mount_contract == ""


def test_mod_selection_is_limited_to_visible_results():
    from pet.mod_management import ModEntry, ModSelection
    rows = [ModEntry("sample.one", "功能扩展", "一", "1.0.0", "", False, Path("one")), ModEntry("sample.two", "角色资源", "二", "1.0.0", "", False, Path("two"))]
    selection = ModSelection()
    selection.set_visible(rows)
    selection.select_all()
    assert selection.selected == {"sample.one", "sample.two"}
    selection.set_visible(rows[1:])
    assert not selection.selected


def test_resource_removal_refuses_in_use_when_fallback_is_rejected(tmp_path):
    from pet.mod_management import ResourceMods
    source = _write_package(tmp_path / "source")
    resources = ResourceMods(tmp_path / "data", prepare_removal=lambda _entry: False)
    entry = resources.install(source)
    outcome = resources.remove(entry.id)
    assert not outcome.success
    assert entry.path.exists() and source.exists()


def test_generic_runtime_mount_stops_old_actions_and_restarts_without_factory_branch(tmp_path):
    from PySide6.QtWidgets import QApplication

    from pet.config import Config
    from pet.feature_host_bindings import bind_local_context
    from pet.mod_api.v1 import Contribution, FeatureDefinition
    from pet.mod_runtime import GenericModMounts
    from pet.plugins.feature_host import FeatureHost

    app = QApplication.instance() or QApplication([])
    host = FeatureHost()
    events = []

    class Runtime:
        commands = {"hello": lambda: events.append("hello")}
        def start(self): events.append("start")
        def stop(self): events.append("stop")
        def close(self): events.append("close")

    owner = "sample.offline"
    host.provide(FeatureDefinition(owner, (Contribution("hello", "menu", command="hello"),), lambda *_: None,
                                  runtime_factory=lambda _context, **_kw: Runtime()), enabled=False)
    mounts = GenericModMounts(host, lambda _owner: bind_local_context(Config(base=tmp_path), owner))
    assert not events
    host.enable(owner)
    assert events == ["start"]
    action = host.menu(owner, mounts.scope, "hello")
    action.invoke()
    host.disable(owner)
    assert events == ["start", "hello", "stop"]
    assert not action.active
    host.enable(owner)
    assert events[-1] == "start"
    mounts.close()
    assert events[-2:] == ["stop", "close"]
    app.processEvents()


def test_discovery_includes_first_install_pending_without_active_version(tmp_path):
    """Interrupted first installs must be found on the next Core/settings start."""
    from pet.config import Config
    from pet.feature_install_state import FeatureInstallStateStore, StateChange
    from pet.feature_management import discover_local_feature_ids
    config = Config(base=tmp_path / "data")
    store = FeatureInstallStateStore(config.dir, feature_id="demo.interrupted")
    store.commit(StateChange(versions={}, pending_transaction="tx-first-install"),
                 expected_revision=0, operation_id="accept-first-install")
    assert "demo.interrupted" in discover_local_feature_ids(config)


def test_discovery_does_not_resurrect_an_empty_uninstalled_tombstone(tmp_path):
    from pet.config import Config
    from pet.feature_install_state import FeatureInstallStateStore, StateChange
    from pet.feature_management import discover_local_feature_ids
    config = Config(base=tmp_path / "data")
    store = FeatureInstallStateStore(config.dir, feature_id="demo.removed")
    store.commit(StateChange(versions={}), expected_revision=0, operation_id="removed")
    assert "demo.removed" not in discover_local_feature_ids(config)


def test_author_tutorial_check_cli_accepts_relative_package_path(tmp_path, monkeypatch, capsys):
    import sys

    from scripts.build_mod_example import build_example, main
    build_example(Path("examples/mods/hello-local"), tmp_path / "hello-local")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_mod_example", "hello-local", "--check"])
    main()
    assert "valid: demo.hello-local 1.0.0 (host-only)" in capsys.readouterr().out
