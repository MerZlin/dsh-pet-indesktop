"""Small local MOD catalog adapters; package services remain the state owners."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .content import CharacterRegistry, ContentError, ContentManager
from .content.manifest import load_manifest
from .feature_state_io import StateError, safe_path


@dataclass(frozen=True)
class ModEntry:
    id: str
    kind: str
    name: str
    version: str
    description: str
    enabled: bool
    path: Path
    source: str = "installed"
    character_id: str = ""
    pending: str = ""

    @property
    def managed(self) -> bool:
        return self.source == "installed"

    @property
    def key(self) -> str:
        return self.kind + ":" + self.id


@dataclass(frozen=True)
class ModOutcome:
    success: bool
    message: str
    pending: bool = False


class ModSelection:
    """A changed visible scope invalidates selection, never hidden deletion."""
    def __init__(self):
        self.visible: set[str] = set()
        self.selected: set[str] = set()

    def set_visible(self, entries: Iterable[ModEntry]) -> None:
        self.visible = {entry.id for entry in entries}
        self.selected.clear()

    def select_all(self) -> None:
        self.selected = set(self.visible)


class ResourceMods:
    """Only managed copies are mutable; callback must release live readers first.

    The application supplies cross-process preparation. Without it, destructive
    resource operations are refused rather than guessing that no pet uses it.
    """
    def __init__(self, data_root: Path, *, prepare_removal: Callable[[ModEntry], bool] | None = None):
        self.data_root = Path(data_root).absolute()
        self.manager = ContentManager(data_root=self.data_root / "content")
        self.prepare_removal = prepare_removal or (lambda _entry: False)

    def entries(self, *, include_unmanaged: bool = True) -> list[ModEntry]:
        rows = []
        root = self.manager.characters_root
        safe_path(root)
        if root.is_dir():
            for character in sorted(root.iterdir()):
                safe_path(character)
                pointer = self.manager._active_for_character(character.name)
                if not pointer:
                    continue
                version = pointer.get("version")
                if not isinstance(version, str) or not version or Path(version).name != version or version in {".", ".."}:
                    continue
                package = character / "versions" / version
                safe_path(package)
                manifest, errors = load_manifest(package)
                if manifest is None or errors or manifest.plugin_id != pointer.get("plugin_id"):
                    continue
                rows.append(ModEntry(manifest.plugin_id, "角色资源", manifest.name or manifest.plugin_id, manifest.version,
                                     manifest.description, pointer.get("enabled", True) is True, package,
                                     character_id=character.name))
        if include_unmanaged:
            # A separate registry deliberately excludes installed versions so an
            # override does not hide the built-in recovery option with the same ID.
            registry = CharacterRegistry(installed_root=self.data_root / ".no-installed-characters",
                                         legacy_roots=[self.data_root / "characters"])
            for character in registry.list_available():
                rows.append(ModEntry("resource:" + character.source + ":" + character.character_id, "角色资源",
                                     character.display_name, character.manifest.version, character.manifest.description,
                                     True, character.root, source=character.source, character_id=character.character_id))
        return rows

    def _entry(self, package_id: str) -> ModEntry:
        entry = next((row for row in self.entries(include_unmanaged=False) if row.id == package_id), None)
        if entry is None:
            raise ContentError("未找到已安装的角色包")
        return entry

    def install(self, source: Path, *, expected_id: str | None = None) -> ModEntry:
        validation = self.manager.validate(Path(source), allow_unsigned=True)
        if not validation.valid or validation.manifest is None:
            raise ContentError("; ".join(validation.errors) or "无效的角色资源包")
        owner = validation.manifest.plugin_id
        if expected_id is not None and owner != expected_id:
            raise ContentError("更新文件不是同一个角色包")
        old = next((row for row in self.entries(include_unmanaged=False) if row.id == owner), None)
        if old and old.version != validation.manifest.version and not self.prepare_removal(old):
            raise ContentError("角色仍在使用：请自然退出使用该素材的桌宠后再更新")
        self.manager.install(Path(source), allow_unsigned=True, enabled=None if old else False)
        return self._entry(owner)

    def set_enabled(self, package_id: str, enabled: bool) -> ModOutcome:
        entry = self._entry(package_id)
        if entry.enabled == enabled:
            return ModOutcome(True, "已启用" if enabled else "已停用")
        if not enabled and not self.prepare_removal(entry):
            return ModOutcome(False, "无法释放正在使用的角色；请先自然退出相关桌宠", True)
        try:
            self.manager.set_enabled(package_id, enabled)
        except (OSError, ValueError, ContentError, StateError):
            return ModOutcome(False, "角色状态保存失败；文件未删除，请检查目录占用")
        return ModOutcome(True, "已启用，可点击“使用”换装" if enabled else "已停用")

    def remove(self, package_id: str) -> ModOutcome:
        entry = self._entry(package_id)
        if not self.prepare_removal(entry):
            return ModOutcome(False, "无法释放正在使用的角色；请先自然退出相关桌宠", True)
        try:
            versions = entry.path.parent
            if not versions.is_relative_to(self.manager.characters_root):
                raise ContentError("删除边界无效")
            safe_path(versions)
            targets = []
            for target in versions.iterdir():
                safe_path(target)
                manifest, errors = load_manifest(target)
                if manifest is None or errors or manifest.plugin_id != package_id:
                    raise ContentError("角色目录含未识别版本，保留文件")
                for child in target.rglob("*"):
                    safe_path(child)
                targets.append(manifest.version)
            # All boundaries are checked before changing state or deleting files.
            self.manager.set_enabled(package_id, False)
            for version in sorted(targets, key=lambda version: version == entry.version):
                self.manager.uninstall(package_id, version)
        except (OSError, ValueError, ContentError, StateError):
            return ModOutcome(False, "删除未完成：请检查文件占用或目录内容；未删除部分已保留")
        return ModOutcome(True, "已删除安装副本；原始文件与个人数据保留")

    def rollback(self, package_id: str) -> ModOutcome:
        entry = self._entry(package_id)
        if not self.prepare_removal(entry):
            return ModOutcome(False, "请自然退出使用该角色的桌宠后重试", True)
        try:
            enabled = entry.enabled
            self.manager.rollback(package_id)
            self.manager.set_enabled(package_id, enabled)
        except (OSError, ValueError, ContentError, StateError):
            return ModOutcome(False, "没有可回滚版本，或文件仍被占用")
        return ModOutcome(True, "已恢复上一版")
