"""Phase5A contract tests for explicit local package activation."""

import sys
from pathlib import Path

import pytest

from pet import __version__
from pet.plugins.package_trust import FeaturePackageVerifier, PackageVerificationError
from scripts.build_screen_delivery import ROOT, assemble_package, prepare_core


def _worker(tmp_path: Path) -> Path:
    worker = tmp_path / "worker"
    worker.mkdir()
    (worker / "proactive-screen-worker.exe").write_bytes(b"fixture-worker")
    return worker


def _local_verifier() -> FeaturePackageVerifier:
    return FeaturePackageVerifier(
        core_version=__version__,
        api_version="1",
        platform=sys.platform,
        allowed_capabilities={"screen.capture"},
        allow_local_packages=True,
    )


def test_local_package_activation_needs_no_signature_and_keeps_integrity_checks(tmp_path):
    package = assemble_package(ROOT, tmp_path / "package", _worker(tmp_path))
    assert not (package / "manifest.sig").exists()

    descriptor = _local_verifier().verify(package)
    assert descriptor.trust_status == "local_user"
    assert descriptor.trust_anchor is None
    assert _local_verifier().accepts_descriptor(descriptor)

    factory = package / "host/factory.py"
    factory.write_bytes(factory.read_bytes() + b"\n# changed after manifest inventory\n")
    with pytest.raises(PackageVerificationError, match="size limit or replaced file"):
        _local_verifier().verify(package)


def test_signed_only_core_rejects_the_same_unsigned_local_package(tmp_path):
    package = assemble_package(ROOT, tmp_path / "package", _worker(tmp_path))
    verifier = FeaturePackageVerifier(core_version=__version__, api_version="1", allowed_capabilities={"screen.capture"})
    with pytest.raises(PackageVerificationError, match="missing official signature"):
        verifier.verify(package)


def test_phase5a_core_snapshot_enables_local_activation_without_keys(tmp_path):
    output = tmp_path / "core"
    prepare_core(ROOT, output, chat=False)
    policy = (output / "source/pet/feature_build_policy.py").read_text(encoding="utf-8")
    assert "OFFICIAL_FEATURE_TRUST_ANCHORS" in policy
    assert "ALLOW_LOCAL_PACKAGE_ACTIVATION = True" in policy
    assert "TEST_PUBLIC_KEY" not in (output / "source/validation_config.py").read_text(encoding="utf-8")
