from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .. import __version__ as CORE_VERSION
from ..contracts.capabilities import CAPABILITIES

SCHEMA_VERSION = 1
API_VERSION = "1"
MOD_FILE = "mod.json"
KINDS = frozenset({"content", "feature"})
EXECUTIONS = frozenset({"host-only", "worker"})
EXECUTABLE_SUFFIXES = frozenset(
    {".py", ".pyc", ".dll", ".so", ".dylib", ".exe", ".js", ".bat", ".cmd", ".ps1"}
)
CONTENT_BLOCK_KEYS = ("sounds", "themes", "phrases", "seasonal")
KNOWN_FIELDS = frozenset(
    {
        "schema",
        "id",
        "kind",
        "name",
        "name_zh",
        # 简介：市场索引用 `summary_zh`，第三方清单也常写着一个；两者都收，
        # 缺了也不影响加载（只用于展示）。
        "summary",
        "summary_zh",
        "version",
        "api_version",
        "core_requires",
        "author",
        "execution",
        "entrypoint",
        "capabilities",
        "requires",
        "character",
        *CONTENT_BLOCK_KEYS,
    }
)
PATH_KEYS = frozenset(
    {
        "path",
        "file",
        "dir",
        "root",
        "media",
        "media_dir",
        "frames_dir",
        "frameseq_dir",
        "move_strides",
        "text_clips",
        "phrases",
        "phrases_path",
        "sound",
        "icon",
        "preview",
        "thumbnail",
    }
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_CLAUSE_RE = re.compile(r"^(>=|<=|==|>|<)\s*(\d+\.\d+\.\d+)$")


@dataclass(frozen=True, slots=True)
class ModManifest:
    id: str
    kind: str
    name: str
    # kw-only：中文名必须跟 name 相邻，但后面还有无默认值的字段（version/path）。
    name_zh: str = field(default="", kw_only=True)
    version: str
    path: Path
    author: str = ""
    execution: str = "host-only"
    entrypoint: str | None = None
    capabilities: tuple[str, ...] = ()
    character: dict[str, Any] | None = None
    diagnostics: tuple[str, ...] = ()
    api_version: str = API_VERSION
    core_requires: str = ""
    requires: tuple[str, ...] = ()
    sounds: dict[str, Any] | None = None
    themes: dict[str, Any] | None = None
    phrases: dict[str, Any] | None = None
    seasonal: dict[str, Any] | None = None


#: NTFS 保留设备名：`mods/nul` 在 Windows 上永远「已存在」，且无法正常替换。
_RESERVED_ID_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def is_valid_id(value: str) -> bool:
    if not _ID_RE.match(value):
        return False
    # 只校验整个 id 与它的第一段：`com1.extra` 同样没法落盘。
    if value.lower() in _RESERVED_ID_NAMES:
        return False
    return value.split(".")[0].lower() not in _RESERVED_ID_NAMES


def parse_version(value: str) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or not _VERSION_RE.match(value):
        return None
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def requirement_valid(requirement: str) -> bool:
    text = requirement.strip()
    if not text:
        return True
    for clause in text.split(","):
        if _CLAUSE_RE.match(clause.strip()) is None:
            return False
    return True


def version_satisfies(requirement: str, version: str) -> bool:
    current = parse_version(version)
    if current is None:
        return False
    text = requirement.strip()
    if not text:
        return True
    for clause in text.split(","):
        match = _CLAUSE_RE.match(clause.strip())
        if match is None:
            return False
        target = parse_version(match.group(2))
        if target is None:
            return False
        operator = match.group(1)
        if operator == ">=" and not current >= target:
            return False
        if operator == "<=" and not current <= target:
            return False
        if operator == ">" and not current > target:
            return False
        if operator == "<" and not current < target:
            return False
        if operator == "==" and not current == target:
            return False
    return True


def path_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


def _safe_path(base: Path, value: str) -> tuple[Path | None, str | None]:
    raw = value.strip()
    if not raw:
        return None, "empty path"
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw)
    if windows.is_absolute() or posix.is_absolute() or windows.drive or raw.startswith("~"):
        return None, f"absolute path not allowed: {value}"
    try:
        resolved = (base / raw).resolve()
    except OSError as exc:
        return None, f"cannot resolve path {value!r}: {exc}"
    if not path_within(base, resolved):
        return None, f"path escapes pack: {value}"
    return resolved, None

def _check_path(base: Path, value: str, context: str, errors: list[str]) -> None:
    _, problem = _safe_path(base, value)
    if problem is not None:
        errors.append(f"{context}: {problem}")


def _scan_paths(
    base: Path,
    value: Any,
    context: str,
    errors: list[str],
    *,
    value_is_path: bool = False,
) -> None:
    if isinstance(value, str):
        if value_is_path:
            _check_path(base, value, context, errors)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            _scan_paths(
                base,
                item,
                f"{context}.{key}",
                errors,
                value_is_path=key in PATH_KEYS,
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_paths(
                base, item, f"{context}[{index}]", errors, value_is_path=value_is_path
            )


def _entrypoint_target(value: str) -> str | None:
    head, separator, tail = value.rpartition(":")
    target = head if separator and tail and "/" not in tail and "\\" not in tail else value
    target = target.strip()
    return target or None


def _relative(pack: Path, path: Path) -> str:
    try:
        return path.relative_to(pack).as_posix()
    except ValueError:
        return str(path)


def _scan_content_pack(pack: Path, errors: list[str]) -> None:
    try:
        entries = sorted(pack.rglob("*"))
    except OSError as exc:
        errors.append(f"cannot scan pack: {exc}")
        return
    for entry in entries:
        try:
            resolved = entry.resolve()
        except OSError as exc:
            errors.append(f"cannot resolve {_relative(pack, entry)}: {exc}")
            continue
        if not path_within(pack, resolved):
            errors.append(f"symlink escapes pack: {_relative(pack, entry)}")
            continue
        if resolved.is_file() and resolved.suffix.lower() in EXECUTABLE_SUFFIXES:
            errors.append(f"executable file in content pack: {_relative(pack, entry)}")


def _build(
    pack: Path, raw: dict[str, Any]
) -> tuple[ModManifest | None, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for key in raw:
        if key not in KNOWN_FIELDS:
            warnings.append(f"unknown field: {key}")

    schema = raw.get("schema")
    if schema != SCHEMA_VERSION:
        errors.append(f"schema must be {SCHEMA_VERSION}, got {schema!r}")

    mod_id = raw.get("id")
    if not isinstance(mod_id, str) or not is_valid_id(mod_id):
        errors.append(f"invalid id: {mod_id!r}")
        mod_id = ""

    kind = raw.get("kind")
    if kind not in KINDS:
        errors.append(f"kind must be one of {sorted(KINDS)}, got {kind!r}")
        kind = ""

    version = raw.get("version")
    if not isinstance(version, str) or parse_version(version) is None:
        errors.append(f"version must be X.Y.Z, got {version!r}")
        version = ""

    api_version = raw.get("api_version", API_VERSION)
    if api_version != API_VERSION:
        errors.append(f"api_version must be {API_VERSION!r}, got {api_version!r}")

    core_requires = raw.get("core_requires", "")
    if not isinstance(core_requires, str):
        errors.append("core_requires must be a string")
        core_requires = ""
    elif not requirement_valid(core_requires):
        errors.append(f"malformed core_requires: {core_requires!r}")
    elif not version_satisfies(core_requires, CORE_VERSION):
        errors.append(
            f"core_requires not satisfied: {core_requires!r} (core {CORE_VERSION})"
        )

    name = raw.get("name")
    if name is None:
        name = mod_id
        warnings.append("name missing; using id")
    elif not isinstance(name, str) or not name.strip():
        errors.append("name must be a non-empty string")
        name = mod_id
    else:
        name = name.strip()

    name_zh_raw = raw.get("name_zh", "")
    name_zh = ""
    if isinstance(name_zh_raw, str):
        name_zh = name_zh_raw.strip()
        if len(name_zh) > 128:
            name_zh = name_zh[:128]
            warnings.append("name_zh truncated to 128 characters")
    if "name_zh" in raw and not name_zh:
        warnings.append("name_zh invalid; ignored")

    author = raw.get("author", "")
    if author is None:
        author = ""
    elif not isinstance(author, str):
        warnings.append("author must be a string")
        author = ""

    execution = raw.get("execution", "host-only")
    if not isinstance(execution, str) or execution not in EXECUTIONS:
        errors.append(
            f"execution must be one of {sorted(EXECUTIONS)}, got {execution!r}"
        )
        execution = "host-only"

    entrypoint_raw = raw.get("entrypoint")
    entrypoint: str | None = None
    if kind == "content":
        if entrypoint_raw is not None:
            errors.append("content mods must not declare entrypoint")
        if execution != "host-only":
            errors.append("content mods must use host-only execution")
    elif kind == "feature":
        if not isinstance(entrypoint_raw, str) or not entrypoint_raw.strip():
            errors.append("feature mods require entrypoint")
        else:
            entrypoint = entrypoint_raw.strip()
            target = _entrypoint_target(entrypoint)
            if target is None:
                errors.append(f"invalid entrypoint: {entrypoint!r}")
            else:
                resolved, problem = _safe_path(pack, target)
                if problem is not None:
                    errors.append(f"entrypoint: {problem}")
                elif resolved is not None and not resolved.is_file():
                    warnings.append(f"entrypoint not found: {target}")

    capabilities_raw = raw.get("capabilities", [])
    if capabilities_raw is None:
        capabilities_raw = []
    capabilities: tuple[str, ...] = ()
    if not isinstance(capabilities_raw, list):
        errors.append("capabilities must be a list")
    else:
        declared: list[str] = []
        for item in capabilities_raw:
            if not isinstance(item, str):
                errors.append(f"invalid capability: {item!r}")
            elif item not in CAPABILITIES:
                errors.append(f"unknown capability: {item!r}")
            else:
                declared.append(item)
        capabilities = tuple(sorted(set(declared)))

    requires_raw = raw.get("requires", [])
    if requires_raw is None:
        requires_raw = []
    requires: tuple[str, ...] = ()
    if not isinstance(requires_raw, list):
        errors.append("requires must be a list")
    else:
        declared_requires: list[str] = []
        for item in requires_raw:
            if not isinstance(item, str) or not is_valid_id(item):
                errors.append(f"invalid requires entry: {item!r}")
            else:
                declared_requires.append(item)
        requires = tuple(dict.fromkeys(declared_requires))

    character = raw.get("character")
    if character is not None and not isinstance(character, dict):
        errors.append("character must be an object")
        character = None
    if isinstance(character, dict):
        _scan_paths(pack, character, "character", errors)
        strides = character.get("move_strides")
        if isinstance(strides, dict):
            for stride_name, stride_value in strides.items():
                if isinstance(stride_value, str):
                    _check_path(
                        pack,
                        stride_value,
                        f"character.move_strides.{stride_name}",
                        errors,
                    )

    blocks: dict[str, dict[str, Any] | None] = {}
    for key in CONTENT_BLOCK_KEYS:
        block = raw.get(key)
        if block is None:
            blocks[key] = None
            continue
        if not isinstance(block, dict):
            errors.append(f"{key} must be an object")
            blocks[key] = None
            continue
        for entry_id, entry_data in block.items():
            if not isinstance(entry_id, str) or not entry_id.strip():
                errors.append(f"{key}: invalid entry id {entry_id!r}")
                continue
            _scan_paths(
                pack,
                entry_data,
                f"{key}.{entry_id}",
                errors,
                value_is_path=True,
            )
        blocks[key] = block

    if kind == "content":
        _scan_content_pack(pack, errors)

    if errors:
        return None, [*errors, *warnings]

    manifest = ModManifest(
        id=mod_id,
        kind=kind,
        name=name,
        name_zh=name_zh,
        version=version,
        path=pack,
        author=author,
        execution=execution,
        entrypoint=entrypoint,
        capabilities=capabilities,
        character=character,
        diagnostics=tuple(warnings),
        api_version=API_VERSION,
        core_requires=core_requires,
        requires=requires,
        sounds=blocks["sounds"],
        themes=blocks["themes"],
        phrases=blocks["phrases"],
        seasonal=blocks["seasonal"],
    )
    return manifest, warnings


def load_manifest(pack_dir: Path) -> tuple[ModManifest | None, list[str]]:
    try:
        pack = Path(pack_dir).resolve()
    except OSError as exc:
        return None, [f"cannot resolve pack path: {exc}"]
    if not pack.is_dir():
        return None, [f"pack is not a directory: {pack}"]
    try:
        mod_file = (pack / MOD_FILE).resolve()
    except OSError as exc:
        return None, [f"{MOD_FILE}: cannot resolve: {exc}"]
    if not path_within(pack, mod_file):
        return None, [f"{MOD_FILE} resolves outside pack: {mod_file}"]
    if not mod_file.is_file():
        return None, [f"{MOD_FILE} not found in {pack}"]
    try:
        raw = json.loads(mod_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"{MOD_FILE}: invalid JSON: {exc}"]
    if not isinstance(raw, dict):
        return None, [f"{MOD_FILE}: top level must be an object"]
    try:
        return _build(pack, raw)
    except Exception as exc:
        return None, [f"{MOD_FILE}: {type(exc).__name__}: {exc}"]
