"""插件 manifest 与运行时诊断模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.-]+)?$")


class PluginManifestError(ValueError):
    """manifest 结构或兼容性声明不合法。"""


def _version_parts(value: str) -> tuple[int, ...]:
    text = str(value).strip().lstrip("v")
    core = text.split("+", 1)[0].split("-", 1)[0]
    try:
        parts = tuple(int(item) for item in core.split("."))
    except ValueError as exc:
        raise PluginManifestError(f"invalid version: {value!r}") from exc
    return parts + (0,) * (3 - len(parts))


def version_satisfies(version: str, specifier: str) -> bool:
    """覆盖 Phase 2 所需的简单 semver 范围，不引入新的运行时依赖。"""
    actual = _version_parts(version)
    text = str(specifier or "").strip()
    if not text or text in {"*", ">=0"}:
        return True
    for clause in (item.strip() for item in text.split(",")):
        if not clause:
            continue
        operator = "=="
        for candidate in (">=", "<=", ">", "<", "==", "="):
            if clause.startswith(candidate):
                operator = candidate
                clause = clause[len(candidate) :].strip()
                break
        expected = _version_parts(clause)
        if operator in {"=", "=="} and actual != expected:
            return False
        if operator == ">=" and actual < expected:
            return False
        if operator == "<=" and actual > expected:
            return False
        if operator == ">" and actual <= expected:
            return False
        if operator == "<" and actual >= expected:
            return False
    return True


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    kind: str
    api_version: str
    core_requires: str
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    entrypoint: str | None = None
    default_enabled: bool = True
    legacy_config_map: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PluginManifest":
        if not isinstance(value, Mapping):
            raise PluginManifestError("plugin manifest must be an object")
        try:
            manifest = cls(
                id=str(value["id"]),
                name=str(value["name"]),
                version=str(value["version"]),
                kind=str(value["kind"]),
                api_version=str(value["api_version"]),
                core_requires=str(value.get("core_requires", ">=0")),
                dependencies=tuple(str(item) for item in value.get("dependencies", ()) or ()),
                capabilities=tuple(str(item) for item in value.get("capabilities", ()) or ()),
                entrypoint=value.get("entrypoint"),
                default_enabled=bool(value.get("default_enabled", True)),
                legacy_config_map=dict(value.get("legacy_config_map", {}) or {}),
                raw=dict(value),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginManifestError(f"invalid plugin manifest fields: {exc}") from exc
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if not _PLUGIN_ID_RE.fullmatch(self.id):
            raise PluginManifestError(f"invalid plugin id: {self.id!r}")
        if not self.name.strip():
            raise PluginManifestError("plugin name must not be empty")
        if not _VERSION_RE.fullmatch(self.version):
            raise PluginManifestError(f"invalid plugin version: {self.version!r}")
        if self.kind != "in_process":
            raise PluginManifestError(f"Phase 2 only accepts in_process plugins, got {self.kind!r}")
        if not self.api_version.strip():
            raise PluginManifestError("plugin api_version must not be empty")
        if self.entrypoint not in (None, ""):
            raise PluginManifestError("Phase 2 does not execute arbitrary Python entrypoints")
        if any(not _PLUGIN_ID_RE.fullmatch(item) for item in self.dependencies):
            raise PluginManifestError("invalid plugin dependency id")
        if any(not item.strip() for item in self.capabilities):
            raise PluginManifestError("plugin capabilities must not be empty")
        for canonical, legacy in self.legacy_config_map.items():
            if not str(canonical).strip() or not str(legacy).strip():
                raise PluginManifestError("legacy config mapping contains an empty key")
        # 先验证格式，兼容范围是否匹配由 Registry 结合当前 Core 版本判断。
        for clause in str(self.core_requires).split(","):
            clause = clause.strip()
            if clause:
                _version_parts(clause.lstrip("><=! "))


@dataclass(frozen=True)
class PluginDiagnostic:
    plugin_id: str
    stage: str
    reason: str
    severity: str = "error"
    status: str = "fault"
    details: Mapping[str, Any] = field(default_factory=dict)
