"""Production material seams, generated fixtures; no frozen/release claim."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pet.official_features import AI_OWNER, SCREEN_OWNER
from scripts.feature_release_signing import create_encrypted_key, sign_package_snapshot

ROOT = Path(__file__).resolve().parents[1]


def test_actual_ai_sources_become_complete_v2_host_only_payload_without_execution(tmp_path):
    from pet.plugins.feature_host import FeatureDefinition
    from pet.plugins.feature_packages import FeaturePackageLoader
    from pet.plugins.package_trust import FeaturePackageVerifier
    from scripts.feature_release_materials import prepare_unsigned_feature

    unsigned = tmp_path / "unsigned-ai"
    manifest = prepare_unsigned_feature(ROOT, unsigned, AI_OWNER, key_id="release-2026", version="1.0.0", owned_root=tmp_path)
    assert manifest["format_version"] == 2 and manifest["execution_kind"] == "host-only"
    assert manifest["worker"] is None
    assert "host/chat/providers.py" in manifest["files"]
    assert "host/chat/modern_styles.qss" in manifest["files"]
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in manifest["files"])
    assert not (unsigned / "manifest.sig").exists()
    for name, item in manifest["files"].items():
        data = (unsigned / name).read_bytes()
        assert item == {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    key_path = tmp_path / "generated.pem"
    record = create_encrypted_key(key_path, b"generated-password-only", repository_root=tmp_path / "repo", key_id="release-2026")
    descriptor = sign_package_snapshot(unsigned, tmp_path / "signed-ai", key_path, b"generated-password-only", record, core_version="5.0.0")
    verifier = FeaturePackageVerifier(
        core_version="5.0.0",
        api_version="1",
        feature_id=AI_OWNER,
        allowed_capabilities=set(manifest["capabilities"]),
        trust_anchors={record.key_id: bytes.fromhex(record.public_key_hex)},
    )
    handle = FeaturePackageLoader(verifier).load_host(descriptor)
    try:
        definition = handle.factory()
        assert isinstance(definition, FeatureDefinition)
        assert definition.owner == AI_OWNER and definition.worker_launch_factory is None
    finally:
        handle.close()


def test_screen_requires_its_own_worker_tree_and_never_fabricates_one(tmp_path):
    from scripts.feature_release_materials import MaterialError, prepare_unsigned_feature

    destination = tmp_path / "screen"
    with pytest.raises(MaterialError, match="worker_bundle_required"):
        prepare_unsigned_feature(ROOT, destination, SCREEN_OWNER, key_id="release-2026", version="1.0.0", owned_root=tmp_path)
    assert not destination.exists()
    worker = tmp_path / "worker-bundle"
    worker.mkdir()
    (worker / "proactive-screen-worker.exe").write_bytes(b"MZ generated fixture, never executed")
    (worker / "_internal").mkdir()
    (worker / "_internal/python311.dll").write_bytes(b"generated dependency")
    manifest = prepare_unsigned_feature(ROOT, destination, SCREEN_OWNER, key_id="release-2026", version="1.0.0", owned_root=tmp_path, worker_bundle=worker)
    assert manifest["worker"] == {"path": "worker/proactive-screen-worker.exe", "args": []}
    assert "worker/_internal/python311.dll" in manifest["files"]
    assert (destination / "worker/_internal/python311.dll").read_bytes() == b"generated dependency"


def test_material_target_is_exclusive_confined_and_budgeted(tmp_path):
    from scripts.feature_release_materials import MaterialError, prepare_unsigned_feature

    owned = tmp_path / "owned"
    owned.mkdir()
    existing = owned / "existing"
    existing.mkdir()
    (existing / "canary.txt").write_text("must be retained", encoding="utf-8")
    for target, reason in ((existing, "output_exists"), (tmp_path / "outside", "output_outside_owned_root")):
        with pytest.raises(MaterialError, match=reason):
            prepare_unsigned_feature(ROOT, target, AI_OWNER, key_id="release-2026", version="1.0.0", owned_root=owned)
    with pytest.raises(MaterialError, match="generation_budget_exceeded"):
        prepare_unsigned_feature(ROOT, owned / "too-large", AI_OWNER, key_id="release-2026", version="1.0.0", owned_root=owned, budget_bytes=1)
    assert not (owned / "too-large").exists()
    assert (existing / "canary.txt").read_text() == "must be retained"


@pytest.mark.parametrize(
    "bad",
    [
        "features.ai_chat.host.runtime",
        "pet.chat.providers",
        "pet.quick_chat",
        "pet.workers.proactive_screen_adapter",
        "pet.workers.proactive_screen_worker",
        "features.screen_understanding.worker.vision",
        "validation_config",
        "package.validation_boundary",
    ],
)
def test_small_core_audit_rejects_business_and_validation_modules(bad):
    from scripts.feature_release_materials import CORE_REQUIRED, MaterialError, audit_core_inventory

    with pytest.raises(MaterialError, match="forbidden_core_module"):
        audit_core_inventory([*CORE_REQUIRED, bad])


def test_core_dependency_collection_keeps_only_generic_ports():
    from scripts.feature_release_materials import CORE_REQUIRED, audit_core_inventory, host_core_dependencies

    modules = host_core_dependencies(ROOT)
    assert CORE_REQUIRED <= set(modules)
    assert "pet.feature_ports" in modules
    assert not any(name.startswith(("pet.chat", "features.")) for name in modules)
    assert audit_core_inventory(modules) == sorted(set(modules))


def _source_fixture(tmp_path):
    root = tmp_path / "repo"
    host = root / "features/ai_chat/host"
    host.mkdir(parents=True)
    (host / "factory.py").write_text("def create_host(): pass\n", encoding="utf-8")
    return root, host


def test_source_changed_during_copy_leaves_no_completed_manifest(tmp_path, monkeypatch):
    import scripts.feature_release_materials as material

    root, host = _source_fixture(tmp_path)
    original = material._digest
    changed = False

    def mutate(path, limit, output=None):
        nonlocal changed
        if output is not None and not changed:
            changed = True
            (host / "factory.py").write_text("def create_host(): return 'changed'\n", encoding="utf-8")
        return original(path, limit, output)

    monkeypatch.setattr(material, "_digest", mutate)
    target = tmp_path / "output"
    with pytest.raises(material.MaterialError, match="source_changed"):
        material.prepare_unsigned_feature(root, target, AI_OWNER, key_id="release-2026", version="1.0.0", owned_root=tmp_path)
    assert not (target / "manifest.json").exists() and not (target / "manifest.sig").exists()


def test_source_hardlink_is_rejected_even_when_its_name_is_a_cache(tmp_path):
    import os

    from scripts.feature_release_materials import MaterialError, prepare_unsigned_feature

    root, host = _source_fixture(tmp_path)
    cache = host / "__pycache__"
    cache.mkdir()
    os.link(host / "factory.py", cache / "hidden.pyc")
    with pytest.raises(MaterialError, match="invalid_release_material"):
        prepare_unsigned_feature(root, tmp_path / "output", AI_OWNER, key_id="release-2026", version="1.0.0", owned_root=tmp_path)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("version,key", [("../1", "release-2026"), ("1.0.0", "../../key")])
def test_manifest_policy_failure_is_pre_output(tmp_path, version, key):
    from scripts.feature_release_materials import MaterialError, prepare_unsigned_feature

    root, _ = _source_fixture(tmp_path)
    with pytest.raises(MaterialError):
        prepare_unsigned_feature(root, tmp_path / "output", AI_OWNER, key_id=key, version=version, owned_root=tmp_path)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "missing",
    [
        "pet.runtime_data_import",
        "pet.runtime_data_import_entry",
        "pet.runtime_data_import_ui",
        "pet.runtime_credential_import",
        "pet.runtime_resource_import",
        "pet.core_maintenance",
        "pet.core_registration_cleanup",
        "pet.core_uninstall",
        "pet.core_uninstall_ui",
        "pet.local_package_intents",
    ],
)
def test_production_core_audit_requires_real_import_and_maintenance_entry_modules(missing):
    from scripts.feature_release_materials import CORE_REQUIRED, MaterialError, audit_core_inventory

    # An otherwise valid PYZ must not pass merely because optional entry points
    # were omitted by dependency collection. This gate audits actual modules,
    # not a build command's requested hidden-import flags.
    with pytest.raises(MaterialError, match="required_core_module_missing"):
        audit_core_inventory(CORE_REQUIRED - {missing})
