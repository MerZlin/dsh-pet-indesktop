"""Bounded, no-follow local package staging and owned-tree cleanup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import zipfile
from pathlib import Path, PurePosixPath
from typing import IO

from . import feature_state_io as io
from .feature_state_io import StateError
from .plugins.package_trust import VerificationLimits

_RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.I)


def canonical_name(name: str, limits: VerificationLimits) -> str:
    if not isinstance(name, str) or len(name) > limits.max_path_chars or "\\" in name:
        raise StateError("unsafe_archive_path")
    parts = name.split("/")
    if (
        len(parts) > limits.max_depth
        or any(
            not part or part in (".", "..") or part.endswith((" ", ".")) or _RESERVED.fullmatch(part) or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
            for part in parts
        )
        or PurePosixPath(name).is_absolute()
    ):
        raise StateError("unsafe_archive_path")
    return name


def _file_limit(name: str, limits: VerificationLimits) -> int:
    return limits.max_manifest_bytes if name == "manifest.json" else 64 if name == "manifest.sig" else limits.max_file_bytes


def _check_node(path: Path) -> os.stat_result:
    io.safe_path(path)
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise StateError("unsafe_source")
    return info


def _identity(info: os.stat_result) -> tuple:
    # atime is changed by ordinary reads (especially Windows/antivirus), not
    # evidence that the bytes or file identity changed.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode, info.st_nlink


def inventory(root: Path, limits: VerificationLimits) -> list[tuple[str, Path, os.stat_result]]:
    if not stat.S_ISDIR(_check_node(root).st_mode):
        raise StateError("unsafe_source")
    entries: list[tuple[str, Path, os.stat_result]] = []
    names: set[str] = set()
    total = 0
    files = 0

    def walk(directory: Path) -> None:
        nonlocal total, files
        io.safe_path(directory)
        with os.scandir(directory) as stream:
            for item in stream:
                path = directory / item.name
                name = canonical_name(path.relative_to(root).as_posix(), limits)
                folded = name.casefold()
                if folded in names:
                    raise StateError("case_collision")
                names.add(folded)
                if len(names) > limits.max_entries:
                    raise StateError("entry_limit")
                info = _check_node(path)
                entries.append((name, path, info))
                if stat.S_ISDIR(info.st_mode):
                    walk(path)
                else:
                    files += 1
                    total += info.st_size
                    if (
                        files > limits.max_files + 2
                        or info.st_size > _file_limit(name, limits)
                        or total > limits.max_total_bytes + limits.max_manifest_bytes + 64
                    ):
                        raise StateError("size_limit")

    walk(root)
    return sorted(entries, key=lambda item: item[0])


def _stream(source: IO[bytes], limit: int, destination: IO[bytes] | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    while True:
        block = source.read(min(1024 * 1024, limit - count + 1))
        if not block:
            return digest.hexdigest(), count
        count += len(block)
        if count > limit:
            raise StateError("size_limit")
        digest.update(block)
        if destination is not None:
            destination.write(block)


def source_fingerprint(source: Path, limits: VerificationLimits) -> tuple[str, str]:
    info = _check_node(source)
    if stat.S_ISDIR(info.st_mode):
        records: list[tuple[str, str] | tuple[str, str, int]] = []
        for name, path, stamp in inventory(source, limits):
            if stat.S_ISDIR(stamp.st_mode):
                records.append((name, "directory"))
            else:
                io.safe_path(path)
                with path.open("rb") as stream:
                    digest, size = _stream(stream, _file_limit(name, limits))
                if _identity(path.stat()) != _identity(stamp):
                    raise StateError("source_changed")
                records.append((name, digest, size))
        return "directory", hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()
    limit = limits.max_total_bytes + limits.max_manifest_bytes + limits.max_entries * 2048
    with source.open("rb") as stream:
        digest, _ = _stream(stream, limit)
    if _identity(source.stat()) != _identity(info):
        raise StateError("source_changed")
    return "zip", digest


def _check_zip_directory(source: Path, limits: VerificationLimits) -> None:
    # ZipFile parses/allocates its central directory in __init__. Check the
    # bounded EOCD first, not merely len(infolist()) after allocation.
    io.safe_path(source)
    with source.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 65557))
        tail = stream.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    while offset >= 0:
        if len(tail) - offset >= 22:
            fields = struct.unpack_from("<4s4H2LH", tail, offset)
            if offset + 22 + fields[-1] == len(tail):
                break
        offset = tail.rfind(b"PK\x05\x06", 0, offset)
    if offset < 0:
        raise StateError("invalid_zip_directory")
    _signature, disk, directory_disk, disk_entries, entries, directory_bytes, directory_offset, _comment = fields
    if disk or directory_disk or disk_entries != entries:
        raise StateError("unsupported_multi_disk_zip")
    if entries == 65535 or directory_bytes == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise StateError("unsupported_zip64_directory")
    if entries > limits.max_entries:
        raise StateError("entry_limit")
    if directory_bytes > limits.max_entries * (limits.max_path_chars * 4 + 128) or directory_offset + directory_bytes > size:
        raise StateError("archive_metadata_limit")


def stage(source: Path, kind: str, destination: Path, limits: VerificationLimits) -> int:
    io.safe_path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    if kind == "directory":
        for name, path, info in inventory(source, limits):
            target = destination / name
            io.safe_path(target)
            if stat.S_ISDIR(info.st_mode):
                target.mkdir(exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                io.safe_path(path)
                with path.open("rb") as incoming, target.open("xb") as outgoing:
                    _, size = _stream(incoming, _file_limit(name, limits), outgoing)
                if _identity(path.stat()) != _identity(info):
                    raise StateError("source_changed")
                total += size
                if os.name != "nt":
                    target.chmod(info.st_mode & 0o777)
        return total
    io.safe_path(source)
    _check_zip_directory(source, limits)
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > limits.max_entries:
            raise StateError("entry_limit")
        names: dict[str, tuple[str, bool]] = {}
        files = 0
        for member in members:
            name = canonical_name(member.filename[:-1] if member.is_dir() else member.filename, limits)
            mode = member.external_attr >> 16
            if member.flag_bits & 1 or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)) or member.external_attr & 0x400:
                raise StateError("unsafe_archive_member")
            folded = name.casefold()
            if folded in names:
                raise StateError("duplicate_archive_entry")
            names[folded] = name, member.is_dir()
            if not member.is_dir():
                files += 1
                total += member.file_size
                if (
                    member.file_size > _file_limit(name, limits)
                    or files > limits.max_files + 2
                    or total > limits.max_total_bytes + limits.max_manifest_bytes + 64
                ):
                    raise StateError("size_limit")
        for folded, (name, _is_dir) in names.items():
            parts = name.split("/")
            for i in range(1, len(parts)):
                prefix = "/".join(parts[:i])
                entry = names.get(prefix.casefold())
                if entry is not None and (entry[0] != prefix or not entry[1]):
                    raise StateError("case_collision")
        implicit: dict[str, str] = {}
        for name, _is_dir in names.values():
            parts = name.split("/")
            for i in range(1, len(parts) + 1):
                prefix = "/".join(parts[:i])
                if implicit.setdefault(prefix.casefold(), prefix) != prefix:
                    raise StateError("case_collision")
        for member in members:
            name = member.filename.rstrip("/")
            target = destination / name
            io.safe_path(target)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as incoming, target.open("xb") as outgoing:
                    _, size = _stream(incoming, _file_limit(name, limits), outgoing)
                if size != member.file_size:
                    raise StateError("archive_size_conflict")
                if os.name != "nt" and member.external_attr >> 16 & 0o111:
                    target.chmod(0o755)
    return total


def remove_owned_tree(path: Path, parent: Path, limits: VerificationLimits) -> None:
    """Delete a strict child only; validate all nodes before the first deletion."""
    io.safe_path(parent)
    io.safe_path(path)
    if path.parent != parent or path.resolve().parent != parent.resolve():
        raise StateError("delete_boundary")
    if not path.exists():
        return
    entries = inventory(path, limits)
    for _name, item, info in sorted(entries, key=lambda entry: len(entry[1].parts), reverse=True):
        io.safe_path(item)
        if stat.S_ISDIR(info.st_mode):
            item.rmdir()
        else:
            item.unlink()
    io.safe_path(path)
    path.rmdir()
