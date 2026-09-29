"""Core bindings for individual data files. No caller-controlled feature paths."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .feature_ports import FeatureDocumentPort, FeatureStateDocumentPort


@dataclass(frozen=True, slots=True)
class _BoundDocument:
    read: Callable[[], dict]
    write: Callable[[dict], None]
    clear: Callable[[], None]


def bind_feature_document(path: Path) -> FeatureDocumentPort:
    """Only Core calls this factory; returned port does not expose a path."""
    path = Path(path)

    def read() -> dict:
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    def write(value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def clear() -> None:
        if path.is_file():
            path.unlink()

    return _BoundDocument(read, write, clear)


@dataclass(frozen=True, slots=True)
class _BoundStateDocument:
    read: Callable[[], dict]
    write: Callable[[dict], None]
    clear: Callable[[], None]
    locked: Callable[[], AbstractContextManager[None]]


def bind_feature_state_document(path: Path) -> FeatureStateDocumentPort:
    """Keep the limiter's existing bounded/best-effort lock and PID temp policy.

    This compatibility adapter does not strengthen the historic lock semantics;
    callers cannot choose another document, lock or temporary file.
    """
    path = Path(path)
    document = bind_feature_document(path)

    def write(value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @contextlib.contextmanager
    def locked():
        """跨进程互斥（多开共用一份频控状态）：Windows 用 msvcrt，POSIX 用 flock。

        锁文件随 state_path 派生；拿不到锁时静默降级为无锁（读改写竞态退化为
        极少数情况下的计数偏差，不影响单实例正确性）。
        """
        fh = None
        try:
            fh = open(path.with_suffix(path.suffix + ".lock"), "a+b")
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)  # append 模式初始位置在 EOF，锁/解锁必须落在同一字节
                # 非阻塞+短重试：allow/try_acquire 会在 GUI 线程（_on_frame_ready）调用，
                # 不能用 LK_LOCK 的 ~10s 阻塞重试；锁持有时间是微秒级，100ms 内必拿到，
                # 拿不到则降级无锁（竞态退化为计数偏差，不影响正确性主线）。
                for _ in range(5):
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.02)
                else:
                    fh.close()
                    fh = None
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            if fh is not None:
                fh.close()
                fh = None
        try:
            yield
        finally:
            if fh is not None:
                try:
                    if sys.platform == "win32":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                fh.close()

    return _BoundStateDocument(document.read, write, document.clear, locked)
