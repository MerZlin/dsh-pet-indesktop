"""Qt/user-service-free self-check contract, distinct from OS permission proof."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_importing_verified_host_probe_does_not_load_user_or_lease_services():
    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; sys.path.insert(0, "
        + repr(str(root))
        + "); from pet.feature_package_probe import verified_host_probe; import json; print(json.dumps([name for name in sys.modules if name in ('ctypes', 'pet.feature_install_state', 'pet.feature_version_lease', 'pet.desktop_query', 'socket') or name.startswith('PySide6')]))"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_real_signed_host_factory_is_headless_and_does_not_open_user_services(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from scripts.build_screen_delivery import assemble_package

    root = Path(__file__).resolve().parents[1]
    worker = tmp_path / "worker-fixture"
    worker.mkdir()
    # Host contract only: this sentinel is never executed and is NOT Worker proof.
    (worker / "proactive-screen-worker.exe").write_bytes(b"host-only sentinel")
    key = Ed25519PrivateKey.generate()
    package = assemble_package(root, tmp_path / "signed-package", worker, key)
    public_key = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    code = f"""import sys; sys.path.insert(0, {str(root)!r})
from pathlib import Path
from pet.feature_package_probe import verified_host_probe
from pet.plugins.package_trust import FeaturePackageVerifier
verifier = FeaturePackageVerifier(core_version="4.2.5", api_version="1", allowed_capabilities={{"screen.capture"}}, trust_anchors={{"validation":bytes.fromhex({public_key!r})}})
verified_host_probe(Path({str(package)!r}), verifier)
import json
print(json.dumps([name for name in sys.modules if name in ('ctypes', 'pet.feature_install_state', 'pet.feature_version_lease', 'pet.desktop_query', 'socket', 'keyring') or name.startswith('PySide6')]))
"""
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_host_entry_rejects_unisolated_process_before_reading_policy(monkeypatch):
    import io

    from scripts.feature_probe_entry import host_probe

    class Unreadable(io.StringIO):
        def readline(self, *args):
            raise AssertionError("must establish isolation before policy/candidate")

    monkeypatch.setattr("scripts.feature_probe_entry.sandbox_enforced", lambda: False)
    assert host_probe(Unreadable(), io.StringIO()) == 77


def test_host_entry_bounds_policy_and_never_accepts_unsigned_mode(monkeypatch):
    import io

    from scripts.feature_probe_entry import host_probe

    monkeypatch.setattr("scripts.feature_probe_entry.sandbox_enforced", lambda: True)
    assert host_probe(io.StringIO("x" * (64 * 1024 + 1)), io.StringIO()) == 78
    source = json.dumps({"allow_developer_unsigned": True}) + "\n"
    assert host_probe(io.StringIO(source), io.StringIO()) == 78


def test_host_entry_reverifies_signed_real_factory_and_binds_receipt(tmp_path, monkeypatch):
    import hashlib
    import io
    from dataclasses import asdict

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from pet.plugins.package_trust import VerificationLimits
    from scripts.build_screen_delivery import assemble_package
    from scripts.feature_probe_entry import host_probe

    root = Path(__file__).resolve().parents[1]
    worker = tmp_path / "worker-sentinel"
    worker.mkdir()
    (worker / "proactive-screen-worker.exe").write_bytes(b"never executed")
    key = Ed25519PrivateKey.generate()
    package = assemble_package(root, tmp_path / "signed", worker, key)
    digest = hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest()
    policy = dict(
        core_version="4.2.5",
        api_version="1",
        platform=sys.platform,
        allowed_capabilities=["screen.capture"],
        trust_anchors={"test": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()},
        allow_developer_unsigned=False,
        limits=asdict(VerificationLimits()),
    )
    from pet.plugins.package_trust import _root_ancestors

    doc = {"schema": 1, "package_root": str(package), "manifest_digest": digest, "policy": policy, "snapshot_ancestors": _root_ancestors(package)}
    monkeypatch.setattr("scripts.feature_probe_entry.sandbox_enforced", lambda: True)
    # Contract only: native frozen gate separately uses real pinned libsodium.
    from pet.feature_probe_crypto import HeadlessFeaturePackageVerifier
    from pet.plugins.package_trust import FeaturePackageVerifier

    monkeypatch.setattr(HeadlessFeaturePackageVerifier, "_valid_signature", staticmethod(FeaturePackageVerifier._valid_signature))
    output = io.StringIO()
    assert host_probe(io.StringIO(json.dumps(doc) + "\n"), output) == 0, output.getvalue()
    assert json.loads(output.getvalue()) == {"schema": 1, "kind": "host_valid", "manifest_digest": digest, "version": "1.0.4"}
    doc["manifest_digest"] = "0" * 64
    assert host_probe(io.StringIO(json.dumps(doc) + "\n"), io.StringIO()) == 78


def test_host_rejection_identifies_trusted_failure_stage_without_exception_text(monkeypatch):
    import io

    from scripts.feature_probe_entry import host_probe

    monkeypatch.setattr("scripts.feature_probe_entry.sandbox_enforced", lambda: True)
    output = io.StringIO()
    assert host_probe(io.StringIO("invalid SECRET diagnostic\n"), output) == 78
    result = json.loads(output.getvalue())
    assert result["stage"] == "policy"
    assert "SECRET" not in output.getvalue()


def test_headless_ancestry_permit_only_accepts_exact_parent_verified_root(tmp_path, monkeypatch):
    import pytest

    from pet.feature_probe_crypto import HeadlessFeaturePackageVerifier
    from pet.plugins.package_trust import PackageVerificationError, _root_ancestors

    root = tmp_path / "snapshot"
    root.mkdir()
    ancestors = _root_ancestors(root)
    verifier = HeadlessFeaturePackageVerifier(core_version="4.2.1", api_version="1", snapshot_ancestors=ancestors)

    def denied(_root):
        raise PermissionError("outside snapshot denied")

    monkeypatch.setattr("pet.plugins.package_trust._root_ancestors", denied)
    assert verifier._ancestors(root) == ancestors
    with pytest.raises(PackageVerificationError):
        verifier._ancestors(tmp_path)
    forged = list(ancestors)
    forged[-1] = (str(root), ancestors[-1][1], ancestors[-1][2] + 1)
    with pytest.raises(PackageVerificationError):
        HeadlessFeaturePackageVerifier(core_version="4.2.1", api_version="1", snapshot_ancestors=forged)._ancestors(root)
    with pytest.raises(PermissionError):
        HeadlessFeaturePackageVerifier(core_version="4.2.1", api_version="1")._ancestors(root)


def test_headless_signature_backend_uses_pinned_native_library_without_cffi(monkeypatch):
    from types import SimpleNamespace

    from pet.feature_probe_crypto import HeadlessFeaturePackageVerifier

    calls = []

    def verify(key, signature, raw):
        calls.append((key, signature, raw))
        return raw == b"valid"

    monkeypatch.setitem(sys.modules, "_dsh_probe_native", SimpleNamespace(verify_ed25519=verify))
    assert HeadlessFeaturePackageVerifier._valid_signature(b"k" * 32, b"s" * 64, b"valid")
    assert not HeadlessFeaturePackageVerifier._valid_signature(b"k" * 32, b"s" * 64, b"invalid")
    assert len(calls) == 2
