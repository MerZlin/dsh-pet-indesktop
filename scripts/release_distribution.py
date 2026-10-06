"""Offline public trust policy and bounded signed five-product inventory.

Verification takes pre-approved public records, never a key from the bundle.
These hashes prove artifact identity, not that build/runtime gates were run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from pet import feature_state_io as io
from pet.official_features import OFFICIAL_FEATURES
from scripts.feature_release_signing import ReleaseKeyRecord, ReleaseSigningError, _approved_key, _validate_id

PRODUCT = "dsh-pet-core-webm"
KINDS = frozenset({"core-setup", "core-zip", "portable-zip", "ai-dlc", "screen-dlc"})
MAX_TOTAL_BYTES = 6 * 1024**3
_CONTROLS = frozenset({"distribution.json", "distribution.sig"})
_ALLOWED_CAPABILITIES = frozenset(cap for feature in OFFICIAL_FEATURES.values() for cap in feature.capabilities)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ReleaseSigningError("duplicate_json_field")
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError):
        raise ReleaseSigningError("invalid_json") from None


def _encoded(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _exclusive(path, raw):
    io.safe_path(path)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _record(value):
    try:
        if not isinstance(value, dict) or set(value) != {"key_id", "public_key_hex", "fingerprint", "revoked", "feature_ids", "capabilities"}:
            raise ValueError()
        _validate_id(value["key_id"])
        if type(value["revoked"]) is not bool or not re.fullmatch(r"[0-9a-f]{64}", value["public_key_hex"]):
            raise ValueError()
        public = bytes.fromhex(value["public_key_hex"])
        if hashlib.sha256(public).hexdigest() != value["fingerprint"]:
            raise ValueError()
        for field, allowed in (("feature_ids", frozenset(OFFICIAL_FEATURES)), ("capabilities", _ALLOWED_CAPABILITIES)):
            items = value[field]
            if (
                not isinstance(items, list)
                or any(not isinstance(item, str) for item in items)
                or len(items) != len(set(items))
                or not set(items).issubset(allowed)
            ):
                raise ValueError()
        if not value["feature_ids"]:
            raise ValueError()
        return ReleaseKeyRecord(
            value["key_id"], value["public_key_hex"], value["fingerprint"], value["revoked"], frozenset(value["feature_ids"]), frozenset(value["capabilities"])
        )
    except (ValueError, TypeError, KeyError):
        raise ReleaseSigningError("invalid_public_policy") from None


def write_public_policy(path: Path, records):
    keys = []
    for record in records:
        item = asdict(record)
        item["feature_ids"] = sorted(record.feature_ids)
        item["capabilities"] = sorted(record.capabilities)
        _record(item)
        keys.append(item)
    if not keys or len(keys) > 32 or len({item["key_id"] for item in keys}) != len(keys):
        raise ReleaseSigningError("invalid_public_policy")
    path = Path(path).absolute()
    io.safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _exclusive(path, _encoded({"format_version": 1, "product": PRODUCT, "keys": keys}))
    except FileExistsError:
        raise ReleaseSigningError("public_policy_exists") from None


def read_public_policy(path: Path):
    value = _json(io.read_bytes(Path(path).absolute(), 65536))
    if (
        not isinstance(value, dict)
        or set(value) != {"format_version", "product", "keys"}
        or type(value["format_version"]) is not int
        or value["format_version"] != 1
        or value["product"] != PRODUCT
    ):
        raise ReleaseSigningError("invalid_public_policy")
    keys = value["keys"]
    if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
        raise ReleaseSigningError("invalid_public_policy")
    records = [_record(item) for item in keys]
    if len({record.key_id for record in records}) != len(records):
        raise ReleaseSigningError("invalid_public_policy")
    return {record.key_id: record for record in records}


def _inventory(root, artifacts):
    if not isinstance(artifacts, dict) or set(artifacts) != KINDS:
        raise ReleaseSigningError("invalid_artifact_inventory")
    names = list(artifacts.values())
    if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(?:exe|zip)", name) for name in names):
        raise ReleaseSigningError("invalid_artifact_path")
    if len({name.casefold() for name in names}) != len(names):
        raise ReleaseSigningError("invalid_artifact_inventory")
    if any((kind == "core-setup") != name.endswith(".exe") for kind, name in artifacts.items()):
        raise ReleaseSigningError("invalid_artifact_type")
    io.safe_path(root)
    if not root.is_dir():
        raise ReleaseSigningError("distribution_root_missing")
    children = list(root.iterdir())
    if any(path.name not in set(names) | _CONTROLS for path in children):
        raise ReleaseSigningError("unlisted_distribution_files")
    result, total = {}, 0
    for kind, name in artifacts.items():
        path = root / name
        io.safe_path(path)
        before = path.stat()
        if not path.is_file() or not 1 <= before.st_size <= MAX_TOTAL_BYTES:
            raise ReleaseSigningError("invalid_artifact_size")
        total += before.st_size
        if total > MAX_TOTAL_BYTES:
            raise ReleaseSigningError("distribution_size_limit")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            count = 0
            while block := stream.read(1024 * 1024):
                count += len(block)
                if count > before.st_size:
                    raise ReleaseSigningError("artifact_changed")
                digest.update(block)
        io.safe_path(path)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or count != before.st_size:
            raise ReleaseSigningError("artifact_changed")
        result[kind] = {"path": name, "size": count, "sha256": digest.hexdigest()}
    return result


def sign_distribution(root: Path, artifacts: dict, key_path: Path, password: bytes, approved: ReleaseKeyRecord, *, release_version: str):
    root = Path(root).absolute()
    key = _approved_key(key_path, password, approved)
    if not isinstance(release_version, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})", release_version):
        raise ReleaseSigningError("invalid_release_version")
    if any((root / name).exists() for name in _CONTROLS):
        raise ReleaseSigningError("distribution_exists")
    inventory = _inventory(root, artifacts)
    raw = _encoded({"format_version": 1, "product": PRODUCT, "version": release_version, "key_id": approved.key_id, "artifacts": inventory})
    _exclusive(root / "distribution.json", raw)
    # Recheck the exact files before publishing a signature. A failure leaves
    # an unsigned manifest for diagnosis; it is not silently overwritten.
    if _inventory(root, artifacts) != inventory:
        raise ReleaseSigningError("artifact_changed")
    _exclusive(root / "distribution.sig", key.sign(raw))
    return verify_distribution(root, {approved.key_id: approved})


def verify_distribution(root: Path, trusted: dict[str, ReleaseKeyRecord]):
    root = Path(root).absolute()
    raw = io.read_bytes(root / "distribution.json", 2 * 1024 * 1024)
    value = _json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"format_version", "product", "version", "key_id", "artifacts"}
        or type(value["format_version"]) is not int
        or value["format_version"] != 1
        or value["product"] != PRODUCT
    ):
        raise ReleaseSigningError("invalid_distribution_manifest")
    record = trusted.get(value["key_id"]) if isinstance(value["key_id"], str) else None
    if not isinstance(record, ReleaseKeyRecord) or record.revoked or record.key_id != value["key_id"]:
        raise ReleaseSigningError("distribution_key_untrusted")
    try:
        signature = io.read_bytes(root / "distribution.sig", 64)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(record.public_key_hex)).verify(signature, raw)
    except (InvalidSignature, ValueError):
        raise ReleaseSigningError("distribution_signature_invalid") from None
    if (
        not isinstance(value["artifacts"], dict)
        or set(value["artifacts"]) != KINDS
        or any(not isinstance(item, dict) or set(item) != {"path", "size", "sha256"} or type(item["size"]) is not int for item in value["artifacts"].values())
    ):
        raise ReleaseSigningError("invalid_artifact_inventory")
    inventory = _inventory(root, {kind: item["path"] for kind, item in value["artifacts"].items()})
    if inventory != value["artifacts"]:
        raise ReleaseSigningError("distribution_artifact_mismatch")
    return value
