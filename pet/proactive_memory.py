# -*- coding: utf-8 -*-
"""主动识屏短期陪伴记忆 — ProactiveMemory。

批6-1 从 proactive.py 整体迁出（纯搬移，逻辑/默认值/时序零改动）：
- 存储文件：<config.dir>/proactive_screen_memory.json；
- 仅记录元数据（时间戳、进程名、活动分类），绝不保存截图；
- 最多保留 max_entries（默认 20 条），新记录置于头部，尾部自动截断；
- 采用 .tmp + 原子替换持久化；损坏回退空列表。

issue #196 / #204 收口（2026-10-01）：原实现是「读—改—写」全程无锁 + 固定
``.json.tmp`` 名 + 无 fsync + 失败静默；而多开（``instance_launcher`` 为每个副窗
起独立进程）是默认路径、两个进程都启用识屏，于是并发写互相覆盖、记录成批丢失
且无任何日志。现在：
- ``record()`` / ``clear()`` 的读改写整体进跨进程锁（Windows msvcrt / POSIX flock），
  锁文件与记忆文件同目录同前缀；
- tmp 名带 pid + 线程 id，两个写者不再写同一个临时文件；
- flush + fsync（含父目录 fsync，Windows 无该语义则跳过），崩溃/断电不再整块丢；
- ``os.replace`` 遇瞬时占用（Windows 杀软/索引，WinError 5/32）有界退避重试；
- 写失败与损坏回退都记 warning，不再用沉默顶替结论。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

# 抢锁的非阻塞重试预算（合计约 1.26s）：record() 可能在 GUI 线程被调用，不能无限
# 等；但持锁方只做一次小文件落盘（实测毫秒级），拿不到锁说明真的异常，此时记
# warning 后无锁继续——宁可留下可诊断的日志，也不静默丢本次记录。
_LOCK_RETRY_DELAYS = (0.02, 0.04, 0.08, 0.16, 0.32, 0.64)

# os.replace 的退避重试（与 pet/chat/session_store.py::_REPLACE_RETRY_DELAYS 同口径）：
# Windows 上杀软/索引服务会短暂占用刚落盘的文件，属可自愈的瞬时错误。
_REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def _replace_with_retry(temp: Path, target: Path) -> None:
    """``os.replace`` 带界退避重试：只重试瞬时共享冲突，真实失败照常上抛。"""
    for delay in _REPLACE_RETRY_DELAYS:
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(temp, target)


def _fsync_dir(folder: Path) -> None:
    """保证 rename 的目录项持久化；Windows 无目录 fsync 语义，直接跳过。"""
    if os.name == "nt":
        return
    fd = os.open(str(folder), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ProactiveMemory:
    """主动识屏短期陪伴记忆管理器。

    存储文件：<config.dir>/proactive_screen_memory.json
    - 仅记录元数据（时间戳、进程名、活动分类），绝不保存截图，敏感内容不入盘；
    - 最多保留 max_entries（默认 20 条），新记录置于头部，尾部自动截断；
    - 采用「跨进程锁 + 唯一 tmp + fsync + 原子替换」持久化；损坏回退空列表。
    """

    def __init__(
        self,
        path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        max_entries: int = 20,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self.max_entries = max(1, max_entries)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def load(self) -> list[dict[str, Any]]:
        """读取记忆列表（按时间倒序，最新在最前）；损坏/读失败回退空列表。"""
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                return raw["entries"]
            logger.warning("主动记忆结构异常，按空列表处理: %s", self.path)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("主动记忆读取失败，按空列表处理: %s (%s)", self.path, exc)
        return []

    def latest(self) -> dict[str, Any] | None:
        """获取最近一条记忆项。"""
        entries = self.load()
        return entries[0] if entries else None

    def record(self, process: str, title: str, activity: str) -> None:
        """记录一条新的陪伴活动记忆。

        注意：title 参数仅用于保持调用签名兼容，**不会落盘**——窗口标题可能含
        文档名/网页标题等敏感信息，记忆只保留进程名与活动分类。"""
        with self._locked():
            entries = self.load()
            new_item = {
                "ts": self._clock(),
                "process": str(process or "").strip(),
                "activity": str(activity or "").strip(),
            }
            entries.insert(0, new_item)
            entries = entries[: self.max_entries]
            self._write_entries(entries)

    def clear(self) -> None:
        """清空陪伴记忆（与 record 同一把锁，避免清空与落盘互相覆盖）。"""
        with self._locked():
            try:
                if self.path.is_file():
                    self.path.unlink()
            except OSError as exc:
                logger.warning("主动记忆清空失败: %s (%s)", self.path, exc)

    def _temp_path(self) -> Path:
        """写盘用的临时文件路径：带 pid + 线程 id，避免两个写者写串同一文件。"""
        return self.path.with_suffix(f".json.tmp-{os.getpid()}-{threading.get_ident():x}")

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """唯一 tmp + flush/fsync + 原子替换落盘；失败记 warning 且不留半成品。"""
        payload = json.dumps({"entries": entries}, ensure_ascii=False, indent=2)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("主动记忆目录不可用: %s (%s)", self.path.parent, exc)
            return
        temp = self._temp_path()
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_with_retry(temp, self.path)
        except OSError as exc:
            logger.warning("主动记忆写盘失败: %s (%s)", self.path, exc)
            return
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            _fsync_dir(self.path.parent)
        except OSError as exc:
            # 数据已落盘，只是目录项未持久化：如实记，但不冒充写失败。
            logger.warning("主动记忆目录 fsync 失败（内容已落盘）: %s (%s)", self.path.parent, exc)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """跨进程互斥（多开共用一份记忆文件）：Windows 用 msvcrt，POSIX 用 flock。

        与 pet/proactive_limiter.py 的锁同族（锁文件随数据文件派生、同进程/跨进程
        都生效）。区别在降级口径：频控计数允许偏差，记忆丢写不允许——所以拿不到
        锁时记 warning 再无锁继续，让「锁没拿到」这件事在日志里可诊断。
        """
        handle = None
        try:
            handle = open(self._lock_path, "a+b")
            if sys.platform == "win32":
                import msvcrt
                acquired = False
                for delay in _LOCK_RETRY_DELAYS:
                    try:
                        handle.seek(0)  # append 模式初始位置在 EOF，锁/解锁必须落在同一字节
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                        break
                    except OSError:
                        time.sleep(delay)
                if not acquired:
                    logger.warning("主动记忆锁被占用，本轮无锁继续: %s", self._lock_path)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            if handle is not None:
                handle.close()
                handle = None
            logger.warning("主动记忆锁不可用，本轮无锁继续: %s (%s)", self._lock_path, exc)
        try:
            yield
        finally:
            if handle is not None:
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                handle.close()
