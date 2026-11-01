"""下载进度显示（设置页共用）。

市场页（插件市场）与「常规 → 素材包下载」用的是同一套口径：2 秒滑动窗口算速度、
``已下载 / 总量 · 速度 · 剩余 · 连接数``，取消按钮只发信号、不动任务对象。
抽出来是为了两处不会各写一份「看起来像」的进度文案。
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from .settings.base import make_button


def format_bytes(value: float) -> str:
    size = float(max(0.0, value))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


class SpeedMeter:
    """把 ``(done, total)`` 采样换算成速度与 ETA（只按最近几秒算）。"""

    def __init__(self, *, window: float = 2.0) -> None:
        self._window = max(0.2, float(window))
        self._samples: list[tuple[float, int]] = []

    def reset(self) -> None:
        self._samples.clear()

    @property
    def samples(self) -> int:
        return len(self._samples)

    def update(self, done: int, total: int) -> tuple[float, float | None]:
        now = time.monotonic()
        self._samples.append((now, int(done)))
        cutoff = now - self._window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.pop(0)
        if len(self._samples) < 2:
            return 0.0, None
        first_time, first_done = self._samples[0]
        elapsed = now - first_time
        speed = (int(done) - first_done) / elapsed if elapsed > 0.05 else 0.0
        speed = max(0.0, speed)
        remaining = max(0, int(total) - int(done))
        eta = remaining / speed if speed > 0 else None
        return speed, eta


def describe_progress(
    *,
    done: int,
    total: int,
    speed: float,
    eta: float | None,
    connections: int,
    resumed: int = 0,
    show_connections: bool = True,
) -> str:
    """进度行文本：已下载/总量 · 速度 · 剩余 · 连接数（可选续传量）。"""
    parts = [f"{format_bytes(done)} / {format_bytes(total)}"]
    if speed > 0:
        parts.append(f"{format_bytes(speed)}/s")
        if eta is not None:
            parts.append(f"剩余 {max(0, int(round(eta)))} 秒")
    else:
        parts.append("正在连接…")
    if resumed > 0:
        parts.append(f"续传 {format_bytes(resumed)}")
    if show_connections:
        parts.append(f"{max(1, int(connections))} 连接")
    return " · ".join(parts)


class DownloadPanel(QWidget):
    """进度条 + 进度文案 + 状态行 +（可选）取消按钮。

    默认整块隐藏：没有下载时卡片不会多出一段空白。
    """

    cancel_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        with_cancel: bool = False,
        object_name: str = "marketProgress",
        accessible_name: str = "下载进度",
    ) -> None:
        super().__init__(parent)
        self._meter = SpeedMeter()
        self._total = 0
        self._connections = 1
        self._resumed = 0
        self._show_connections = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setObjectName(object_name)
        self.progress_bar.setAccessibleName(accessible_name)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.progress_label = QLabel("", self)
        self.progress_label.setObjectName("settingHint")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)
        self.status_label = QLabel("", self)
        self.status_label.setObjectName("settingHint")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.cancel_button = make_button("取消", self) if with_cancel else None
        if self.cancel_button is not None:
            self.cancel_button.setToolTip("取消正在进行的下载；已下载的字节会保留，下次续传")
            self.cancel_button.clicked.connect(self.cancel_requested.emit)
            layout.addWidget(self.cancel_button)
        self.hide()

    # ------------------------------------------------------------ 状态
    def begin(
        self,
        total: int,
        *,
        connections: int = 1,
        resumed: int = 0,
        show_connections: bool = True,
    ) -> None:
        self._meter.reset()
        self._total = max(0, int(total or 0))
        self._connections = max(1, int(connections))
        self._resumed = max(0, int(resumed))
        self._show_connections = bool(show_connections)
        self.progress_bar.setRange(0, max(1, self._total))
        self.progress_bar.setValue(0)
        self.progress_label.setText("")
        self.status_label.setText("")
        self.show()

    def update_progress(self, done: int, total: int) -> None:
        total = max(1, int(total or self._total or 1))
        self._total = total
        done = max(0, min(int(done), total))
        speed, eta = self._meter.update(done, total)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)
        self.progress_label.setText(
            describe_progress(
                done=done,
                total=total,
                speed=speed,
                eta=eta,
                connections=self._connections,
                resumed=self._resumed,
                show_connections=self._show_connections,
            )
        )

    def set_status(self, text: str) -> None:
        self.status_label.setText(str(text or ""))

    def finish(self, text: str = "") -> None:
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.progress_label.setText("")
        if text:
            self.set_status(text)

    def fail(self, text: str) -> None:
        self.progress_label.setText("")
        self.set_status(str(text or ""))

    def reset(self) -> None:
        self._meter.reset()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_label.setText("")
        self.status_label.setText("")
        if self.cancel_button is not None:
            self.cancel_button.setEnabled(True)
        self.hide()


def make_progress_callback(
    panel: DownloadPanel,
) -> Callable[[int, int], None]:
    """把引擎的 ``progress(done, total)`` 直接接到面板上。"""

    def report(done: int, total: int) -> None:
        panel.update_progress(done, total)

    return report
