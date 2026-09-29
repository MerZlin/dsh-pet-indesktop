"""Official package verification and loading tests; keys are generated per test."""

from __future__ import annotations

import builtins
import copy
import hashlib
import importlib
import json
import os
import sys
import uuid
from dataclasses import FrozenInstanceError, dataclass, replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def api():
    return importlib.import_module("pet.plugins.feature_packages")


@dataclass
class Package:
    root: Path
    manifest: dict
    key: Ed25519PrivateKey
    counter: str

    def seal(self, raw=None, *, key=None):
        raw = raw if raw is not None else json.dumps(self.manifest, indent=2).encode()
        (self.root / "manifest.json").write_bytes(raw)
        (self.root / "manifest.sig").write_bytes((key or self.key).sign(raw))
        return raw

    def add(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.manifest["files"][path] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    def verifier(self, **kwargs):
        settings = dict(
            core_version="5.0.0",
            api_version="1",
            platform=sys.platform,
            allowed_capabilities={"screen.capture"},
            trust_anchors={"isolated-test": self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
        )
        settings.update(kwargs)
        return api().FeaturePackageVerifier(**settings)


@pytest.fixture
def package(tmp_path, monkeypatch):
    counter = "_feature_package_test_" + uuid.uuid4().hex
    monkeypatch.setattr(builtins, counter, [], raising=False)
    root = tmp_path / "versions" / "1.2.3"
    root.mkdir(parents=True)
    pkg = Package(
        root,
        dict(
            id="official.screen-understanding",
            version="1.2.3",
            api_version="1",
            core_requires=">=5.0.0,<6.0.0",
            platforms=[sys.platform],
            capabilities=["screen.capture"],
            factory="screen-understanding/v1",
            worker={"path": "worker/screen-worker.exe", "args": ["--stdio"]},
            files={},
        ),
        Ed25519PrivateKey.generate(),
        counter,
    )
    pkg.add("host/__init__.py", f"import builtins\nbuiltins.{counter}.append('host-import')\n".encode())
    pkg.add("host/helpers.py", b"VALUE = 123\n")
    pkg.add(
        "host/factory.py",
        (
            f"import builtins\nfrom .helpers import VALUE\nbuiltins.{counter}.append('factory-import')\n"
            f"def create_host(*args, **kwargs):\n    builtins.{counter}.append('factory-call')\n    return VALUE\n"
        ).encode(),
    )
    pkg.add("worker/screen-worker.exe", b"not-a-real-executable")
    pkg.add("resources/defaults.json", b'{"enabled":false}')
    pkg.seal()
    return pkg


def assert_not_executed(package):
    assert getattr(builtins, package.counter) == []


def test_valid_signature_returns_deeply_immutable_evidence_without_execution(package):
    mod = api()
    descriptor = package.verifier().verify(package.root)
    assert descriptor.id == "official.screen-understanding"
    assert descriptor.version == "1.2.3"
    assert descriptor.root == package.root
    assert descriptor.worker_path == package.root / "worker/screen-worker.exe"
    assert descriptor.worker_args == ("--stdio",)
    assert descriptor.factory == "screen-understanding/v1"
    assert descriptor.trust_status == "trusted_official"
    assert descriptor.trust_anchor == "isolated-test"
    assert descriptor.evidence.core_version == "5.0.0"
    assert descriptor.evidence.api_version == "1"
    assert descriptor.evidence.platform == sys.platform
    assert descriptor.manifest["worker"]["args"] == ("--stdio",)
    assert set(descriptor.files) == set(package.manifest["files"])
    assert isinstance(descriptor, mod.VerifiedFeatureDescriptor)
    with pytest.raises(FrozenInstanceError):
        descriptor.version = "9.0.0"
    with pytest.raises(TypeError):
        descriptor.manifest["worker"]["path"] = "elsewhere"
    with pytest.raises(TypeError):
        descriptor.files["unlisted"] = None
    assert_not_executed(package)


@pytest.mark.parametrize("case", ["none", "wrong", "short", "raw-change", "self-key"])
def test_signature_failures_never_execute_package(package, case):
    mod = api()
    kwargs = {}
    if case == "none":
        kwargs["trust_anchors"] = {}
    elif case == "wrong":
        package.seal(key=Ed25519PrivateKey.generate())
    elif case == "short":
        (package.root / "manifest.sig").write_bytes(b"bad")
    elif case == "raw-change":
        with (package.root / "manifest.json").open("ab") as stream:
            stream.write(b"\n")
    else:
        kwargs["trust_anchors"] = {}
        package.manifest["public_key"] = package.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        package.seal()
    with pytest.raises(mod.PackageVerificationError):
        package.verifier(**kwargs).verify(package.root)
    assert_not_executed(package)


def test_no_builtin_anchors_and_developer_unsigned_is_explicit(package):
    mod = api()
    no_anchors = mod.FeaturePackageVerifier(core_version="5.0.0", api_version="1", allowed_capabilities={"screen.capture"})
    with pytest.raises(mod.PackageVerificationError):
        no_anchors.verify(package.root)
    (package.root / "manifest.sig").unlink()
    with pytest.raises(mod.PackageVerificationError):
        package.verifier().verify(package.root)
    verifier = package.verifier(trust_anchors={}, allow_developer_unsigned=True)
    descriptor = verifier.verify(package.root)
    assert descriptor.trust_status == "developer_unsigned"
    assert descriptor.trust_anchor is None
    assert_not_executed(package)
    with mod.FeaturePackageLoader(verifier).load_host(descriptor) as handle:
        assert callable(handle.factory)
    (package.root / "manifest.sig").write_bytes(b"x" * 64)
    with pytest.raises(mod.PackageVerificationError):
        verifier.verify(package.root)


@pytest.mark.parametrize("case", ["top-duplicate", "nested-duplicate", "file-duplicate", "nan", "bom", "utf8", "deep", "not-object"])
def test_ambiguous_or_invalid_json_is_rejected_even_when_signed(package, case):
    raw = json.dumps(package.manifest)
    if case == "top-duplicate":
        raw = raw.replace('"version": "1.2.3"', '"version": "1.2.3", "version": "1.2.3"')
    elif case == "nested-duplicate":
        raw = raw.replace('"args": ["--stdio"]', '"args": ["--stdio"], "args": []')
    elif case == "file-duplicate":
        raw = raw.replace('"size": 20', '"size": 20, "size": 20')
        if raw == json.dumps(package.manifest):
            raw = raw.replace('"files": {', '"files": {"host/factory.py": {},')
    elif case == "nan":
        raw = raw.replace('"size":', '"bad": NaN, "size":', 1)
    elif case == "bom":
        raw = "\ufeff" + raw
    elif case == "utf8":
        raw = b"\xff"
    elif case == "deep":
        raw = "[" * 1500 + "0" + "]" * 1500
    else:
        raw = "[]"
    package.seal(raw if isinstance(raw, bytes) else raw.encode())
    with pytest.raises(api().PackageVerificationError):
        package.verifier().verify(package.root)
    assert_not_executed(package)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "community.anything"),
        ("version", "../1.0.0"),
        ("version", "1.0.0-rc1"),
        ("api_version", "2"),
        ("api_version", 1),
        ("core_requires", ">=6.0.0"),
        ("core_requires", ">=5.0.0,,<6.0.0"),
        ("core_requires", "*"),
        ("platforms", ["plan9"]),
        ("platforms", []),
        ("platforms", [sys.platform, sys.platform]),
        ("capabilities", ["secrets.read"]),
        ("capabilities", ["screen.capture", "screen.capture"]),
        ("factory", "host/factory.py:evil"),
        ("entrypoint", "evil.py"),
        ("worker", {"path": "worker/screen-worker.exe", "args": "--stdio"}),
        ("worker", {"path": "worker/screen-worker.exe", "args": ["bad\x00arg"]}),
        ("worker", {"path": "worker/missing.exe", "args": []}),
    ],
)
def test_schema_and_compatibility_are_closed(package, field, value):
    package.manifest[field] = value
    package.seal()
    with pytest.raises(api().PackageVerificationError):
        package.verifier().verify(package.root)
    assert_not_executed(package)


@pytest.mark.parametrize(
    "bad",
    [
        "/abs.py",
        "../escape.py",
        "a/../escape.py",
        "C:/evil.py",
        "C:evil.py",
        "\\\\server\\share",
        "host\\evil.py",
        "host/f.py:ads",
        "host//f.py",
        "host/./f.py",
        "host/NUL.txt",
        "host/com1.py",
        "host/f.py.",
        "host/f.py ",
        "host/é.py",
        "host/SHORT~1.py",
        "manifest.json",
        "manifest.sig",
    ],
)
def test_unsafe_declared_paths_never_reach_import(package, bad):
    package.manifest["files"][bad] = {"sha256": "0" * 64, "size": 0}
    package.seal()
    with pytest.raises(api().PackageVerificationError):
        package.verifier().verify(package.root)
    assert_not_executed(package)


@pytest.mark.parametrize("path", ["HOST/other.py", "host/FACTORY.py", "host", "host/factory.py/child.py"])
def test_case_collisions_and_file_directory_collisions_are_rejected(package, path):
    package.manifest["files"][path] = {"sha256": "0" * 64, "size": 0}
    package.seal()
    with pytest.raises(api().PackageVerificationError):
        package.verifier().verify(package.root)
    assert_not_executed(package)


@pytest.mark.parametrize(
    "case", ["hash", "missing", "extra", "bytecode", "empty-directory", "missing-factory", "size", "bool-size", "bad-digest", "extra-file-field"]
)
def test_inventory_must_match_every_payload_file(package, case):
    if case == "hash":
        (package.root / "host/helpers.py").write_bytes(b"VALUE = 999\n")
    elif case == "missing":
        (package.root / "resources/defaults.json").unlink()
    elif case in ("extra", "bytecode"):
        (package.root / "host" / ("unlisted.py" if case == "extra" else "factory.pyc")).write_bytes(b"unlisted")
    elif case == "empty-directory":
        (package.root / "unlisted-directory").mkdir()
    elif case == "missing-factory":
        del package.manifest["files"]["host/factory.py"]
        package.seal()
    else:
        evidence = package.manifest["files"]["host/helpers.py"]
        if case == "extra-file-field":
            evidence["untrusted"] = True
        elif case == "bad-digest":
            evidence["sha256"] = "G" * 64
        else:
            evidence["size"] = True if case == "bool-size" else evidence["size"] + 1
        package.seal()
    with pytest.raises(api().PackageVerificationError):
        package.verifier().verify(package.root)
    assert_not_executed(package)


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_manifest_bytes", 64), ("max_files", 2), ("max_file_bytes", 8), ("max_total_bytes", 16), ("max_entries", 2), ("max_depth", 1), ("max_host_bytes", 8)],
)
def test_configured_resource_bounds_are_enforced(package, field, value):
    mod = api()
    limits = replace(mod.VerificationLimits(), **{field: value})
    with pytest.raises(mod.PackageVerificationError):
        package.verifier(limits=limits).verify(package.root)
    assert_not_executed(package)


def test_symlink_payload_and_ancestor_are_rejected(package, tmp_path):
    mod = api()
    target = tmp_path / "outside.py"
    target.write_bytes(b"VALUE = 123\n")
    source = package.root / "host/helpers.py"
    source.unlink()
    try:
        source.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"OS denies creating symlinks: {exc}")
    with pytest.raises(mod.PackageVerificationError):
        package.verifier().verify(package.root)
    source.unlink()
    source.write_bytes(target.read_bytes())
    alias = tmp_path / "alias"
    alias.symlink_to(package.root.parent, target_is_directory=True)
    with pytest.raises(mod.PackageVerificationError):
        package.verifier().verify(alias / package.root.name)
    assert_not_executed(package)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows junction boundary")
def test_windows_junction_root_and_payload_are_rejected(package, tmp_path):
    import _winapi

    mod = api()
    alias = tmp_path / "junction"
    _winapi.CreateJunction(str(package.root.parent), str(alias))
    with pytest.raises(mod.PackageVerificationError):
        package.verifier().verify(alias / package.root.name)
    outside = tmp_path / "outside"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(package.root / "linked"))
    with pytest.raises(mod.PackageVerificationError):
        package.verifier().verify(package.root)
    assert_not_executed(package)


def test_hardlinked_payload_is_rejected(package, tmp_path):
    original = package.root / "host/helpers.py"
    os.link(original, tmp_path / "alias.py")
    with pytest.raises(api().PackageVerificationError):
        package.verifier().verify(package.root)
    assert_not_executed(package)


def test_loader_defers_factory_invocation_uses_relative_imports_and_no_sys_path_change(package):
    mod = api()
    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    loader = mod.FeaturePackageLoader(verifier)
    before = list(sys.path)
    with loader.load_host(descriptor) as host:
        assert getattr(builtins, package.counter) == ["host-import", "factory-import"]
        assert host.factory() == 123
        assert sys.path == before
        assert host.factory.__module__ == host.namespace + ".host.factory"
        assert not list(package.root.rglob("*.pyc"))
        assert loader.lease_counts(descriptor).host == 1
    assert getattr(builtins, package.counter)[-1] == "factory-call"
    assert host.namespace + ".host.factory" in sys.modules
    assert loader.lease_counts(descriptor).imported
    assert not loader.lease_counts(descriptor).can_remove
    with pytest.raises(mod.PackageVerificationError):
        _ = host.factory


@pytest.mark.parametrize("kind", ["payload", "manifest", "signature", "unlisted", "forged-descriptor"])
def test_reverify_refuses_changed_or_forged_descriptor_before_import(package, kind):
    mod = api()
    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    if kind == "payload":
        (package.root / "host/helpers.py").write_bytes(b"VALUE = 999\n")
    elif kind == "manifest":
        package.manifest["version"] = "1.2.4"
        package.seal()
    elif kind == "signature":
        (package.root / "manifest.sig").write_bytes(b"z" * 64)
    elif kind == "unlisted":
        (package.root / "extra.py").write_bytes(b"print('bad')")
    else:
        descriptor = replace(descriptor, worker_path=Path(sys.executable))
    with pytest.raises(mod.PackageVerificationError):
        mod.FeaturePackageLoader(verifier).load_host(descriptor)
    assert_not_executed(package)


def test_worker_rechecks_immediately_before_command_without_importing_host(package):
    mod = api()
    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    loader = mod.FeaturePackageLoader(verifier)
    with loader.acquire_worker(descriptor) as worker:
        assert worker.command() == (str(descriptor.worker_path), ("--stdio",))
        assert loader.lease_counts(descriptor).worker == 1
        assert_not_executed(package)
        (package.root / "worker/screen-worker.exe").write_bytes(b"replaced")
        with pytest.raises(mod.PackageVerificationError):
            worker.command()
    assert loader.lease_counts(descriptor).can_remove
    with pytest.raises(mod.PackageVerificationError):
        worker.command()


def test_leases_are_process_wide_kind_separated_and_idempotent(package):
    mod = api()
    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    first = mod.FeaturePackageLoader(verifier)
    second = mod.FeaturePackageLoader(verifier)
    host = first.load_host(descriptor)
    again = second.load_host(descriptor)
    settings = first.acquire_settings(descriptor)
    worker = second.acquire_worker(descriptor)
    counts = second.lease_counts(descriptor)
    assert (counts.host, counts.settings, counts.worker) == (2, 1, 1)
    assert host.namespace == again.namespace
    duplicate = copy.copy(host)
    host.close()
    duplicate.close()
    host.close()
    assert second.lease_counts(descriptor).host == 1
    assert settings.descriptor == descriptor
    assert worker.descriptor == descriptor
    settings.close()
    again.close()
    worker.close()
    counts = first.lease_counts(descriptor)
    assert (counts.host, counts.settings, counts.worker) == (0, 0, 0)
    assert counts.imported and not counts.can_remove
    assert getattr(builtins, package.counter).count("factory-import") == 1


def test_new_generation_is_isolated_and_old_handles_do_not_remove_it(package):
    mod = api()
    verifier = package.verifier()
    loader = mod.FeaturePackageLoader(verifier)
    old_descriptor = verifier.verify(package.root)
    old = loader.load_host(old_descriptor)
    old_settings = loader.acquire_settings(old_descriptor)
    old_root = package.root
    package.root = old_root.parent / "2.0.0"
    package.root.mkdir()
    for path in package.manifest["files"]:
        package.add(path, (old_root / path).read_bytes())
    package.manifest["version"] = "2.0.0"
    package.add("host/helpers.py", b"VALUE = 456\n")
    package.seal()
    new_descriptor = verifier.verify(package.root)
    with loader.load_host(new_descriptor) as new:
        assert old.factory() == 123
        assert new.factory() == 456
        assert new.namespace != old.namespace
        old.close()
        old_settings.close()
        assert loader.lease_counts(new_descriptor).host == 1
        assert new.factory() == 456
        assert new.namespace + ".host.factory" in sys.modules


def test_lazy_relative_import_is_reverified_before_execution(package):
    mod = api()
    package.add("host/later.py", f"import builtins\nbuiltins.{package.counter}.append('later-import')\nVALUE=7\n".encode())
    package.add("host/factory.py", b"def create_host():\n    from .later import VALUE\n    return VALUE\n")
    package.seal()
    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    with mod.FeaturePackageLoader(verifier).load_host(descriptor) as host:
        factory = host.factory
        (package.root / "host/later.py").write_bytes(b"raise AssertionError('must not execute')\n")
        with pytest.raises(mod.PackageVerificationError):
            factory()
    assert "later-import" not in getattr(builtins, package.counter)


def test_nested_relative_packages_and_cached_load_still_reverify(package):
    mod = api()
    package.add("host/nested/__init__.py", b"from .value import VALUE\n")
    package.add("host/nested/value.py", b"VALUE = 321\n")
    package.add("host/factory.py", b"from .nested import VALUE\ndef create_host():\n    return VALUE\n")
    package.seal()
    verifier = package.verifier()
    loader = mod.FeaturePackageLoader(verifier)
    descriptor = verifier.verify(package.root)
    with loader.load_host(descriptor) as host:
        assert host.factory() == 321
        package.add("host/nested/value.py", b"VALUE = 999\n")
        package.seal()
        with pytest.raises(mod.PackageVerificationError):
            loader.load_host(descriptor)


def test_non_callable_factory_and_failed_import_are_not_retried_or_hot_unloaded(package):
    mod = api()
    package.add("host/factory.py", f"import builtins\nbuiltins.{package.counter}.append('bad-import')\ncreate_host=4\n".encode())
    package.seal()
    verifier = package.verifier()
    loader = mod.FeaturePackageLoader(verifier)
    descriptor = verifier.verify(package.root)
    for _ in range(2):
        with pytest.raises(mod.PackageVerificationError):
            loader.load_host(descriptor)
    assert getattr(builtins, package.counter).count("bad-import") == 1
    assert loader.lease_counts(descriptor).host == 0
    assert not loader.lease_counts(descriptor).can_remove


def test_host_can_import_verified_common_without_loading_worker(package):
    package.add("common/__init__.py", b"")
    package.add("common/models.py", b"VALUE = 456\n")
    package.add("worker/forbidden.py", b"raise AssertionError('worker must not run in host')\n")
    package.add("host/factory.py", b"from ..common.models import VALUE\ndef create_host():\n    return VALUE\n")
    package.seal()
    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    original_path = list(sys.path)
    with api().FeaturePackageLoader(verifier).load_host(descriptor) as host:
        assert host.factory() == 456
        assert host.namespace + ".common.models" in sys.modules
        assert not any(name.startswith(host.namespace + ".worker") for name in sys.modules)
        assert sys.path == original_path


def test_common_python_counts_towards_captured_host_limit(package):
    package.add("common/models.py", b"#" * 2048)
    package.seal()
    verifier = package.verifier(limits=api().VerificationLimits(max_host_bytes=1024))
    with pytest.raises(api().PackageVerificationError, match="host size limit"):
        verifier.verify(package.root)
    assert_not_executed(package)


def test_deferred_common_import_rechecks_integrity(package):
    package.add("common/models.py", b"VALUE = 456\n")
    package.add("host/factory.py", b"def create_host():\n    from ..common.models import VALUE\n    return VALUE\n")
    package.seal()
    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    with api().FeaturePackageLoader(verifier).load_host(descriptor) as host:
        factory = host.factory
        (package.root / "common/models.py").write_bytes(b"VALUE = 999\n")
        with pytest.raises(api().PackageVerificationError):
            factory()


def test_verified_launch_binds_signed_command_and_releases_only_own_lease(package, tmp_path):
    from pet.plugins.worker_launch import verified_worker_launch

    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    loader = api().FeaturePackageLoader(verifier)
    work = tmp_path / "runtime"
    work.mkdir()
    launch = verified_worker_launch(loader, descriptor, runtime_directory=work, environment={"PYTHONPATH": "bad", "HTTPS_PROXY": "proxy"})
    assert launch.program == str(descriptor.worker_path)
    assert launch.arguments == descriptor.worker_args
    assert launch.working_directory == str(work)
    assert dict(launch.environment) == {"HTTPS_PROXY": "proxy"}
    assert loader.lease_counts(descriptor).worker == 1
    launch.close()
    launch.close()
    assert loader.lease_counts(descriptor).worker == 0
    (package.root / "worker/screen-worker.exe").write_bytes(b"tampered")
    with pytest.raises(api().PackageVerificationError):
        verified_worker_launch(loader, descriptor, runtime_directory=work)
    assert loader.lease_counts(descriptor).worker == 0
    assert_not_executed(package)


def test_verified_launch_cannot_use_package_as_writable_runtime(package):
    from pet.plugins.worker_launch import verified_worker_launch

    verifier = package.verifier()
    descriptor = verifier.verify(package.root)
    loader = api().FeaturePackageLoader(verifier)
    with pytest.raises(ValueError, match="runtime directory"):
        verified_worker_launch(loader, descriptor, runtime_directory=package.root / "worker")
    assert loader.lease_counts(descriptor).worker == 0
