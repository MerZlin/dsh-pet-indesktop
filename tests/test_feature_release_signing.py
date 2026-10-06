"""Offline release seams; generated encrypted keys, never user's real key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from scripts.feature_release_signing import ReleaseSigningError, create_encrypted_key, inspect_key, sign_package_snapshot, verify_key_backup
from tests.test_official_feature_contracts import signed_package


def test_encrypted_key_creation_is_exclusive_and_outside_repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    target = tmp_path / "release" / "key.pem"
    record = create_encrypted_key(target, b"generated-password-only", repository_root=repo, key_id="release-2026")
    raw = target.read_bytes()
    assert b"ENCRYPTED PRIVATE KEY" in raw and b"BEGIN PRIVATE KEY" not in raw
    key = load_pem_private_key(raw, b"generated-password-only")
    assert record == inspect_key(target, b"generated-password-only", key_id="release-2026")
    assert len(record.public_key_hex) == 64 and len(record.fingerprint) == 64
    before = raw
    with pytest.raises(ReleaseSigningError, match="key_exists"):
        create_encrypted_key(target, b"another-generated-password", repository_root=repo, key_id="release-2026")
    assert target.read_bytes() == before
    with pytest.raises(ReleaseSigningError, match="key_inside_repository"):
        create_encrypted_key(repo / "key.pem", b"generated-password-only", repository_root=repo, key_id="release-2026")
    assert key is not None


def test_key_wrong_password_plaintext_and_backup_mismatch_are_safe_errors(tmp_path):
    target = tmp_path / "release" / "key.pem"
    record = create_encrypted_key(target, b"generated-password-only", repository_root=tmp_path / "repo", key_id="release-2026")
    with pytest.raises(ReleaseSigningError, match="key_unavailable"):
        inspect_key(target, b"wrong-generated-password", key_id="release-2026")
    backup = tmp_path / "encrypted-backup.pem"
    backup.write_bytes(target.read_bytes())
    assert verify_key_backup(backup, b"generated-password-only", record)
    other = create_encrypted_key(tmp_path / "other.pem", b"generated-password-only", repository_root=tmp_path / "repo", key_id="release-2026")
    with pytest.raises(ReleaseSigningError, match="key_identity_mismatch"):
        verify_key_backup(backup, b"generated-password-only", other)
    plaintext = tmp_path / "plaintext.pem"
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    key = load_pem_private_key(target.read_bytes(), b"generated-password-only")
    plaintext.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    with pytest.raises(ReleaseSigningError, match="encrypted_pkcs8_required"):
        inspect_key(plaintext, b"generated-password-only", key_id="release-2026")


def test_offline_signing_snapshot_reuses_verifier_and_never_executes_factory(tmp_path, monkeypatch):
    import builtins

    monkeypatch.setattr(builtins, "_phase5a_candidate_executed", False, raising=False)
    key_path = tmp_path / "key.pem"
    record = create_encrypted_key(key_path, b"generated-password-only", repository_root=tmp_path / "repo", key_id="release-2026")
    source = tmp_path / "unsigned"
    manifest, _ = signed_package(source)
    (source / "manifest.sig").unlink()
    result = sign_package_snapshot(source, tmp_path / "signed", key_path, b"generated-password-only", record, core_version="5.0.0")
    assert result.trust_status == "trusted_official" and result.manifest["format_version"] == 2
    assert not builtins._phase5a_candidate_executed
    assert not (source / "manifest.sig").exists()
    assert (tmp_path / "signed/manifest.sig").stat().st_size == 64


@pytest.mark.parametrize("mutation", ["payload", "key_id", "execution_kind", "unregistered"])
def test_invalid_release_package_gets_no_usable_signed_output(tmp_path, mutation):
    key_path = tmp_path / "key.pem"
    record = create_encrypted_key(key_path, b"generated-password-only", repository_root=tmp_path / "repo", key_id="release-2026")
    source = tmp_path / "unsigned"
    manifest, _ = signed_package(source)
    (source / "manifest.sig").unlink()
    if mutation == "payload":
        (source / "host/factory.py").write_text('raise AssertionError("never execute")', encoding="utf-8")
    elif mutation == "unregistered":
        (source / "unknown.txt").write_text("generated", encoding="utf-8")
    else:
        manifest[mutation] = "unknown"
        (source / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    destination = tmp_path / "rejected"
    with pytest.raises(ReleaseSigningError):
        sign_package_snapshot(source, destination, key_path, b"generated-password-only", record, core_version="5.0.0")
    assert not (destination / "manifest.sig").exists()
    assert (source / "host/factory.py").exists()


def test_revoked_signer_and_unapproved_public_key_do_not_sign(tmp_path):
    from dataclasses import replace

    key_path = tmp_path / "key.pem"
    record = create_encrypted_key(key_path, b"generated-password-only", repository_root=tmp_path / "repo", key_id="release-2026")
    source = tmp_path / "unsigned"
    signed_package(source)
    (source / "manifest.sig").unlink()
    with pytest.raises(ReleaseSigningError, match="signer_revoked"):
        sign_package_snapshot(source, tmp_path / "revoked", key_path, b"generated-password-only", replace(record, revoked=True), core_version="5.0.0")
    with pytest.raises(ReleaseSigningError, match="key_identity_mismatch"):
        sign_package_snapshot(source, tmp_path / "wrong", key_path, b"generated-password-only", replace(record, public_key_hex="00" * 32), core_version="5.0.0")
    assert not (tmp_path / "revoked").exists() and not (tmp_path / "wrong").exists()
