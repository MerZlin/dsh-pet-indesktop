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
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..contracts.archive import ArchiveLimits, MARKET_ARCHIVE_LIMITS
from ..net import aboutme_repo
from ..net import errors as net_errors
from ..net.github_mirrors import expand_github_urls
from ..net.http import fetch_url
from .. import paths
from . import install as mod_install
from .manifest import load_manifest

#: 直链下载上限：aboutme 的 13.9 MiB 素材清单与常见功能包都远低于此值。
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
#: 解包预算：比市场 feature 包宽松（角色包含大量 PNG/WebP），但仍有硬上限。
URL_ARCHIVE_LIMITS = ArchiveLimits(MAX_DOWNLOAD_BYTES, 512 * 1024 * 1024, 20_000)
#: GitHub 角色目录（无 zip）的逐文件预算：整包可能上百个精灵表。
TREE_MAX_FILES = 400
TREE_MAX_BYTES = 512 * 1024 * 1024
TREE_FILE_BYTES = 16 * 1024 * 1024
TREE_INDEX_BYTES = 4 * 1024 * 1024
#: 目录下载落在缓存里的临时目录；``discard`` 只删自己登记过的路径。
_TEMP_DIRS: set[Path] = set()
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


def is_tree_url(url: str) -> bool:
    """是不是 GitHub 角色目录链接（``…/tree/<ref>/<path>``）。"""
    return aboutme_repo.is_tree_url(url)


def _fetch_bytes(
    url: str, *, timeout: float, max_bytes: int, opener
) -> bytes:
    """取一个 GitHub raw 文件；非 200/截断/传输失败都转成可读分类。"""
    try:
        status, _headers, body = fetch_url(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            opener=opener,
            prefer_direct=False,
        )
    except net_errors.NetworkError as exc:
        raise UrlInstallError(
            str(getattr(exc, "reason", "") or "network"),
            f"下载失败：{type(exc).__name__}",
        ) from None
    data = bytes(body)
    name = url.rsplit("/", 1)[-1] or "文件"
    if int(status) != 200:
        reason = "unsupported" if int(status) in (403, 404) else "network"
        raise UrlInstallError(reason, f"{name} 下载失败（HTTP {int(status)}）")
    if getattr(body, "truncated", False):
        raise UrlInstallError("protocol", f"{name} 下载被截断")
    if not data:
        raise UrlInstallError("protocol", f"{name} 是空文件")
    return data


def _sprite_preview(pack: Path, *, source: str = "") -> PackagePreview:
    """单角色目录（``manifest.json``）→ 角色包预览；id 非法时抛错。"""
    from ..content.sprite_pack import character_id_for

    try:
        data = json.loads((Path(pack) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UrlInstallError(
            "protocol", f"角色包 manifest.json 不可读：{type(exc).__name__}"
        ) from None
    if not isinstance(data, dict):
        raise UrlInstallError("protocol", "角色包 manifest.json 不是对象")
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


def _sweep_stale(cache: Path, *, max_age_s: float = 24 * 3600) -> None:
    """清掉上次进程留下的半截目录（只动 ``tree-`` 前缀，失败不管）。"""
    now = time.time()
    try:
        entries = list(cache.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith("tree-"):
            continue
        try:
            if now - entry.stat().st_mtime < max_age_s:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)


def _is_valid_json_dict(data: bytes) -> bool:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(parsed, dict)


def _is_valid_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _fetch_tree_resource(
    raw_url: str,
    *,
    mirror_policy: str,
    custom_mirror_prefix: str,
    timeout: float,
    max_bytes: int,
    opener,
    validator=None,
) -> bytes:
    candidates = expand_github_urls(
        (raw_url,), mirror_policy, custom_prefix=custom_mirror_prefix
    )
    last_index = len(candidates) - 1
    for index, candidate in enumerate(candidates):
        try:
            data = _fetch_bytes(
                candidate, timeout=timeout, max_bytes=max_bytes, opener=opener
            )
        except UrlInstallError:
            if index < last_index:
                continue
            raise
        if index < last_index and validator is not None and not validator(data):
            continue
        return data
    raise UrlInstallError("network", "下载失败")


def fetch_tree(
    url: str,
    *,
    root: Path,
    timeout: float = 30.0,
    opener=None,
    progress=None,
    all_actions: bool = False,
    mirror_policy: str = "auto",
    custom_mirror_prefix: str = "",
) -> tuple[Path, PackagePreview]:
    """从 GitHub 角色目录逐文件取回 WebP 精灵表，返回 ``(目录, 预览)``。

    上游只提供目录、没有单包 zip；这里按 ``manifest.json`` / ``actions.json`` 里
    列出的 ``sources.webp`` 逐个下载（``raw.githubusercontent.com``），不经
    GitHub API、不消耗 API 配额。默认只取常用动作（``essential_actions``，
    ≤8 张）；``all_actions=True`` 才整包下载。调用方负责 :func:`discard` 临时目录。
    """
    parsed = aboutme_repo.parse_tree_url(url)
    if parsed is None:
        raise UrlInstallError(
            "invalid_url",
            "只支持 GitHub 角色目录链接（形如 …/tree/main/pets/gpt）",
        )
    owner, repo, ref, folder = parsed
    manifest_bytes = _fetch_tree_resource(
        aboutme_repo.raw_file_url(owner, repo, ref, f"{folder}/manifest.json"),
        mirror_policy=mirror_policy,
        custom_mirror_prefix=custom_mirror_prefix,
        timeout=timeout,
        max_bytes=TREE_INDEX_BYTES,
        opener=opener,
        validator=_is_valid_json_dict,
    )
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UrlInstallError("protocol", "manifest.json 不是有效 JSON") from None
    if not isinstance(manifest, dict):
        raise UrlInstallError("protocol", "manifest.json 不是对象")

    actions_doc: dict = {}
    try:
        actions_bytes = _fetch_tree_resource(
            aboutme_repo.raw_file_url(owner, repo, ref, f"{folder}/actions.json"),
            mirror_policy=mirror_policy,
            custom_mirror_prefix=custom_mirror_prefix,
            timeout=timeout,
            max_bytes=TREE_INDEX_BYTES,
            opener=opener,
            validator=_is_valid_json_dict,
        )
        try:
            parsed_actions = json.loads(actions_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise UrlInstallError("protocol", "actions.json 不是有效 JSON") from None
        if isinstance(parsed_actions, dict):
            actions_doc = parsed_actions
        else:
            actions_bytes = b""
    except UrlInstallError as exc:
        # 老包只有 manifest.json：404 时退回 manifest.animations，其余错误照抛。
        if exc.reason != "unsupported":
            raise
        actions_bytes = b""

    actions = actions_doc.get("animations")
    if not isinstance(actions, list) or not actions:
        actions = manifest.get("animations")
    selected = (
        actions
        if all_actions
        else aboutme_repo.essential_actions(manifest, actions or [])
    )
    relative: list[str] = []
    seen: set[str] = set()
    for action in selected if isinstance(selected, list) else []:
        if not isinstance(action, dict):
            continue
        sources = action.get("sources")
        if not isinstance(sources, dict):
            continue
        safe = aboutme_repo.safe_relative(str(sources.get("webp") or ""))
        if safe and safe not in seen:
            seen.add(safe)
            relative.append(safe)
    if not relative:
        raise UrlInstallError("protocol", "目录里没有可用的 WebP 精灵表")
    if len(relative) > TREE_MAX_FILES:
        raise UrlInstallError("protocol", f"精灵表超过 {TREE_MAX_FILES} 个，拒绝安装")

    cache = paths.cache_dir(Path(root), "mods", "url-install", "tree")
    cache.mkdir(parents=True, exist_ok=True)
    _sweep_stale(cache)
    temp = Path(tempfile.mkdtemp(prefix="tree-", dir=str(cache)))
    _TEMP_DIRS.add(temp)
    try:
        total = 0
        for index, rel in enumerate(relative, 1):
            data = _fetch_tree_resource(
                aboutme_repo.raw_file_url(owner, repo, ref, f"{folder}/{rel}"),
                mirror_policy=mirror_policy,
                custom_mirror_prefix=custom_mirror_prefix,
                timeout=timeout,
                max_bytes=TREE_FILE_BYTES,
                opener=opener,
                validator=_is_valid_webp,
            )
            total += len(data)
            if total > TREE_MAX_BYTES:
                raise UrlInstallError("protocol", "角色包超过 512MB 上限")
            target = (temp / rel).resolve()
            try:
                target.relative_to(temp.resolve())
            except ValueError:
                raise UrlInstallError("protocol", f"路径越界：{rel}") from None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if callable(progress):
                try:
                    progress(index, len(relative), rel)
                except Exception:  # noqa: BLE001 - 进度回调失败不影响下载
                    pass
        (temp / "manifest.json").write_bytes(manifest_bytes)
        if actions_bytes:
            (temp / "actions.json").write_bytes(actions_bytes)
        return temp, _sprite_preview(temp, source=url)
    except BaseException:
        _TEMP_DIRS.discard(temp)
        shutil.rmtree(temp, ignore_errors=True)
        raise


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
                return _sprite_preview(pack, source=source)
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
        work = paths.cache_dir(root, "mods", "url-install", "work")
        with mod_install.open_archive(
            Path(path), work_root=work, limits=URL_ARCHIVE_LIMITS
        ) as dest:
            pack, _marker = _package_root(dest)
            return _install_sprite_dir(pack, root, preview)
    return False, "未知的包类型"


def install_tree(
    path: Path,
    *,
    root: Path,
    preview: PackagePreview,
) -> tuple[bool, str]:
    """安装 :func:`fetch_tree` 取回的目录（同一份 staging/回滚逻辑）。"""
    path = Path(path)
    if preview.kind != "sprite":
        return False, "未知的包类型"
    if not path.is_dir():
        return False, "角色包目录不存在（下载缓存可能已被清理）"
    return _install_sprite_dir(path, Path(root), preview)


def _install_sprite_dir(
    pack: Path, root: Path, preview: PackagePreview
) -> tuple[bool, str]:
    """把单角色目录打成 ``mods/character.<id>``；失败回滚到旧包。"""
    from ..content.sprite_pack import build_sprite_pack

    character_id = preview.package_id.removeprefix("character.")
    mods_root = Path(root) / "mods"
    mods_root.mkdir(parents=True, exist_ok=True)
    target = mods_root / preview.package_id
    staging = mods_root / f".staging-url-{character_id}-{os.getpid()}"
    backup: Path | None = None
    try:
        shutil.rmtree(staging, ignore_errors=True)
        ok, message = build_sprite_pack(pack, staging, character_id=character_id)
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


def discard(path: Path | None) -> None:
    """删除下载缓存（失败静默：临时文件不该影响安装结果）。

    目录只删 :func:`fetch_tree` 自己登记的临时目录：调用方传进来的任意路径
    都不能被这里递归删除。
    """
    if path is None:
        return
    target = Path(path)
    try:
        if target.is_dir():
            if target in _TEMP_DIRS:
                _TEMP_DIRS.discard(target)
                shutil.rmtree(target, ignore_errors=True)
            return
        target.unlink(missing_ok=True)
    except OSError:
        pass


__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "TREE_MAX_BYTES",
    "TREE_MAX_FILES",
    "URL_ARCHIVE_LIMITS",
    "PackagePreview",
    "UrlInstallError",
    "discard",
    "fetch_tree",
    "fetch_zip",
    "install_tree",
    "install_zip",
    "is_tree_url",
    "normalize_url",
    "preview_zip",
]
