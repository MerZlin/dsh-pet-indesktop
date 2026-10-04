"""Closed, bounded schema and trust verification for official version directories.

Schema v1 has exactly id/version/api_version/core_requires/platforms/capabilities/
factory/worker/files. Versions are numeric X.Y.Z; Core ranges are comma-separated
<, <=, ==, !=, >=, > comparisons (no wildcards/prereleases). API is a string;
platforms are win32/linux/darwin. Paths use portable ASCII, forward slashes and
no Windows aliases. worker is {path, args}; files is {path: {sha256, size}}.

manifest.sig is a raw 64-byte Ed25519 signature of the *original* UTF-8 manifest
bytes. Only explicitly supplied Core public keys are authorities. No installed
state, key discovery, network, installer or cryptographic implementation here.
"""

from __future__ import annotations

import hashlib
import json
import operator
import os
import re
import stat
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

OFFICIAL_FEATURE_ID = "official.screen-understanding"
OFFICIAL_FACTORY_ID = "screen-understanding/v1"
FACTORY_PATH = "host/factory.py"
_CONTROLS = frozenset({"manifest.json", "manifest.sig"})
_FIELDS = frozenset({"id", "version", "api_version", "core_requires", "platforms", "capabilities", "factory", "worker", "files"})
_PLATFORMS = frozenset({"win32", "linux", "darwin"})
_VERSION = r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})"
_CAPABILITY = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\Z", re.IGNORECASE)
_COMPARISONS = {"<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne, ">=": operator.ge, ">": operator.gt}


class PackageVerificationError(ValueError):
    """An untrusted, incompatible, changed or unusable feature was refused."""


@dataclass(frozen=True)
class VerificationLimits:
    """Core policy bounds; override explicitly for a measured build, not a package."""

    max_manifest_bytes: int = 2 * 1024 * 1024
    max_files: int = 8192
    max_entries: int = 16384
    max_file_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_host_bytes: int = 16 * 1024 * 1024
    max_depth: int = 16
    max_path_chars: int = 512
    max_args: int = 32
    max_arg_chars: int = 2048
    max_capabilities: int = 64

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not int or value < 1:
                raise PackageVerificationError(f"invalid limit: {item.name}")


@dataclass(frozen=True)
class FileStamp:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    links: int
    attributes: int


@dataclass(frozen=True)
class VerifiedFile:
    sha256: str
    size: int
    stamp: FileStamp


@dataclass(frozen=True)
class CompatibilityEvidence:
    core_version: str
    api_version: str
    platform: str
    allowed_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedFeatureDescriptor:
    """Immutable evidence, not installation state or a transferable authorization.

    Consumers must use the verifying Core policy again before import/launch.
    Nested manifest structures, file records and filesystem evidence are frozen.
    """

    id: str
    version: str
    api_version: str
    root: Path
    worker_path: Path
    worker_args: tuple[str, ...]
    factory: str
    capabilities: tuple[str, ...]
    manifest: Mapping
    files: Mapping[str, VerifiedFile]
    raw_manifest: bytes
    signature: bytes | None
    trust_status: str
    trust_anchor: str | None
    evidence: CompatibilityEvidence
    tree: tuple[tuple[str, FileStamp], ...]
    ancestors: tuple[tuple[str, int, int], ...]


def _stamp(value: os.stat_result) -> FileStamp:
    # Windows lstat guesses executable bits from the filename; fstat has no
    # filename and does not. They are not ACLs or stable file identity there.
    mode = value.st_mode & ~0o111 if os.name == "nt" and stat.S_ISREG(value.st_mode) else value.st_mode
    return FileStamp(
        value.st_dev, value.st_ino, mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink, getattr(value, "st_file_attributes", 0)
    )


def _filesystem_path(path: Path) -> Path:
    """Use Windows extended syntax only at a validated local filesystem seam.

    Logical roots, relative paths and descriptor/ancestry identities are never
    rewritten. LPAC must not depend on access to global long-path policy. No
    resolve(), UNC/device input, traversal normalization or ACL changes here.
    """
    if os.name != "nt" or len(str(path)) < 248:
        return path
    if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ":" or ".." in path.parts:
        raise PackageVerificationError("extended I/O requires a validated absolute local path")
    return Path("\\\\?\\" + str(path))


def _checked_stat(path: Path, *, directory: bool = False) -> FileStamp:
    value = _stamp(_filesystem_path(path).lstat())
    if stat.S_ISLNK(value.mode) or value.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise PackageVerificationError(f"link/reparse point: {path}")
    if directory:
        if not stat.S_ISDIR(value.mode):
            raise PackageVerificationError(f"not a directory: {path}")
    elif not stat.S_ISREG(value.mode) or value.links != 1:
        raise PackageVerificationError(f"not a single regular file: {path}")
    return value


def _root_ancestors(root: Path) -> tuple[tuple[str, int, int], ...]:
    # Do not resolve() first: it would conceal the very links we must reject.
    if not root.is_absolute() or ".." in root.parts or root.drive.startswith("\\\\"):
        raise PackageVerificationError("version root must be an absolute local path without traversal")
    result = []
    for path in (*reversed(root.parents), root):
        stamp = _checked_stat(path, directory=True)
        result.append((str(path), stamp.device, stamp.inode))
    return tuple(result)


def _safe_relative(value, limits: VerificationLimits) -> str:
    if not isinstance(value, str) or not value or len(value) > limits.max_path_chars:
        raise PackageVerificationError("invalid/oversized relative path")
    parts = value.split("/")
    if len(parts) > limits.max_depth:
        raise PackageVerificationError(f"path depth limit: {value}")
    for part in parts:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", part) or part in {".", ".."} or part.endswith(".") or _DEVICE.fullmatch(part.split(".", 1)[0]):
            raise PackageVerificationError(f"unsafe relative path: {value!r}")
    return value


def _expected_tree(paths, limits: VerificationLimits) -> dict[str, bool]:
    """Map each expected path to is_directory; reject case and prefix aliases."""
    tree: dict[str, bool] = {}
    folded: dict[str, str] = {}
    for path in paths:
        _safe_relative(path, limits)
        parts = path.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            directory = index < len(parts)
            prior = folded.setdefault(prefix.casefold(), prefix)
            if prior != prefix or (prefix in tree and tree[prefix] != directory):
                raise PackageVerificationError(f"case/file-directory collision: {path}")
            tree[prefix] = directory
    if len(tree) > limits.max_entries:
        raise PackageVerificationError("inventory entry limit")
    return tree


def _inventory(root: Path, expected: Mapping[str, bool], limits: VerificationLimits) -> dict[str, FileStamp]:
    found = {"": _checked_stat(root, directory=True)}
    stack = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        # Iterate incrementally: never materialize an attacker-sized directory.
        with os.scandir(_filesystem_path(directory)) as entries:
            for entry in entries:
                relative = prefix + entry.name
                _safe_relative(relative, limits)
                if len(found) > limits.max_entries or relative not in expected:
                    raise PackageVerificationError(f"unlisted file/directory or entry limit: {relative}")
                is_directory = expected[relative]
                child = directory / entry.name
                found[relative] = _checked_stat(child, directory=is_directory)
                if is_directory:
                    stack.append((child, relative + "/"))
    if set(found) != {"", *expected}:
        raise PackageVerificationError("missing declared payload/control file")
    return found


def _read_file(path: Path, bound: int, expected: FileStamp | None = None, *, capture: bool = True) -> tuple[bytes, str, FileStamp]:
    before = _checked_stat(path)
    if before.size > bound or (expected is not None and before != expected):
        raise PackageVerificationError(f"size limit or replaced file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(_filesystem_path(path), flags)
    with os.fdopen(fd, "rb") as stream:
        if _stamp(os.fstat(stream.fileno())) != before:
            raise PackageVerificationError(f"file changed before read: {path}")
        digest = hashlib.sha256()
        chunks = []
        length = 0
        while True:
            block = stream.read(min(1024 * 1024, bound - length + 1))
            if not block:
                break
            length += len(block)
            if length > bound:
                raise PackageVerificationError(f"file size limit: {path}")
            digest.update(block)
            if capture:
                chunks.append(block)
        if length != before.size or _stamp(os.fstat(stream.fileno())) != before:
            raise PackageVerificationError(f"file changed during read: {path}")
    if _checked_stat(path) != before:
        raise PackageVerificationError(f"file replaced during read: {path}")
    return b"".join(chunks), digest.hexdigest(), before


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageVerificationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise PackageVerificationError(f"non-JSON constant: {value}")


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PackageVerificationError(f"invalid {label} fields")


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(_VERSION, value):
        raise PackageVerificationError("version must be numeric X.Y.Z")
    return tuple(int(part) for part in value.split("."))


def _core_matches(core_version, requirement):
    actual = _version(core_version)
    if not isinstance(requirement, str) or len(requirement) > 1024:
        raise PackageVerificationError("invalid Core version requirement")
    clauses = requirement.split(",")
    if not 1 <= len(clauses) <= 16:
        raise PackageVerificationError("Core requirement clause limit")
    for clause in clauses:
        match = re.fullmatch(r"(<=|>=|==|!=|<|>)\s*(" + _VERSION + r")", clause.strip())
        if match is None:
            raise PackageVerificationError("invalid Core version requirement")
        if not _COMPARISONS[match[1]](actual, _version(match[2])):
            raise PackageVerificationError("incompatible Core version")


def _string_list(value, limit, label):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(item, str) for item in value):
        raise PackageVerificationError(f"invalid {label}")
    if len(value) != len(set(value)):
        raise PackageVerificationError(f"duplicate {label}")
    return value


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class FeaturePackageVerifier:
    """Core-owned policy. No default keys, inferred trust, or package key lookup.

    allowed_capabilities is a compatibility ceiling, not runtime permission.
    Policy is snapshotted so mutation of a caller's mapping cannot change trust.
    """

    core_version: str
    api_version: str
    platform: str = sys.platform
    allowed_capabilities: frozenset[str] = frozenset()
    trust_anchors: Mapping[str, bytes] = field(default_factory=dict)
    allow_developer_unsigned: bool = False
    limits: VerificationLimits = field(default_factory=VerificationLimits)

    def __post_init__(self):
        _version(self.core_version)
        if not isinstance(self.api_version, str) or not re.fullmatch(r"[1-9][0-9]{0,8}", self.api_version):
            raise PackageVerificationError("invalid Core API version")
        if self.platform not in _PLATFORMS or type(self.allow_developer_unsigned) is not bool:
            raise PackageVerificationError("invalid Core platform/developer policy")
        if not isinstance(self.limits, VerificationLimits):
            raise PackageVerificationError("invalid Core resource policy")
        caps = frozenset(self.allowed_capabilities)
        if len(caps) > self.limits.max_capabilities or any(not isinstance(cap, str) or not _CAPABILITY.fullmatch(cap) for cap in caps):
            raise PackageVerificationError("invalid Core capability policy")
        if not isinstance(self.trust_anchors, Mapping) or len(self.trust_anchors) > 32:
            raise PackageVerificationError("invalid Core trust anchors")
        anchors = dict(self.trust_anchors)
        for name, key in anchors.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name) or type(key) is not bytes or len(key) != 32:
                raise PackageVerificationError("anchors must be named 32-byte Ed25519 public keys")
        object.__setattr__(self, "allowed_capabilities", caps)
        object.__setattr__(self, "trust_anchors", MappingProxyType(anchors))

    @staticmethod
    def _valid_signature(key: bytes, signature: bytes, raw: bytes) -> bool:
        # Default production backend is unchanged. A Core-owned headless helper
        # subclass supplies libsodium because the Windows cryptography wheel
        # imports USER32; the SAME signature/schema/payload policy is reused.
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        try:
            Ed25519PublicKey.from_public_bytes(key).verify(signature, raw)
            return True
        except InvalidSignature:
            return False

    def _authenticate(self, raw: bytes, signature: bytes | None) -> tuple[str, str | None]:
        if signature is None:
            if self.allow_developer_unsigned:
                return "developer_unsigned", None
            raise PackageVerificationError("missing official signature; developer loading is disabled")
        if len(signature) != 64:
            raise PackageVerificationError("manifest.sig must contain a raw 64-byte Ed25519 signature")
        for name, key in self.trust_anchors.items():
            if self._valid_signature(key, signature, raw) is True:
                return "trusted_official", name
        raise PackageVerificationError("signature has no explicitly trusted Core signer")

    def _schema(self, raw: bytes) -> dict:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)
        _keys(payload, _FIELDS, "manifest")
        if payload["id"] != OFFICIAL_FEATURE_ID or payload["factory"] != OFFICIAL_FACTORY_ID:
            raise PackageVerificationError("unsupported official feature/factory")
        _version(payload["version"])
        if payload["api_version"] != self.api_version:
            raise PackageVerificationError("incompatible Core API")
        _core_matches(self.core_version, payload["core_requires"])
        platforms = _string_list(payload["platforms"], 3, "platforms")
        if self.platform not in platforms or not set(platforms) <= _PLATFORMS:
            raise PackageVerificationError("incompatible platform")
        capabilities = _string_list(payload["capabilities"], self.limits.max_capabilities, "capabilities")
        if any(not _CAPABILITY.fullmatch(cap) for cap in capabilities) or not set(capabilities) <= self.allowed_capabilities:
            raise PackageVerificationError("unsupported capabilities")
        worker = payload["worker"]
        _keys(worker, {"path", "args"}, "worker")
        worker_path = _safe_relative(worker["path"], self.limits)
        if not worker_path.startswith("worker/"):
            raise PackageVerificationError("worker executable must be below worker/")
        args = worker["args"]
        if not isinstance(args, list) or len(args) > self.limits.max_args:
            raise PackageVerificationError("invalid worker args")
        if any(not isinstance(arg, str) or len(arg) > self.limits.max_arg_chars or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in arg) for arg in args):
            raise PackageVerificationError("invalid worker argument")
        files = payload["files"]
        if not isinstance(files, dict) or not 1 <= len(files) <= self.limits.max_files:
            raise PackageVerificationError("payload file count limit")
        total = host_total = 0
        for path, info in files.items():
            _safe_relative(path, self.limits)
            if path.casefold() in _CONTROLS:
                raise PackageVerificationError("payload cannot include manifest control files")
            _keys(info, {"sha256", "size"}, "file")
            if not isinstance(info["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", info["sha256"]):
                raise PackageVerificationError("invalid SHA-256 digest")
            size = info["size"]
            if type(size) is not int or not 0 <= size <= self.limits.max_file_bytes:
                raise PackageVerificationError("invalid payload size")
            total += size
            if path.startswith(("host/", "common/")) and path.endswith(".py"):
                host_total += size
            if total > self.limits.max_total_bytes or host_total > self.limits.max_host_bytes:
                raise PackageVerificationError("total payload/host size limit")
        if FACTORY_PATH not in files or worker_path not in files:
            raise PackageVerificationError("factory/worker missing from payload inventory")
        return payload

    def _ancestors(self, root: Path) -> tuple[tuple[str, int, int], ...]:
        return _root_ancestors(root)

    def verify(self, root: Path | str) -> VerifiedFeatureDescriptor:
        return self._verify(root, capture_sources=False)[0]

    def check_snapshot_identity(self, descriptor: VerifiedFeatureDescriptor) -> None:
        """Bounded final filesystem identity check, NOT execution authorization.

        Full hashing/signature validation happens before the management lock.
        Every production import/Worker launch still uses full verification;
        persisted candidates remain pending/non-executable until that receipt.
        """
        if not isinstance(descriptor, VerifiedFeatureDescriptor):
            raise PackageVerificationError("a verified descriptor is required")
        controls = ["manifest.json"] + (["manifest.sig"] if descriptor.signature is not None else [])
        expected = _expected_tree([*descriptor.files, *controls], self.limits)
        if self._ancestors(descriptor.root) != descriptor.ancestors or _inventory(descriptor.root, expected, self.limits) != dict(descriptor.tree):
            raise PackageVerificationError("verified version identity changed before persistence")

    def reverify(self, descriptor: VerifiedFeatureDescriptor) -> VerifiedFeatureDescriptor:
        self._snapshot(descriptor, capture_sources=False)
        return descriptor

    def _snapshot(self, descriptor, *, capture_sources=True):
        if not isinstance(descriptor, VerifiedFeatureDescriptor):
            raise PackageVerificationError("a verified descriptor is required")
        current, sources = self._verify(descriptor.root, capture_sources=capture_sources)
        if current != descriptor:
            raise PackageVerificationError("verified version/evidence changed")
        return sources

    def _verify(self, root, *, capture_sources):
        try:
            return self._inspect(Path(root), capture_sources=capture_sources)
        except PackageVerificationError:
            raise
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise PackageVerificationError(f"invalid/inaccessible feature package: {exc}") from exc

    def _inspect(self, root: Path, *, capture_sources: bool):
        ancestors = self._ancestors(root)
        raw, _, manifest_stamp = _read_file(root / "manifest.json", self.limits.max_manifest_bytes)
        try:
            signature, _, signature_stamp = _read_file(root / "manifest.sig", 64)
        except FileNotFoundError:
            signature, signature_stamp = None, None
        status, anchor = self._authenticate(raw, signature)
        payload = self._schema(raw)
        controls = ["manifest.json"] + (["manifest.sig"] if signature is not None else [])
        expected = _expected_tree([*payload["files"], *controls], self.limits)
        tree = _inventory(root, expected, self.limits)
        if tree["manifest.json"] != manifest_stamp or (signature is not None and tree["manifest.sig"] != signature_stamp):
            raise PackageVerificationError("manifest/signature changed while checking")
        files = {}
        sources = {}
        for path, info in payload["files"].items():
            capture = capture_sources and path.startswith(("host/", "common/")) and path.endswith(".py")
            data, digest, stamp = _read_file(root / path, info["size"], tree[path], capture=capture)
            if stamp.size != info["size"] or digest != info["sha256"]:
                raise PackageVerificationError(f"payload digest/size mismatch: {path}")
            files[path] = VerifiedFile(digest, stamp.size, stamp)
            if capture:
                sources[path] = data
        # Catch replacements/additions during hashing; execution uses captured,
        # digest-checked Python bytes, never a fresh unchecked SourceFileLoader.
        if _inventory(root, expected, self.limits) != tree or self._ancestors(root) != ancestors:
            raise PackageVerificationError("version directory changed during verification")
        descriptor = VerifiedFeatureDescriptor(
            id=payload["id"],
            version=payload["version"],
            api_version=payload["api_version"],
            root=root,
            worker_path=root / payload["worker"]["path"],
            worker_args=tuple(payload["worker"]["args"]),
            factory=payload["factory"],
            capabilities=tuple(payload["capabilities"]),
            manifest=_freeze(payload),
            files=MappingProxyType(files),
            raw_manifest=raw,
            signature=signature,
            trust_status=status,
            trust_anchor=anchor,
            evidence=CompatibilityEvidence(self.core_version, self.api_version, self.platform, tuple(sorted(self.allowed_capabilities))),
            tree=tuple(sorted(tree.items())),
            ancestors=ancestors,
        )
        return descriptor, sources
