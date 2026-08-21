# -*- coding: utf-8 -*-
"""
配置持久化（跨平台）：
- Windows：%APPDATA%/dsh-pet-standalone/config.json
- macOS：~/Library/Application Support/dsh-pet-standalone/config.json
- Linux：~/.config/dsh-pet-standalone/config.json

记录：位置（相对屏幕可用区的中心比例，分辨率变化后仍正确）、
朝向、缩放、置顶开关。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import catalog


def _default_base() -> Path:
    """按平台返回配置根目录（Windows=APPDATA，macOS=Application Support，Linux=~/.config）。"""
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA') or Path.home())
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support'
    return Path.home() / '.config'


class Config:
    def __init__(self, base: Path | str | None = None) -> None:
        base = Path(base) if isinstance(base, str) else (base or _default_base())
        self.dir = base / 'dsh-pet-standalone'
        self.path = self.dir / 'config.json'
        self.data: dict = {
            'version': 2,  # 配置结构版本；scale 语义变更时递增
            'rx': None,    # 窗口中心 x / 屏幕可用区宽（None=默认右下角）
            'ry': None,    # 窗口中心 y / 屏幕可用区高
            'facing': 'left',
            'scale': catalog.DEFAULT_SCALE,
            'on_top': True,
            'no_move': False,  # 不移动：勾选后状态机不再自动移动，仅手动点移动动画才走动
            'character': catalog.DEFAULT_CHARACTER,  # 当前形象 ID
            'playback_speed': 1.0,       # 动画播放速率
            'mouse_through': False,        # 鼠标穿透
            'drag_physics': False,         # 拖动物理效果
        }
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            if int(raw.get('version', 1)) < 2:
                # v1 → v2：素材从 220×124 换成 640×360，scale 语义变化，
                # 旧 scale（如 1.0 表示 220px）需重置为新的默认值。
                raw.pop('scale', None)
                raw['version'] = 2
            for key in self.data:
                if key in raw and raw[key] is not None:
                    self.data[key] = raw[key]

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def save(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except OSError:
            pass  # 配置写失败不致命
