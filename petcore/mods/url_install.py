"""从直链 zip 安装扩展包 / aboutme 角色包（设置页「从链接安装」的核心）。

支持的包形态（与 ``mods.install.open_pack`` 的单层根目录规则一致）：

1. 扩展包：根目录有 ``mod.json``（``kind=feature`` 或 ``content``）；
2. aboutme 单角色包：根目录有 ``manifest.json`` + ``actions.json``。

安全边界沿用本地导入：zip-slip、Windows 设备名、大小写重复、symlink 与解包预算
都在 ``mods.install.open_pack`` 里校验；本模块只负责下载、大小上限与路由。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ..contracts.archive import ArchiveLimits, MARKET_ARCHIVE_LIMITS
from ..net import errors as net_errors
from ..net.http import fetch_url
from .. import paths
from . import install as mod_install
from .manifest import load_manifest

#: 直链下载上限：aboutme 的 13.9 MiB 素材清单与常见功能包都远低于此值。
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
#: 解包预算：比市场 feature 包宽松（角色包含大量 PNG/WebP），但仍有硬上限。
URL_ARCHIVE_LIMITS = ArchiveLimits(MAX_DOWNLOAD_BYTES, 512 * 1024 * 1024, 20_000)
_URL_RE = re.compile(r"^https?://[^/\s]+", re.IGNORECASE)


class UrlInstallError(RuntimeError):
    """下载/预览/安装失败；``reason`` 是稳定分类，界面按它出文案。"""

    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = str(reason or "unavailable")
        super().__init__(message or self.reason)


@dataclass(frozen=True, slots=True)
class PackagePreview:
    kind: str  # "mod" | "sprite"
    package_id: str
    name: str
    version: str
    mod_kind: str = ""
    source: str = ""

    @property
    def label(self) -> str:
        if self.kind == "sprite":
            return f"aboutme 角色包 · {self.name}"
        kind = "功能扩展" if self.mod_kind == "feature" else "内容扩展"
        return f"{kind} · {self.name}"


def normalize_url(value: str) -> str:
    url = str(value or "").strip()
    if not _URL_RE.match(url):
        raise UrlInstallError("invalid_url", "链接必须以 http:// 或 https:// 开头")
    return url


def fetch_zip(
    url: str,
    *,
    root: Path,
    timeout: float = 30.0,
    opener=None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Path:
    """下载直链 zip 到缓存目录，返回本地路径；调用方负责删除。"""
    url = normalize_url(url)
    try:
        _status, _headers, body = fetch_url(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            opener=opener,
        )
    except net_errors.NetworkError as exc:
        raise UrlInstallError(
            str(getattr(exc, "reason", "") or "network"),
            f"下载失败：{type(exc).__name__}",
        ) from None
    data = bytes(body)
    if not data:
        raise UrlInstallError("protocol", "下载内容为空")
    if not data.startswith(b"PK"):
        raise UrlInstallError("not_zip", "链接内容不是 zip 包")
    cache = paths.cache_dir(Path(root), "mods", "url-install")
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"url-{int(time.time() * 1000)}-{abs(hash(url)) & 0xFFFFFFFF}.zip"
    target.write_bytes(data)
    return target


def _package_root(dest: Path) -> tuple[Path, str]:
    """在解包目录里找唯一包根：``mod.json`` 优先，其次 aboutme 的 manifest/actions。"""
    for marker in ("mod.json", "manifest.json"):
        if (dest / marker).is_file():
            return dest, marker
    try:
        candidates = [child for child in dest.iterdir() if child.is_dir()]
    except OSError as exc:
        raise UrlInstallError("protocol", f"无法读取解包目录：{exc}") from None
    for marker, extra in (("mod.json", ()), ("manifest.json", ("actions.json",))):
        found = [
            child
            for child in candidates
            if (child / marker).is_file()
            and all((child / name).is_file() for name in extra)
        ]
        if len(found) == 1:
            return found[0], marker
        if len(found) > 1:
            raise UrlInstallError(
                "protocol", "压缩包里有多套包目录，无法确定要安装哪一个"
            )
    raise UrlInstallError(
        "unsupported", "包内没有 mod.json，也不是 aboutme 单角色目录"
    )


def preview_zip(path: Path, *, source: str = "") -> PackagePreview:
    """识别 zip 是扩展包还是 aboutme 角色包；无法识别时抛 :class:`UrlInstallError`。"""
    path = Path(path)
    try:
        with mod_install.open_archive(
            path, work_root=path.parent, limits=URL_ARCHIVE_LIMITS
        ) as dest:
            pack, marker = _package_root(dest)
            if marker == "manifest.json":
                manifest_path = Path(pack) / "manifest.json"
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise UrlInstallError(
                        "protocol", f"角色包 manifest.json 不可读：{type(exc).__name__}"
                    ) from None
                from ..content.sprite_pack import character_id_for

                raw_id = str(data.get("id") or Path(pack).name)
                character_id = character_id_for(raw_id)
                if not character_id:
                    raise UrlInstallError("protocol", f"角色 id 不合法：{raw_id!r}")
                return PackagePreview(
                    kind="sprite",
                    package_id=f"character.{character_id}",
                    name=str(data.get("name") or character_id),
                    version=str(data.get("version") or ""),
                    mod_kind="content",
                    source=str(source or ""),
                )
            manifest, _problems = load_manifest(pack)
            if manifest is not None:
                return PackagePreview(
                    kind="mod",
                    package_id=str(manifest.id),
                    name=str(manifest.name_zh or manifest.name or manifest.id),
                    version=str(manifest.version or ""),
                    mod_kind=str(manifest.kind or ""),
                    source=str(source or ""),
                )
    except UrlInstallError:
        raise
    except Exception as exc:  # noqa: BLE001 - 解包/清单错误统一成可读原因
        raise UrlInstallError("protocol", f"包结构无法识别：{exc}") from None
    raise UrlInstallError(
        "unsupported", "包内没有 mod.json，也不是 aboutme 单角色目录"
    )


def install_zip(
    path: Path,
    *,
    root: Path,
    preview: PackagePreview,
    allow_feature: bool = False,
) -> tuple[bool, str]:
    """安装已预览过的 zip；``preview`` 必须来自同一个文件。"""
    root = Path(root)
    if preview.kind == "mod":
        if preview.mod_kind == "feature" and not allow_feature:
            return (
                False,
                "这是功能扩展：请先在「扩展管理 → 允许功能扩展」勾选并保存，"
                "再重新点「下载并安装」。",
            )
        return mod_install.install(
            Path(path),
            root,
            force=True,
            limits=(
                MARKET_ARCHIVE_LIMITS
                if preview.mod_kind == "feature"
                else URL_ARCHIVE_LIMITS
            ),
        )
    if preview.kind == "sprite":
        from ..content.sprite_pack import build_sprite_pack

        work = paths.cache_dir(root, "mods", "url-install", "work")
        character_id = preview.package_id.removeprefix("character.")
        mods_root = root / "mods"
        mods_root.mkdir(parents=True, exist_ok=True)
        target = mods_root / preview.package_id
        staging = mods_root / f".staging-url-{character_id}-{os.getpid()}"
        backup: Path | None = None
        try:
            shutil.rmtree(staging, ignore_errors=True)
            with mod_install.open_archive(
                Path(path), work_root=work, limits=URL_ARCHIVE_LIMITS
            ) as dest:
                pack, _marker = _package_root(dest)
                ok, message = build_sprite_pack(
                    pack, staging, character_id=character_id
                )
            if not ok:
                shutil.rmtree(staging, ignore_errors=True)
                return False, message
            if target.exists():
                backup = mods_root / f".trash-url-{character_id}-{os.getpid()}"
                shutil.rmtree(backup, ignore_errors=True)
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except OSError:
                if backup is not None:
                    os.replace(backup, target)
                raise
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)
            return True, message
        except Exception as exc:  # noqa: BLE001 - 回滚后按安装失败返回
            shutil.rmtree(staging, ignore_errors=True)
            if backup is not None and backup.exists() and not target.exists():
                try:
                    os.replace(backup, target)
                except OSError:
                    pass
            return False, f"角色包安装失败：{exc}"
    return False, "未知的包类型"


def discard(path: Path | None) -> None:
    """删除下载缓存（失败静默：临时文件不该影响安装结果）。"""
    if path is None:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "URL_ARCHIVE_LIMITS",
    "PackagePreview",
    "UrlInstallError",
    "discard",
    "fetch_zip",
    "install_zip",
    "normalize_url",
    "preview_zip",
]
