"""Bounded local metadata I/O; kernel contention is not a filesystem failure."""

from __future__ import annotations

import errno
import os
import stat
import sys
import uuid
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator


class StateError(Exception):
    """Safe reason code only: never include document contents or OS paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def safe_path(path: Path) -> None:
    """Reject links/reparse points in existing ancestors and hardlinked metadata."""
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise StateError("unsafe_path")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise StateError("unsafe_path")
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise StateError("io_error")


def read_bytes(path: Path, limit: int) -> bytes:
    safe_path(path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise StateError("metadata_limit")
    return data


def atomic_write(path: Path, data: bytes) -> None:
    """Flush a same-directory exclusive temporary file, then replace atomically.

    Only our temporary file is removed on failure. This is not a promise of
    power-loss durability on every filesystem; state receipts support recovery.
    """
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def _windows_lock() -> Callable[[int], None]:
    # Configure once, lazily: ctypes caches POINTER types, so defining the
    # structure on every query would retain a new native type per lock call.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("internal", ctypes.c_size_t),
            ("internal_high", ctypes.c_size_t),
            ("offset", wintypes.DWORD),
            ("offset_high", wintypes.DWORD),
            ("event", wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    acquire = kernel.LockFileEx
    acquire.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    acquire.restype = wintypes.BOOL

    def lock(fd: int) -> None:
        # Synchronous handle, immediate exclusive lock. Locking beyond EOF is OK.
        if not acquire(msvcrt.get_osfhandle(fd), 3, 0, 1, 0, ctypes.byref(Overlapped())):
            if ctypes.get_last_error() == 33:  # ERROR_LOCK_VIOLATION, not ACCESS_DENIED
                raise StateError("lock_busy")
            raise StateError("io_error")

    return lock


def _lock(fd: int) -> None:
    if sys.platform == "win32":
        _windows_lock()(fd)
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise StateError("lock_busy") from None
            raise StateError("io_error") from None


class KernelLock:
    """One non-blocking OS lock held until :meth:`close` is called.

    The file is deliberately separate from the JSON record.  Closing the file
    releases the kernel lock even when a process dies; the record is only
    diagnostic evidence and is never treated as liveness proof.
    """

    def __init__(self, path: Path, fd: int):
        self.path = path
        self._fd: int | None = fd

    @property
    def closed(self) -> bool:
        return self._fd is None

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> "KernelLock":
        if self.closed:
            raise StateError("lock_closed")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def open_kernel_lock(path: Path, *, create: bool = True) -> KernelLock:
    """Acquire an exclusive non-blocking lock and keep it until closed."""
    fd = None
    try:
        safe_path(path)
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            safe_path(path)
        try:
            fd = os.open(path, os.O_RDWR | (os.O_CREAT if create else 0), 0o600)
        except FileNotFoundError:
            if create:
                raise
            raise StateError("missing") from None
        os.set_inheritable(fd, False)
        safe_path(path)
        _lock(fd)
        return KernelLock(path, fd)
    except StateError:
        if fd is not None:
            os.close(fd)
        raise
    except OSError:
        if fd is not None:
            os.close(fd)
        raise StateError("io_error") from None


@contextmanager
def state_lock(path: Path, *, create: bool = True) -> Iterator[None]:
    """Nonblocking metadata coordinator; this is NOT a version lease."""
    try:
        lock = open_kernel_lock(path, create=create)
    except StateError as exc:
        if not create and exc.code == "missing":
            yield
            return
        raise
    try:
        yield
    finally:
        lock.close()
