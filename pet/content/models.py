"""资源型 DLC 的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ContentManifest:
    plugin_id: str
    name: str
    version: str
    kind: str
    api_version: str
    core_requires: str
    platforms: tuple[str, ...]
    dependencies: tuple[str, ...]
    capabilities: tuple[str, ...]
    entrypoint: str | None
    characters: tuple[str, ...]
    integrity_sha256: str | None
    signature: str | None
    raw: dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True)
class ContentPackage:
    manifest: ContentManifest
    root: Path
    source: str
    content_sha256: str | None = None

    @property
    def plugin_id(self) -> str:
        return self.manifest.plugin_id

    @property
    def version(self) -> str:
        return self.manifest.version


@dataclass(frozen=True)
class CharacterPackage:
    character_id: str
    package: ContentPackage
    root: Path
    source: str
    fallback_rank: int = 0

    @property
    def manifest(self) -> ContentManifest:
        return self.package.manifest

    @property
    def video_dir(self) -> Path:
        return self.root / "videos"

    @property
    def display_name(self) -> str:
        return self.manifest.name if len(self.manifest.characters) == 1 else self.character_id


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    source: Path
    source_kind: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest: ContentManifest | None = None
    extracted_root: Path | None = None
    content_sha256: str | None = None


@dataclass(frozen=True)
class InstallResult:
    plugin_id: str
    version: str
    activated: bool
    path: Path
    rolled_back: bool = False
    warnings: tuple[str, ...] = ()
