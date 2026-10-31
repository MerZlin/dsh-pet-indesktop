"""市场安装编排：下载 → 校验 → 备份 → 原子安装 → 锁定 → 回滚。"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..contracts.archive import ArchiveLimits, BudgetExceeded, effective_archive_limits
from ..contracts.market import PackageOrigin, writes_trusted_marker
from ..mods import install as mod_install
from ..mods.manifest import ModManifest, load_manifest
from ..mods.trust import TRUST_MARKER
from . import model, verify
from .download import ArtifactFetcher, download_artifact
from .errors import MarketError

LOCK_FILE = "market-lock.json"
LOCK_SCHEMA = 1
MAX_BACKUPS = 2

ApplyCallback = Callable[[], None]


@dataclass(frozen=True, slots=True)
class InstallOutcome:
    ok: bool
    message: str
    mod_id: str = ""
    version: str = ""
    previous_version: str = ""
    restart_required: bool = False
    applied: bool = False
    attempts: tuple[str, ...] = ()
    backup: str = ""
    #: 下载阶段沿用的已有字节（续传）与实际并行连接数。
    resumed_from: int = 0
    connections: int = 1


@dataclass(frozen=True, slots=True)
class RollbackResult(InstallOutcome):
    """回滚的结构化结果，区分磁盘恢复、清理和锁文件状态。"""

    state: str = "failed"
    restored_version: str = ""
    backup_path: str = ""
    residual_path: str = ""

def market_root(root: Path) -> Path:
    return Path(root) / "market"


def _raw_version(pack_dir: Path) -> str:
    try:
        raw = json.loads((pack_dir / "mod.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    version = raw.get("version") if isinstance(raw, dict) else None
    return version.strip() if isinstance(version, str) else ""


def installed_version(root: Path, mod_id: str) -> str:
    return _raw_version(Path(root) / "mods" / mod_id)


def scan_installed(root: Path) -> dict[str, str]:
    base = Path(root) / "mods"
    try:
        children = list(base.iterdir())
    except OSError:
        return {}
    versions: dict[str, str] = {}
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        version = _raw_version(child)
        if version:
            versions[child.name] = version
    return versions


def load_lock(root: Path) -> dict:
    path = market_root(root) / LOCK_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": LOCK_SCHEMA, "packages": {}}
    if not isinstance(raw, dict) or not isinstance(raw.get("packages"), dict):
        return {"schema": LOCK_SCHEMA, "packages": {}}
    return raw


def save_lock(root: Path, payload: dict) -> None:
    path = market_root(root) / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(body, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _relative(root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def backup_pack(root: Path, mod_id: str, version: str) -> Path | None:
    source = Path(root) / "mods" / mod_id
    if not version or not source.is_dir():
        return None
    target = market_root(root) / "backup" / mod_id / version
    if target.is_dir():
        return target
    try:
        _link_or_copy_tree(source, target)
    except OSError:
        return None
    _prune_backups(market_root(root) / "backup" / mod_id)
    return target


def _link_or_copy_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for directory, _dirs, files in os.walk(source):
        base = Path(directory)
        relative = base.relative_to(source)
        out_dir = target / relative
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            src = base / name
            dest = out_dir / name
            try:
                os.link(src, dest)
            except OSError:
                shutil.copy2(src, dest)


def _prune_backups(directory: Path) -> None:
    if not directory.is_dir():
        return
    versions = [item for item in directory.iterdir() if item.is_dir()]
    versions.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in versions[MAX_BACKUPS:]:
        shutil.rmtree(stale, ignore_errors=True)


def _restore(root: Path, mod_id: str, backup: Path | None) -> str:
    if backup is None:
        return "没有可恢复的旧版本"
    # 恢复旧版本：备份里如果原本带官方标记，按原样还原。
    ok, message = mod_install.install(backup, Path(root), force=True, keep_trust_marker=True)
    return f"恢复旧版本成功：{message}" if ok else f"恢复旧版本失败：{message}"


def _rollback_failed_commit(root: Path, mod_id: str, backup: Path | None) -> str:
    """锁写失败时把磁盘恢复到安装前：有备份就换回旧版，没有就删掉新装目录。"""
    if backup is not None:
        return f"已回滚：{_restore(root, mod_id, backup)}"
    ok, message = mod_install.remove_at(Path(root) / "mods" / mod_id, root=Path(root))
    return "已回滚到安装前状态" if ok else f"回滚失败：{message}"


def restart_required(package: model.MarketPackage) -> bool:
    return package.kind == "feature" and package.execution != "host-only"


def _stage_manifest(
    pack: Path,
    package: model.MarketPackage,
    version: model.MarketVersion,
    *,
    core_version: str = model.CORE_VERSION,
) -> ModManifest:
    manifest = verify.read_pack_manifest(pack)
    verify.verify_manifest(manifest, package, version, core_version=core_version)
    return manifest


def _strip_untrusted_marker(pack: Path, origin: PackageOrigin) -> bool:
    """来源没有授信资格时剔除包内自带的 `.trusted`（防标记走私）。"""
    if writes_trusted_marker(origin):
        return False
    marker = Path(pack) / TRUST_MARKER
    if not marker.exists():
        return False
    marker.unlink(missing_ok=True)
    return True


def install_package(
    root: Path,
    package: model.MarketPackage,
    version: model.MarketVersion,
    *,
    fetcher: ArtifactFetcher,
    allow_feature: bool = False,
    origin: PackageOrigin = PackageOrigin.MARKET,
    limits: ArchiveLimits | None = None,
    core_version: str = model.CORE_VERSION,
    platform: str | None = None,
    apply: ApplyCallback | None = None,
    progress=None,
    cancel=None,
    local_file_root: Path | None = None,
    index_origin: str = "",
    connections: int = 1,
    mirror_policy: str = "auto",
) -> InstallOutcome:
    root = Path(root)
    platform = platform or model.normalize_platform(sys.platform)
    effective_limits = effective_archive_limits(origin.value, package.kind, limits)
    allow_local_file = (
        str(index_origin).lower().startswith("file://")
        and local_file_root is not None
    )

    if package.blocked:
        return InstallOutcome(
            False, package.blocked_reason or "该包已被阻止", mod_id=package.id
        )
    if package.superseded_by:
        return InstallOutcome(
            False, f"该包已由 {package.superseded_by} 替代", mod_id=package.id
        )
    if package.kind == "feature" and not allow_feature:
        return InstallOutcome(
            False,
            "功能扩展需要先在「扩展管理」里允许功能扩展",
            mod_id=package.id,
        )

    previous = installed_version(root, package.id)
    state = model.package_state(
        package,
        installed_version=previous or None,
        core_version=core_version,
        platform=platform,
    )
    if state.state in (model.STATE_BLOCKED, model.STATE_SUPERSEDED):
        return InstallOutcome(False, state.reason, mod_id=package.id)
    if state.state == model.STATE_INSTALLED:
        message = (
            f"已安装 v{previous}，索引没有更新的版本"
            if previous == version.version
            else f"已安装 v{previous}，不降级到 {version.version}"
        )
        return InstallOutcome(
            False,
            message,
            mod_id=package.id,
            version=previous,
            previous_version=previous,
        )
    if state.state not in (model.STATE_AVAILABLE, model.STATE_UPDATE):
        return InstallOutcome(False, state.reason, mod_id=package.id)

    try:
        downloaded = download_artifact(
            fetcher,
            root,
            package,
            version,
            limits=effective_limits,
            progress=progress,
            cancel=cancel,
            local_file_root=local_file_root,
            allow_local_file=allow_local_file,
            connections=connections,
            mirror_policy=mirror_policy,
        )
    except MarketError as exc:
        return InstallOutcome(False, str(exc), mod_id=package.id, previous_version=previous)
    if cancel is not None and cancel():
        return InstallOutcome(
            False,
            "安装已取消，未进入备份和替换阶段",
            mod_id=package.id,
            previous_version=previous,
            attempts=downloaded.attempts,
            resumed_from=downloaded.resumed_from,
            connections=downloaded.connections,
        )

    staging_root = market_root(root) / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    marker_note = ""
    try:
        with mod_install.open_pack(
            downloaded.path, work_root=staging_root, limits=effective_limits
        ) as pack:
            if _strip_untrusted_marker(pack, origin):
                marker_note = "；已移除包内 .trusted（该来源无授信资格）"
            _stage_manifest(pack, package, version, core_version=core_version)
            if previous:
                backup = backup_pack(root, package.id, previous)
                if backup is None:
                    return InstallOutcome(
                        False,
                        f"无法备份旧版本 {previous}，已取消更新",
                        mod_id=package.id,
                        previous_version=previous,
                        attempts=downloaded.attempts,
                    )
            ok, message = mod_install.install(
                pack, root, force=True, keep_trust_marker=writes_trusted_marker(origin)
            )
        if not ok:
            return InstallOutcome(
                False,
                message,
                mod_id=package.id,
                previous_version=previous,
                attempts=downloaded.attempts,
            )
    except (MarketError, OSError, BudgetExceeded) as exc:
        return InstallOutcome(
            False,
            str(exc),
            mod_id=package.id,
            previous_version=previous,
            attempts=downloaded.attempts,
            backup=_relative(root, backup),
        )

    installed = installed_version(root, package.id)
    if installed != version.version:
        restored = _restore(root, package.id, backup)
        return InstallOutcome(
            False,
            f"安装后版本校验失败（{installed or '缺失'}），{restored}",
            mod_id=package.id,
            previous_version=previous,
            attempts=downloaded.attempts,
            backup=_relative(root, backup),
        )

    lock = load_lock(root)
    packages = dict(lock.get("packages") or {})
    packages[package.id] = {
        "version": version.version,
        "sha256": version.sha256,
        "source_url": downloaded.url,
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backup": _relative(root, backup),
    }
    lock["schema"] = LOCK_SCHEMA
    lock["packages"] = packages
    try:
        save_lock(root, lock)
    except OSError as exc:
        restored = _rollback_failed_commit(root, package.id, backup)
        return InstallOutcome(
            False,
            f"写入 market-lock.json 失败（{exc}），{restored}",
            mod_id=package.id,
            previous_version=previous,
            attempts=downloaded.attempts,
            backup=_relative(root, backup),
        )

    installed_manifest, _installed_problems = load_manifest(root / "mods" / package.id)
    needs_restart = bool(
        installed_manifest is not None
        and installed_manifest.kind == "feature"
        and installed_manifest.execution != "host-only"
    )
    applied = False
    reload_error = ""
    if apply is not None and not needs_restart:
        try:
            apply()
            applied = True
        except Exception as exc:  # noqa: BLE001 - 已写盘成功，只把重载失败说清楚
            reload_error = f"；重新加载失败：{type(exc).__name__}: {exc}"
            needs_restart = True

    message = f"已安装 {package.id} {version.version}"
    if previous and previous != version.version:
        message = f"已更新 {package.id} {previous} → {version.version}"
    message += marker_note
    message += reload_error
    if needs_restart:
        message += "，重启后生效"
    return InstallOutcome(
        True,
        message,
        mod_id=package.id,
        version=version.version,
        previous_version=previous,
        restart_required=needs_restart,
        applied=applied,
        attempts=downloaded.attempts,
        backup=_relative(root, backup),
        resumed_from=downloaded.resumed_from,
        connections=downloaded.connections,
    )


def _rollback_failure(
    *,
    message: str,
    mod_id: str,
    state: str,
    previous_version: str = "",
    backup: Path | None = None,
    restored_version: str = "",
    residual: Path | None = None,
    restart_required: bool = False,
    root: Path | None = None,
) -> RollbackResult:
    return RollbackResult(
        False,
        message,
        mod_id=mod_id,
        version=restored_version,
        previous_version=previous_version,
        restart_required=restart_required,
        backup=_relative(root, backup) if backup is not None and root is not None else (str(backup) if backup is not None else ""),
        state=state,
        restored_version=restored_version,
        backup_path=str(backup) if backup is not None else "",
        residual_path=str(residual) if residual is not None else "",
    )


def rollback(
    root: Path,
    mod_id: str,
    *,
    apply: ApplyCallback | None = None,
) -> RollbackResult:
    root = Path(root)
    lock = load_lock(root)
    entry = (lock.get("packages") or {}).get(mod_id)
    if not isinstance(entry, dict):
        return _rollback_failure(
            message=f"{mod_id} 没有市场安装记录",
            mod_id=mod_id,
            state="no-record",
            root=root,
        )
    backup_rel = str(entry.get("backup") or "")
    if not backup_rel:
        return _rollback_failure(
            message=f"{mod_id} 没有可回滚的备份",
            mod_id=mod_id,
            state="no-backup",
            root=root,
        )
    backup = root / backup_rel
    if not backup.is_dir():
        return _rollback_failure(
            message=f"备份目录不存在：{backup}",
            mod_id=mod_id,
            state="backup-missing",
            backup=backup,
            root=root,
        )

    current = installed_version(root, mod_id)
    current_path = root / "mods" / mod_id
    manifest, problems = load_manifest(backup)
    if manifest is None:
        return _rollback_failure(
            message=f"备份无效：{'；'.join(problems) or '缺少 mod.json'}",
            mod_id=mod_id,
            state="backup-invalid",
            previous_version=current,
            backup=backup,
            residual=current_path if current_path.exists() else None,
            root=root,
        )
    target_version = manifest.version
    if manifest.id != mod_id:
        return _rollback_failure(
            message=f"备份的 mod id 不匹配：期望 {mod_id}，实际 {manifest.id}",
            mod_id=mod_id,
            state="backup-id-mismatch",
            previous_version=current,
            backup=backup,
            residual=current_path if current_path.exists() else None,
            root=root,
        )

    staging_current: Path | None = None
    if current:
        staging_current = market_root(root) / "staging" / f".rollback-{mod_id}-{os.getpid()}"
        try:
            if staging_current.exists():
                shutil.rmtree(staging_current)
            _link_or_copy_tree(current_path, staging_current)
        except OSError as exc:
            return _rollback_failure(
                message=f"无法备份当前版本：{exc}",
                mod_id=mod_id,
                state="current-backup-failed",
                previous_version=current,
                backup=backup,
                residual=current_path,
                root=root,
            )

    ok, message = mod_install.install(backup, root, force=True, keep_trust_marker=True)
    if not ok:
        if staging_current is not None:
            shutil.rmtree(staging_current, ignore_errors=True)
        return _rollback_failure(
            message=f"回滚失败：{message}",
            mod_id=mod_id,
            state="restore-failed",
            previous_version=current,
            backup=backup,
            residual=current_path if current_path.exists() else None,
            root=root,
        )

    current_backup: Path | None = None
    if current and staging_current is not None:
        target = market_root(root) / "backup" / mod_id / current
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging_current, target)
            _prune_backups(target.parent)
            current_backup = target
        except OSError as exc:
            residual = staging_current if staging_current.exists() else None
            return _rollback_failure(
                message=f"已恢复 {mod_id} {target_version}，但旧版本备份清理失败：{exc}",
                mod_id=mod_id,
                state="restored-cleanup-failed",
                previous_version=current,
                backup=backup,
                restored_version=target_version,
                residual=residual,
                root=root,
            )

    restored = installed_version(root, mod_id)
    if restored != target_version:
        return _rollback_failure(
            message=f"回滚后版本校验失败（{restored or '缺失'}）；原安装目录：{current_path}",
            mod_id=mod_id,
            state="verification-failed",
            previous_version=current,
            backup=backup,
            restored_version=restored,
            residual=current_path if current_path.exists() else None,
            root=root,
        )

    packages = dict(lock.get("packages") or {})
    packages[mod_id] = {
        "version": restored,
        "sha256": "",
        "source_url": "rollback",
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backup": _relative(root, current_backup),
    }
    lock["schema"] = LOCK_SCHEMA
    lock["packages"] = packages
    try:
        save_lock(root, lock)
    except OSError as exc:
        return _rollback_failure(
            message=f"已恢复 {mod_id} {restored}，但写入 market-lock.json 失败：{exc}",
            mod_id=mod_id,
            state="restored-lock-save-failed",
            previous_version=current,
            backup=backup,
            restored_version=restored,
            residual=current_path if current_path.exists() else None,
            root=root,
        )

    manifest, _problems = load_manifest(current_path)
    needs_restart = bool(
        manifest is not None
        and manifest.kind == "feature"
        and manifest.execution != "host-only"
    )
    applied = False
    if apply is not None and not needs_restart:
        try:
            apply()
            applied = True
        except Exception as exc:  # noqa: BLE001 - 磁盘已恢复，重载失败单独留痕
            return RollbackResult(
                True,
                f"已回滚 {mod_id} {current or '未知'} → {restored}，但重新加载失败：{type(exc).__name__}: {exc}",
                mod_id=mod_id,
                version=restored,
                previous_version=current,
                restart_required=True,
                applied=False,
                backup=_relative(root, current_backup),
                state="restored-reload-failed",
                restored_version=restored,
                backup_path=str(backup),
                residual_path="",
            )

    return RollbackResult(
        True,
        f"已回滚 {mod_id} {current or '未知'} → {restored}",
        mod_id=mod_id,
        version=restored,
        previous_version=current,
        restart_required=needs_restart,
        applied=applied,
        backup=_relative(root, current_backup),
        state="restored",
        restored_version=restored,
        backup_path=str(backup),
        residual_path="",
    )
