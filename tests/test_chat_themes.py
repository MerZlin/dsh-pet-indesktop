# -*- coding: utf-8 -*-
"""聊天背景主题注册表完整性 + 解析。"""

import re
from pathlib import Path

from pet.chat.themes import (
    ANCHOR_RATIO,
    THEMES,
    build_modern_custom_overlay_qss,
    build_overlay_qss,
    scale_background_pixmap,
    theme_names,
)

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "chat"


def test_every_theme_has_art_file():
    for key, theme in THEMES.items():
        assert (ASSETS / theme["file"]).is_file(), f"{key} 缺壁纸 {theme['file']}"


def test_theme_fields_valid():
    for key, theme in THEMES.items():
        assert theme["anchor"] in ANCHOR_RATIO, key
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", theme["accent"]), key
        assert len(theme["scrim"]) == 4 and all(0 <= c <= 255 for c in theme["scrim"]), key
        assert theme["name"], key


def test_overlay_matches_dark_mode():
    dark = next(t for t in THEMES.values() if t["dark"])
    light = next(t for t in THEMES.values() if not t["dark"])
    assert "#e8ecf8" in build_overlay_qss(dark)  # 暗色面板要有亮文字
    assert "#e8ecf8" not in build_overlay_qss(light)
    assert dark["accent"] in build_overlay_qss(dark)  # accent 注入模板


def test_modern_custom_background_uses_readable_message_cards_without_hiding_image():
    qss = build_modern_custom_overlay_qss("#3994ff")
    assert "QFrame#message-bubble { background: transparent;" in qss
    assert "QFrame#message-surface {" in qss
    assert "padding: 12px 16px" in qss
    assert "border-radius: 12px" in qss
    assert 'QFrame#message-surface[state="error"] {' in qss
    assert "QWidget#message-timeline { background: transparent; }" in qss


def test_background_fill_modes_scale_as_expected():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(100, 50)
    assert scale_background_pixmap(pixmap, 100, 100, "cover").size().toTuple() == (200, 100)
    assert scale_background_pixmap(pixmap, 100, 100, "contain").size().toTuple() == (100, 50)
    assert scale_background_pixmap(pixmap, 100, 100, "stretch").size().toTuple() == (100, 100)


def test_theme_names_unique_and_nonempty():
    keys = [k for k, _ in theme_names()]
    assert len(keys) == len(set(keys)) == len(THEMES)


def test_all_builtin_themes_resolve():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from pet.chat.legacy_widgets import ChatWindow
    from pet.chat import session_store
    from pet.config import Config
    import tempfile

    app = QApplication.instance() or QApplication([])
    for key in THEMES:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(tmp)
            cfg.set("chat_background", f"builtin:{key}")
            win = ChatWindow(cfg, "shenshen")
            assert win._bg_pixmap is not None and not win._bg_pixmap.isNull(), key
            assert win._bg_theme["accent"] == THEMES[key]["accent"]
            win.close()
            # conftest 的 writer 收口在 fixture teardown，晚于本 with 块的
            # rmtree——必须在目录清理前排空并关闭后台写盘线程，否则 writer
            # 的 mkdir+写盘与 rmtree 竞态（CI windows-latest 实录 WinError 145）。
            assert session_store.close_all_writers() is True


def test_classic_background_supports_builtin_theme_while_modern_background_is_independent(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication
    from pet.chat.legacy_widgets import ChatWindow as LegacyChatWindow
    from pet.chat.widgets import ChatWindow as ModernChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    custom = tmp_path / "modern.png"
    image = QPixmap(32, 32)
    image.fill(QColor("#123456"))
    assert image.save(str(custom))

    cfg = Config(tmp_path / "config")
    cfg.set("chat_background", "builtin:whale")
    cfg.set("modern_chat_background", "")
    legacy = LegacyChatWindow(cfg, "shenshen")
    modern = ModernChatWindow(cfg, "shenshen")
    assert legacy._bg_pixmap is not None and not legacy._bg_pixmap.isNull()
    assert legacy._bg_theme["accent"] == THEMES["whale"]["accent"]
    assert modern._bg_pixmap is None
    legacy.close()
    modern.close()

    cfg.set("modern_chat_background", str(custom.resolve()))
    modern_custom = ModernChatWindow(cfg, "shenshen")
    assert modern_custom._bg_pixmap is not None and not modern_custom._bg_pixmap.isNull()
    assert modern_custom._bg_theme is None
    assert "QFrame#chat-main { background: transparent; }" in modern_custom.styleSheet()
    assert "QFrame#deepseek-sidebar { background: rgba(" in modern_custom.styleSheet()
    modern_custom.close()
    app.processEvents()


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
    cfg.set("chat_bg_crops", {"builtin:whale": [0.1, 0.2, 0.5, 0.8]})
    cfg.save()
    loaded = Config(tmp_path).get("chat_bg_crops")
    assert loaded["builtin:whale"] == [0.1, 0.2, 0.5, 0.8]


# ---------------------------------------------------------- 取景框（focus/自定义裁切）
def test_focus_rect_custom_crop_wins_over_theme_focus():
    from pet.chat.themes import background_focus_rect, get_theme

    theme = get_theme("whale")
    crops = {"builtin:whale": [0.0, 0.0, 0.25, 1.0]}
    assert background_focus_rect(theme, crops, "builtin:whale") == (0.0, 0.0, 0.25, 1.0)


def test_focus_rect_falls_back_to_theme_focus_without_crop():
    from pet.chat.themes import background_focus_rect, get_theme

    theme = get_theme("whale")
    assert background_focus_rect(theme, {}, "builtin:whale") == tuple(theme["focus"])
    # crops 非 dict / 无该背景的条目，同样回退主题
    assert background_focus_rect(theme, None, "builtin:whale") == tuple(theme["focus"])
    assert background_focus_rect(theme, {"builtin:furina": [0, 0, 1, 1]}, "builtin:whale") == tuple(theme["focus"])


def test_focus_rect_malformed_crop_falls_back_to_theme_focus():
    from pet.chat.themes import background_focus_rect, get_theme

    theme = get_theme("whale")
    for bad in ([0.1, 0.2, 0.5], "junk", [0.1, "x", 0.5, 1.0], 42):
        crops = {"builtin:whale": bad}
        assert background_focus_rect(theme, crops, "builtin:whale") == tuple(theme["focus"]), bad


def test_focus_rect_no_theme_uses_default():
    from pet.chat.themes import DEFAULT_FOCUS, background_focus_rect

    assert background_focus_rect(None, {}, "/tmp/custom.jpg") == DEFAULT_FOCUS
    crops = {"/tmp/custom.jpg": [0.1, 0.1, 0.5, 0.5]}
    assert background_focus_rect(None, crops, "/tmp/custom.jpg") == (0.1, 0.1, 0.5, 0.5)


def test_cover_draw_offset_keeps_focus_rect_fully_visible():
    from pet.chat.themes import background_draw_offset

    # 200x100 源图 cover 进 100x100 窗口 → sw=200, sh=100；取景框 [0.25,0.75] 应完整可见
    x, y = background_draw_offset(0, 0, 100, 100, 200, 100, (0.25, 0.0, 0.5, 1.0), "cover")
    assert (x, y) == (-50, 0)
    # 取景框像素 [50,150] 平移后落在 [0,100]
    assert x + 0.25 * 200 >= 0 and x + 0.75 * 200 <= 100


def test_cover_draw_offset_custom_crop_left_edge():
    from pet.chat.themes import background_draw_offset

    # 自定义取景框贴左缘 [0,0.25]：应左对齐（x=0）而非居中
    x, y = background_draw_offset(0, 0, 100, 100, 200, 100, (0.0, 0.0, 0.25, 1.0), "cover")
    assert (x, y) == (0, 0)


def test_cover_draw_offset_clamped_to_cover_bounds():
    from pet.chat.themes import background_draw_offset

    # 取景框比窗口还大（fw=1.0）：居中于主体，且不超出 cover 边界 [-100, 0]
    x, y = background_draw_offset(0, 0, 100, 100, 200, 100, (0.0, 0.0, 1.0, 1.0), "cover")
    assert -100 <= x <= 0 and y == 0


def test_non_cover_fill_modes_stay_centered():
    from pet.chat.themes import background_draw_offset

    # contain：缩放图比窗口小，永远居中，focus 不参与
    assert background_draw_offset(0, 0, 100, 100, 100, 50, (0.0, 0.0, 0.25, 1.0), "contain") == (0, 25)
    # stretch：缩放图恰等于窗口，贴原点
    assert background_draw_offset(0, 0, 100, 100, 100, 100, (0.0, 0.0, 0.25, 1.0), "stretch") == (0, 0)


def _check_paint_honors_custom_crop(window_cls, tmp_path, monkeypatch):
    """渲染路径必须真读 chat_bg_crops：paintEvent 拿配置里的自定义裁切框计算偏移。

    回归：聊天窗重写曾丢掉整个取景消费，设置里裁了等于没裁。
    纯函数正确性由 background_focus_rect/background_draw_offset 的用例覆盖，
    本用例钉接线：两套渲染路径（modern/legacy）都必须经 helper 且传入自定义裁切。
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication

    from pet.chat import themes as chat_themes
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    cfg = Config(tmp_path)
    window = window_cls(cfg, "shenshen")

    bg_key = "/tmp/fake-wallpaper.jpg"
    window._bg_pixmap = QPixmap(200, 100)
    window._bg_pixmap.fill(QColor("blue"))
    window._bg_value = bg_key
    window._bg_theme = None
    window._bg_scaled = None
    window.resize(480, 360)
    window.show()
    app.processEvents()

    seen = []
    orig = chat_themes.background_draw_offset

    def spy(*args, **kwargs):
        seen.append(args[6] if len(args) > 6 else kwargs.get("focus"))
        return orig(*args, **kwargs)

    monkeypatch.setattr(chat_themes, "background_draw_offset", spy)

    crop = [0.75, 0.0, 0.25, 1.0]
    cfg.set("chat_bg_crops", {bg_key: crop})
    window._bg_scaled = None
    window.update()
    app.processEvents()
    window.close()
    app.processEvents()

    assert seen, "paintEvent 必须经 background_draw_offset 计算背景偏移"
    assert tuple(crop) in [tuple(f) for f in seen], f"裁切框未传到渲染路径: {seen}"


def test_paint_honors_custom_crop(tmp_path, monkeypatch):
    """modern 渲染路径：见 _check_paint_honors_custom_crop。"""
    from pet.chat.widgets import ChatWindow

    _check_paint_honors_custom_crop(ChatWindow, tmp_path, monkeypatch)


def test_legacy_paint_honors_custom_crop(tmp_path, monkeypatch):
    """legacy 渲染路径同样必须真读 chat_bg_crops（回归曾两路齐丢）。"""
    from pet.chat.legacy_widgets import ChatWindow as LegacyChatWindow

    _check_paint_honors_custom_crop(LegacyChatWindow, tmp_path, monkeypatch)
