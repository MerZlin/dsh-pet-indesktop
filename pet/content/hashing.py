"""资源型 DLC 的安全路径和逻辑内容哈希。"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_FILENAME = "manifest.json"


def safe_relative_path(value: str) -> PurePosixPath:
    """返回安全的 POSIX 相对路径，拒绝绝对路径和路径穿越。"""
    raw = str(value).replace("\\", "/")
    raw_parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"unsafe relative path: {value!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe relative path: {value!r}")
    if len(path.parts) == 1 and ":" in path.parts[0]:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def iter_directory_files(root: Path):
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ValueError(f"symlink is not allowed: {path}")
        if path.is_file():
            rel = safe_relative_path(path.relative_to(root).as_posix())
            yield rel, path


def _update_hash(digest: Any, rel: PurePosixPath, read_chunks) -> None:
    if rel.as_posix() == MANIFEST_FILENAME:
        return
    encoded = rel.as_posix().encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    for chunk in read_chunks():
        digest.update(chunk)


def sha256_directory(root: Path) -> str:
    digest = hashlib.sha256()
    for rel, path in iter_directory_files(root):
        _update_hash(digest, rel, lambda p=path: _read_file_chunks(p))
    return digest.hexdigest()


def _read_file_chunks(path: Path, chunk_size: int = 1024 * 1024):
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            yield chunk


def validate_zip_members(zf: zipfile.ZipFile) -> list[PurePosixPath]:
    members: list[PurePosixPath] = []
    seen: set[str] = set()
    for info in zf.infolist():
        if info.is_dir():
            continue
        rel = safe_relative_path(info.filename)
        key = rel.as_posix()
        if key in seen:
            raise ValueError(f"duplicate ZIP member: {key}")
        seen.add(key)
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"symlink is not allowed: {key}")
        members.append(rel)
    return sorted(members, key=lambda p: p.as_posix())


def sha256_zip(path: Path) -> str:
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as zf:
        members = validate_zip_members(zf)
        for rel in members:
            _update_hash(digest, rel, lambda name=rel.as_posix(): _zip_file_chunks(zf, name))
    return digest.hexdigest()


def _zip_file_chunks(zf: zipfile.ZipFile, name: str, chunk_size: int = 1024 * 1024):
    with zf.open(name, "r") as handle:
        while chunk := handle.read(chunk_size):
            yield chunk


def content_sha256(path: Path) -> str:
    if path.is_dir():
        return sha256_directory(path)
    if path.is_file() and zipfile.is_zipfile(path):
        return sha256_zip(path)
    raise ValueError(f"unsupported content source: {path}")
