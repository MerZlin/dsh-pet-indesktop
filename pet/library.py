# -*- coding: utf-8 -*-
"""
Media library —— webm 主路线（无 GIF 回退）。

对外保持与窗口层一致的形状：
- movie(name) -> clip object
- movies() -> name -> clip mapping
- frames(name) / duration(name)（秒）

WebMClip 基于 imageio-ffmpeg 解码 640×360 透明 webm（RGBA）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QObject

from . import catalog
from .webm_clip import WebMClip


class MovieLibrary(QObject):
    """素材库：加载全部 webm 动画。"""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        asset_dir: Path | str | None = None,
        manifest: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_dir = Path(asset_dir) if asset_dir is not None else catalog.webm_dir()
        self._manifest = dict(manifest or catalog.ANIM_FILES)
        self._movies: dict[str, WebMClip] = {}

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
            self._movies[name] = WebMClip(path, parent=self)

    def movie(self, name: str) -> WebMClip:
        return self._movies[name]

    def frames(self, name: str) -> int:
        return self._movies[name].frameCount()

    def duration(self, name: str) -> float:
        return self._movies[name].duration()

    def names(self) -> list[str]:
        return list(self._movies.keys())

    def movies(self) -> dict[str, WebMClip]:
        """Name -> clip mapping for window wiring."""
        return dict(self._movies)
