"""数据根 / 缓存根的引导指针、搬迁与清理（零 Qt，只依赖标准库）。

**指针必须放在默认根之外**：常规安装是 ``%APPDATA%/dsh-pet-core/location.json``，
绿色版是 ``<exe 目录>/location.json``。指针要是跟着数据根一起搬走，
下次启动就只能看到默认根（一个空目录），用户的会话与配置等于凭空消失。

搬迁语义是「复制 → 校验 → 写指针 → 重启 → 下次启动删旧目录」：
复制期间应用仍写在旧根上，所以调用方写完指针必须立刻要求重启；
旧目录的删除放到下一次启动（``cleanup_moved_source``），此时已经没有文件句柄，
且能顺手保住放在旧根里的指针文件。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

logger = logging.getLogger("petcore.storage.roots")

LOCATION_SCHEMA = 1
LOCATION_FILENAME = "location.json"
JOURNAL_FILENAME = "root-move.json"
LOCK_FILENAME = "petcore.lock"

COPY_CHUNK_BYTES = 1024 * 1024

#: 搬迁时永远不复制的东西：进程锁、本模块自己的日志文件、临时写盘残留。
DEFAULT_SKIP_NAMES: frozenset[str] = frozenset(
    {LOCK_FILENAME, JOURNAL_FILENAME, "config.json.tmp", "api.json.tmp"}
)

#: 缓存根接管的三处大件（相对数据根的路径）。
CACHE_PARTS: tuple[str, ...] = ("cache", "market/downloads", "updates", "packs")


class RootsError(Exception):
    """指针或搬迁的显式错误（消息可直接给用户看）。"""


class MoveCancelled(Exception):
    """用户在复制过程中取消；已复制的内容保留，下次继续。"""


# --------------------------------------------------------------------- 指针
@dataclass(frozen=True, slots=True)
class Pointer:
    """``location.json`` 的内容；两项都可缺省。"""

    data_root: Path | None = None
    cache_root: Path | None = None

    def is_empty(self) -> bool:
        return self.data_root is None and self.cache_root is None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"schema": LOCATION_SCHEMA}
        if self.data_root is not None:
            payload["data_root"] = str(self.data_root)
        if self.cache_root is not None:
            payload["cache_root"] = str(self.cache_root)
        return payload


@dataclass(frozen=True, slots=True)
class Roots:
    """启动时解析出来的两个根。"""

    data_root: Path
    cache_root: Path
    pointer_path: Path
    #: ``cli``（显式 --root）/ ``pointer``（引导指针）/ ``default``（默认根）
    source: str
    note: str = ""

    def is_split(self) -> bool:
        return self.cache_root != self.data_root


def _absolute(value: object, *, base_dir: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not path.is_absolute():
        # 相对路径按指针文件所在目录解析：用户手写配置时不会被工作目录坑到。
        path = base_dir / path
    return path


def parse_pointer(payload: object, *, base_dir: Path) -> Pointer:
    """严格解析指针；结构不对抛 :class:`RootsError`（调用方负责回落默认根）。"""
    if not isinstance(payload, dict):
        raise RootsError("指针文件不是 JSON 对象")
    schema = payload.get("schema")
    if schema is not None:
        try:
            version = int(schema)
        except (TypeError, ValueError):
            raise RootsError(f"指针版本不是数字：{schema!r}") from None
        if version != LOCATION_SCHEMA:
            raise RootsError(f"指针版本不支持：{schema!r}")
    data_root = _absolute(payload.get("data_root"), base_dir=base_dir)
    cache_root = _absolute(payload.get("cache_root"), base_dir=base_dir)
    for label, path in (("data_root", data_root), ("cache_root", cache_root)):
        if path is not None and path.parent == path:
            raise RootsError(f"{label} 不能是盘符根：{path}")
    return Pointer(data_root=data_root, cache_root=cache_root)


def read_pointer(path: Path) -> Pointer | None:
    """读指针；文件不存在返回 ``None``，坏文件抛 :class:`RootsError`。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RootsError(f"读取位置文件失败：{type(exc).__name__}") from exc
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise RootsError(f"位置文件不是合法 JSON：{type(exc).__name__}") from exc
    return parse_pointer(payload, base_dir=path.parent)


def write_pointer(path: Path, pointer: Pointer) -> None:
    """原子写指针（临时文件 + ``os.replace``）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(pointer.to_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def resolve_roots(
    default_root: Path,
    *,
    pointer_path: Path,
    explicit: Path | None = None,
) -> Roots:
    """解析启动用的两个根。

    优先级：显式 ``--root`` > 引导指针 > 默认根。指针坏掉时回落默认根
    并留下 ``note``，**不删不覆盖**用户的文件。
    """
    default_root = Path(default_root)
    pointer_path = Path(pointer_path)
    if explicit is not None:
        root = Path(explicit)
        return Roots(root, root, pointer_path, "cli")
    try:
        pointer = read_pointer(pointer_path)
    except RootsError as exc:
        logger.warning("位置文件不可用（回落默认目录）：%s", exc)
        return Roots(default_root, default_root, pointer_path, "default", str(exc))
    if pointer is None or pointer.is_empty():
        return Roots(default_root, default_root, pointer_path, "default")
    data_root = pointer.data_root or default_root
    cache_root = pointer.cache_root or data_root
    return Roots(data_root, cache_root, pointer_path, "pointer")


# --------------------------------------------------------------------- 校验
def validate_target(source: Path, target: Path) -> str | None:
    """检查能否把 ``source`` 搬到 ``target``；返回中文原因，``None`` 表示可以。"""
    source = Path(source)
    target = Path(target)
    try:
        if not source.is_dir():
            return "原目录不存在"
        if target.parent == target:
            return "不能直接用盘符根目录"
        if target == source:
            return "新旧目录相同"
        try:
            if target.resolve() == source.resolve():
                return "新旧目录相同"
            if target.resolve().is_relative_to(source.resolve()):
                return "新目录不能放在旧目录里面"
            if source.resolve().is_relative_to(target.resolve()):
                return "新目录不能是旧目录的上级"
        except OSError:
            return "目录路径无法解析"
        if target.exists():
            if not target.is_dir():
                return "目标位置已存在同名文件"
            if any(target.iterdir()):
                return "目标目录不是空目录"
        parent = target.parent
        if not parent.is_dir():
            return "目标位置的上级目录不存在"
        probe = parent / f".petcore-write-{os.getpid()}"
        try:
            probe.write_bytes(b"")
        except OSError:
            return "目标位置不可写"
        finally:
            try:
                probe.unlink()
            except OSError:
                pass
    except OSError as exc:
        return f"检查目标位置失败：{type(exc).__name__}"
    return None


# --------------------------------------------------------------------- 计划
@dataclass(frozen=True, slots=True)
class MoveItem:
    relative: Path
    size: int


@dataclass(frozen=True, slots=True)
class MovePlan:
    source: Path
    target: Path
    items: tuple[MoveItem, ...]
    total_bytes: int
    files: int = 0
    skipped: tuple[str, ...] = field(default=())

    def final_path(self, item: MoveItem) -> Path:
        return self.target / item.relative


def _walk(base: Path, *, prefix: Path, skip: frozenset[str]) -> Iterable[MoveItem]:
    for current, dirnames, filenames in os.walk(base):
        here = Path(current)
        dirnames[:] = sorted(name for name in dirnames if name not in skip)
        for name in sorted(filenames):
            if name in skip:
                continue
            path = here / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            relative = prefix / path.relative_to(base) if prefix.parts else path.relative_to(base)
            yield MoveItem(relative, int(size))


def plan_move(
    source: Path,
    *,
    include: Sequence[str] | None = None,
    skip_names: frozenset[str] = DEFAULT_SKIP_NAMES,
) -> MovePlan:
    """列出要复制的文件。

    ``include=None`` 表示整棵树（数据根搬迁）；给相对路径元组则只搬这些子树
    （缓存根搬迁用 :data:`CACHE_PARTS`）。
    """
    source = Path(source)
    items: list[MoveItem] = []
    skipped: list[str] = []
    if include is None:
        items.extend(_walk(source, prefix=Path(), skip=skip_names))
    else:
        for name in include:
            base = source / name
            if base.is_dir():
                items.extend(_walk(base, prefix=Path(name), skip=skip_names))
            elif base.is_file():
                try:
                    size = base.stat().st_size
                except OSError:
                    skipped.append(name)
                    continue
                items.append(MoveItem(Path(name), int(size)))
            else:
                skipped.append(name)
    total = sum(item.size for item in items)
    return MovePlan(
        source=source,
        target=Path(source),
        items=tuple(items),
        total_bytes=total,
        files=len(items),
        skipped=tuple(skipped),
    )


def with_target(plan: MovePlan, target: Path) -> MovePlan:
    return MovePlan(
        source=plan.source,
        target=Path(target),
        items=plan.items,
        total_bytes=plan.total_bytes,
        files=plan.files,
        skipped=plan.skipped,
    )


# --------------------------------------------------------------------- 复制
ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class CopyResult:
    entries: int
    bytes_written: int
    removed: tuple[str, ...] = ()


def copy_tree(
    plan: MovePlan,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
) -> CopyResult:
    """按计划复制；取消抛 :class:`MoveCancelled`，已复制的内容保留。"""
    done = 0
    entries = 0
    removed: list[str] = []
    plan.target.mkdir(parents=True, exist_ok=True)
    for item in plan.items:
        if cancel is not None and cancel():
            raise MoveCancelled()
        source = plan.source / item.relative
        target = plan.final_path(item)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as reader, target.open("wb") as writer:
                while True:
                    if cancel is not None and cancel():
                        raise MoveCancelled()
                    block = reader.read(COPY_CHUNK_BYTES)
                    if not block:
                        break
                    writer.write(block)
                    done += len(block)
                    if progress is not None:
                        progress(done, plan.total_bytes)
        except MoveCancelled:
            raise
        except FileNotFoundError:
            # 计划生成后源文件被删：不算失败，真正的差异交给 verify_copy。
            removed.append(str(item.relative))
            continue
        except OSError as exc:
            raise RootsError(f"复制失败：{item.relative}（{type(exc).__name__}）") from exc
        entries += 1
    if progress is not None:
        progress(plan.total_bytes, plan.total_bytes)
    return CopyResult(entries=entries, bytes_written=done, removed=tuple(removed))


def verify_copy(plan: MovePlan) -> tuple[bool, str]:
    """逐个核对文件数与字节数；不一致时返回人话（可直接进日志）。"""
    missing: list[str] = []
    size_mismatch: list[str] = []
    for item in plan.items:
        target = plan.final_path(item)
        try:
            size = target.stat().st_size
        except OSError:
            missing.append(str(item.relative))
            continue
        if size != item.size:
            size_mismatch.append(str(item.relative))
    if missing or size_mismatch:
        parts = []
        if missing:
            parts.append(f"缺少 {len(missing)} 个文件（例：{missing[0]}）")
        if size_mismatch:
            parts.append(f"大小不一致 {len(size_mismatch)} 个（例：{size_mismatch[0]}）")
        return False, "；".join(parts)
    return True, f"{len(plan.items)} 个文件 / {plan.total_bytes} 字节"


# ------------------------------------------------------------------- 搬迁日志
def journal_path(root: Path) -> Path:
    return Path(root) / JOURNAL_FILENAME


def write_journal(root: Path, *, old_root: Path, pointer_path: Path, kind: str) -> None:
    """在**新根**登记「旧目录待删」，下次启动时收尾。"""
    payload = {
        "schema": 1,
        "kind": str(kind),
        "old_root": str(Path(old_root)),
        "pointer": str(Path(pointer_path)),
    }
    path = journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_journal(root: Path) -> dict[str, object] | None:
    path = journal_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def clear_journal(root: Path) -> None:
    try:
        journal_path(root).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("清理搬迁日志失败：%s", journal_path(root))


def cleanup_moved_source(root: Path) -> bool:
    """删掉搬迁后的旧目录；返回是否真的删了。

    旧目录里可能躺着指针文件（常规安装的指针就在默认根里），删树前先读出内容，
    删完再写回去——否则下次启动会退化成「默认根 + 空数据」。
    """
    root = Path(root)
    journal = read_journal(root)
    if journal is None:
        return False
    raw_old = str(journal.get("old_root") or "").strip()
    if not raw_old:
        clear_journal(root)
        return False
    old_root = Path(raw_old)
    # journal 是磁盘上的外部输入：相对路径会按进程工作目录解析，盘符根只有一段。
    # 这两种都不可能是搬迁写出的旧根，绝不能交给下面的 rmtree。
    if not old_root.is_absolute() or len(old_root.parts) < 2:
        logger.warning("忽略不可信的旧根记录：%r", raw_old)
        clear_journal(root)
        return False
    pointer = Path(str(journal.get("pointer") or "").strip())
    try:
        if old_root.resolve() == root.resolve():
            clear_journal(root)
            return False
    except OSError:
        return False
    if not old_root.is_dir():
        clear_journal(root)
        return False
    kept: tuple[Path, bytes] | None = None
    if pointer and pointer.is_file():
        try:
            if pointer.resolve().is_relative_to(old_root.resolve()):
                kept = (pointer, pointer.read_bytes())
        except OSError:
            kept = None
    try:
        shutil.rmtree(old_root)
    except OSError as exc:
        logger.warning("清理旧目录失败（下次启动再试）：%s", type(exc).__name__)
        return False
    if kept is not None:
        path, payload = kept
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        except OSError:
            logger.warning("恢复位置文件失败：%s", path)
    clear_journal(root)
    logger.info("已清理搬迁后的旧目录：%s", old_root)
    return True
