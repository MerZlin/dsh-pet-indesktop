"""资源型 DLC manifest 的解析、兼容性和内容安全校验。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from .hashing import content_sha256, iter_directory_files
from .models import ContentManifest

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SUPPORTED_PLATFORMS = {"windows", "macos", "linux"}
_EXECUTABLE_SUFFIXES = {
    ".py",
    ".pyw",
    ".pyc",
    ".pyd",
    ".dll",
    ".so",
    ".dylib",
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".ps1",
    ".sh",
    ".js",
    ".vbs",
}


class ManifestValidationError(ValueError):
    """manifest 或资源内容不符合资源 DLC 契约。"""


def _semver(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError(value)
    return tuple(int(part) for part in match.groups())


def _version_satisfies(version: str, requirement: str) -> bool:
    current = _semver(version)
    requirement = requirement.strip()
    if not requirement or requirement in {"*", ">=0"}:
        return True
    for clause in requirement.split(","):
        clause = clause.strip()
        match = re.match(r"^(<=|>=|==|<|>)(\d+\.\d+\.\d+)$", clause)
        if not match:
            raise ValueError(f"invalid core_requires clause: {clause}")
        target = _semver(match.group(2))
        operator = match.group(1)
        if operator == ">=" and not current >= target:
            return False
        if operator == ">" and not current > target:
            return False
        if operator == "<=" and not current <= target:
            return False
        if operator == "<" and not current < target:
            return False
        if operator == "==" and not current == target:
            return False
    return True


def _require(mapping: dict[str, Any], key: str, expected: type, errors: list[str]):
    value = mapping.get(key)
    if not isinstance(value, expected):
        errors.append(f"{key} must be {expected.__name__}")
        return None
    return value


def load_manifest(root: Path) -> tuple[ContentManifest | None, list[str]]:
    path = root / "manifest.json"
    errors: list[str] = []
    if not path.is_file():
        return None, ["manifest.json is missing"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"manifest.json is invalid: {exc}"]
    if not isinstance(raw, dict):
        return None, ["manifest.json must contain an object"]

    plugin_id = _require(raw, "id", str, errors)
    name = _require(raw, "name", str, errors)
    version = _require(raw, "version", str, errors)
    kind = _require(raw, "kind", str, errors)
    api_version = _require(raw, "api_version", str, errors)
    core_requires = _require(raw, "core_requires", str, errors)
    platforms = _require(raw, "platforms", list, errors)
    dependencies = _require(raw, "dependencies", list, errors)
    capabilities = _require(raw, "capabilities", list, errors)
    content = _require(raw, "content", dict, errors)
    integrity = _require(raw, "integrity", dict, errors)
    if "entrypoint" not in raw:
        errors.append("entrypoint is required")
    entrypoint = raw.get("entrypoint")

    if plugin_id is not None and not _ID_RE.fullmatch(plugin_id):
        errors.append("id contains unsupported characters")
    if version is not None and not _VERSION_RE.fullmatch(version):
        errors.append("version must be semantic x.y.z")
    if kind != "content":
        errors.append("kind must be content")
    if api_version != "1":
        errors.append("api_version must be 1")
    if entrypoint is not None:
        errors.append("content DLC must not declare entrypoint")

    normalized_platforms: tuple[str, ...] = ()
    if isinstance(platforms, list):
        if not all(isinstance(item, str) for item in platforms):
            errors.append("platforms must contain strings")
        normalized_platforms = tuple(platforms)
        if not set(normalized_platforms) <= _SUPPORTED_PLATFORMS:
            errors.append("platforms contains unsupported platform")
    normalized_dependencies = tuple(item for item in dependencies or () if isinstance(item, str)) if isinstance(dependencies, list) else ()
    normalized_capabilities = tuple(item for item in capabilities or () if isinstance(item, str)) if isinstance(capabilities, list) else ()

    characters: tuple[str, ...] = ()
    if isinstance(content, dict):
        raw_characters = content.get("characters")
        if not isinstance(raw_characters, list) or not raw_characters or not all(isinstance(item, str) for item in raw_characters):
            errors.append("content.characters must be a non-empty list of strings")
        else:
            characters = tuple(raw_characters)
            for character_id in characters:
                if not _ID_RE.fullmatch(character_id):
                    errors.append(f"invalid character id: {character_id!r}")
    sha256 = None
    signature = None
    if isinstance(integrity, dict):
        sha256 = integrity.get("sha256")
        signature = integrity.get("signature")
        if sha256 is not None and (not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256)):
            errors.append("integrity.sha256 must be a 64-character hexadecimal string or null")
        if signature is not None and not isinstance(signature, str):
            errors.append("integrity.signature must be a string or null")

    if errors:
        return None, errors
    return ContentManifest(
        plugin_id=plugin_id,
        name=name,
        version=version,
        kind=kind,
        api_version=api_version,
        core_requires=core_requires,
        platforms=normalized_platforms,
        dependencies=normalized_dependencies,
        capabilities=normalized_capabilities,
        entrypoint=None,
        characters=characters,
        integrity_sha256=sha256,
        signature=signature,
        raw=raw,
    ), []


def current_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def validate_package_root(
    root: Path,
    *,
    core_version: str,
    platform_name: str | None = None,
    allow_unsigned: bool = False,
    expected_hash: str | None = None,
    verify_hash: bool = True,
) -> tuple[ContentManifest | None, list[str], str | None]:
    errors: list[str] = []
    try:
        manifest, manifest_errors = load_manifest(root)
        errors.extend(manifest_errors)
        for _rel, path in iter_directory_files(root):
            if path.suffix.lower() in _EXECUTABLE_SUFFIXES:
                errors.append(f"executable file is not allowed: {path.relative_to(root).as_posix()}")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        return None, errors, None
    if manifest is None:
        return None, errors, None
    try:
        if not _version_satisfies(core_version, manifest.core_requires):
            errors.append(f"core version {core_version} does not satisfy {manifest.core_requires}")
    except ValueError as exc:
        errors.append(str(exc))
    if platform_name and manifest.platforms and platform_name not in manifest.platforms:
        errors.append(f"platform {platform_name} is not supported")
    for character_id in manifest.characters:
        direct_character_root = root / "videos"
        nested_character_root = root / "characters" / character_id
        if not direct_character_root.is_dir() and not nested_character_root.is_dir():
            errors.append(f"declared character directory is missing: {character_id}")
            continue
        character_root = root if direct_character_root.is_dir() else nested_character_root
        videos = character_root / "videos"
        video_files = [path for path in videos.rglob("*") if path.is_file() and path.suffix.lower() in {".webm", ".gif"}] if videos.is_dir() else []
        if not video_files:
            errors.append(f"character videos are missing: {character_id}")
        else:
            try:
                if any(path.stat().st_size == 0 for path in video_files):
                    errors.append(f"character videos contain an empty file: {character_id}")
            except OSError as exc:
                errors.append(f"cannot inspect character videos: {exc}")
    actual_hash = None
    declared_hash = expected_hash or manifest.integrity_sha256
    if verify_hash or declared_hash:
        try:
            actual_hash = content_sha256(root)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot hash package: {exc}")
    if declared_hash:
        if actual_hash != declared_hash.lower():
            errors.append("content SHA-256 does not match manifest")
    elif not allow_unsigned:
        errors.append("unsigned content package is not allowed")
    return manifest, errors, actual_hash


def verify_signature(manifest: ContentManifest, root: Path) -> bool:
    """签名验证扩展点。Phase 1 仅保留字段，不强制公钥验证。

    返回 True 代表当前包未声明签名或尚未进入强制签名阶段；
    正式发布阶段应在这里接入公钥轮换和签名校验。
    """
    del root
    return manifest.signature is None
