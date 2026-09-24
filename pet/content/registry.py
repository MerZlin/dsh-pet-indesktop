"""角色资源 Registry：把 bundled、installed 和 legacy 来源收口为统一模型。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from .. import __version__
from .manifest import current_platform, load_manifest, validate_package_root
from .models import CharacterPackage, ContentManifest, ContentPackage

_LOG = logging.getLogger(__name__)


class CharacterRegistry:
    """按固定优先级发现角色资源，并隔离失效 DLC。"""

    def __init__(
        self,
        *,
        bundled_root: Path | None = None,
        installed_root: Path | None = None,
        legacy_roots: Iterable[Path] | None = None,
        core_version: str = __version__,
        platform_name: str | None = None,
        allow_unsigned: bool = False,
    ) -> None:
        self.bundled_root = Path(bundled_root) if bundled_root is not None else self._default_bundled_root()
        self.installed_root = Path(installed_root) if installed_root is not None else self._default_installed_root()
        self.legacy_roots = [Path(path) for path in legacy_roots] if legacy_roots is not None else self._default_legacy_roots()
        self.core_version = core_version
        self.platform_name = platform_name or current_platform()
        self.allow_unsigned = allow_unsigned
        self._cache: dict[str, CharacterPackage] | None = None
        self.diagnostics: list[dict[str, str]] = []

    @staticmethod
    def _default_bundled_root() -> Path:
        from .paths import bundled_content_root

        return bundled_content_root() / "characters"

    @staticmethod
    def _default_installed_root() -> Path:
        from .paths import installed_characters_root

        return installed_characters_root()

    @staticmethod
    def _default_legacy_roots() -> list[Path]:
        from .. import catalog

        roots = list(catalog.external_character_dirs())
        roots.append(catalog.characters_dir())
        roots.append(catalog.characters_gif_dir())
        return roots

    def invalidate(self) -> None:
        self._cache = None
        self.diagnostics.clear()

    def _record_failure(self, plugin_id: str, version: str, stage: str, reason: str, fallback: str) -> None:
        self.diagnostics.append(
            {
                "plugin_id": plugin_id,
                "version": version,
                "failure_stage": stage,
                "reason": reason,
                "fallback_source": fallback,
            }
        )
        _LOG.warning(
            "content DLC rejected plugin_id=%s version=%s stage=%s fallback=%s: %s",
            plugin_id,
            version,
            stage,
            fallback,
            reason,
        )

    def _package_from_root(self, root: Path, source: str, fallback_rank: int) -> list[CharacterPackage]:
        if not root.is_dir():
            return []
        manifest, errors = load_manifest(root)
        if manifest is None:
            self._record_failure(root.name, "unknown", "manifest", "; ".join(errors), source)
            return []
        manifest, errors, digest = validate_package_root(
            root,
            core_version=self.core_version,
            platform_name=self.platform_name,
            allow_unsigned=self.allow_unsigned,
            verify_hash=False,
        )
        if manifest is None or errors:
            self._record_failure(
                manifest.plugin_id if manifest else root.name, manifest.version if manifest else "unknown", "validation", "; ".join(errors), source
            )
            return []
        package = ContentPackage(manifest=manifest, root=root, source=source, content_sha256=digest)
        return [
            CharacterPackage(
                character_id=character_id,
                package=package,
                root=root if root.name == character_id else root / "characters" / character_id,
                source=source,
                fallback_rank=fallback_rank,
            )
            for character_id in manifest.characters
        ]

    def _installed(self) -> list[CharacterPackage]:
        result: list[CharacterPackage] = []
        if not self.installed_root.is_dir():
            return result
        for character_dir in sorted(self.installed_root.iterdir(), key=lambda item: item.name):
            if not character_dir.is_dir():
                continue
            active_file = character_dir / "active.json"
            if not active_file.is_file():
                continue
            try:
                import json

                active = json.loads(active_file.read_text(encoding="utf-8"))
                version = str(active["version"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._record_failure(character_dir.name, "unknown", "active-pointer", str(exc), "installed")
                continue
            root = character_dir / "versions" / version
            result.extend(self._package_from_root(root, "installed", 0))
        return result

    def _bundled(self) -> list[CharacterPackage]:
        if not self.bundled_root.is_dir():
            return []
        result: list[CharacterPackage] = []
        for root in sorted(self.bundled_root.iterdir(), key=lambda item: item.name):
            result.extend(self._package_from_root(root, "bundled", 1))
        return result

    @staticmethod
    def _legacy_manifest(character_id: str) -> ContentManifest:
        return ContentManifest(
            plugin_id=f"legacy.character.{character_id}",
            name=character_id,
            version="0.0.0",
            kind="content",
            api_version="1",
            core_requires=">=0",
            platforms=("windows", "macos", "linux"),
            dependencies=(),
            capabilities=("character", "animation"),
            entrypoint=None,
            characters=(character_id,),
            integrity_sha256=None,
            signature=None,
            raw={},
        )

    def _legacy(self) -> list[CharacterPackage]:
        result: list[CharacterPackage] = []
        seen: set[str] = set()
        for root in self.legacy_roots:
            if not root.is_dir():
                continue
            for character_dir in sorted(root.iterdir(), key=lambda item: item.name):
                if not character_dir.is_dir() or character_dir.name in seen:
                    continue
                videos = character_dir / "videos"
                if not videos.is_dir() or not any(v.suffix.lower() in {".webm", ".gif"} for v in videos.rglob("*")):
                    continue
                character_id = character_dir.name
                seen.add(character_id)
                manifest = ContentManifest(
                    **{**self._legacy_manifest(character_id).__dict__},
                )
                package = ContentPackage(manifest=manifest, root=character_dir, source="legacy")
                result.append(CharacterPackage(character_id, package, character_dir, "legacy", 2))
        return result

    def scan(self) -> list[CharacterPackage]:
        self.invalidate()
        selected: dict[str, CharacterPackage] = {}
        for package in self._installed() + self._bundled() + self._legacy():
            selected.setdefault(package.character_id, package)
        self._cache = selected
        return self.list_available()

    def list_available(self) -> list[CharacterPackage]:
        if self._cache is None:
            self.scan()
        return [self._cache[key] for key in sorted(self._cache)]

    def get(self, character_id: str) -> CharacterPackage | None:
        if self._cache is None:
            self.scan()
        return self._cache.get(character_id)

    def resolve(self, character_id: str) -> CharacterPackage | None:
        return self.get(character_id)
