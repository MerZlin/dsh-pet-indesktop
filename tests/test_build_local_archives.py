"""Public release archive seams; compiled audit is a separate OS/build boundary."""

from __future__ import annotations

import builtins
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from tests.test_official_feature_contracts import AI, signed_package, verifier


def feature(tmp_path):
    source = tmp_path / "source"
    _, key = signed_package(source)
    owned = tmp_path / "owned"
    owned.mkdir()
    return source, owned, verifier(key)


def core(tmp_path, monkeypatch):
    import scripts.build_feature_release as builder

    output = tmp_path / "core"
    bundle = output / "dist/dsh-pet-core-webm"
    bundle.mkdir(parents=True)
    (bundle / "dsh-pet-core-webm.exe").write_bytes(b"MZ-generated-compiled-boundary-fixture")
    (bundle / "_internal").mkdir()
    (bundle / "_internal/library.dll").write_bytes(b"generated-dll")
    evidence = output / "evidence"
    evidence.mkdir()
    table = builder._table(bundle, "bundle")
    (evidence / "artifact.json").write_text(
        json.dumps(
            dict(
                schema=1,
                scope="production-frozen-unsigned",
                size_bytes=sum(x[2] for x in table.values()),
                build_seconds=1.0,
                files={k.removeprefix("bundle/"): dict(sha256=v[1], size=v[2]) for k, v in table.items()},
                pyz_modules=["pet.__main__"],
            )
        )
    )
    monkeypatch.setattr(builder, "inspect_core_bundle", lambda *args, **kwargs: (bundle / "dsh-pet-core-webm.exe", ["pet.__main__"]), raising=False)
    owned = tmp_path / "owned"
    owned.mkdir()
    return output, bundle, owned


def test_signed_feature_zip_round_trips_without_candidate_execution(tmp_path, monkeypatch):
    from pet import feature_package_files as files
    from scripts.build_local_archives import build_feature_zip

    source, owned, policy = feature(tmp_path)
    monkeypatch.setattr(builtins, "_phase5a_candidate_executed", False, raising=False)
    receipt = build_feature_zip(source, owned / "ai.zip", policy, owned_root=owned)
    assert receipt["feature_id"] == AI
    assert receipt["size_bytes"] == (owned / "ai.zip").stat().st_size
    assert receipt["sha256"] == hashlib.sha256((owned / "ai.zip").read_bytes()).hexdigest()
    assert not builtins._phase5a_candidate_executed
    files.stage(owned / "ai.zip", "zip", owned / "restaged", policy.limits)
    assert policy.verify(owned / "restaged").raw_manifest == policy.verify(source).raw_manifest


@pytest.mark.parametrize("portable", [False, True])
def test_core_zip_preserves_audited_bytes_and_only_explicit_portable_marker(tmp_path, monkeypatch, portable):
    from scripts.build_local_archives import build_core_zip

    output, bundle, owned = core(tmp_path, monkeypatch)
    (bundle / ".core-files.lock").write_bytes(b"")
    receipt = build_core_zip(output, owned / "core.zip", probe_manifest_sha256="a" * 64, portable=portable, owned_root=owned)
    assert receipt["kind"] == ("portable-zip" if portable else "core-zip")
    with zipfile.ZipFile(owned / "core.zip") as archive:
        assert archive.read("dsh-pet-core-webm.exe") == (bundle / "dsh-pet-core-webm.exe").read_bytes()
        assert ".core-files.lock" not in archive.namelist()
        assert "portable.json" in archive.namelist() if portable else "portable.json" not in archive.namelist()
        if portable:
            assert json.loads(archive.read("portable.json")) == dict(format_version=1, product_id="dsh-pet-core-webm", data="data")
        assert not any(name.startswith("data/") for name in archive.namelist())


@pytest.mark.parametrize("change", ["changed", "extra", "data", "portable", "invalid-lock", "missing", "artifact"])
def test_core_changed_or_personal_material_cannot_be_archived(tmp_path, monkeypatch, change):
    from scripts.build_local_archives import ArchiveBuildError, build_core_zip

    output, bundle, owned = core(tmp_path, monkeypatch)
    if change == "changed":
        (bundle / "dsh-pet-core-webm.exe").write_bytes(b"changed")
    elif change == "missing":
        (bundle / "_internal/library.dll").unlink()
    elif change == "artifact":
        (output / "evidence/artifact.json").write_text("{}")
    else:
        name = {"extra": "unknown.txt", "data": "data/config.json", "portable": "portable.json", "invalid-lock": ".core-files.lock"}[change]
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"generated-personal-material")
    with pytest.raises(ArchiveBuildError):
        build_core_zip(output, owned / "core.zip", probe_manifest_sha256="a" * 64, owned_root=owned)
    assert not (owned / "core.zip").exists()


def test_core_archive_runs_real_compiled_audit_before_opening_output(tmp_path, monkeypatch):
    import scripts.build_feature_release as builder
    from scripts.build_local_archives import ArchiveBuildError, build_core_zip

    output, _, owned = core(tmp_path, monkeypatch)

    def reject(*args, **kwargs):
        raise builder.BuildError("invalid_core_archive")

    monkeypatch.setattr(builder, "inspect_core_bundle", reject)
    with pytest.raises(ArchiveBuildError, match="invalid_core_archive"):
        build_core_zip(output, owned / "core.zip", probe_manifest_sha256="a" * 64, owned_root=owned)
    assert not (owned / "core.zip").exists()


@pytest.mark.parametrize("change", ["invalid-signature", "unlisted", "hardlink", "budget", "outside", "exists"])
def test_feature_archive_rejects_untrusted_or_unowned_material_before_output(tmp_path, change):
    from scripts.build_local_archives import ArchiveBuildError, build_feature_zip

    source, owned, policy = feature(tmp_path)
    destination = owned / "ai.zip"
    kwargs = dict(owned_root=owned)
    if change == "invalid-signature":
        (source / "manifest.sig").write_bytes(bytes(64))
    elif change == "unlisted":
        (source / "unlisted.txt").write_bytes(b"not declared")
    elif change == "hardlink":
        (owned / "linked").hardlink_to(source / "host/factory.py")
    elif change == "budget":
        kwargs["budget_bytes"] = 1
    elif change == "outside":
        destination = tmp_path / "not-owned.zip"
    elif change == "exists":
        destination.write_bytes(b"preserve")
    with pytest.raises(ArchiveBuildError):
        build_feature_zip(source, destination, policy, **kwargs)
    assert destination.read_bytes() == b"preserve" if change == "exists" else not destination.exists()


def test_feature_mutation_during_stream_is_not_success(tmp_path, monkeypatch):
    from scripts import feature_release_materials as materials
    from scripts.build_local_archives import ArchiveBuildError, build_feature_zip

    source, owned, policy = feature(tmp_path)
    original = materials._digest

    def change(path, limit, output=None):
        if output is not None and Path(path).name == "factory.py":
            Path(path).write_bytes(b"changed during stream")
        return original(path, limit, output)

    monkeypatch.setattr(materials, "_digest", change)
    with pytest.raises(ArchiveBuildError, match="source_changed"):
        build_feature_zip(source, owned / "ai.zip", policy, owned_root=owned)
    # Owned partial evidence is retained, never represented as a signed distribution.
    assert (owned / "ai.zip").exists()
