# -*- coding: utf-8 -*-
"""
Media library —— 多形象，自动识别 webm / gif。

支持按角色 ID 加载不同形象：
- 默认从内置 assets/characters/<character_id>/videos/ 加载
- 也支持外部扩展目录（exe 同目录/用户数据目录下的 characters/<id>/videos）
- 如果目录里是 *.webm 则用 WebMClip；如果是 *.gif 则用 GifClip

对外保持与窗口层一致的形状：
- movie(name) -> clip object
- movies() -> name -> clip mapping
- frames(name) / duration(name)（秒）

WebMClip 基于 imageio-ffmpeg 解码 640×360 透明 webm（RGBA）。
GifClip 基于 QMovie 播放透明 GIF（兼容旧 GIF 路线）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QMovie

from . import catalog
from .webm_clip import WebMClip


# QMovie 播放速度补偿（%）：GIF 路线使用，校准 QMovie 偏慢问题
PLAYBACK_SPEED = 120


class GifClip(QObject):
    """QMovie 包装：与 WebMClip 接口兼容的 GIF 播放器。"""

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self._movie = QMovie(str(path))
        self._movie.setCacheMode(QMovie.CacheMode.CacheNone)
        self._movie.setSpeed(PLAYBACK_SPEED)
        self._movie.frameChanged.connect(self._on_frame_changed)
        self._movie.finished.connect(self.finished)
        self._movie.error.connect(lambda err: self.errorOccurred.emit(str(err)))
        self._frame_count = 0
        self.playback_speed = 1.0
        self._movie.jumpToFrame(0)
        self._frame_count = max(0, self._movie.frameCount())

    def frameCount(self) -> int:
        if self._frame_count <= 0:
            self._frame_count = max(0, self._movie.frameCount())
        return max(1, self._frame_count)

    def duration(self) -> float:
        return self.frameCount() * catalog.FRAME_MS / 1000.0 / self.playback_speed

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

    def set_playback_speed(self, speed: float) -> None:
        self.playback_speed = max(0.1, float(speed))
        self._movie.setSpeed(int(round(PLAYBACK_SPEED * self.playback_speed)))

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

    def warm_meta(self) -> None:
        # GIF 由 QMovie 直接管理元数据，无需额外预热
        return

    def _on_frame_changed(self, n: int) -> None:
        fc = self._movie.frameCount()
        if fc > 0:
            self._frame_count = fc
        self.frameChanged.emit(n)


class MovieLibrary(QObject):
    """素材库：加载指定形象的 webm 或 gif 动画。"""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        character_id: str | None = None,
        asset_dir: Path | str | None = None,
        manifest: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.character_id = character_id or catalog.DEFAULT_CHARACTER
        if asset_dir is not None:
            self._asset_dir = Path(asset_dir)
        else:
            self._asset_dir = catalog.resolve_character_video_dir(self.character_id)
        self._manifest = None if manifest is None else dict(manifest)
        self.manifest = catalog.load_character_manifest(self.character_id, self._asset_dir)
        self.folder_map: dict[str, str] = {}
        self.folder_files: dict[str, list[str]] = {}
        self._movies: dict[str, object] = {}
        self.media_type: str = 'webm'

        self._load_all()

    def _load_all(self) -> None:
        if self._manifest is None:
            # 自动扫描该形象目录下的 webm 或 gif，支持不同角色有不同动作集
            if not self._asset_dir.is_dir():
                raise FileNotFoundError(
                    f"角色素材目录不存在: {self._asset_dir}（character_id={self.character_id}）"
                )
            webm_files = sorted(self._asset_dir.rglob('*.webm'))
            gif_files = sorted(self._asset_dir.rglob('*.gif'))
            files = webm_files + gif_files
            if not files:
                raise FileNotFoundError(
                    f"角色素材目录中没有 webm/gif 文件: {self._asset_dir}"
                )
            if webm_files and gif_files:
                self.media_type = 'mixed'
            elif webm_files:
                self.media_type = 'webm'
            else:
                self.media_type = 'gif'
            self._manifest = {}
            self.folder_map = {}
            self.folder_files = {}
            for f in files:
                rel = f.relative_to(self._asset_dir)
                name = f.stem
                self._manifest[name] = rel.as_posix()
                folder = rel.parts[0].lower() if len(rel.parts) > 1 else ''
                self.folder_map[name] = folder
                self.folder_files.setdefault(folder, []).append(name)

        missing: list[str] = []
        resolved: dict[str, Path] = {}
        for name, fname in self._manifest.items():
            path = self._asset_dir / fname
            if not path.exists():
                missing.append(f"{name}: {path}")
                continue
            resolved[name] = path

        if missing:
            raise FileNotFoundError("缺少素材文件: " + ", ".join(missing))

        for name, path in resolved.items():
            if path.suffix.lower() == '.gif':
                self._movies[name] = GifClip(path, parent=self)
            else:
                self._movies[name] = WebMClip(path, parent=self)

        # 后台并行预热元数据，不阻塞启动/切角色
        if self._movies:
            threading.Thread(target=self._warm_all_meta_background, daemon=True).start()

    def _warm_all_meta_background(self) -> None:
        try:
            workers = min(8, len(self._movies))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(lambda clip: clip.warm_meta(), list(self._movies.values())))
                # 预解码各动画首帧（QImage 线程安全），首次播放时零阻塞切换，
                # 避免点击 Q 弹瞬间同步 ffmpeg 解码造成卡顿与旧动画帧残留。
                list(ex.map(
                    lambda clip: getattr(clip, 'warm_first_frame', lambda: None)(),
                    list(self._movies.values()),
                ))
        except Exception:
            # 预热失败不致命，后续按需读取时会再尝试
            pass

    def movie(self, name: str):
        return self._movies[name]

    def frames(self, name: str) -> int:
        return self._movies[name].frameCount()

    def duration(self, name: str) -> float:
        return self._movies[name].duration()

    def names(self) -> list[str]:
        return list(self._movies.keys())

    def movies(self) -> dict[str, object]:
        """Name -> clip mapping for window wiring."""
        return dict(self._movies)
