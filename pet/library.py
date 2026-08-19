# -*- coding: utf-8 -*-
"""
Media library —— 多形象 webm 主路线（无 GIF 回退）。

支持按角色 ID 加载不同形象：
- 默认从内置 assets/characters/<character_id>/videos/ 加载
- 也支持外部扩展目录（exe 同目录/用户数据目录下的 characters/<id>/videos）

对外保持与窗口层一致的形状：
- movie(name) -> clip object
- movies() -> name -> clip mapping
- frames(name) / duration(name)（秒）

WebMClip 基于 imageio-ffmpeg 解码 640×360 透明 webm（RGBA）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QObject

from . import catalog
from .webm_clip import WebMClip


class MovieLibrary(QObject):
    """素材库：加载指定形象的 webm 动画。"""

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
        self._movies: dict[str, WebMClip] = {}

        self._load_all()

    def _load_all(self) -> None:
        if self._manifest is None:
            # 自动扫描该形象目录下的所有 webm，支持不同角色有不同动作集
            if not self._asset_dir.is_dir():
                raise FileNotFoundError(
                    f"角色素材目录不存在: {self._asset_dir}（character_id={self.character_id}）"
                )
            files = sorted(self._asset_dir.rglob('*.webm'))
            if not files:
                raise FileNotFoundError(
                    f"角色素材目录中没有 webm 文件: {self._asset_dir}"
                )
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
            self._movies[name] = WebMClip(path, parent=self)

        # 后台并行预热元数据，不阻塞启动/切角色
        if self._movies:
            threading.Thread(target=self._warm_all_meta_background, daemon=True).start()

    def _warm_all_meta_background(self) -> None:
        try:
            workers = min(8, len(self._movies))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(lambda clip: clip.warm_meta(), list(self._movies.values())))
        except Exception:
            # 预热失败不致命，后续按需读取时会再尝试
            pass

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
