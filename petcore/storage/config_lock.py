"""跨进程配置写锁：CLI 与应用同时写 config.json 时的短临界区。

只锁一个固定字节，不锁整个配置；调用方在锁内重读、修改、整份原子替换。
Windows 用 ``msvcrt.locking``，POSIX 用 ``fcntl.flock``。
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = ".settings-write.lock"


def lock_path(root: Path | str) -> Path:
    return Path(root) / LOCK_NAME


@contextmanager
def config_write_lock(root: Path | str, *, timeout: float = 5.0):
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    deadline = time.monotonic() + max(0.05, float(timeout))
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("配置文件正被另一个进程写入") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("配置文件正被另一个进程写入") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


__all__ = ["config_write_lock", "lock_path", "LOCK_NAME"]
