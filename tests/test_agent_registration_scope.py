"""Generated HOME/profile fixtures: never read or alter real Agent registrations."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pet import agent_link, runtime_layout
from pet.agent_link import ClaudeCodeMonitor, DshMonitor

CORE_FLAG = "x-dsh-pet-core-webm-owner"


@pytest.fixture
def scope(tmp_path, monkeypatch):
    executable = tmp_path / "Core" / "dsh-pet-core-webm.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"MZ generated never executed")
    layout = SimpleNamespace(executable=executable, data_root=tmp_path / "data", data_root_id="a" * 32)
    monkeypatch.setattr(runtime_layout, "_current_layout", layout)
    return layout


def test_new_core_hooks_preserve_legacy_and_other_data_roots(scope, tmp_path, monkeypatch):
    settings = tmp_path / "generated-home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    foreign = [
        {"x-dsh-pet": True, "hooks": [{"command": "old/claude_event_hook.ps1"}]},
        {CORE_FLAG: "b" * 32, "hooks": [{"command": "other/core_claude_hook.ps1"}]},
        {"hooks": [{"command": "user/claude_event_hook.ps1"}]},
    ]
    settings.write_text(json.dumps({"hooks": {"Stop": foreign}, "user": "keep"}), encoding="utf-8")
    monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)
    events = scope.data_root / "agent-events/claude.jsonl"
    events.parent.mkdir(parents=True)
    assert ClaudeCodeMonitor.install_hooks(events)
    assert ClaudeCodeMonitor.install_hooks(events)
    doc = json.loads(settings.read_text(encoding="utf-8"))
    assert doc["hooks"]["Stop"][:3] == foreign
    own = doc["hooks"]["Stop"][3:]
    assert len(own) == 1 and own[0][CORE_FLAG] == scope.data_root_id
    assert "x-dsh-pet" not in own[0]
    assert "core_claude_hook." in own[0]["hooks"][0]["command"]
    assert ClaudeCodeMonitor.uninstall_hooks()
    doc = json.loads(settings.read_text(encoding="utf-8"))
    assert doc == {"hooks": {"Stop": foreign}, "user": "keep"}


def test_scope_does_not_adopt_unmarked_script_name_matches(scope):
    assert not ClaudeCodeMonitor._is_our_hook_entry({"hooks": [{"command": "user/claude_event_hook.ps1"}]})
    assert not ClaudeCodeMonitor._is_our_hook_entry({"x-dsh-pet": True})
    assert not ClaudeCodeMonitor._is_our_hook_entry({CORE_FLAG: "b" * 32})
    assert ClaudeCodeMonitor._is_our_hook_entry({CORE_FLAG: scope.data_root_id})


@pytest.fixture
def bridge(scope, tmp_path, monkeypatch):
    plugin = scope.executable.parent / "_internal/integrations/dsh-pet-bridge"
    plugin.mkdir(parents=True)
    (plugin / "package.json").write_text('{"name":"@dsh-pet/bridge"}', encoding="utf-8")
    profile = tmp_path / "generated-home/.dsh/profiles/web"
    profile.mkdir(parents=True)
    manifest = profile / "package.json"
    monkeypatch.setattr(agent_link, "DSH_PROFILE_HOME", profile.parent.parent)
    monkeypatch.setattr(DshMonitor, "bundled_plugin_dir", classmethod(lambda cls: plugin))
    monkeypatch.setattr(agent_link, "_which", lambda name: "generated-node")
    monkeypatch.setattr(agent_link, "_pnpm_command", lambda: ["generated-pnpm"])
    calls = []
    monkeypatch.setattr(agent_link, "_run_pnpm", lambda *args: calls.append(args) or (0, ""))
    monkeypatch.setattr(agent_link, "_run_pnpm_repairing_specs", lambda *args: calls.append(args) or (0, "", []))
    return plugin, profile, manifest, calls


def write_bridge(manifest, spec):
    manifest.write_text(
        json.dumps({"dependencies": {agent_link.DSH_PLUGIN_NAME: spec}, "dsh": {"profile": {"bundles": [agent_link.DSH_PLUGIN_NAME]}}, "user": "keep"}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("spec", ["link:../../old-Core/bridge", "file:../../portable/bridge", "1.0.0", ""])
def test_new_core_bridge_never_refreshes_foreign_registration(scope, bridge, spec):
    plugin, profile, manifest, calls = bridge
    write_bridge(manifest, spec)
    before = manifest.read_bytes()
    ok, reason = DshMonitor.install_bridge()
    assert not ok and "其他安装" in reason
    assert DshMonitor.bridge_link_stale() == []
    assert DshMonitor.refresh_stale_bridge_links() == []
    assert not calls and manifest.read_bytes() == before


def test_new_core_disable_preserves_foreign_bridge(scope, bridge):
    plugin, profile, manifest, calls = bridge
    write_bridge(manifest, "link:../../old-Core/bridge")
    before = manifest.read_bytes()
    assert DshMonitor.uninstall_bridge()
    assert not calls and manifest.read_bytes() == before


def test_scoped_bridge_removal_only_changes_exact_core_reference(scope, bridge):
    plugin, profile, manifest, calls = bridge
    write_bridge(manifest, "link:" + str(plugin))
    assert DshMonitor.uninstall_bridge(scope_root=plugin)
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    assert agent_link.DSH_PLUGIN_NAME not in doc["dependencies"]
    assert agent_link.DSH_PLUGIN_NAME not in doc["dsh"]["profile"]["bundles"]
    assert doc["user"] == "keep" and not calls
    assert DshMonitor.uninstall_bridge(scope_root=plugin)


def test_scoped_bridge_removal_rejects_partial_cleanup(scope, bridge, monkeypatch):
    plugin, profile, manifest, calls = bridge
    write_bridge(manifest, "link:" + str(plugin))
    from pet import feature_state_io as io

    def denied(*args, **kwargs):
        raise PermissionError("generated write denial")

    monkeypatch.setattr(io, "atomic_write", denied)
    assert not DshMonitor.uninstall_bridge(scope_root=plugin)
    assert agent_link.DSH_PLUGIN_NAME in json.loads(manifest.read_text(encoding="utf-8"))["dependencies"]


def test_core_removal_does_not_block_on_scoped_bridge_cleanup(scope, monkeypatch):
    from pet import core_maintenance, core_registration_cleanup
    from pet.core_maintenance import CoreRemovalPermit

    evidence = CoreRemovalPermit(scope)
    calls = []
    monkeypatch.setattr(DshMonitor, "uninstall_bridge", classmethod(lambda cls, *, scope_root: calls.append(scope_root) or False))
    monkeypatch.setattr(
        core_registration_cleanup,
        "remove_owned_autostart",
        lambda *args, **kwargs: type("Result", (), {"status": "completed", "reason": ""})(),
    )
    assert core_maintenance.finish_core_removal(evidence, executable=scope.executable) == 0
    assert calls == [scope.executable.parent / "_internal/integrations/dsh-pet-bridge"] * 3


def test_core_removal_retries_transient_bridge_cleanup(scope, monkeypatch):
    from pet import core_maintenance, core_registration_cleanup
    from pet.core_maintenance import CoreRemovalPermit

    evidence = CoreRemovalPermit(scope)
    calls = []
    outcomes = iter([False, True])
    monkeypatch.setattr(
        DshMonitor,
        "uninstall_bridge",
        classmethod(lambda cls, *, scope_root: calls.append(scope_root) or next(outcomes)),
    )
    monkeypatch.setattr(
        core_registration_cleanup,
        "remove_owned_autostart",
        lambda *args, **kwargs: type("Result", (), {"status": "completed", "reason": ""})(),
    )

    assert core_maintenance.finish_core_removal(evidence, executable=scope.executable) == 0
    assert calls == [
        scope.executable.parent / "_internal/integrations/dsh-pet-bridge",
        scope.executable.parent / "_internal/integrations/dsh-pet-bridge",
    ]


def test_current_registration_owner_uses_stable_root_identity_not_absolute_location(scope, tmp_path, monkeypatch):
    settings = tmp_path / "generated-home/.claude/settings.json"
    monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)
    for data in (scope.data_root, tmp_path / "moved-data"):
        scope.data_root = data
        events = data / "agent-events/claude.jsonl"
        events.parent.mkdir(parents=True)
        assert ClaudeCodeMonitor.install_hooks(events)
    doc = json.loads(settings.read_text(encoding="utf-8"))
    for entries in doc["hooks"].values():
        assert len(entries) == 1 and entries[0][CORE_FLAG] == scope.data_root_id
        assert "moved-data" in entries[0]["hooks"][0]["command"]


def test_scoped_bridge_cleanup_rechecks_a_leftover_owned_link(scope, bridge):
    plugin, profile, manifest, calls = bridge
    write_bridge(manifest, "link:" + str(plugin))
    link = profile / "node_modules/@dsh-pet/bridge"
    link.parent.mkdir(parents=True)
    make_link(link, plugin)
    manifest.write_text('{"dependencies":{},"user":"keep"}', encoding="utf-8")
    assert DshMonitor.uninstall_bridge(scope_root=plugin)
    assert not link.is_symlink() and not link.exists()
    assert (plugin / "package.json").is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["user"] == "keep"


def test_scoped_bridge_cleanup_preserves_a_foreign_link(scope, bridge, tmp_path):
    plugin, profile, manifest, calls = bridge
    foreign = tmp_path / "foreign-bridge"
    foreign.mkdir()
    write_bridge(manifest, "link:" + str(foreign))
    link = profile / "node_modules/@dsh-pet/bridge"
    link.parent.mkdir(parents=True)
    make_link(link, foreign)
    before = manifest.read_bytes()
    assert DshMonitor.uninstall_bridge(scope_root=plugin)
    assert (link.is_symlink() or getattr(link.lstat(), "st_file_attributes", 0) & 0x400) and foreign.is_dir() and manifest.read_bytes() == before


def test_scoped_bridge_cleanup_does_not_block_on_a_locked_foreign_profile(scope, bridge, tmp_path):
    plugin, profile, manifest, calls = bridge
    foreign = tmp_path / "foreign-bridge"
    foreign.mkdir()
    write_bridge(manifest, "link:" + str(foreign))
    link = profile / "node_modules/@dsh-pet/bridge"
    link.parent.mkdir(parents=True)
    make_link(link, foreign)
    before = manifest.read_bytes()
    from pet import feature_state_io as io

    with io.open_kernel_lock(profile / ".dsh-pet-core-bridge.lock"):
        assert DshMonitor.uninstall_bridge(scope_root=plugin)
    assert manifest.read_bytes() == before
    assert link.is_symlink() or getattr(link.lstat(), "st_file_attributes", 0) & 0x400
    assert not calls


def test_scoped_bridge_cleanup_does_not_turn_bad_manifest_into_empty_state(scope, bridge):
    plugin, profile, manifest, calls = bridge
    manifest.write_bytes(b"{bad generated JSON")
    assert not DshMonitor.uninstall_bridge(scope_root=plugin)
    assert manifest.read_bytes() == b"{bad generated JSON" and not calls


def make_link(link, target):
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_new_hook_registration_lock_busy_does_not_clobber_settings(scope, tmp_path, monkeypatch, action):
    from pet import feature_state_io as io

    settings = tmp_path / "generated-home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    before = json.dumps({"hooks": {"Stop": [{CORE_FLAG: scope.data_root_id}]}}).encode()
    settings.write_bytes(before)
    events = scope.data_root / "agent-events/claude.jsonl"
    events.parent.mkdir(parents=True)
    monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)
    with io.open_kernel_lock(settings.with_name(".dsh-pet-core-hooks.lock")):
        ok = ClaudeCodeMonitor.install_hooks(events) if action == "install" else ClaudeCodeMonitor.uninstall_hooks()
        assert not ok
        assert settings.read_bytes() == before
        assert not (events.parent / "core_claude_hook.ps1").exists()


@pytest.mark.parametrize(
    "document",
    ["[]", "null", '{"hooks": []}', '{"hooks": "bad"}', '"' + "x" * (2 * 1024 * 1024) + '"'],
    ids=["array", "null", "bad-hooks-array", "bad-hooks-string", "oversized"],
)
def test_scoped_hooks_do_not_sanitize_invalid_or_oversized_foreign_settings(scope, tmp_path, monkeypatch, document):
    settings = tmp_path / "generated-home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    before = document.encode()
    settings.write_bytes(before)
    monkeypatch.setattr(ClaudeCodeMonitor, "get_settings_path", lambda: settings)
    assert not ClaudeCodeMonitor.uninstall_hooks()
    assert settings.read_bytes() == before


def test_legacy_hook_matching_does_not_claim_new_product_hooks(scope, monkeypatch):
    monkeypatch.setattr(runtime_layout, "_current_layout", None)
    assert not ClaudeCodeMonitor._is_our_hook_entry({CORE_FLAG: scope.data_root_id, "hooks": [{"command": "data/core_claude_hook.ps1"}]})
