# -*- coding: utf-8 -*-
"""
QMovie-backed clip library（GIF 主路线，零额外依赖）。

对外保持与窗口层一致的形状：
- movie(name) -> clip object
- movies() -> name -> clip mapping
- frames(name) / duration(name)（秒）

QMovieClip 包装 QMovie，补齐 start/stop/jumpToFrame/currentPixmap/
currentFrameNumber/currentTimeSeconds/frameChanged/finished/errorOccurred；
时间统一为秒（QMovie 无 duration()，按 帧数 × FRAME_MS 换算）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QMovie

from . import catalog

# QMovie 播放速度补偿（%）：实测 QMovie 每帧比 GIF 原生延迟慢约 15ms，
# 120% 恰好校准回原生 80ms/帧；如更换素材帧率可调整该常量。
PLAYBACK_SPEED = 120


class QMovieClip(QObject):
    """QMovie 包装：播放接口与窗口层兼容。"""

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._movie = QMovie(str(path))
        self._movie.setCacheMode(QMovie.CacheMode.CacheNone)
        # QMovie 定时器+解码开销实测约 +15ms/帧（80ms 帧实际 ~95ms），
        # setSpeed(120) 校准回 GIF 原生 80ms/帧（实测 79.6ms）
        self._movie.setSpeed(PLAYBACK_SPEED)
        self._movie.frameChanged.connect(self._on_frame_changed)
        self._movie.finished.connect(self.finished)
        self._movie.error.connect(lambda err: self.errorOccurred.emit(str(err)))
        self._frame_count = 0
        # 首帧预解码：确定帧数 + 防切换空白
        self._movie.jumpToFrame(0)
        self._frame_count = max(0, self._movie.frameCount())

    # ------------------------------------------------------------ metadata
    def frameCount(self) -> int:
        if self._frame_count <= 0:
            self._frame_count = max(0, self._movie.frameCount())
        return max(1, self._frame_count)

    def duration(self) -> float:
        """总时长（秒）= 帧数 × FRAME_MS。"""
        return self.frameCount() * catalog.FRAME_MS / 1000.0

    def currentFrameNumber(self) -> int:
        return self._movie.currentFrameNumber()

    def currentTimeSeconds(self) -> float:
        n = self._movie.currentFrameNumber()
        frames = self.frameCount()
        if frames <= 0:
            return 0.0
        return n * (self.duration() / frames)

    def currentPixmap(self):
        return self._movie.currentPixmap()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._movie.start()

    def stop(self) -> None:
        self._movie.stop()

    def jumpToFrame(self, frame_index: int) -> bool:
        if frame_index < 0:
            frame_index = 0
        total = self._movie.frameCount()
        if total > 0 and frame_index >= total:
            frame_index = total - 1
        return self._movie.jumpToFrame(frame_index)

    # ------------------------------------------------------------ internals
    def _on_frame_changed(self, n: int) -> None:
        fc = self._movie.frameCount()
        if fc > 0:
            self._frame_count = fc
        self.frameChanged.emit(n)


class MovieLibrary(QObject):
    """GIF 素材库：预加载全部 QMovie（CacheNone + 首帧预解码）。"""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        asset_dir: Path | str | None = None,
        manifest: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_dir = Path(asset_dir) if asset_dir is not None else catalog.assets_dir()
        self._manifest = dict(manifest or catalog.ANIM_FILES)
        self._movies: dict[str, QMovieClip] = {}

        self._load_all()

    def _load_all(self) -> None:
        missing: list[str] = []
        resolved: dict[str, Path] = {}
        for name, fname in self._manifest.items():
            path = catalog.resolve_asset_path(name, fname, base_dir=self._asset_dir)
            if not path.exists():
                missing.append(f"{name}: {path}")
                continue
            resolved[name] = path

        if missing:
            raise FileNotFoundError("缺少素材文件: " + ", ".join(missing))

        for name, path in resolved.items():
            self._movies[name] = QMovieClip(path, parent=self)

    def movie(self, name: str) -> QMovieClip:
        return self._movies[name]

    def frames(self, name: str) -> int:
        return self._movies[name].frameCount()

    def duration(self, name: str) -> float:
        return self._movies[name].duration()

    def names(self) -> list[str]:
        return list(self._movies.keys())

    def movies(self) -> dict[str, QMovieClip]:
        """Name -> clip mapping for window wiring."""
        return dict(self._movies)