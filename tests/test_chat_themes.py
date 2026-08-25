# -*- coding: utf-8 -*-
"""聊天背景主题注册表完整性 + 解析。"""

import re
from pathlib import Path

from pet.chat.themes import ANCHOR_RATIO, THEMES, build_overlay_qss, theme_names

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'chat'


def test_every_theme_has_art_file():
    for key, theme in THEMES.items():
        assert (ASSETS / theme['file']).is_file(), f'{key} 缺壁纸 {theme["file"]}'


def test_theme_fields_valid():
    for key, theme in THEMES.items():
        assert theme['anchor'] in ANCHOR_RATIO, key
        assert re.fullmatch(r'#[0-9a-fA-F]{6}', theme['accent']), key
        assert len(theme['scrim']) == 4 and all(0 <= c <= 255 for c in theme['scrim']), key
        assert theme['name'], key


def test_overlay_matches_dark_mode():
    dark = next(t for t in THEMES.values() if t['dark'])
    light = next(t for t in THEMES.values() if not t['dark'])
    assert '#e8ecf8' in build_overlay_qss(dark)      # 暗色面板要有亮文字
    assert '#e8ecf8' not in build_overlay_qss(light)
    assert dark['accent'] in build_overlay_qss(dark)  # accent 注入模板


def test_theme_names_unique_and_nonempty():
    keys = [k for k, _ in theme_names()]
    assert len(keys) == len(set(keys)) == len(THEMES)


def test_all_builtin_themes_resolve():
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config
    import tempfile

    app = QApplication.instance() or QApplication([])
    for key in THEMES:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(tmp)
            cfg.set('chat_background', f'builtin:{key}')
            win = ChatWindow(cfg, 'shenshen')
            assert win._bg_pixmap is not None and not win._bg_pixmap.isNull(), key
            assert win._bg_theme['accent'] == THEMES[key]['accent']
            win.close()


def test_clamp_box_keeps_inside_and_aspect():
    from pet.chat.crop_dialog import VIEW_ASPECT, clamp_box

    # 越界夹回
    x, y, w, h = clamp_box(-0.5, -0.5, 0.5, 16 / 9)
    assert x >= 0 and y >= 0 and x + w <= 1.0 and y + h <= 1.0
    # 纵横比保持（图像素坐标下 = VIEW_ASPECT）
    assert abs((w * (16 / 9)) / h - VIEW_ASPECT) < 1e-6
    # 过宽夹到不超高
    x, y, w, h = clamp_box(0.0, 0.0, 3.0, 16 / 9)
    assert h <= 1.0 and w <= 1.0
    # 最小选区
    x, y, w, h = clamp_box(0.4, 0.4, 0.001, 16 / 9)
    assert w >= 0.12


def test_custom_crop_config_roundtrip(tmp_path):
    from pet.config import Config

    cfg = Config(tmp_path)
    cfg.set('chat_bg_crops', {'builtin:whale': [0.1, 0.2, 0.5, 0.8]})
    cfg.save()
    loaded = Config(tmp_path).get('chat_bg_crops')
    assert loaded['builtin:whale'] == [0.1, 0.2, 0.5, 0.8]
