from __future__ import annotations

import logging
import os
import re
import sys
import threading
from pathlib import Path

from .storage import roots as storage_roots

APP_NAME = "dsh-pet-core"
LEGACY_APP_NAME = "dsh-pet-standalone"

logger = logging.getLogger("petcore.paths")

#: 缓存根（进程级，启动时由 ``cli`` / ``ui.app`` 设一次）。为空表示跟随数据根，
#: 这条缺省路径必须与拆分前的行为逐字节一致。
_cache_root: Path | None = None
#: 设置缓存根时的数据根；两者绑定，避免「按 A 根设置、却被 B 根取走」。
_cache_data_root: Path | None = None
_cache_lock = threading.Lock()
#: 本次启动实际使用的引导指针路径（设置页搬迁时要知道写到哪）。
_pointer_path: Path | None = None


def _norm(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return Path(os.path.normcase(os.path.abspath(str(path))))
    except OSError:
        return Path(path)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    """安装目录：冻结包 = exe 所在目录；源码树 = 仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return repo_root()


def install_mods_dir() -> Path | None:
    """安装器投放的扩展目录（仅在冻结包里存在）。源码树不参与，避免把
    `repo/mods` 误当成「安装器授信」目录。"""
    if not is_frozen():
        return None
    return install_root() / "mods"


def user_mods_dirs(root: Path) -> tuple[Path, ...]:
    """允许加载 feature 的两个根：用户数据目录 + 安装目录（冻结包）。"""
    candidates = [Path(root) / "mods"]
    install = install_mods_dir()
    if install is not None:
        candidates.append(install)
    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(candidate)
    return tuple(ordered)


def base_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home())
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / ".config"


_INSTANCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def normalize_instance(instance: str | None) -> str | None:
    if instance is None or not str(instance).strip():
        return None
    value = str(instance).strip()
    if not _INSTANCE_RE.match(value):
        raise ValueError("instance name must match [A-Za-z0-9_-]{1,32}")
    return value


def location_path(portable: bool) -> Path:
    """引导指针的位置。**必须在默认根之外**，否则根本一搬指针就跟着没了。"""
    if portable:
        return install_root() / storage_roots.LOCATION_FILENAME
    return base_dir() / APP_NAME / storage_roots.LOCATION_FILENAME


def default_data_root(portable: bool = False, instance: str | None = None) -> Path:
    """不含引导指针的默认根（拆之前 ``data_root`` 的完整语义）。"""
    name = normalize_instance(instance)
    if portable:
        # 冻结包是 onedir：`repo_root()` 指向 `_internal`，升级/卸载替换该目录即丢数据。
        # 绿色版的数据应该在 exe 旁边。
        return install_root() / (f"data-{name}" if name else "data")
    return base_dir() / (f"{APP_NAME}-{name}" if name else APP_NAME)


def resolve_roots(
    *,
    portable: bool = False,
    instance: str | None = None,
    explicit: Path | None = None,
) -> storage_roots.Roots:
    """解析数据根与缓存根。

    优先级：显式 ``--root`` > ``location.json`` 引导指针 > 默认根。
    ``--instance`` 一律走默认根：多个实例共享同一份指针会互相抢数据。
    """
    default = default_data_root(portable, instance)
    pointer = location_path(portable)
    global _pointer_path
    _pointer_path = pointer
    if instance:
        return storage_roots.Roots(default, default, pointer, "default")
    return storage_roots.resolve_roots(default, pointer_path=pointer, explicit=explicit)


def pointer_path() -> Path:
    """本次启动用的引导指针；还没解析过时给出常规安装的默认位置。"""
    return _pointer_path or location_path(portable=False)


def data_root(portable: bool = False, instance: str | None = None) -> Path:
    """数据根（有引导指针时以指针为准）。"""
    return resolve_roots(portable=portable, instance=instance).data_root


def set_cache_root(path: Path | None, *, data_root: Path | None = None) -> None:
    """设置进程级缓存根；``None`` 表示跟随数据根。

    ``data_root`` 给了就绑定：只有该数据根会用到这个缓存根，别的根（例如
    ``--check`` 临时指向的目录）仍回落到自己的 ``<root>/cache``。
    这让「按 A 根设置、被 B 根取走」不可能发生。
    """
    global _cache_root, _cache_data_root
    with _cache_lock:
        _cache_root = Path(path) if path is not None else None
        _cache_data_root = _norm(data_root) if path is not None else None


def cache_root() -> Path | None:
    with _cache_lock:
        return _cache_root


def cache_dir(root: Path, *parts: str) -> Path:
    """缓存目录：设了缓存根就落在缓存根，否则就是 ``<root>/<parts>``。

    未拆开（默认）时返回值与拆分前逐字节一致，这是拆分的兼容底线。
    """
    with _cache_lock:
        base = _cache_root
        bound = _cache_data_root
    if base is not None and bound is not None and _norm(Path(root)) != bound:
        base = None
    if base is None:
        return Path(root).joinpath(*parts) if parts else Path(root)
    return base.joinpath(*parts) if parts else base




def config_path(root: Path) -> Path:
    return root / "config.json"


def pet_config_path(root: Path, slot: int) -> Path:
    return config_path(root) if int(slot) <= 0 else root / f"config-pet-{int(slot)}.json"


def builtin_features_dir() -> Path:
    return repo_root() / "petcore" / "builtin" / "features"


def mods_state_path(root: Path) -> Path:
    return root / "mods-state.json"


def logs_dir(root: Path) -> Path:
    return root / "logs"


def feature_data_dir(root: Path, owner: str) -> Path:
    return root / "feature-data" / owner


def mods_dirs(root: Path, extra: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    install = install_mods_dir()
    candidates = [
        root / "mods",
        *((install,) if install is not None else ()),
        repo_root() / "mods",
        *extra,
    ]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(candidate)
    return tuple(ordered)


def legacy_characters_root() -> Path:
    override = os.environ.get("PETCORE_LEGACY_ASSETS")
    if override:
        return Path(override)
    return repo_root() / "assets" / "characters"


def legacy_asset_root() -> Path:
    return repo_root() / "assets"


def legacy_config_path() -> Path:
    return base_dir() / LEGACY_APP_NAME / "config.json"


def legacy_data_roots() -> tuple[Path, ...]:
    """Return old standalone data roots, including published variants."""
    base = base_dir()
    candidates = [base / LEGACY_APP_NAME]
    try:
        candidates.extend(sorted(base.glob(f"{LEGACY_APP_NAME}-*")))
    except OSError:
        pass
    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        result.append(resolved)
    return tuple(result)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
