"""数据/缓存目录搬迁的后台任务。

与 ``credits_job`` 同款：守护线程 + 信号回 GUI，任务对象留在模块级集合里直到
线程结束（避免「C++ 对象已删除还在 emit」）。搬迁可能持续几十秒，
必须能取消，且**只有校验通过才写引导指针**——写到一半就改指针等于把用户数据丢在
一个没人指向的目录里。
"""

from __future__ import annotations

import logging
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QCoreApplication, QObject, Signal

from ...storage import roots as storage_roots

logger = logging.getLogger("petcore.ui.settings.roots_job")

#: 进度信号节流：10 Hz 足够平滑，又不会把 GUI 线程刷满。
PROGRESS_INTERVAL_S = 0.1

_LIVE: set["RootsMoveJob"] = set()


@dataclass(frozen=True, slots=True)
class MoveOutcome:
    kind: str
    source: Path
    target: Path
    files: int
    bytes: int
    skipped: tuple[str, ...] = ()
    elapsed: float = 0.0


class RootsMoveJob(QObject):
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        *,
        source: Path,
        target: Path,
        pointer_path: Path,
        pointer_after: storage_roots.Pointer,
        kind: str = "data",
        include: Sequence[str] | None = None,
    ) -> None:
        super().__init__()
        self._source = Path(source)
        self._target = Path(target)
        self._pointer_path = Path(pointer_path)
        self._pointer_after = pointer_after
        self._kind = str(kind)
        self._include = tuple(include) if include is not None else None
        self._cancelled = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._thread is not None:
            return
        _LIVE.add(self)
        self._thread = threading.Thread(
            target=self._run, name="petcore-roots-move", daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancelled = True
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for name in ("progress", "done", "failed", "cancelled"):
                try:
                    getattr(self, name).disconnect()
                except (RuntimeError, TypeError):
                    pass

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_cancelled(self) -> bool:
        return self._cancelled

    # ------------------------------------------------------------ 实现
    def _emit_progress(self, done: int, total: int) -> None:
        if self._cancelled or QCoreApplication.instance() is None:
            return
        self.progress.emit(int(done), int(total))

    def _run(self) -> None:
        started = time.perf_counter()
        try:
            plan = storage_roots.plan_move(self._source, include=self._include)
            plan = storage_roots.with_target(plan, self._target)
            last = [0.0, -1]

            def report(done: int, total: int) -> None:
                now = time.monotonic()
                if now - last[0] < PROGRESS_INTERVAL_S and done < total:
                    return
                last[0] = now
                self._emit_progress(done, total)

            copied = storage_roots.copy_tree(
                plan, progress=report, cancel=self.is_cancelled
            )
            ok, detail = storage_roots.verify_copy(plan)
            if not ok:
                raise storage_roots.RootsError(f"校验未通过：{detail}")
            storage_roots.write_pointer(self._pointer_path, self._pointer_after)
            storage_roots.write_journal(
                self._target,
                old_root=self._source,
                pointer_path=self._pointer_path,
                kind=self._kind,
            )
            outcome = MoveOutcome(
                kind=self._kind,
                source=self._source,
                target=self._target,
                files=copied.entries,
                bytes=copied.bytes_written,
                skipped=plan.skipped,
                elapsed=time.perf_counter() - started,
            )
        except storage_roots.MoveCancelled:
            logger.info("目录搬迁已取消：%s", self._kind)
            if QCoreApplication.instance() is not None:
                self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - 线程里的异常必须变成信号
            logger.warning("目录搬迁失败（%s）：%s", self._kind, type(exc).__name__)
            if not self._cancelled and QCoreApplication.instance() is not None:
                message = (
                    str(exc)
                    if isinstance(exc, storage_roots.RootsError)
                    else f"{type(exc).__name__}：{exc}"
                )
                self.failed.emit(message)
        else:
            if not self._cancelled and QCoreApplication.instance() is not None:
                self.done.emit(outcome)
        finally:
            self._thread = None
            _LIVE.discard(self)


def cancel_all(timeout: float = 0.5) -> None:
    """关闭设置窗时统一取消；最多等 ``timeout`` 秒。"""
    for job in list(_LIVE):
        job.cancel()
    for job in list(_LIVE):
        thread = job._thread
        if thread is None or thread is threading.current_thread():
            continue
        try:
            thread.join(max(0.0, float(timeout)))
        except RuntimeError:
            continue
