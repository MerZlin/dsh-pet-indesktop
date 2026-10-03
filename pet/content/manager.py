"""本地资源 DLC 的发现、安装、激活、升级和回滚服务。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import __version__
from .hashing import validate_zip_members
from .manifest import current_platform, validate_package_root
from .models import ContentPackage, InstallResult, ValidationResult
from .paths import installed_characters_root
from .registry import CharacterRegistry


class ContentError(RuntimeError):
    """资源 DLC 操作失败。"""


class ContentManager:
    def __init__(
        self,
        *,
        data_root: Path | None = None,
        core_version: str = __version__,
        platform_name: str | None = None,
        registry: CharacterRegistry | None = None,
    ) -> None:
        self.data_root = Path(data_root) if data_root is not None else installed_characters_root().parent
        self.characters_root = self.data_root / "characters"
        self.staging_root = self.data_root / "staging"
        self.transaction_root = self.staging_root / "transactions"
        self.recovery_diagnostics: list[str] = []
        self.core_version = core_version
        self.platform_name = platform_name or current_platform()
        self.registry = registry or CharacterRegistry(
            installed_root=self.characters_root,
            core_version=core_version,
            platform_name=self.platform_name,
        )

    def _package_root(self, character_id: str, version: str) -> Path:
        return self.characters_root / character_id / "versions" / version

    def _pointer_path(self, character_id: str, name: str = "active") -> Path:
        return self.characters_root / character_id / f"{name}.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    @staticmethod
    def _snapshot_file(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    @staticmethod
    def _restore_file_snapshot(path: Path, contents: bytes | None) -> None:
        if contents is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.restore.tmp")
        try:
            temp.write_bytes(contents)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def _transaction_path(self, operation_id: str) -> Path:
        return self.transaction_root / f"{operation_id}.json"

    def _write_transaction(self, record: dict[str, Any]) -> None:
        self._write_json_atomic(self._transaction_path(str(record["operation_id"])), record)

    def _remove_transaction(self, operation_id: str) -> None:
        self._transaction_path(operation_id).unlink(missing_ok=True)

    def _cleanup_empty_resource_dirs(self, character_id: str) -> None:
        character_root = self.characters_root / character_id
        versions_root = character_root / "versions"
        for directory in (versions_root, character_root):
            try:
                directory.rmdir()
            except OSError:
                pass

    def _ensure_no_unresolved_operations(self) -> None:
        self.recover_pending_operations()
        if self.recovery_diagnostics:
            raise ContentError("pending resource operation requires recovery: " + "; ".join(self.recovery_diagnostics))

    def _apply_pointer_state(self, character_id: str, name: str, value: dict[str, Any] | None) -> None:
        path = self._pointer_path(character_id, name)
        if value is None:
            path.unlink(missing_ok=True)
        else:
            self._write_json_atomic(path, value)

    def recover_pending_operations(self) -> None:
        """Recover only resource uninstall journals that are provably consistent.

        This method never guesses from timestamps or directory names.  An
        ambiguous journal is kept on disk and reported to the caller so a
        future management layer can present an explicit recovery decision.
        """

        self.recovery_diagnostics = []
        if not self.transaction_root.is_dir():
            return

        for journal_path in sorted(self.transaction_root.glob("*.json")):
            record = self._read_json(journal_path)
            if record is None:
                self.recovery_diagnostics.append(f"invalid transaction record: {journal_path.name}")
                continue
            if record.get("kind") != "uninstall":
                self.recovery_diagnostics.append(f"unsupported transaction kind: {journal_path.name}")
                continue

            operation_id = record.get("operation_id")
            character_id = record.get("character_id")
            version = record.get("version")
            phase = record.get("phase")
            plugin_id = record.get("plugin_id")
            before_active = record.get("active_before")
            before_previous = record.get("previous_before")
            after_active = record.get("active_after")
            after_previous = record.get("previous_after")
            expected_digest = record.get("target_content_sha256")
            if not all(isinstance(value, str) and value for value in (operation_id, character_id, version, phase, plugin_id)):
                self.recovery_diagnostics.append(f"malformed transaction record: {journal_path.name}")
                continue
            assert isinstance(operation_id, str)
            assert isinstance(character_id, str)
            assert isinstance(version, str)
            assert isinstance(phase, str)
            assert isinstance(plugin_id, str)
            if not all(value is None or isinstance(value, dict) for value in (before_active, before_previous, after_active, after_previous)):
                self.recovery_diagnostics.append(f"malformed pointer snapshot: {journal_path.name}")
                continue

            target = self._package_root(character_id, version)
            active_path = self._pointer_path(character_id)
            previous_path = self._pointer_path(character_id, "previous")
            current_active = self._read_json(active_path)
            current_previous = self._read_json(previous_path)

            if phase == "prepared":
                if not target.exists():
                    self.recovery_diagnostics.append(f"uninstall target missing before commit: {journal_path.name}")
                    continue
                validation = self.validate(target, allow_unsigned=True)
                if not validation.valid or (expected_digest and validation.content_sha256 != expected_digest):
                    self.recovery_diagnostics.append(f"uninstall target changed during preparation: {journal_path.name}")
                    continue
                if current_active != before_active or current_previous != before_previous:
                    self.recovery_diagnostics.append(f"pointers changed during preparation: {journal_path.name}")
                    continue
                self._remove_transaction(operation_id)
                continue

            if phase == "version_removed":
                if target.exists():
                    self.recovery_diagnostics.append(f"removed transaction still has target: {journal_path.name}")
                    continue
                if current_active == before_active and current_previous == before_previous:
                    self._apply_pointer_state(character_id, "active", after_active)
                    self._apply_pointer_state(character_id, "previous", after_previous)
                elif current_active == after_active and current_previous == after_previous:
                    pass
                else:
                    self.recovery_diagnostics.append(f"pointers diverged during recovery: {journal_path.name}")
                    continue
                record["phase"] = "pointers_committed"
                self._write_transaction(record)
                phase = "pointers_committed"
                # The pointer writes above may have changed either file to a
                # non-empty snapshot.  Re-read unconditionally before checking
                # the committed phase; stale values must never authorize cleanup.
                current_active = self._read_json(active_path)
                current_previous = self._read_json(previous_path)

            if phase == "pointers_committed":
                if target.exists():
                    self.recovery_diagnostics.append(f"committed uninstall target still exists: {journal_path.name}")
                    continue
                if current_active != after_active or current_previous != after_previous:
                    # A journal can be discovered after another process has
                    # changed the pointers.  Do not overwrite that newer state.
                    self.recovery_diagnostics.append(f"committed pointers do not match: {journal_path.name}")
                    continue
                self._remove_transaction(operation_id)
                self._cleanup_empty_resource_dirs(character_id)
                self._invalidate_registry()
                continue

            if phase not in {"prepared", "version_removed", "pointers_committed"}:
                self.recovery_diagnostics.append(f"unsupported transaction phase: {journal_path.name}")

    @staticmethod
    def _safe_copytree(source: Path, destination: Path) -> None:
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            target = destination / relative
            if path.is_symlink():
                raise ContentError(f"symlink is not allowed: {relative.as_posix()}")
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

    @staticmethod
    def _extract_zip(source: Path, destination: Path) -> Path:
        with zipfile.ZipFile(source) as archive:
            members = validate_zip_members(archive)
            for rel in members:
                target = destination.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(rel.as_posix(), "r") as source_handle, target.open("wb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
        if (destination / "manifest.json").is_file():
            return destination
        children = [path for path in destination.iterdir() if path.is_dir()]
        if len(children) == 1 and (children[0] / "manifest.json").is_file():
            return children[0]
        return destination

    def validate(self, source: Path, *, allow_unsigned: bool = False) -> ValidationResult:
        source = Path(source)
        source_kind = "directory" if source.is_dir() else "zip" if source.is_file() and zipfile.is_zipfile(source) else "unknown"
        if source_kind == "unknown":
            return ValidationResult(False, source, source_kind, errors=("source must be a directory or ZIP",))
        temp_dir: Path | None = None
        try:
            root = source
            if source_kind == "zip":
                temp_dir = Path(tempfile.mkdtemp(prefix="validate-", dir=self.staging_root if self.staging_root.exists() else None))
                root = self._extract_zip(source, temp_dir)
            manifest, errors, digest = validate_package_root(
                root,
                core_version=self.core_version,
                platform_name=self.platform_name,
                allow_unsigned=allow_unsigned,
            )
            return ValidationResult(
                valid=not errors,
                source=source,
                source_kind=source_kind,
                errors=tuple(errors),
                manifest=manifest,
                extracted_root=root if source_kind == "zip" else None,
                content_sha256=digest,
            )
        except (OSError, ValueError, zipfile.BadZipFile, ContentError) as exc:
            return ValidationResult(False, source, source_kind, errors=(str(exc),))
        finally:
            if temp_dir is not None:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _invalidate_registry(self) -> None:
        self.registry.invalidate()
        try:
            from .. import catalog

            catalog.character_body_box.cache_clear()
            catalog.character_head_box.cache_clear()
        except (AttributeError, ImportError):
            pass

    def install(self, source: Path, *, allow_unsigned: bool = False) -> InstallResult:
        self._ensure_no_unresolved_operations()
        source = Path(source)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="install-", dir=self.staging_root))
        installed_root: Path | None = None
        temp_version: Path | None = None
        installed_root_created = False
        activation_attempted = False
        active_snapshot: bytes | None = None
        previous_snapshot: bytes | None = None
        active_path: Path | None = None
        previous_path: Path | None = None
        try:
            package_root = work_dir / "package"
            package_root.mkdir()
            if source.is_dir():
                self._safe_copytree(source, package_root)
            elif source.is_file() and zipfile.is_zipfile(source):
                package_root = self._extract_zip(source, package_root)
            else:
                raise ContentError("source must be a directory or ZIP")
            manifest, errors, digest = validate_package_root(
                package_root,
                core_version=self.core_version,
                platform_name=self.platform_name,
                allow_unsigned=allow_unsigned,
            )
            if errors or manifest is None:
                raise ContentError("; ".join(errors) or "invalid content package")
            if len(manifest.characters) != 1:
                raise ContentError("Phase 1 installation requires exactly one character per package")
            character_id = manifest.characters[0]
            installed_root = self._package_root(character_id, manifest.version)
            active_path = self._pointer_path(character_id)
            previous_path = self._pointer_path(character_id, "previous")
            active_snapshot = self._snapshot_file(active_path)
            previous_snapshot = self._snapshot_file(previous_path)
            installed_root.parent.mkdir(parents=True, exist_ok=True)
            if installed_root.exists():
                _existing_manifest, existing_errors, existing_hash = validate_package_root(
                    installed_root,
                    core_version=self.core_version,
                    platform_name=self.platform_name,
                    allow_unsigned=True,
                )
                if not existing_errors and existing_hash == digest:
                    activation_attempted = True
                    self.activate(manifest.plugin_id, manifest.version)
                    return InstallResult(manifest.plugin_id, manifest.version, True, installed_root)
                raise ContentError(f"version already exists with different content: {manifest.version}")
            temp_version = installed_root.parent / f".{manifest.version}.{uuid4().hex}.tmp"
            self._safe_copytree(package_root, temp_version)
            os.replace(temp_version, installed_root)
            installed_root_created = True
            activation_attempted = True
            self._activate_character(character_id, manifest.plugin_id, manifest.version, validate=False)
            self._self_check_active(character_id, allow_unsigned=allow_unsigned)
            self._invalidate_registry()
            return InstallResult(manifest.plugin_id, manifest.version, True, installed_root)
        except Exception:
            if activation_attempted and active_path is not None and previous_path is not None:
                self._restore_file_snapshot(active_path, active_snapshot)
                self._restore_file_snapshot(previous_path, previous_snapshot)
            if temp_version is not None:
                shutil.rmtree(temp_version, ignore_errors=True)
            if installed_root_created and installed_root is not None:
                shutil.rmtree(installed_root, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def _active_for_character(self, character_id: str) -> dict[str, Any] | None:
        return self._read_json(self._pointer_path(character_id))

    def active_version(self, plugin_id: str) -> str | None:
        for character_dir in self.characters_root.glob("*/"):
            active = self._active_for_character(character_dir.name)
            if active and active.get("plugin_id") == plugin_id:
                return str(active.get("version"))
        return None

    def _activate_character(self, character_id: str, plugin_id: str, version: str, *, validate: bool = True) -> None:
        root = self._package_root(character_id, version)
        if not root.is_dir():
            raise ContentError(f"package version not found: {plugin_id}@{version}")
        if validate:
            manifest, errors, _digest = validate_package_root(
                root,
                core_version=self.core_version,
                platform_name=self.platform_name,
                allow_unsigned=True,
            )
            if errors or manifest is None or manifest.plugin_id != plugin_id:
                raise ContentError("; ".join(errors) or "package activation validation failed")
        active_path = self._pointer_path(character_id)
        previous = self._read_json(active_path)
        if previous:
            self._write_json_atomic(self._pointer_path(character_id, "previous"), previous)
        self._write_json_atomic(active_path, {"plugin_id": plugin_id, "version": version})

    def activate(self, plugin_id: str, version: str) -> None:
        for character_dir in self.characters_root.glob("*/"):
            candidate = self._package_root(character_dir.name, version)
            manifest_path = candidate / "manifest.json"
            if not manifest_path.is_file():
                continue
            result = self.validate(candidate, allow_unsigned=True)
            if result.valid and result.manifest and result.manifest.plugin_id == plugin_id:
                self._activate_character(character_dir.name, plugin_id, version)
                self._self_check_active(character_dir.name, allow_unsigned=True)
                self._invalidate_registry()
                return
        raise ContentError(f"package version not found: {plugin_id}@{version}")

    def _self_check_active(self, character_id: str, *, allow_unsigned: bool) -> None:
        active = self._active_for_character(character_id)
        if not active:
            raise ContentError(f"active pointer is missing: {character_id}")
        root = self._package_root(character_id, str(active["version"]))
        manifest, errors, _digest = validate_package_root(
            root,
            core_version=self.core_version,
            platform_name=self.platform_name,
            allow_unsigned=allow_unsigned,
        )
        if errors or manifest is None:
            previous = self._read_json(self._pointer_path(character_id, "previous"))
            if previous:
                self._write_json_atomic(self._pointer_path(character_id), previous)
            else:
                self._pointer_path(character_id).unlink(missing_ok=True)
            raise ContentError("active package self-check failed: " + "; ".join(errors))

    def rollback(self, plugin_id: str) -> None:
        for character_dir in self.characters_root.glob("*/"):
            active = self._active_for_character(character_dir.name)
            previous = self._read_json(self._pointer_path(character_dir.name, "previous"))
            if not active or not previous or active.get("plugin_id") != plugin_id:
                continue
            self._write_json_atomic(self._pointer_path(character_dir.name), previous)
            self._write_json_atomic(self._pointer_path(character_dir.name, "previous"), active)
            self._self_check_active(character_dir.name, allow_unsigned=True)
            self._invalidate_registry()
            return
        raise ContentError(f"no rollback version for {plugin_id}")

    def uninstall(self, plugin_id: str, version: str | None = None) -> None:
        self._ensure_no_unresolved_operations()
        for character_dir in self.characters_root.glob("*/"):
            active = self._active_for_character(character_dir.name)
            target_version = version
            if target_version is None and active and active.get("plugin_id") == plugin_id:
                target_version = str(active.get("version"))
            if target_version is None:
                continue
            target = self._package_root(character_dir.name, target_version)
            manifest_result = self.validate(target, allow_unsigned=True) if target.exists() else None
            if not manifest_result or not manifest_result.manifest or manifest_result.manifest.plugin_id != plugin_id:
                continue

            active_path = self._pointer_path(character_dir.name)
            previous_path = self._pointer_path(character_dir.name, "previous")
            active_before = self._read_json(active_path)
            previous_before = self._read_json(previous_path)
            was_active = bool(active_before and active_before.get("version") == target_version)
            if was_active and previous_before:
                active_after = previous_before
            elif was_active:
                active_after = None
            else:
                active_after = active_before
            previous_after = None if was_active else previous_before

            operation_id = uuid4().hex
            self._write_transaction(
                {
                    "operation_id": operation_id,
                    "kind": "uninstall",
                    "phase": "prepared",
                    "plugin_id": plugin_id,
                    "character_id": character_dir.name,
                    "version": target_version,
                    "was_active": was_active,
                    "target_content_sha256": manifest_result.content_sha256,
                    "active_before": active_before,
                    "previous_before": previous_before,
                    "active_after": active_after,
                    "previous_after": previous_after,
                }
            )

            # Remove the version first; only then publish the new active pointer.
            # If removal fails, the prepared journal remains and the active state
            # is untouched.
            shutil.rmtree(target)
            self._write_transaction(
                {
                    "operation_id": operation_id,
                    "kind": "uninstall",
                    "phase": "version_removed",
                    "plugin_id": plugin_id,
                    "character_id": character_dir.name,
                    "version": target_version,
                    "was_active": was_active,
                    "target_content_sha256": manifest_result.content_sha256,
                    "active_before": active_before,
                    "previous_before": previous_before,
                    "active_after": active_after,
                    "previous_after": previous_after,
                }
            )
            self._apply_pointer_state(character_dir.name, "active", active_after)
            self._apply_pointer_state(character_dir.name, "previous", previous_after)
            self._write_transaction(
                {
                    "operation_id": operation_id,
                    "kind": "uninstall",
                    "phase": "pointers_committed",
                    "plugin_id": plugin_id,
                    "character_id": character_dir.name,
                    "version": target_version,
                    "was_active": was_active,
                    "target_content_sha256": manifest_result.content_sha256,
                    "active_before": active_before,
                    "previous_before": previous_before,
                    "active_after": active_after,
                    "previous_after": previous_after,
                }
            )
            self._remove_transaction(operation_id)
            self._cleanup_empty_resource_dirs(character_dir.name)
            self._invalidate_registry()
            return
        raise ContentError(f"package not found: {plugin_id}@{version or '*'}")

    def discover(self) -> list[ContentPackage]:
        self.recover_pending_operations()
        if self.recovery_diagnostics:
            return []
        packages: list[ContentPackage] = []
        if not self.characters_root.is_dir():
            return packages
        for root in self.characters_root.glob("*/versions/*"):
            result = self.validate(root, allow_unsigned=True)
            if result.valid and result.manifest:
                packages.append(ContentPackage(result.manifest, root, "installed", result.content_sha256))
        return packages
