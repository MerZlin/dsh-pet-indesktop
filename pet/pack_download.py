# -*- coding: utf-8 -*-
"""角色包注册表与分件下载（HTTP Range 断点续传）。

**为什么不直接复用 ``pet/updater.py::download_asset``**：那条路给安装包用，
固定从 0 覆盖写（``wb``），没有断点续传。角色分件是几百 MB 级，断点续传是必需
能力。这里复用它的两条安全判据（可信 URL 白名单、文件名净化）与最终
sha256/字节数校验，但自己实现 Range 续传。

安全契约（与下载安装包同级）：

- 只允许白名单里的 https 域名；
- 有字节上限，超过立即中止（防服务端返回超大流）；
- **最终以 sha256 + 字节数双重校验**，任一不符即删掉半成品并报错；
- 半成品固定放在 ``<name>.part``，只有校验通过才 ``os.replace`` 成正式文件。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .updater import _safe_filename, _trusted_https_url, trusted_urlopen

SCHEMA = 1
PART_SUFFIX = ".part"
_CHUNK = 1024 * 1024
_MAX_PART_BYTES = 4 * 1024 * 1024 * 1024        # 单分件上限 4GiB
_USER_AGENT = "dsh-pet-pack/1"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _safe_component(value, field: str) -> str:
    """单个**路径组件**级校验：不许分隔符、盘符、上跳、控制字符。

    ``id`` / ``variant`` / ``version`` 会被直接拼进安装路径
    （``characters_root()/id/folder_name()``）：绝对 id 或带 ``..`` 的版本能把
    整个 target_dir 带到角色根之外，而后面基于 target_dir 的越界检查会一并失效
    （终审点名）。所以这三个字段必须在**解析阶段**就被限定为单段。
    """
    text = str(value)
    if not text or text in (".", ".."):
        raise RegistryError(f"{field} 不能为空或点号: {value!r}")
    if any(ch in text for ch in ("/", "\\", ":")) or text.startswith(".."):
        raise RegistryError(f"{field} 必须是单个路径组件（不含分隔符/盘符/上跳）: {value!r}")
    if any(ord(ch) < 32 for ch in text):
        raise RegistryError(f"{field} 含控制字符: {value!r}")
    return text


class RegistryError(ValueError):
    """注册表非法。"""


class DownloadError(RuntimeError):
    """下载/校验失败。"""


@dataclass(frozen=True)
class Part:
    name: str
    urls: tuple[str, ...]
    bytes: int
    sha256: str
    #: 解包后体积（磁盘预检用；0 = 清单没给，按压缩体积估算）
    unpacked_bytes: int = 0


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    variant: str
    version: str
    canvas: tuple[int, int]
    pipeline: str
    format: str
    app_min: str
    parts: tuple[Part, ...]

    def folder_name(self) -> str:
        w, h = self.canvas
        return f"{self.version}-{self.variant}-{w}x{h}"

    def labels(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.parts)


def _str(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{field} 必须是非空字符串: {value!r}")
    return value.strip()


def _int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistryError(f"{field} 必须是整数: {value!r}")
    return value


def parse_registry(data) -> tuple[Pack, ...]:
    """解析角色包注册表（``packs`` 数组）。非法即抛 :class:`RegistryError`。"""
    if not isinstance(data, dict):
        raise RegistryError("注册表顶层必须是对象")
    schema = _int(data.get("schema"), "schema")
    if schema != SCHEMA:
        raise RegistryError(f"不支持的注册表 schema: {schema}（本版支持 {SCHEMA}）")
    raw_packs = data.get("packs")
    if not isinstance(raw_packs, (list, tuple)) or not raw_packs:
        raise RegistryError("packs 必须是非空数组")

    packs: list[Pack] = []
    #: 去重键含**分辨率**：同一版本号的 640 与 720 是两条独立产品线，不是重复包
    seen: set[tuple[str, str, str, int, int]] = set()
    for i, raw in enumerate(raw_packs):
        if not isinstance(raw, dict):
            raise RegistryError(f"packs[{i}] 必须是对象")
        canvas = raw.get("canvas") or raw.get("resolution")
        if (not isinstance(canvas, (list, tuple)) or len(canvas) != 2
                or any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in canvas)):
            raise RegistryError(f"packs[{i}].canvas 必须是两个正整数: {canvas!r}")
        raw_parts = raw.get("parts")
        if not isinstance(raw_parts, (list, tuple)) or not raw_parts:
            raise RegistryError(f"packs[{i}].parts 必须是非空数组")
        parts: list[Part] = []
        for j, rp in enumerate(raw_parts):
            if not isinstance(rp, dict):
                raise RegistryError(f"packs[{i}].parts[{j}] 必须是对象")
            urls = rp.get("urls") or ([rp.get("url")] if rp.get("url") else [])
            if not isinstance(urls, (list, tuple)) or not urls:
                raise RegistryError(f"packs[{i}].parts[{j}] 缺少 url")
            urls = tuple(_str(u, f"packs[{i}].parts[{j}].urls[]") for u in urls)
            bad = [u for u in urls if not _trusted_https_url(u)]
            if bad:
                raise RegistryError(f"packs[{i}].parts[{j}] 含不受信任的地址: {bad[0]}")
            size = _int(rp.get("bytes"), f"packs[{i}].parts[{j}].bytes")
            if size <= 0 or size > _MAX_PART_BYTES:
                raise RegistryError(f"packs[{i}].parts[{j}].bytes 越界: {size}")
            sha = _str(rp.get("sha256"), f"packs[{i}].parts[{j}].sha256").lower()
            if not _SHA_RE.match(sha):
                raise RegistryError(f"packs[{i}].parts[{j}].sha256 非法: {sha!r}")
            unpacked = _int(rp.get("unpacked_bytes", 0),
                            f"packs[{i}].parts[{j}].unpacked_bytes")
            if unpacked < 0:
                raise RegistryError(f"packs[{i}].parts[{j}].unpacked_bytes 越界: {unpacked}")
            parts.append(Part(name=_str(rp.get("name"), f"packs[{i}].parts[{j}].name"),
                              urls=urls, bytes=size, sha256=sha,
                              unpacked_bytes=unpacked))
        key = (_safe_component(_str(raw.get("id"), f"packs[{i}].id"), f"packs[{i}].id"),
               _safe_component(_str(raw.get("variant", "base"), f"packs[{i}].variant"),
                               f"packs[{i}].variant"),
               _safe_component(_str(raw.get("version"), f"packs[{i}].version"),
                               f"packs[{i}].version"))
        key = key + (int(canvas[0]), int(canvas[1]))
        if key in seen:
            raise RegistryError(f"注册表里重复的包: {key}")
        seen.add(key)
        packs.append(Pack(
            id=key[0],
            name=_str(raw.get("name", key[0]), f"packs[{i}].name"),
            variant=key[1],
            version=key[2],
            canvas=(int(canvas[0]), int(canvas[1])),
            pipeline=_str(raw.get("pipeline"), f"packs[{i}].pipeline"),
            format=_str(raw.get("format"), f"packs[{i}].format"),
            app_min=_str(raw.get("app_min", "0"), f"packs[{i}].app_min"),
            parts=tuple(parts),
        ))
    return tuple(packs)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()





def download_part(part: Part, target_dir: str | os.PathLike, *,
                  timeout: float = 30.0,
                  progress: Callable[[int, int], None] | None = None) -> Path:
    """下载一个分件（支持断点续传），返回已校验的正式文件路径。

    续传协议：已存在 ``<name>.part`` 时，用 ``Range: bytes=<已收字节>-`` 请求剩余
    部分；服务端不支持 Range（回 200 而非 206）时，**丢弃已收字节从头下**——
    宁可从零重来，也不把整段内容追加到半截文件上。
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / _safe_filename(part.name)
    partial = final.with_name(final.name + PART_SUFFIX)

    # 已经下好了（上次校验通过但没来得及换名）：直接复检
    if final.is_file() and final.stat().st_size == part.bytes and _hash_file(final) == part.sha256:
        return final

    # 下载对象锁：两个任务同时写同一个 .part 会互相撕碎文件（终审点名）。
    # 等都不等——用户再点一次是明确信号，直接报"另一个下载正在进行"。
    with _TargetLock(target_dir, timeout=0.1,
                     name=final.name + ".dl.lock", what="下载"):
        return _download_locked(part, final, partial,
                                timeout=timeout, progress=progress)


def _download_locked(part: Part, final: Path, partial: Path, *,
                     timeout: float, progress: Callable[[int, int], None] | None) -> Path:
    """已持下载锁的下载主体（见 :func:`download_part`）。

    **断流自动重试**（实机踩出来的需求：到 GitHub 的链路每十几 MB 掉一次，
    让人点第二次才续传 = 看着像"没反应"）。网络层错误与截短（字节数不符）
    在同一个地址上重试 ``_TRANSIENT_RETRIES`` 次——半成品留着，Range 从断点续；
    只有 sha256 不符（内容不可信）才删半成品并直接换下一个地址。
    """
    errors: list[str] = []
    for url in part.urls:
        last_error = ""
        for attempt in range(1, _TRANSIENT_RETRIES + 1):
            try:
                _download_one(url, partial, part, timeout=timeout, progress=progress)
                if partial.stat().st_size != part.bytes:
                    raise DownloadError(
                        f"字节数不符: 期望 {part.bytes} 实收 {partial.stat().st_size}")
                actual = _hash_file(partial)
                if actual != part.sha256:
                    raise DownloadError(f"sha256 不符: 期望 {part.sha256} 实得 {actual}")
                os.replace(partial, final)
                return final
            except urllib.error.HTTPError as exc:
                # 永久 HTTP 错误（404/403/410…）：同址重试无意义，立即换下一个
                # 地址（P2：原来按瞬断重试 10 次，白等 ~32s 才换源）
                last_error = str(exc)
                break
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                last_error = str(exc)
                if attempt < _TRANSIENT_RETRIES:
                    time.sleep(min(1.5 * attempt, 4.0))
            except DownloadError as exc:
                if "sha256 不符" in str(exc):
                    # 内容不可信：删半成品，换下一个地址，不重试
                    try:
                        partial.unlink(missing_ok=True)
                    except OSError:
                        pass
                    last_error = str(exc)
                    break
                # 截短（字节数不符）：半成品保留，续传重试
                last_error = str(exc)
                if attempt < _TRANSIENT_RETRIES:
                    time.sleep(min(1.5 * attempt, 4.0))
        errors.append(f"{url}: {last_error}")
    raise DownloadError("；".join(errors) or "没有可用的下载地址")


#: 单个地址上断流/截短的重试次数（Range 续传，不是从头下）。
#: 实机口径：到 GitHub 的链路每 ~15MB 掉一次，250MB 的分件最坏要 ~17 次
#: 续传；一次点击覆盖 ~150MB+ 的进度，配合断点保留，一两轮点击内必完成。
_TRANSIENT_RETRIES = 10


def _download_one(url: str, partial: Path, part: Part, *,
                  timeout: float, progress: Callable[[int, int], None] | None) -> None:
    have = partial.stat().st_size if partial.is_file() else 0
    if have > part.bytes:
        partial.unlink(missing_ok=True)
        have = 0
    if have == part.bytes:
        # 上次崩在"下完、rename 之前"：半成品恰好是全长（终审第六轮）。
        # 直接交给调用方复核摘要——发 `Range: bytes=<总长>-` 会吃 416 卡死。
        return
    headers = {"User-Agent": _USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with trusted_urlopen(req, timeout=timeout) as response:
        status = getattr(response, "status", 200)
        if have and status != 206:
            have = 0                     # 服务端不支持续传：丢弃已收，从头写
        mode = "ab" if have else "wb"
        received = have
        with partial.open(mode) as out:
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > part.bytes:
                    out.close()
                    partial.unlink(missing_ok=True)
                    raise DownloadError(f"收到超过声明字节数（{part.bytes}）")
                out.write(chunk)
                if progress:
                    progress(received, part.bytes)


# ------------------------------------------------------------------ 安装
LOCK_NAME = ".install.lock"
OLD_SUFFIX = ".__old__"       # 落地时旧目录的临时名（崩溃恢复认它）
STAGE_PREFIX = ".install-"    # 解压暂存目录前缀
JOURNAL_NAME = ".install-journal.json"   # 安装事务日志（崩溃后逐步骤收口）
#: 安装凭证（来源/摘要/时间）。只用于更新检测与排查，不是安装状态的真相来源。
RECEIPT_NAME = "install-receipt.json"

#: 解包资源预算（终审：``extractall`` 不许裸奔——压缩包体积小不代表解出来小）。
#: webp 本身已是压缩格式，解包 ≈ 下载体积，上限给的是"防 zip 炸弹"的余量，
#: 不是精打细算。
MAX_UNPACK_BYTES = 8 * 1024 ** 3     # 解包总字节 8 GiB
MAX_UNPACK_FILES = 200_000           # 文件数上限
MAX_MEMBER_BYTES = 64 * 1024 ** 2    # 单文件 64 MiB（一帧正常 < 1 MiB）
#: 分件里允许出现的文件类型（帧 + 元数据/sidecar，没有别的）
_ALLOWED_SUFFIXES = frozenset({".webp", ".json"})
#: Windows 保留名（NTFS 会静默改写或拒绝，包内容不允许依赖平台净化行为）
_WIN_RESERVED = ({"con", "prn", "aux", "nul"}
                 | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})


def _validate_zip_entries(zf: zipfile.ZipFile) -> None:
    """解包**前**的结构与资源校验（终审点名的第一道防线）。

    只信 ZIP 目录自己声明的内容，不信清单：总字节 / 文件数 / 单文件上限、
    路径组件逐个过（绝对路径 / 盘符 / 上跳 / 反斜杠 / ADS 冒号 / 尾点尾空格 /
    控制字符 / Windows 保留名）、大小写折叠后的重名、符号链接、文件类型白名单。
    任一不符整个分件拒绝解包。
    """
    infos = zf.infolist()
    if len(infos) > MAX_UNPACK_FILES:
        raise DownloadError(f"分件文件数超限（{len(infos)} > {MAX_UNPACK_FILES}）")
    total = 0
    seen: set[str] = set()
    seen_dirs: dict[str, str] = {}
    for info in infos:
        name = info.filename
        # 路径组件级校验（文件与**目录项**都过——终审第六轮：目录项不许成为
        # 免检通道）。目录项先剥掉结尾分隔符再查组件——``anim/`` 直接 split
        # 会得到末尾空组件，合法目录项不能被误拒（七轮实测抓到的回归）。
        check = name.rstrip("/")
        if check.startswith("/") or re.match(r"^[A-Za-z]:", check):
            raise DownloadError(f"分件含绝对路径: {name}")
        for comp in check.split("/"):
            if comp in ("", ".", ".."):
                raise DownloadError(f"分件含非法路径组件: {name}")
            if "\\" in comp or ":" in comp:
                raise DownloadError(f"分件含反斜杠/ADS 冒号: {name}")
            if comp != comp.rstrip(" ."):
                raise DownloadError(f"分件含尾点/尾空格路径: {name}")
            if any(ord(ch) < 32 for ch in comp):
                raise DownloadError(f"分件路径含控制字符: {name}")
            if comp.split(".")[0].lower() in _WIN_RESERVED:
                raise DownloadError(f"分件含 Windows 保留名: {name}")
        # 目录级大小写冲突（终审第六轮）：``A/x.json`` 与 ``a/y.json`` 全路径不
        # 重复，但 Windows 上 ``A`` 与 ``a`` 是同一个目录。同一路径的合法共享
        # （``anim/idle/c1`` 与 ``anim/idle/c2``）不算冲突——只拦「小写相同、
        # 原始大小写不同」的。
        parts = check.split("/")
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            first = seen_dirs.get(prefix.lower())
            if first is not None and first != prefix:
                raise DownloadError(f"分件含（大小写折叠后）目录冲突: {name}")
            seen_dirs.setdefault(prefix.lower(), prefix)
        if name.endswith("/"):            # 目录项本身不占预算
            continue
        total += info.file_size
        if info.file_size > MAX_MEMBER_BYTES:
            raise DownloadError(f"分件含超大单文件（{name}: {info.file_size} 字节）")
        if total > MAX_UNPACK_BYTES:
            raise DownloadError(f"分件解包总字节超限（> {MAX_UNPACK_BYTES}）")
        key = name.lower()
        if key in seen:
            raise DownloadError(f"分件含（大小写折叠后）重复条目: {name}")
        seen.add(key)
        dot = name.rfind(".")
        if dot < 0 or name[dot:].lower() not in _ALLOWED_SUFFIXES:
            raise DownloadError(f"分件含不允许的文件类型: {name}")
        # unix mode 符号链接（zipfile 不会解成链接，但内容语义不可信，直接拒）
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise DownloadError(f"分件含符号链接: {name}")


def _try_os_lock(fh) -> bool:
    """非阻塞拿一把整文件互斥锁（跨平台）；拿到返回 True。

    Windows 用 ``msvcrt.locking``、POSIX 用 ``fcntl.flock``——两者都是**内核级
    互斥原语**：进程死亡（崩溃/被 kill）内核自动放锁，不存在"死锁残留要靠
    年龄启发式接管"的问题（七轮终审：那条链加多少次复核都不是原子的）。
    """
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _release_os_lock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


class _TargetLock:
    """每安装目标一把锁（跨进程），**OS 级互斥**（msvcrt/fcntl）。

    终审指出的问题：core/extra 并发安装会共写同一个 ``character.json.tmp``、
    甚至互相删对方的半成品。锁挂在**版本目录**上，同一角色同一版本串行，
    不同版本互不影响。``name``/``what`` 让同一把锁也能用在下载对象上
    （两个任务不许同时写同一个 ``.part``——终审点名）。

    锁文件只是内核锁的**载体**（内容无意义）：靠"文件存在 + PID"判锁的旧方案
    无论加多少次复核都不是原子操作（七轮终审），已废弃。
    """

    def __init__(self, target_dir: Path, *, timeout: float = 120.0,
                 name: str = LOCK_NAME, what: str = "安装"):
        self.path = target_dir / name
        self.timeout = timeout
        self.what = what
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+b")
        deadline = time.time() + self.timeout
        while True:
            if _try_os_lock(self._fh):
                return self
            if time.time() > deadline:
                self._fh.close()
                self._fh = None
                raise DownloadError(
                    f"另一个{self.what}正在进行（锁 {self.path}），请稍后重试")
            time.sleep(0.05)

    def __exit__(self, *exc):
        if self._fh is not None:
            _release_os_lock(self._fh)
            self._fh.close()
            self._fh = None
        return False


def recover_target(target_dir: Path) -> dict:
    """清理崩溃残留：暂存目录、落地用的旧目录临时名。

    崩溃可能留下的三种：
    1. ``.install-*`` 解压暂存（未落地）→ 直接删；
    2. ``<clip>.__old__``（旧目录已改名、新内容已/未就位）→ 见下方两种策略；
    3. 锁文件 → 只是 ``_TargetLock`` 的载体（OS 级互斥，进程死亡内核自动放锁，
       无需按内容/年龄接管）。

    ``__old__`` 的处理取决于事务日志（终审 B4）：

    - 有 ``phase=place`` 日志（索引**还没**提交）→ **回滚**：删掉新内容、把
      ``__old__`` 改回原名。盘上内容与索引必须成对，宁可退回旧素材。
    - ``phase=merge`` → 不在这一层动：完整性判定与回滚/提交由
      ``_roll_forward_journal`` 一并决定（先删 ``__old__`` 会把回滚依据毁掉）。
    - 否则（无日志的遗留 / 已合并）→ 只要正式目录在就用它，清掉 ``__old__``。

    ``__old__`` 可以是目录（clip）也可以是文件（sidecar），两种都要认。
    """
    restored = removed = rolled_back = 0
    if not target_dir.is_dir():
        return {"restored": 0, "removed": 0, "rolled_back": 0, "merged": 0}
    phase = _journal_phase(target_dir)
    if phase != "merge":
        for old in list(target_dir.rglob(f"*{OLD_SUFFIX}")):
            final = old.with_name(old.name[: -len(OLD_SUFFIX)])
            try:
                if final.exists() and phase == "place":
                    if final.is_dir():
                        shutil.rmtree(final, ignore_errors=True)  # 未提交的新内容
                    else:
                        final.unlink(missing_ok=True)
                    old.rename(final)
                    rolled_back += 1
                elif final.exists():
                    if old.is_dir():
                        shutil.rmtree(old, ignore_errors=True)
                    else:
                        old.unlink(missing_ok=True)
                    removed += 1
                else:
                    old.rename(final)
                    restored += 1
            except OSError:
                pass
        if phase == "place":
            # place 阶段崩在 sidecar 复制中途：只有标记为 "new"（本次新放、原本
            # 没有旧文件）的 sidecar 才允许删（八轮 P1："replaced" 的旧文件由
            # 上面的 __old__ 循环恢复，尚未触碰的旧文件不在日志里，都不许碰）。
            doc = _journal_doc(target_dir) or {}
            sidecars = doc.get("sidecars")
            items = sidecars.items() if isinstance(sidecars, dict) else []
            for name, how in items:
                if how != "new":
                    continue
                dst = target_dir / str(name)
                if dst.is_file():
                    try:
                        dst.unlink()
                        rolled_back += 1
                    except OSError:
                        pass
    for stage in list(target_dir.glob(f"{STAGE_PREFIX}*")):
        shutil.rmtree(stage, ignore_errors=True)
        removed += 1
    # sidecar 复制中断留下的 .tmp（九轮收尾项）：持锁状态下不可能是进行中的写
    for name in ("manifest.json", "text_clips.json", "move_strides.json"):
        try:
            (target_dir / f"{name}.tmp").unlink(missing_ok=True)
        except OSError:
            pass
    rolled = _roll_forward_journal(target_dir)
    return {"restored": restored, "removed": removed, "rolled_back": rolled_back,
            "merged": rolled["merged"]}


def _journal_doc(target_dir: Path) -> dict | None:
    """读事务日志全文（没有/读不出来 → None）。"""
    path = target_dir / JOURNAL_NAME
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _journal_phase(target_dir: Path) -> str | None:
    """读日志阶段（没有/读不出来 → None）。只用于恢复策略判定。"""
    doc = _journal_doc(target_dir)
    return doc.get("phase") if isinstance(doc, dict) else None


def _write_journal(target_dir: Path, payload: dict) -> Path:
    """写安装事务日志（原子替换：先写 tmp 再 ``os.replace``）。"""
    path = target_dir / JOURNAL_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _clips_on_disk(target_dir: Path, part_doc: dict) -> list[dict]:
    """日志声明的 clips 中，**磁盘上确实就位**的那几条。

    判据用安装时写进 meta 的 ``content_sha`` 凭证（与运行时采纳闸门同一口径）：
    不重算全量帧摘要 —— 启动恢复不许变成一次全库读放大。但「删掉帧、留下
    meta」不算就位（终审第六轮）：帧数对不上照样排除。
    """
    out: list[dict] = []
    clips = part_doc.get("clips") if isinstance(part_doc, dict) else None
    if not isinstance(clips, list):
        return out
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        rel = clip.get("dir")
        if not isinstance(rel, str):
            continue
        clip_dir = target_dir / rel
        if not clip_dir.is_dir():
            continue
        try:
            meta = json.loads((clip_dir / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("content_sha") != clip.get("content_sha"):
            continue
        declared = meta.get("frames")
        if isinstance(declared, bool) or not isinstance(declared, int) or declared <= 0:
            continue
        try:
            on_disk = sum(1 for p in clip_dir.iterdir()
                          if p.name.startswith("f_") and p.suffix == ".webp")
        except OSError:
            continue
        if on_disk != declared:
            continue
        out.append(clip)
    return out


def _rollback_placed(target_dir: Path, part_doc: dict, sidecars: list | None = None) -> None:
    """``phase=merge`` 但内容不完整：撤掉这次落地的内容，恢复 ``__old__``。

    不承认半个分件（终审）：帧在 phase=place 全部就位后才进 merge，此刻缺段
    说明落地后遭破坏——回滚到旧素材，分件按「未安装」处理，用户重下。
    sidecar 同进退：有旧备份的由 ``__old__`` 恢复，**没有旧备份的新 sidecar
    也要删掉**（终审第六轮：回滚后盘上不许留这次安装的痕迹）。
    """
    for clip in part_doc.get("clips", []):
        if not isinstance(clip, dict):
            continue
        rel = clip.get("dir")
        if not isinstance(rel, str):
            continue
        placed = target_dir / rel
        if placed.is_dir():
            shutil.rmtree(placed, ignore_errors=True)
    for name in sidecars or []:
        dst = target_dir / str(name)
        if dst.is_file() and not dst.with_name(dst.name + OLD_SUFFIX).exists():
            try:
                dst.unlink()
            except OSError:
                pass
    for old in list(target_dir.rglob(f"*{OLD_SUFFIX}")):
        final = old.with_name(old.name[: -len(OLD_SUFFIX)])
        try:
            if final.is_dir():
                shutil.rmtree(final, ignore_errors=True)
            elif final.exists():
                final.unlink(missing_ok=True)
            old.rename(final)
        except OSError:
            pass


def _roll_forward_journal(target_dir: Path) -> dict:
    """收口一份安装事务日志；返回 ``{"merged": n, "phase": ?}``。

    - ``phase == "merge"``（帧已全部落地，只差合并索引）→ **完整性判定**：
      分件声明的 clips **全部**在盘（带摘要凭证）才前滚合并，并在提交后清掉
      ``__old__``；缺任何一段 = 安装已损坏，**回滚**这次落地的内容、恢复
      ``__old__``，不承认半个分件（终审：原实现接受任意非空子集）。
    - 其它阶段（还在逐个落地）→ **不回滚也不合并**：已落地的目录留着（下次
      安装会把它当旧版覆盖），没进索引就不会被当成"已安装"，宁可让用户重下
      也不装半套。
    除「合并写盘失败」（日志保留待重试，终审 B3）外，收口后日志一定删除——
    同一份事务不会被收第二次。
    """
    path = target_dir / JOURNAL_NAME
    if not path.is_file():
        return {"merged": 0, "phase": None, "kept": False}   # 没有日志：静默
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            '安装事务日志不可读（%s），按"无可前滚"处理并清理: %s', path.name, exc)
        doc = None
    phase = doc.get("phase") if isinstance(doc, dict) else None
    merged = 0
    keep_journal = False
    if isinstance(doc, dict) and phase == "merge":
        part_doc = doc.get("part_doc") or {}
        declared = part_doc.get("clips") or []
        if not isinstance(declared, list):
            declared = []                    # 坏日志：按"无可前滚"处理
        clips = _clips_on_disk(target_dir, part_doc)
        sidecars_doc = doc.get("sidecars")
        sidecar_names = list(sidecars_doc) if isinstance(sidecars_doc, dict) else []
        sidecars_missing = [n for n in sidecar_names
                            if not (target_dir / str(n)).is_file()]
        if declared and (len(clips) != len(declared) or sidecars_missing):
            logging.getLogger(__name__).warning(
                '安装事务内容不完整（%d/%d 段在盘，缺 sidecar %s），'
                '回滚本次落地，分件按未安装处理',
                len(clips), len(declared), sidecars_missing or "无")
            _rollback_placed(target_dir, part_doc, sidecar_names)
        elif clips:
            try:
                _merge_index(target_dir, {**part_doc, "clips": clips})
                merged = len(clips)
                # 提交完成才清旧目录/旧 sidecar（它们是回滚依据）
                for old in target_dir.rglob(f"*{OLD_SUFFIX}"):
                    try:
                        if old.is_dir():
                            shutil.rmtree(old, ignore_errors=True)
                        else:
                            old.unlink(missing_ok=True)
                    except OSError:
                        pass
            except (OSError, ValueError, TypeError) as exc:
                # 索引写不进去（只读/占用）不该让调用方炸，但**日志必须留着**：
                # 删了就等于把重试依据丢掉，下一次启动/安装也修不回来了（终审 B3）。
                logging.getLogger(__name__).warning(
                    '安装事务前滚失败（素材保持现状，日志保留待重试）: %s', exc)
                keep_journal = True
    if not keep_journal:
        try:
            path.unlink()
        except OSError:
            pass
    return {"merged": merged, "phase": phase, "kept": keep_journal}


def recover_installs(target_dir: str | os.PathLike, *, timeout: float = 2.0) -> dict:
    """**启动期**收口崩溃残留的安装事务。

    ``install_part`` 每次安装开始时都会 ``recover_target``；崩在两次安装之间就
    没人修（终审：无事务日志、无启动恢复）。这里在库加载前收口一次：没有日志 →
    一次 ``is_file()`` 就返回（常规启动零成本）；有日志 → 抢短超时锁（有安装
    正在跑就跳过，它自己会收口）→ 恢复旧目录 + 前滚/丢弃事务。
    """
    target_dir = Path(target_dir)
    if not (target_dir / JOURNAL_NAME).is_file():
        return {"journal": False, "restored": 0, "removed": 0, "merged": 0}
    try:
        with _TargetLock(target_dir, timeout=timeout):
            info = recover_target(target_dir)
    except (DownloadError, OSError) as exc:
        logging.getLogger(__name__).info('安装事务收口跳过: %s', exc)
        return {"journal": False, "skipped": True, "restored": 0, "removed": 0, "merged": 0}
    return {"journal": True, **info}


def frame_digest(clip_dir: Path) -> str:
    """逐 clip 的**存储身份**：按帧序把帧文件名字节 + 内容字节喂进同一个 sha256。

    与 ``scripts/build_character_pack.py::_clip_digest`` 同一口径——两边必须逐字节
    一致，否则安装校验必然失败（这条由测试锁住）。
    """
    h = hashlib.sha256()
    for p in sorted(Path(clip_dir).glob("f_*.webp")):
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def _validate_part_doc(part_doc) -> list:
    """``part.json`` 结构校验（不可信输入）。非法即抛 :class:`DownloadError`。

    判据与运行时索引解析（``character_pack.parse_index``）**同一把尺**（终审
    第六轮）：只查 clips 的路径/帧数/摘要不够——schema / 格式与处理链白名单 /
    category / key_every / 有限 fps 不过的话，装出来的 ``character.json`` 会被
    ``load_index`` 整个拒掉（装得上、用不了）。
    """
    if not isinstance(part_doc, dict):
        raise DownloadError("part.json 顶层必须是对象")
    for key in ("id", "pipeline", "format"):
        if not isinstance(part_doc.get(key), str) or not part_doc[key].strip():
            raise DownloadError(f"part.json 缺 {key}（或非字符串）")
    canvas = part_doc.get("canvas")
    if (not isinstance(canvas, (list, tuple)) or len(canvas) != 2
            or any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in canvas)):
        raise DownloadError(f"part.json canvas 必须是两个正整数: {canvas!r}")
    fps = part_doc.get("fps")
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or fps <= 0:
        raise DownloadError(f"part.json fps 非法: {fps!r}")
    clips = part_doc.get("clips")
    if not isinstance(clips, list) or not clips:
        raise DownloadError("part.json clips 必须是非空数组")
    from .character_pack import parse_index, safe_rel_dir
    for clip in clips:
        if not isinstance(clip, dict):
            raise DownloadError("part.json clips[] 必须是对象")
        if not isinstance(clip.get("name"), str) or not clip["name"].strip():
            raise DownloadError("part.json clips[].name 必须是非空字符串")
        try:
            safe_rel_dir(clip.get("dir"), f"clips[{clip['name']}].dir")
        except ValueError as exc:
            raise DownloadError(f"分件索引含非法路径: {exc}") from exc
        frames = clip.get("frames")
        if isinstance(frames, bool) or not isinstance(frames, int) or frames <= 0:
            raise DownloadError(f"clips[{clip['name']}].frames 非法: {frames!r}")
        if frames > 1_000_000:
            # 上限先于 expected 列表分配（终审第六轮：巨大 frames 会在真实文件
            # 数量核对之前先吃掉内存）
            raise DownloadError(f"clips[{clip['name']}].frames 超限: {frames}")
        if not _SHA_RE.match(str(clip.get("content_sha") or "").lower()):
            raise DownloadError(f"clips[{clip['name']}].content_sha 非法")
        if not re.match(r"^[A-Za-z0-9_-]{1,32}$", str(clip.get("part", "core"))):
            raise DownloadError(f"clips[{clip['name']}].part 非法: {clip.get('part')}")
    # 同尺核对：把 part_doc 当一份完整索引过运行时解析器（schema/白名单/
    # category/key_every/有限 fps/重名全在这里面）。**默认值就地写回**（终审
    # 第七轮）：预检补了默认、合并落盘却写原文 = 索引里出现 None，装得上用不了。
    part_doc.setdefault("schema", 1)
    part_doc.setdefault("name", part_doc["id"])
    part_doc["parts"] = (part_doc.get("parts")
                         or sorted({str(c.get("part", "core")) for c in clips}))
    try:
        parse_index({
            "schema": part_doc["schema"],
            "id": part_doc["id"],
            "name": part_doc["name"],
            "canvas": canvas,
            "fps": fps,
            "pipeline": part_doc["pipeline"],
            "format": part_doc["format"],
            "parts": part_doc["parts"],
            "clips": clips,
        })
    except ValueError as exc:
        raise DownloadError(f"part.json 过不了运行时索引解析: {exc}") from exc
    return clips


def _check_release_identity(target_dir: Path, part_doc: dict) -> None:
    """不可变发行边界（终审点名）：同一版本目录只许「同一发行补装分件」。

    - 已有索引的顶层身份（id/canvas/fps/pipeline/format）必须与分件一致 → 拒绝混装；
    - 分件 clip 与已装 clip **同名**：part 归属与 content_sha 都必须一致
      （一致 = 幂等重装；不同 = **同路径不同内容**，拒绝——重发必须新版本号）；
    - 分件 clip 与已装 clip **同 dir 不同名**：一个新名字指向已有 clip 的目录
      就能绕过按名查重、把别人的目录换掉（终审第六轮）——dir 所有权也要查。
    """
    index_path = target_dir / "character.json"
    if not index_path.is_file():
        return
    try:
        current = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DownloadError(f"已有索引不可读，拒绝在受损目录上续装: {exc}") from exc
    if not isinstance(current, dict):
        raise DownloadError("已有索引非法，拒绝续装")
    for key in ("id", "canvas", "fps", "pipeline", "format"):
        if current.get(key) != part_doc.get(key):
            raise DownloadError(
                f"分件身份与已装发行不符（{key}: {part_doc.get(key)!r} != "
                f"{current.get(key)!r}），拒绝混装")
    by_name = {c.get("name"): c for c in current.get("clips", []) if isinstance(c, dict)}
    from .character_pack import safe_rel_dir as _norm_dir

    def _dkey(value) -> str:
        """dir 的比对键：规范化（``./``/空组件）+ 小写（Windows 大小写别名）。

        终审第七轮：用原文做所有权键，``anim/idle/./c`` 或大小写变体就能绕过
        按名查重换掉别人的目录。
        """
        try:
            return _norm_dir(str(value), "dir").lower()
        except ValueError:
            return str(value).lower()

    dir_owner = {_dkey(c.get("dir")): c.get("name")
                 for c in current.get("clips", []) if isinstance(c, dict)}
    for clip in part_doc.get("clips", []):
        name = clip.get("name")
        owner = dir_owner.get(_dkey(clip.get("dir")))
        if owner is not None and owner != name:
            raise DownloadError(
                f"目录归属冲突: {clip.get('dir')} 已属于 {owner}，{name} 不得占用"
                "（发行目录不可变：内容变了必须新版本号）")
        old = by_name.get(name)
        if old is None:
            continue
        if _dkey(old.get("dir")) != _dkey(clip.get("dir")):
            raise DownloadError(
                f"同名 clip 的目录被挪动: {name}（{old.get('dir')} → {clip.get('dir')}），"
                "拒绝（发行目录不可变）")
        if str(old.get("part", "core")) != str(clip.get("part", "core")):
            raise DownloadError(f"clip 分件归属冲突（core/extra 不可互盖）: {name}")
        if str(old.get("content_sha", "")).lower() != str(clip.get("content_sha", "")).lower():
            raise DownloadError(
                f"同路径不同内容，拒绝覆盖: {name}"
                "（发行目录不可变：内容变了必须新版本号）")


def _verify_clip_playable(clip_dir: Path, clip: dict, canvas) -> str:
    """安装时的「能播」验证（一次性）：返回错误文本，``""`` = 通过。

    摘要只证明「收到的是那批字节」，不证明那批字节能播（终审点名）。补三条：
    帧连续且数量与声明一致、meta 帧数自洽、**首尾帧真实可解码且画布与发行
    声明一致**（把 32×24 声明成 640×360 的包在这一条上现形）。
    """
    frames = clip["frames"]                     # _validate_part_doc 已保证正整数
    names = sorted(p.name for p in clip_dir.glob("f_*.webp"))
    expected = [f"f_{i:04d}.webp" for i in range(1, frames + 1)]
    if names != expected:
        return f"帧文件不连续或不完整（声明 {frames}，实有 {len(names)}）"
    try:
        meta = json.loads((clip_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "meta.json 缺失或不可读"
    if not isinstance(meta, dict) or meta.get("frames") != frames:
        return "meta.frames 与声明不符"
    try:
        from .frameseq_provision import _pil
        Image, *_ = _pil()
    except Exception:            # noqa: BLE001 - 没有 Pillow 就验不了，明确失败
        return "Pillow 不可用，无法验证帧可解码"
    for probe in (names[0], names[-1]):
        try:
            with Image.open(clip_dir / probe) as im:
                im.load()
                size = im.size
        except Exception:        # noqa: BLE001 - 解不开就是不能播
            return f"帧不可解码: {probe}"
        if tuple(size) != (int(canvas[0]), int(canvas[1])):
            return f"画布不符: 帧 {size[0]}x{size[1]} != 声明 {canvas[0]}x{canvas[1]}"
    return ""


def _merge_index(target_dir: Path, part_doc: dict) -> Path:
    """把分件的 clips 并进 ``character.json``（幂等、原子替换）。

    合并结果先过运行时索引解析（``parse_index``）再落盘（终审第六轮）：
    写一份 ``load_index`` 读不了的索引，等于把整个包装坏。
    """
    index_path = target_dir / "character.json"
    current: dict = {}
    if index_path.is_file():
        try:
            current = json.loads(index_path.read_text(encoding="utf-8"))
        except ValueError:
            current = {}
    if not current:
        current = {k: part_doc.get(k) for k in
                   ("schema", "id", "name", "canvas", "fps", "pipeline", "format")}
        current["clips"] = []
    by_name = {c["name"]: c for c in current.get("clips", [])}
    for clip in part_doc.get("clips", []):
        by_name[clip["name"]] = clip
    current["clips"] = [by_name[k] for k in sorted(by_name)]
    current["parts"] = sorted({c.get("part", "core") for c in current["clips"]})
    from .character_pack import parse_index
    parse_index(current)          # 非法即抛，不落盘
    tmp = index_path.with_name(index_path.name + ".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(index_path)
    return index_path


def install_part(archive: str | os.PathLike, target_dir: str | os.PathLike, *,
                 verify: bool = True) -> dict:
    """解压一个分件到角色目录并合并索引。

    流程：解到临时目录 → 逐 clip 重算 ``content_sha`` 校验 → 逐个 rename 落地 →
    合并 ``part.json`` 进 ``character.json``。**任何一帧校验不过整个分件不落地**
    （宁可不装，也不让半坏素材进播放池）。
    """
    archive = Path(archive)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    with _TargetLock(target_dir):
        recover_target(target_dir)
        # 上一次事务前滚失败（日志保留待重试）时**不许开新安装**（终审第六轮）：
        # 继续装会把原 journal 覆盖掉，那笔事务的重试依据就没了。
        if (target_dir / JOURNAL_NAME).is_file():
            raise DownloadError(
                "存在未完成的安装事务（索引合并失败待重试）。"
                "请重启应用让它自动收口，或手动删除 .install-journal.json 后重试")
        return _install_locked(archive, target_dir, verify=verify)


def _install_locked(archive: Path, target_dir: Path, *, verify: bool) -> dict:
    """已持锁的安装主体（见 :func:`install_part`）。"""
    staging = target_dir / f"{STAGE_PREFIX}{os.getpid()}-{archive.stem}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            _validate_zip_entries(zf)     # 解包前的结构与资源预算（第一道防线）
            zf.extractall(staging)
        part_doc_path = staging / "part.json"
        if not part_doc_path.is_file():
            raise DownloadError("分件缺少 part.json")
        try:
            part_doc = json.loads(part_doc_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise DownloadError(f"part.json 不是合法 JSON: {exc}") from exc
        # part.json 是不可信输入：结构、路径、帧数、摘要、part 归属全部显式校验
        # （路径随后会用于 rmtree/move，绝对路径/上跳能删到目标目录之外——终审点名）。
        _validate_part_doc(part_doc)
        from .character_pack import safe_rel_dir
        safe_dirs = [safe_rel_dir(clip["dir"], f"clips[{clip['name']}].dir")
                     for clip in part_doc["clips"]]
        # 不可变发行：已有内容与本分件身份不符（混装/同路径不同内容）直接拒绝
        _check_release_identity(target_dir, part_doc)
        bad: list[str] = []
        if verify:
            for clip in part_doc["clips"]:
                clip_dir = staging / clip["dir"]
                if not clip_dir.is_dir():
                    bad.append(f"{clip['name']}: 目录缺失")
                    continue
                got = frame_digest(clip_dir)
                if got != clip.get("content_sha"):
                    bad.append(f"{clip['name']}: 内容摘要不符")
                    continue
                # 「能播」验证（终审）：摘要只证明字节对，不证明能播——帧连续性、
                # meta 自洽、首尾帧真实可解码、画布与声明一致，全过才算数。
                problem = _verify_clip_playable(clip_dir, clip, part_doc["canvas"])
                if problem:
                    bad.append(f"{clip['name']}: {problem}")
        if bad:
            raise DownloadError("分件校验失败，未安装: " + "; ".join(bad[:5]))
        # 事务日志：帧逐个落地与索引合并是两步，中间崩掉会留下「目录在、索引
        # 没有」的半装状态。先落日志（phase=place），全部放完改 phase=merge，
        # 合并成功才删 —— 启动期据此前滚，不必等下一次安装（终审）。
        journal = {"schema": 1, "archive": archive.stem,
                   "phase": "place", "part_doc": part_doc, "sidecars": {}}
        _write_journal(target_dir, journal)
        # 落地：按目录整体替换（先改名旧目录、新内容就位；旧目录**留到索引提交**）
        root_resolved = target_dir.resolve()
        staging_resolved = staging.resolve()
        old_dirs: list[Path] = []
        for clip, rel in zip(part_doc.get("clips", []), safe_dirs):
            src = (staging / rel).resolve()
            dst = (target_dir / rel).resolve()
            # resolve 之后再比"是不是在根目录里"——字符串 startswith 会被
            # `..` 和符号链接绕过；这里用路径组件级判断。
            if not src.is_relative_to(staging_resolved):
                raise DownloadError(f"分件路径越界（源）: {rel}")
            if not dst.is_relative_to(root_resolved):
                raise DownloadError(f"分件路径越界（目标）: {rel}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            # **先改名、后落地**（终审指出的空窗）：直接 rmtree 旧目录再 move，
            # 中途崩溃会让那段动画彻底消失。改成 dst → dst.__old__ → 新版就位
            # → 删 __old__；崩溃后由 `recover_target` 把 __old__ 改回去。
            old = dst.with_name(dst.name + OLD_SUFFIX)
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)
            if dst.exists():
                dst.rename(old)
            shutil.move(str(src), str(dst))
            # **旧目录留到索引提交之后才删**（终审 B4）：原来每段落地完立刻删
            # ``__old__``，一旦在合并索引前崩溃，phase=place 的回滚已无旧素材
            # 可退 —— 新内容在盘、索引还是旧的，两边对不上且不可恢复。
            if old.exists():
                old_dirs.append(old)
            # 把**已校验过**的内容摘要写进 meta：运行时采纳闸门据此确认
            # "磁盘上的帧就是索引声明的那批"（终审 B2）。写进 meta 而不是
            # 每次启动重算全量帧，避免 106 段 × 12MB 的启动读放大。
            meta_path = dst / "meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            if isinstance(meta, dict) and verify:
                # 只有**真校验过**才写凭证；verify=False 写"已校验"摘要会破坏
                # 凭证语义（终审 B2）。
                meta["content_sha"] = clip.get("content_sha")
                # **原子写**（终审 B4）：原地覆盖时若中断，meta.json 可能是半截
                # → 该段既不被采纳又带着新内容，恢复也无判据。
                meta_tmp = meta_path.with_name(meta_path.name + ".tmp")
                meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
                os.replace(meta_tmp, meta_path)
        # 单文件 sidecar 落地（manifest / 文字不镜像表 / 移动步幅）：与 clip 目录
        # **同提交同回滚**（终审：sidecar 不许留在事务外——clip 回滚旧版、sidecar
        # 留新版就是"新图配旧步幅"）。每放一个就把「实际放了、原本有没有旧文件」
        # 增量记进日志（八轮 P1：按计划名单回滚分不清"本次新放/刚恢复的旧文件/
        # 尚未触碰的旧文件"，会误删用户的旧 sidecar——只有 "new"（原本没有旧文件）
        # 才允许被回滚删除，"replaced" 由 ``__old__`` 备份恢复）。
        for name in ("manifest.json", "text_clips.json", "move_strides.json"):
            src = staging / name
            if not src.is_file():
                continue
            dst = target_dir / name
            old = dst.with_name(dst.name + OLD_SUFFIX)
            had_old = dst.exists()
            if old.exists():
                old.unlink()
            if had_old:
                dst.rename(old)
                old_dirs.append(old)      # 复用"提交后才清"的同一张表（文件也认）
            # **先记账、后落地**（九轮：落地后、记录前崩溃会让新 sidecar 既无备份
            # 又不在日志里 = 漏删）。登记在前，恢复按"有则删、缺则忍"处理，
            # 残余窗口只可能多删一个没放成的（无害），不可能漏删。
            journal["sidecars"][name] = "replaced" if had_old else "new"
            _write_journal(target_dir, journal)
            tmp = dst.with_name(dst.name + ".tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
        journal["phase"] = "merge"
        _write_journal(target_dir, journal)
        _merge_index(target_dir, part_doc)
        # 提交完成：此刻才允许清旧目录/旧 sidecar（它们是回滚依据）
        for old in old_dirs:
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
            else:
                old.unlink(missing_ok=True)
        try:
            (target_dir / JOURNAL_NAME).unlink()
        except OSError:
            pass
        # 安装凭证（对应 dsh 的 `.dsh-remote.json`）：更新检测与排查读它，
        # 不必回扫远端；它只记录"这一版是怎么来的"，不构成安装状态的第二份真相。
        try:
            receipt = target_dir / RECEIPT_NAME
            doc = {"part": archive.stem, "archive": archive.name,
                   "archive_sha256": _hash_file(archive),
                   "archive_bytes": archive.stat().st_size,
                   "clips": len(part_doc.get("clips", [])),
                   "installed_at": time.time()}
            tmp = receipt.with_name(receipt.name + ".tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, receipt)
        except OSError:
            logging.getLogger(__name__).warning('写安装凭证失败（不影响安装）', exc_info=True)
        return {"part": archive.stem, "clips": len(part_doc.get("clips", [])),
                "target": str(target_dir)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
