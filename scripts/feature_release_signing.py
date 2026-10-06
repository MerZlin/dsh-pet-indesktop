"""Offline release signing; secrets never enter arguments, logs, or package files.

Library entry points require an explicitly approved public-key record. They do
not discover a signing key or trust a public key supplied by the candidate.
Only newly owned snapshots are signed; source packages are never modified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pet import feature_package_files as package_files
from pet import feature_state_io as io
from pet.official_features import OFFICIAL_FEATURES, official_feature
from pet.plugins.package_trust import FeaturePackageVerifier, PackageVerificationError, SigningKeyPolicy, VerificationLimits, VerifiedFeatureDescriptor


class ReleaseSigningError(ValueError):
    """Reason codes only; never expose decrypted material or password errors."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReleaseKeyRecord:
    key_id: str
    public_key_hex: str
    fingerprint: str
    revoked: bool = False
    feature_ids: frozenset[str] = frozenset(OFFICIAL_FEATURES)
    capabilities: frozenset[str] = frozenset(cap for feature in OFFICIAL_FEATURES.values() for cap in feature.capabilities)


def _validate_id(key_id: str) -> None:
    if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key_id):
        raise ReleaseSigningError("invalid_key_id")


def _record(key: Ed25519PrivateKey, key_id: str) -> ReleaseKeyRecord:
    _validate_id(key_id)
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return ReleaseKeyRecord(key_id, public.hex(), hashlib.sha256(public).hexdigest())


def _load_key(path: Path, password: bytes) -> Ed25519PrivateKey:
    if type(password) is not bytes or not password:
        raise ReleaseSigningError("password_required")
    try:
        raw = io.read_bytes(Path(path).absolute(), 16384)
    except (OSError, io.StateError):
        raise ReleaseSigningError("key_unavailable") from None
    if not raw.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"):
        raise ReleaseSigningError("encrypted_pkcs8_required")
    try:
        key = serialization.load_pem_private_key(raw, password)
    except (ValueError, TypeError):
        raise ReleaseSigningError("key_unavailable") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise ReleaseSigningError("ed25519_required")
    return key


def create_encrypted_key(target: Path, password: bytes, *, repository_root: Path, key_id: str) -> ReleaseKeyRecord:
    """Create exclusively, outside the repository, after caller confirmation.

    The caller supplies the password from a trusted local UI, never argv/env.
    No existing key is replaced and no plaintext key file is created.
    """
    _validate_id(key_id)
    if type(password) is not bytes or len(password) < 12:
        raise ReleaseSigningError("password_too_short")
    target, repository_root = Path(target).absolute(), Path(repository_root).absolute()
    try:
        io.safe_path(target)
        io.safe_path(repository_root)
        if target.resolve().is_relative_to(repository_root.resolve()):
            raise ReleaseSigningError("key_inside_repository")
        if target.exists():
            raise ReleaseSigningError("key_exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        io.safe_path(target)
        key = Ed25519PrivateKey.generate()
        encrypted = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(password))
        # Exclusive O_CREAT handles a race after the preceding diagnostic check.
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        return _record(key, key_id)
    except FileExistsError:
        raise ReleaseSigningError("key_exists") from None
    except (OSError, io.StateError):
        # A partial encrypted file is kept as failure evidence, never retried
        # with truncation or mistaken for a successfully created key.
        raise ReleaseSigningError("key_write_failed") from None


def inspect_key(path: Path, password: bytes, *, key_id: str) -> ReleaseKeyRecord:
    return _record(_load_key(path, password), key_id)


def _approved_key(path: Path, password: bytes, approved: ReleaseKeyRecord) -> Ed25519PrivateKey:
    if not isinstance(approved, ReleaseKeyRecord) or type(approved.revoked) is not bool:
        raise ReleaseSigningError("invalid_key_record")
    if approved.revoked:
        raise ReleaseSigningError("signer_revoked")
    key = _load_key(path, password)
    actual = _record(key, approved.key_id)
    if actual.public_key_hex != approved.public_key_hex or actual.fingerprint != approved.fingerprint:
        raise ReleaseSigningError("key_identity_mismatch")
    return key


def verify_key_backup(path: Path, password: bytes, approved: ReleaseKeyRecord) -> bool:
    """Decrypt only the explicitly chosen backup and compare public identity."""
    _approved_key(path, password, approved)
    return True


def sign_package_snapshot(
    source: Path,
    destination: Path,
    key_path: Path,
    password: bytes,
    approved: ReleaseKeyRecord,
    *,
    core_version: str,
    expected_manifest_digest: str | None = None,
) -> VerifiedFeatureDescriptor:
    """Sign a new immutable-format v2 snapshot then perform full Core verification.

    A failed candidate never retains a usable signature. This is not OS sandbox
    self-test or production loading confirmation; no factory is executed here.
    """
    key = _approved_key(key_path, password, approved)
    source, destination = Path(source).absolute(), Path(destination).absolute()
    signature = destination / "manifest.sig"
    wrote_signature = False
    limits = VerificationLimits()
    try:
        io.safe_path(source)
        io.safe_path(destination)
        if destination.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(destination.resolve()):
            raise ReleaseSigningError("unsafe_snapshot_target")
        if destination.exists():
            raise ReleaseSigningError("snapshot_exists")
        kind, before = package_files.source_fingerprint(source, limits)
        if kind != "directory" or (source / "manifest.sig").exists():
            raise ReleaseSigningError("unsigned_directory_required")
        raw = io.read_bytes(source / "manifest.json", limits.max_manifest_bytes)
        if expected_manifest_digest is not None and hashlib.sha256(raw).hexdigest() != expected_manifest_digest:
            raise ReleaseSigningError("batch_source_changed")
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or manifest.get("format_version") != 2 or manifest.get("key_id") != approved.key_id:
            raise ReleaseSigningError("invalid_release_manifest")
        feature_id = manifest.get("id")
        if not isinstance(feature_id, str):
            raise ReleaseSigningError("invalid_release_manifest")
        feature = official_feature(feature_id)
        if feature.id not in approved.feature_ids:
            raise ReleaseSigningError("signer_owner_denied")
        verifier = FeaturePackageVerifier(
            core_version=core_version,
            api_version="1",
            feature_id=feature.id,
            allowed_capabilities=feature.capabilities,
            trust_anchors={approved.key_id: bytes.fromhex(approved.public_key_hex)},
            anchor_policy={approved.key_id: SigningKeyPolicy(approved.feature_ids, approved.capabilities, revoked=approved.revoked)},
        )
        package_files.stage(source, kind, destination, limits)
        if io.read_bytes(destination / "manifest.json", limits.max_manifest_bytes) != raw or package_files.source_fingerprint(source, limits) != (kind, before):
            raise ReleaseSigningError("source_changed")
        io.safe_path(signature)
        with signature.open("xb") as stream:
            wrote_signature = True
            stream.write(key.sign(raw))
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = verifier.verify(destination)
        verifier.reverify(descriptor)
        return descriptor
    except (OSError, io.StateError, PackageVerificationError, ValueError, TypeError) as exc:
        if wrote_signature:
            io.safe_path(signature)
            signature.unlink(missing_ok=True)
        if isinstance(exc, ReleaseSigningError):
            raise
        raise ReleaseSigningError("release_package_rejected") from None
