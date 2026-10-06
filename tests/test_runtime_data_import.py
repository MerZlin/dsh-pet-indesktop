"""Generated profiles only: no personal config, OS secrets or real installation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet.runtime_layout import RuntimeLayout


def fixture_import(tmp_path, monkeypatch):
    from pet import runtime_layout
    from pet.runtime_data_import import RuntimeDataImporter

    monkeypatch.setattr(runtime_layout, "filesystem_name", lambda _: "NTFS")
    core = tmp_path / "core"
    core.mkdir()
    (core / "portable.json").write_text(json.dumps({"format_version": 1, "product_id": runtime_layout.PRODUCT_ID, "data": "data"}))
    layout = RuntimeLayout.discover(core / "pet.exe")
    source = tmp_path / "old-profile"
    (source / "sessions-instance-A" / "dsh").mkdir(parents=True)
    (source / "config-instance-A.json").write_text(json.dumps({"instance_id": "instance-A", "character": "dsh", "chat": {"providers": []}}))
    (source / "sessions-instance-A/dsh/session-A.json").write_text(json.dumps({"session_id": "session-A", "messages": [{"text": "generated text"}]}))
    (source / "plugins/official.ai-chat/versions/9.0.0").mkdir(parents=True)
    (source / "plugins/official.ai-chat/versions/9.0.0/factory.py").write_text("raise RuntimeError('must never import')")
    return layout, source, RuntimeDataImporter(layout)


def confirm(importer, preview):
    assert preview.status == "awaiting_confirmation", preview
    return importer.apply(preview.plan, confirmation_token=preview.plan.confirmation_token)


def test_import_preview_binds_one_source_without_adopting_code(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    result = importer.preflight(source)
    assert result.status == "awaiting_confirmation"
    assert result.plan.data_root_id == layout.data_root_id
    assert result.plan.files == ("config-instance-A.json", "sessions-instance-A/dsh/session-A.json")
    assert result.plan.confirmation_token and result.plan.confirmation_digest
    assert not (layout.data_root / "config-instance-A.json").exists()
    assert not (layout.data_root / "plugins").exists()
    assert source.is_dir()
    assert importer.apply(result.plan, confirmation_token="not-confirmed").status == "rejected"


def test_import_preserves_ids_and_original_and_is_idempotent(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    original = (source / "config-instance-A.json").read_bytes()
    assert confirm(importer, preview).status == "completed"
    assert (layout.data_root / "config-instance-A.json").read_bytes() == original
    assert json.loads((layout.data_root / "feature-data/official.ai-chat/sessions-instance-A/dsh/session-A.json").read_bytes())["session_id"] == "session-A"
    assert (source / "config-instance-A.json").read_bytes() == original
    assert confirm(importer, preview).status == "idempotent"
    assert not (layout.data_root / "plugins").exists()


@pytest.mark.parametrize("side", ["source", "target"])
def test_import_rejects_changes_after_confirmation_summary(tmp_path, monkeypatch, side):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    path = source / "config-instance-A.json" if side == "source" else layout.data_root / "config-instance-A.json"
    path.write_text('{"new_edit":true}')
    result = confirm(importer, preview)
    assert result.status == "rejected" and result.reason == side + "_changed"
    assert path.read_text() == '{"new_edit":true}'
    assert not (layout.data_root / "data-import/pending.json").exists()


def test_import_waits_for_live_runtime_and_blocks_runtime_during_partial_import(tmp_path, monkeypatch):
    from pet.runtime_layout import RuntimeLayoutError

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    with layout.acquire_session():
        result = confirm(importer, preview)
        assert result.status == "awaiting_release" and result.reason == "data_root_in_use"
    assert confirm(importer, preview).status == "completed"
    with layout.acquire_session():
        pass


@pytest.mark.parametrize("failure_step", ["accepted", "before_file", "after_file", "completed"])
def test_import_recovers_each_durable_window(tmp_path, monkeypatch, failure_step):
    from pet.runtime_layout import RuntimeLayoutError

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    original = importer._checkpoint
    fired = False

    def fail_once(step):
        nonlocal fired
        if step == failure_step and not fired:
            fired = True
            raise OSError("generated boundary failure")
        return original(step)

    monkeypatch.setattr(importer, "_checkpoint", fail_once)
    assert confirm(importer, preview).status == "recovery_required"
    assert fired
    with pytest.raises(RuntimeLayoutError, match="data_import_recovery_required"):
        layout.acquire_session()
    from pet.runtime_data_import import RuntimeDataImporter

    resumed = RuntimeDataImporter(layout)
    assert resumed.recover_pending().status in ("completed", "idempotent")
    assert resumed.recover_pending().status == "idempotent"
    assert json.loads((layout.data_root / "config-instance-A.json").read_bytes())["instance_id"] == "instance-A"
    with layout.acquire_session():
        pass


def test_import_recovery_does_not_overwrite_new_edit(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)

    def fail(step):
        if step == "after_file":
            raise OSError("generated failure")

    monkeypatch.setattr(importer, "_checkpoint", fail)
    assert confirm(importer, preview).status == "recovery_required"
    path = layout.data_root / "config-instance-A.json"
    path.write_text('{"user_new_edit":1}')
    assert importer.recover_pending().status == "recovery_required"
    assert path.read_text() == '{"user_new_edit":1}'


@pytest.mark.parametrize("secret", [{"api_key": "generated-secret"}, {"api_key_ref": "old-scoped-reference"}])
def test_import_never_copies_plain_secrets_or_unmigrated_secret_references(tmp_path, monkeypatch, secret):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    (source / "config-instance-A.json").write_text(json.dumps({"chat": {"providers": [secret]}}))
    result = importer.preflight(source)
    assert result.status == "rejected" and result.reason == "credential_migration_required"
    assert not (layout.data_root / "config-instance-A.json").exists()
    assert not list((layout.data_root / "data-import").rglob("snapshot/*"))


def test_import_never_automatically_merges_unmapped_target_data(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    (layout.data_root / "config-other.json").write_text('{"instance_id":"other"}')
    result = importer.preflight(source)
    assert result.status == "rejected" and result.reason == "target_has_unmapped_data"
    assert json.loads((layout.data_root / "config-other.json").read_bytes())["instance_id"] == "other"


def test_import_detects_edit_at_final_write_boundary(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)

    def edit(step):
        if step == "before_file":
            (layout.data_root / "config-instance-A.json").write_text('{"late_edit":true}')

    monkeypatch.setattr(importer, "_checkpoint", edit)
    result = confirm(importer, preview)
    assert result.status == "recovery_required" and result.reason == "target_changed"
    assert (layout.data_root / "config-instance-A.json").read_text() == '{"late_edit":true}'


def test_import_cancel_cleans_only_unaccepted_operation(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    first, second = importer.preflight(source), importer.preflight(source)
    second_dir = layout.data_root / "data-import" / second.plan.operation_id
    assert importer.cancel_preflight(first.plan).status == "completed"
    assert second_dir.is_dir() and source.is_dir()
    assert confirm(importer, second).status == "completed"
    assert importer.cancel_preflight(second.plan).status == "rejected"
    assert (layout.data_root / "config-instance-A.json").is_file()


def test_import_refuses_modified_snapshot_before_acceptance(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    snapshot = layout.data_root / "data-import" / preview.plan.operation_id / "snapshot/config-instance-A.json"
    snapshot.write_text('{"mutated":true}')
    result = confirm(importer, preview)
    assert result.status == "rejected" and result.reason == "import_snapshot_changed"
    assert not (layout.data_root / "data-import/pending.json").exists()


def test_real_process_root_lease_blocks_import_until_natural_exit(tmp_path, monkeypatch):
    import subprocess
    import sys

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    script = (
        "from pathlib import Path; from pet.runtime_layout import RuntimeLayout; import sys; "
        + "layout = RuntimeLayout.discover(Path(sys.argv[1])); lock=layout.acquire_session(); "
        + "print('ready', flush=True); sys.stdin.readline()"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(layout.executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        preview = importer.preflight(source)
        assert confirm(importer, preview).status == "awaiting_release"
        child.communicate("exit\n", timeout=20)
        assert child.returncode == 0
        assert confirm(importer, preview).status == "completed"
    finally:
        if child.poll() is None:
            child.communicate("exit\n", timeout=20)


def test_preflight_uuid_collision_never_deletes_preexisting_directory(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from pet import runtime_data_import

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    operation = "import-" + "a" * 32
    monkeypatch.setattr(runtime_data_import.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    existing = importer.root / operation
    existing.mkdir(parents=True)
    marker = existing / "not-owned.txt"
    marker.write_text("generated prior owner evidence")
    result = importer.preflight(source)
    assert result.status == "rejected"
    assert marker.read_text() == "generated prior owner evidence"


def test_imported_chat_history_is_readable_from_real_ai_owner_root(tmp_path, monkeypatch):
    from features.ai_chat.host.chat.models import ChatMessage, ChatSession
    from features.ai_chat.host.chat.session_store import SessionStore
    from features.ai_chat.host.config import AiConfiguration
    from pet.config import Config
    from pet.feature_host_bindings import bind_ai_context

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    session = ChatSession("session-A", "dsh", "provider-A", "generated prompt", messages=[ChatMessage("user", "generated history")])
    (source / "sessions-instance-A/dsh/session-A.json").write_text(json.dumps(session.to_dict()))
    preview = importer.preflight(source)
    assert preview.status == "awaiting_confirmation"
    expected = "feature-data/official.ai-chat/sessions-instance-A/dsh/session-A.json"
    assert ("sessions-instance-A/dsh/session-A.json", expected) in preview.plan.file_mappings
    assert confirm(importer, preview).status == "completed"
    cfg = AiConfiguration(bind_ai_context(Config(layout=layout, instance_id="instance-A")))
    restored = SessionStore(cfg.dir, cfg.instance_id).list("dsh")
    assert [item.session_id for item in restored] == ["session-A"]
    assert restored[0].messages[0].content == "generated history"
    assert (source / "sessions-instance-A/dsh/session-A.json").is_file()
    assert not (layout.data_root / "sessions-instance-A").exists()


def test_import_rejects_ambiguous_legacy_and_owner_chat_history(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    duplicate = source / "feature-data/official.ai-chat/sessions-instance-A/dsh/session-A.json"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text('{"generated":"different owner version"}')
    preview = importer.preflight(source)
    assert preview.status == "rejected" and preview.reason == "import_mapping_collision"
    assert not (layout.data_root / "config-instance-A.json").exists()


def test_import_of_existing_ai_owner_data_does_not_remap_twice(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    old = source / "sessions-instance-A"
    new = source / "feature-data/official.ai-chat/sessions-instance-A"
    new.parent.mkdir(parents=True)
    old.rename(new)
    preview = importer.preflight(source)
    assert preview.status == "awaiting_confirmation"
    assert confirm(importer, preview).status == "completed"
    assert (layout.data_root / "feature-data/official.ai-chat/sessions-instance-A/dsh/session-A.json").is_file()


@pytest.mark.parametrize("field", ["vision_api_key", "vision_api_key_ref", "credential_ref", "refresh_token", "authorization_token"])
def test_import_does_not_snapshot_additional_known_credentials(tmp_path, monkeypatch, field):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    path = source / "config-instance-A.json"
    path.write_text(json.dumps({"chat": {"providers": {"generated": {field: "GENERATED-NO-LOG-SECRET"}}}}))
    original = path.read_bytes()
    preview = importer.preflight(source)
    assert preview.status == "rejected" and preview.reason == "credential_migration_required"
    assert path.read_bytes() == original
    assert not (layout.data_root / "config-instance-A.json").exists()
    assert not list((layout.data_root / "data-import").rglob("snapshot/*"))


def test_modified_import_mapping_cannot_write_into_package_state(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    journal = layout.data_root / "data-import" / preview.plan.operation_id / "journal.json"
    document = json.loads(journal.read_bytes())
    document["plan"]["file_mappings"][0][1] = "plugins/official.ai-chat/state.json"
    journal.write_text(json.dumps(document))
    result = confirm(importer, preview)
    assert result.status == "rejected" and result.reason == "import_journal_invalid"
    assert not (layout.data_root / "plugins").exists()
    assert not (layout.data_root / "data-import/pending.json").exists()


def test_unaccepted_cleanup_does_not_wait_for_normal_core_data_access(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    with layout.acquire_session():
        result = importer.cancel_preflight(preview.plan)
        assert result.status == "completed" and result.reason == "preflight_cancelled"
        assert not list(importer.root.glob("import-*"))
        assert not (layout.data_root / "config-instance-A.json").exists()
    assert source.is_dir()


def test_import_acceptance_and_owned_cleanup_share_kernel_serialization(tmp_path, monkeypatch):
    from pet import feature_state_io as io

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    preview = importer.preflight(source)
    with io.open_kernel_lock(importer.management_lock):
        assert confirm(importer, preview).status == "awaiting_release"
        assert importer.cancel_preflight(preview.plan).status == "awaiting_release"
        assert not importer.pending.exists()
        assert (importer.root / preview.plan.operation_id).is_dir()
    assert confirm(importer, preview).status == "completed"
    assert importer.cancel_preflight(preview.plan).reason == "accepted_import_cannot_be_cancelled"
    assert (layout.data_root / "config-instance-A.json").is_file()


def resource_fixture(source, *, version="1.0.0"):
    from pet.content.hashing import content_sha256

    root = source / "content/characters/generated-demo/versions" / version
    (root / "videos").mkdir(parents=True)
    (root / "videos/idle.webm").write_bytes(b"\x00\xffGENERATED-NON-JSON-MEDIA")
    manifest = {
        "id": "test.character.generated-demo",
        "name": "Generated resource",
        "version": version,
        "kind": "content",
        "api_version": "1",
        "core_requires": ">=4.2.1,<6.0.0",
        "platforms": ["windows", "linux", "macos"],
        "dependencies": [],
        "capabilities": ["character", "animation"],
        "entrypoint": None,
        "content": {"characters": ["generated-demo"]},
        "integrity": {"sha256": None, "signature": None},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    manifest["integrity"]["sha256"] = content_sha256(root)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    (root.parent.parent / "active.json").write_text(json.dumps({"plugin_id": manifest["id"], "version": version}), encoding="utf8")
    return root


@pytest.mark.parametrize("with_credentials", [False, True])
def test_explicit_import_preserves_verified_character_assets_without_importing_code(tmp_path, monkeypatch, with_credentials):
    from pet.content.registry import CharacterRegistry
    from pet.runtime_credential_import import RuntimeCredentialImporter
    from pet.runtime_data_import import RuntimeDataImporter
    from tests.test_feature_config_ports import MemoryVault

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    resource = resource_fixture(source)
    if with_credentials:
        # The reference importer accepts the real dict-shaped provider schema,
        # not the ordinary-only fixture's legacy empty list shorthand.
        config_path = source / "config-instance-A.json"
        document = json.loads(config_path.read_text())
        document["chat"]["providers"] = {}
        config_path.write_text(json.dumps(document))
        importer = RuntimeDataImporter(layout, credentials=RuntimeCredentialImporter(layout, backend=MemoryVault()))
    preview = importer.preflight(source)
    assert preview.status == "awaiting_confirmation", preview
    name = "content/characters/generated-demo/versions/1.0.0/videos/idle.webm"
    assert name in preview.plan.files, "preserving only character IDs silently loses user-owned resources"
    assert (name, name) in preview.plan.destinations
    expected = (resource / "videos/idle.webm").read_bytes()
    assert confirm(importer, preview).status == "completed"
    assert (layout.data_root / name).read_bytes() == expected
    assert (resource / "videos/idle.webm").read_bytes() == expected
    registry = CharacterRegistry(bundled_root=tmp_path / "empty", installed_root=layout.data_root / "content/characters", legacy_roots=[])
    assert registry.get("generated-demo").video_dir == layout.data_root / "content/characters/generated-demo/versions/1.0.0/videos"
    assert confirm(importer, preview).status == "idempotent"
    assert not (layout.data_root / "plugins").exists()


@pytest.mark.parametrize("damage", ["payload", "pointer", "executable", "unhashed", "incompatible"])
def test_resource_import_rejects_broken_or_executable_material_before_acceptance(tmp_path, monkeypatch, damage):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    root = resource_fixture(source)
    if damage == "payload":
        (root / "videos/idle.webm").write_bytes(b"changed")
    elif damage == "pointer":
        (root.parent.parent / "active.json").write_text('{"plugin_id":"test.character.generated-demo","version":"../escape"}')
    elif damage == "executable":
        (root / "factory.py").write_text("raise AssertionError('must not run')")
    else:
        manifest = json.loads((root / "manifest.json").read_text())
        if damage == "unhashed":
            manifest["integrity"]["sha256"] = None
        else:
            manifest["core_requires"] = ">=99.0.0"
        (root / "manifest.json").write_text(json.dumps(manifest))
    result = importer.preflight(source)
    assert result.status == "rejected", "invalid resources must not disappear silently from the confirmation"
    assert not (layout.data_root / "data-import/pending.json").exists()
    assert not (layout.data_root / "content/characters").exists()


def test_resource_import_recovers_after_pointer_was_written_but_payload_was_not(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    resource_fixture(source)
    preview = importer.preflight(source)
    assert preview.status == "awaiting_confirmation", preview
    assert any(name.startswith("content/") for name in preview.plan.files)
    written = 0

    def fault(step):
        nonlocal written
        if step == "after_file":
            written += 1
            if written == 2:
                raise OSError("generated interrupted resource copy")

    importer._checkpoint = fault
    assert confirm(importer, preview).status == "recovery_required"
    assert (layout.data_root / "data-import/pending.json").exists()
    importer._checkpoint = lambda step: None
    assert importer.recover_pending().status == "completed"
    assert importer.recover_pending().status == "idempotent"
    assert (layout.data_root / "content/characters/generated-demo/versions/1.0.0/videos/idle.webm").read_bytes().startswith(b"\x00\xff")


def test_resource_change_after_preview_rejects_without_accepting_import(tmp_path, monkeypatch):
    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    root = resource_fixture(source)
    preview = importer.preflight(source)
    (root / "videos/idle.webm").write_bytes(b"changed")
    assert confirm(importer, preview).status == "rejected"
    assert not (layout.data_root / "data-import/pending.json").exists()


@pytest.mark.parametrize("limit", ["file", "aggregate"])
def test_resource_import_obeys_bounds_without_reading_unbounded_media(tmp_path, monkeypatch, limit):
    from pet import runtime_data_import

    layout, source, importer = fixture_import(tmp_path, monkeypatch)
    resource_fixture(source)
    if limit == "file":
        original = runtime_data_import.file_limit
        monkeypatch.setattr(runtime_data_import, "file_limit", lambda name: 1 if name.endswith(".webm") else original(name))
    else:
        monkeypatch.setattr(runtime_data_import, "RESOURCE_TOTAL_BYTES", 1)
    preview = importer.preflight(source)
    assert preview.status == "rejected"
    assert not (layout.data_root / "data-import/pending.json").exists()
    assert not (layout.data_root / "content/characters").exists()
