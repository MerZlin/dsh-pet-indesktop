"""Production source/builder seams; generated trust in fixtures is not a release."""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from pet.official_features import OFFICIAL_FEATURES
from scripts.feature_release_signing import ReleaseKeyRecord
from scripts.release_distribution import write_public_policy

ROOT = Path(__file__).resolve().parents[1]


def inputs(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pet/chat").mkdir(parents=True)
    (repo / "pet/workers").mkdir()
    (repo / "pet/__init__.py").write_text('__version__ = "4.2.1"\n')
    (repo / "pet/__main__.py").write_text("def _main(): return 0\n")
    for name in ("app", "runtime_layout", "official_features", "feature_build_policy", "feature_distribution"):
        (repo / f"pet/{name}.py").write_text("VALUE = 1\n")
    (repo / "pet/chat/providers.py").write_text('raise RuntimeError("AI business must not run")\n')
    (repo / "pet/quick_chat.py").write_text('raise RuntimeError("AI quick must not run")\n')
    (repo / "pet/vision.py").write_text('raise RuntimeError("screen must not run")\n')
    (repo / "pet/workers/proactive_screen_adapter.py").write_text('raise RuntimeError("screen must not run")\n')
    for relative in ("assets/characters/example/manifest.json", "assets/chat/forbidden.png", "pet/menu_templates/default.json"):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    (repo / "assets/icon.ico").write_bytes((ROOT / "assets/icon.ico").read_bytes())
    public = bytes(range(32))
    record = ReleaseKeyRecord("release-2026", public.hex(), hashlib.sha256(public).hexdigest())
    policy = tmp_path / "approved-public.json"
    write_public_policy(policy, [record])
    helper = tmp_path / "trusted-helper"
    helper.mkdir()
    (helper / "dsh-feature-probe.exe").write_bytes(b"MZ-generated-fixture-not-executable")
    raw = json.dumps(
        {
            "schema": 1,
            "entry": "dsh-feature-probe.exe",
            "files": {"dsh-feature-probe.exe": hashlib.sha256((helper / "dsh-feature-probe.exe").read_bytes()).hexdigest()},
        },
        sort_keys=True,
    ).encode()
    (helper / "bundle.json").write_bytes(raw)
    owned = tmp_path / "owned"
    owned.mkdir()
    return repo, owned, policy, record, helper, hashlib.sha256(raw).hexdigest()


def prepare(tmp_path, **changes):
    from scripts.build_feature_release import prepare_core

    repo, owned, policy, record, helper, digest = inputs(tmp_path)
    kwargs = dict(
        owned_root=owned,
        version="5.0.0",
        public_policy=policy,
        approved_fingerprints={record.key_id: record.fingerprint},
        probe_bundle=helper,
        probe_manifest_sha256=digest,
    )
    kwargs.update(changes)
    return prepare_core(repo, owned / "core", **kwargs), repo, owned, record


def test_core_has_fixed_normal_bootstrap_and_physical_owner_exclusion(tmp_path):
    metadata, repo, owned, record = prepare(tmp_path)
    source = owned / "core/source"
    assert metadata["product"] == "dsh-pet-core-webm"
    assert metadata["scope"] == "production-source"
    assert not any(path.name.startswith("validation_") for path in source.rglob("*"))
    for name in ("pet/chat", "pet/quick_chat.py", "pet/vision.py", "pet/workers/proactive_screen_adapter.py", "assets/chat", "features"):
        assert not (source / name).exists(), name
    assert (source / "assets/characters/example/manifest.json").exists()
    assert "pet.__main__" in (source / "core_entry.py").read_text()
    assert "initialize_for_current_build" not in (source / "core_entry.py").read_text(), "use real _main bootstrap, not duplicate it"
    distribution = {}
    exec((source / "pet/feature_distribution.py").read_text(), distribution)
    assert distribution["BUILTIN_AI"] is False and distribution["BUILTIN_SCREEN"] is False
    assert 'VARIANT = "core-webm"' in (source / "build_variant.py").read_text()
    assert '__version__ = "5.0.0"' in (source / "pet/__init__.py").read_text()
    assert "4.2.1" in (repo / "pet/__init__.py").read_text(), "never modify repository version or accepted build"
    compiled = {}
    exec((source / "pet/feature_build_policy.py").read_text(), compiled)
    assert compiled["VALIDATION_BUILD"] is False
    assert compiled["ALLOW_LOCAL_PACKAGE_ACTIVATION"] is True
    assert compiled["OFFICIAL_FEATURE_TRUST_ANCHORS"] == ((record.key_id, record.public_key_hex),)
    scopes = dict(compiled["OFFICIAL_FEATURE_KEY_POLICIES"])
    assert scopes[record.key_id]["allow_legacy_v1"] is False
    assert set(scopes[record.key_id]["feature_ids"]) == set(OFFICIAL_FEATURES)
    spec = (owned / "core/core.spec").read_text()
    assert "console=False" in spec and "upx=False" in spec
    assert "validation_entry" not in spec and "assets/chat" not in spec
    assert metadata["probe_manifest_sha256"] == compiled["PROBE_BUNDLE_MANIFEST_SHA256"]
    assert ast.parse(spec)


def test_embedded_policy_requires_independently_approved_identity(tmp_path):
    from scripts.build_feature_release import BuildError

    with pytest.raises(BuildError, match="public_identity_not_approved"):
        prepare(tmp_path, approved_fingerprints={})
    assert not (tmp_path / "owned/core").exists()


@pytest.mark.parametrize("case", ["revoked", "testing_key", "generated_key", "integration_key", "missing_screen_scope", "insufficient_capability"])
def test_production_policy_is_closed_and_not_validation_trust(tmp_path, case):
    from scripts.build_feature_release import BuildError, prepare_core

    repo, owned, policy, record, helper, digest = inputs(tmp_path)
    from dataclasses import replace

    policy.unlink()  # Generated fixture only; no production policy is overwritten.
    changes = {
        "revoked": {"revoked": True},
        "testing_key": {"key_id": "validation-only"},
        "generated_key": {"key_id": "generated-release"},
        "integration_key": {"key_id": "integration-only"},
        "missing_screen_scope": {"feature_ids": frozenset({"official.ai-chat"})},
        "insufficient_capability": {"capabilities": frozenset({"menu.contribute"})},
    }[case]
    record = replace(record, **changes)
    write_public_policy(policy, [record])
    with pytest.raises(BuildError, match="production_trust_policy"):
        prepare_core(
            repo,
            owned / "core",
            owned_root=owned,
            version="5.0.0",
            public_policy=policy,
            approved_fingerprints={record.key_id: record.fingerprint},
            probe_bundle=helper,
            probe_manifest_sha256=digest,
        )
    assert not (owned / "core").exists()


def test_exclusive_target_budget_and_probe_tamper_are_prewrite(tmp_path):
    from scripts.build_feature_release import BuildError, prepare_core

    repo, owned, policy, record, helper, digest = inputs(tmp_path)
    args = dict(
        owned_root=owned,
        version="5.0.0",
        public_policy=policy,
        approved_fingerprints={record.key_id: record.fingerprint},
        probe_bundle=helper,
        probe_manifest_sha256=digest,
    )
    occupied = owned / "occupied"
    occupied.mkdir()
    (occupied / "retained").write_text("retain")
    for target, reason, extra in (
        (occupied, "output_exists", {}),
        (tmp_path / "outside", "output_outside_owned_root", {}),
        (owned / "too-large", "generation_budget_exceeded", {"budget_bytes": 1}),
    ):
        with pytest.raises(BuildError, match=reason):
            prepare_core(repo, target, **(args | extra))
    assert (occupied / "retained").read_text() == "retain"
    (helper / "dsh-feature-probe.exe").write_bytes(b"tampered")
    with pytest.raises(BuildError, match="trusted_probe_unavailable"):
        prepare_core(repo, owned / "tampered", **args)
    assert not (owned / "too-large").exists() and not (owned / "tampered").exists()


def test_core_snapshot_has_no_hardlink_or_validation_source_escape(tmp_path):
    import os

    from scripts.build_feature_release import BuildError, prepare_core

    repo, owned, policy, record, helper, digest = inputs(tmp_path)
    os.link(repo / "pet/app.py", repo / "pet/borrowed.py")
    with pytest.raises(BuildError, match="invalid_build_material"):
        prepare_core(
            repo,
            owned / "core",
            owned_root=owned,
            version="5.0.0",
            public_policy=policy,
            approved_fingerprints={record.key_id: record.fingerprint},
            probe_bundle=helper,
            probe_manifest_sha256=digest,
        )
    assert not (owned / "core").exists()


def test_worker_entry_uses_real_lease_before_business_and_no_synthetic_fixture(tmp_path):
    from scripts.build_feature_release import prepare_worker

    metadata = prepare_worker(ROOT, tmp_path / "worker", owned_root=tmp_path)
    source = tmp_path / "worker/source"
    entry = (source / "worker_entry.py").read_text()
    assert "run_screen_worker_entry" in entry
    assert "synthetic" not in entry and "validation" not in entry
    assert (source / "pet/official_features.py").is_file()
    assert (source / "pet/workers/lease_bootstrap.py").is_file()
    assert not (source / "pet/app.py").exists() and not (source / "pet/chat").exists()
    assert metadata["scope"] == "production-worker-source"
    assert metadata["source_files"]["pet/official_features.py"]["sha256"]


def test_probe_preparation_is_closed_headless_source_not_repository_runtime(tmp_path):
    from scripts.build_feature_release import prepare_probe

    native = tmp_path / "_dsh_probe_native.pyd"
    native.write_bytes(b"MZ-generated-only-preparation-fixture")
    metadata = prepare_probe(ROOT, tmp_path / "probe", owned_root=tmp_path, native_extension=native)
    source = tmp_path / "probe/source"
    assert metadata["scope"] == "production-probe-source"
    for name in ("pet/app.py", "pet/config.py", "features", "pet/chat", "pet/vision.py", "pet/feature_version_lease.py"):
        assert not (source / name).exists(), name
    for name in (
        "pet/official_features.py",
        "pet/feature_ports.py",
        "pet/plugins/package_trust.py",
        "pet/feature_probe_crypto.py",
        "pet/feature_package_probe.py",
        "scripts/feature_probe_entry.py",
        "_dsh_probe_native.pyd",
    ):
        assert (source / name).is_file(), name
    assert "validation_config" not in str(metadata)


def test_real_worker_build_rejects_unproven_native_inputs_before_snapshot(tmp_path):
    from scripts.build_feature_release import BuildError, build_worker

    with pytest.raises(BuildError, match="native_provenance_required"):
        build_worker(
            ROOT, tmp_path / "worker", owned_root=tmp_path, probe_bootloader=tmp_path / "fake.exe", probe_native_extension=tmp_path / "_dsh_probe_native.pyd"
        )
    assert not (tmp_path / "worker").exists()


def test_changed_build_source_after_compiler_is_not_a_release_artifact(tmp_path, monkeypatch):
    import scripts.build_feature_release as build

    repo, owned, policy, record, helper, digest = inputs(tmp_path)

    def compiler_mutates(output, *args):
        (output / "source/pet/app.py").write_text("VALUE = 'modified during compiler'\n")
        return 1.0

    monkeypatch.setattr(build, "_run", compiler_mutates)
    monkeypatch.setattr(build, "verify_core_bundle", lambda *args, **kwargs: pytest.fail("changed source cannot enter artifact audit"))
    with pytest.raises(build.BuildError, match="source_changed"):
        build.build_core(
            repo,
            owned / "core",
            owned_root=owned,
            version="5.0.0",
            public_policy=policy,
            approved_fingerprints={record.key_id: record.fingerprint},
            probe_bundle=helper,
            probe_manifest_sha256=digest,
        )


def test_core_collects_only_published_bridge_files_not_linked_developer_dependencies(tmp_path):
    from scripts.build_feature_release import prepare_core

    repo, owned, policy, record, helper, digest = inputs(tmp_path)
    bridge = repo / "integrations/dsh-pet-bridge"
    bridge.mkdir(parents=True)
    for name in ("index.js", "package.json", "cordis.patch.yml"):
        (bridge / name).write_text("{}")
    dependencies = bridge / "node_modules"
    dependencies.mkdir()
    source = tmp_path / "generated_dependency.js"
    source.write_text("never published or executed")
    (dependencies / "hardlinked.js").hardlink_to(source)
    (bridge / "verify_import.mjs").write_text("development-only")
    prepare_core(
        repo,
        owned / "core",
        owned_root=owned,
        version="5.0.0",
        public_policy=policy,
        approved_fingerprints={record.key_id: record.fingerprint},
        probe_bundle=helper,
        probe_manifest_sha256=digest,
    )
    published = owned / "core/source/integrations/dsh-pet-bridge"
    assert {p.name for p in published.iterdir()} == {"index.js", "package.json", "cordis.patch.yml"}
    assert (dependencies / "hardlinked.js").stat().st_nlink == 2


def test_core_still_rejects_a_hardlinked_published_bridge_file(tmp_path):
    from scripts.build_feature_release import BuildError, prepare_core

    repo, owned, policy, record, helper, digest = inputs(tmp_path)
    bridge = repo / "integrations/dsh-pet-bridge"
    bridge.mkdir(parents=True)
    source = tmp_path / "generated_dependency.js"
    source.write_text("never executed")
    (bridge / "index.js").hardlink_to(source)
    for name in ("package.json", "cordis.patch.yml"):
        (bridge / name).write_text("{}")
    with pytest.raises(BuildError):
        prepare_core(
            repo,
            owned / "core",
            owned_root=owned,
            version="5.0.0",
            public_policy=policy,
            approved_fingerprints={record.key_id: record.fingerprint},
            probe_bundle=helper,
            probe_manifest_sha256=digest,
        )
    assert not (owned / "core").exists()


def test_core_repair_candidate_accepts_compatible_4_2_2_version(tmp_path):
    metadata, repo, owned, record = prepare(tmp_path, version="4.2.2")
    assert metadata["version"] == "4.2.2"
    assert '__version__ = "4.2.2"' in (owned / "core/source/pet/__init__.py").read_text()
    assert '__version__ = "4.2.1"' in (repo / "pet/__init__.py").read_text()


def test_production_core_embeds_existing_whale_icon(tmp_path):
    metadata, repo, owned, _ = prepare(tmp_path)
    icon = owned / "core/source/assets/icon.ico"
    assert icon.read_bytes() == (repo / "assets/icon.ico").read_bytes()
    assert "source/assets/icon.ico" in metadata["source_files"]
    assert f"icon={str(icon)!r}" in (owned / "core/core.spec").read_text()
