"""动图控件：``QMovie`` 的帧按屏幕 DPR 解码，绘制时 1:1 贴物理像素。

`QMovie` 出的 ``QPixmap`` 的 ``devicePixelRatio`` 恒为 1，所以不能直接
``QLabel.setMovie()``：在 1.5 倍屏上会被 Qt 当成逻辑像素再放大一次（发虚）。
这里沿用 `media/images.py` 的口径——逻辑尺寸不动，帧按
``逻辑 × screen_dpr`` 解码，绘制前写回 DPR，``paintEvent`` 里就是 1:1 贴图。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QMovie, QPainter
from PySide6.QtWidgets import QWidget

from ..media.images import logical_size_for, sanitize_dpr, screen_dpr

#: 读不出图片时的占位尺寸（有最小尺寸的控件不能被 setFixedSize(0, 0)）。
PLACEHOLDER_SIZE = (1, 1)


class AnimatedImage(QWidget):
    """按路径播放动图，逻辑尺寸决定版面，物理像素跟随屏幕 DPR。"""

    def __init__(
        self,
        path: Path | str,
        max_side: int,
        parent: QWidget | None = None,
        *,
        dpr: float | None = None,
    ):
        super().__init__(parent)
        # 与静态图的 QLabel 同口径：点击要能落到气泡/弹窗上。
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._max_side = max(1, int(max_side))
        #: 调用方显式给了 dpr（弹窗/气泡按宿主屏算好的）就不再跟随窗口重算。
        self._explicit_dpr = dpr is not None
        self._dpr = sanitize_dpr(dpr if dpr is not None else screen_dpr(parent))
        self._path = Path(path)
        self._logical: tuple[int, int] = PLACEHOLDER_SIZE
        self._movie: QMovie | None = None
        self._movie_path = ""
        self._build()

    # ---------------------------------------------------------------- 查询
    @property
    def movie_path(self) -> str:
        """当前 movie 装载的文件（空串 = 已释放）。"""
        return self._movie_path

    @property
    def is_running(self) -> bool:
        movie = self._movie
        if movie is None:
            return False
        try:
            return movie.state() == QMovie.MovieState.Running
        except RuntimeError:
            return False

    def movie(self) -> QMovie | None:
        """底层 ``QMovie``（诊断/测试用；窗口销毁后为 None）。"""
        return self._movie

    def logical_size(self) -> tuple[int, int]:
        return self._logical

    def device_pixel_ratio(self) -> float:
        return self._dpr

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(*self._logical)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(*self._logical)

    # ---------------------------------------------------------------- 播放
    def start(self) -> None:
        movie = self._movie
        if movie is None:
            return
        movie.start()
        self.update()

    def stop(self) -> None:
        movie = self._movie
        if movie is None:
            return
        movie.stop()
        self.update()

    # ---------------------------------------------------------------- 换图
    def set_path(self, path: Path | str) -> None:
        """换一张图；同一路径重复设置直接复用现有 movie。"""
        target = Path(path)
        if self._movie is not None and str(target) == self._movie_path:
            return
        self._release_movie()
        self._path = target
        self._build()

    def set_max_side(self, max_side: int) -> None:
        """改显示上限（``image_scale`` 变了）：重算逻辑尺寸并重建 movie。"""
        limit = max(1, int(max_side))
        if limit == self._max_side:
            return
        self._max_side = limit
        self._release_movie()
        self._build()

    # ---------------------------------------------------------------- 内部
    def _physical_size(self) -> QSize:
        width, height = self._logical
        return QSize(
            max(1, int(round(width * self._dpr))),
            max(1, int(round(height * self._dpr))),
        )

    def _build(self) -> None:
        size = logical_size_for(self._path, self._max_side)
        self._logical = size if size is not None else PLACEHOLDER_SIZE
        self.setFixedSize(*self._logical)
        movie = QMovie(str(self._path))
        movie.setParent(self)
        movie.setCacheMode(QMovie.CacheMode.CacheNone)
        movie.setScaledSize(self._physical_size())
        movie.frameChanged.connect(self._on_frame)
        # 预热首帧：没起播前也有画面可画，避免第一拍闪空。
        movie.jumpToFrame(0)
        self._movie = movie
        self._movie_path = str(self._path)
        self.update()

    def _release_movie(self) -> None:
        movie = self._movie
        self._movie = None
        self._movie_path = ""
        if movie is None:
            return
        try:
            movie.frameChanged.disconnect(self._on_frame)
        except (RuntimeError, TypeError):
            pass
        try:
            movie.stop()
        except RuntimeError:
            return
        movie.deleteLater()

    def _sync_dpr(self) -> None:
        """窗口落到另一块屏（DPR 不同）时按新比例重新出帧。

        显式传入 ``dpr`` 的调用方（气泡/弹窗按宿主屏算好了）由调用方负责，
        这里不覆盖，否则会把人给的 1.5 倍改回当前窗口的 1.0。
        """
        movie = self._movie
        if movie is None or self._explicit_dpr:
            return
        current = sanitize_dpr(screen_dpr(self))
        if current == self._dpr:
            return
        self._dpr = current
        movie.setScaledSize(self._physical_size())
        self.update()

    def _on_frame(self, frame: int) -> None:
        del frame
        self.update()

    # ---------------------------------------------------------------- 事件
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        del event
        movie = self._movie
        if movie is None:
            return
        pixmap = movie.currentPixmap()
        if pixmap is None or pixmap.isNull():
            return
        pixmap.setDevicePixelRatio(self._dpr)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self._sync_dpr()
        self.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 窗口销毁前停播并断开连接，避免 QMovie 在宿主消失后继续解码。
        self._release_movie()
        super().closeEvent(event)
