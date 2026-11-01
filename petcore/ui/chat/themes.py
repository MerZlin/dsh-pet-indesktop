"""聊天窗背景主题注册表：每套主题 = 壁纸 + 强调色 + 裁剪锚点 + 明/暗面板 + 纱罩。

移植自主分支 ``pet/chat/themes.py``（皮肤资产相同，``assets/chat/*.jpg`` 随包分发）。
anchor: 画面主体所在侧（left/center/right），竖窗裁剪时保住主体。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

THEMES: dict[str, dict] = {
    "whale": {
        "name": "蓝色幻想 · 鲸鱼娘",
        "file": "whale.jpg",
        "accent": "#4a5fa8",
        "focus": (0.56, 0.04, 0.38, 0.94),  # 主体框（归一化）
        "anchor": "right",  # Q版鲸鱼娘在画面右侧
        "dark": False,
        "scrim": (253, 246, 236, 128),
    },
    "whale-v2": {
        "name": "星海鲸裙",
        "file": "whale-v2.jpg",
        "accent": "#5a7fc8",
        "focus": (0.04, 0.05, 0.42, 0.9),
        "anchor": "left",
        "dark": False,
        "scrim": (240, 244, 252, 118),
    },
    "whale-mom": {
        "name": "鲸鱼妈妈",
        "file": "whale-mom.jpg",
        "accent": "#d9a53c",
        "focus": (0.25, 0.05, 0.5, 0.95),
        "anchor": "center",
        "dark": False,
        "scrim": (247, 244, 252, 128),
    },
    "whale-song": {
        "name": "鲸吟",
        "file": "whale-song.jpg",
        "accent": "#4d8fd4",
        "focus": (0.02, 0.05, 0.45, 0.9),
        "anchor": "left",
        "dark": False,
        "scrim": (244, 248, 253, 118),
    },
    "furina": {
        "name": "芙宁娜",
        "file": "furina.jpg",
        "accent": "#4a5fb5",
        "focus": (0.55, 0.1, 0.42, 0.85),
        "anchor": "right",
        "dark": False,
        "scrim": (240, 246, 252, 128),
    },
    "harbor": {
        "name": "夕港",
        "file": "harbor.jpg",
        "accent": "#ff9d5c",
        "focus": (0.45, 0.05, 0.55, 0.95),
        "anchor": "right",
        "dark": False,
        "scrim": (253, 244, 236, 138),  # 暮色图偏暗，纱罩略厚
    },
    "cyber-night": {
        "name": "赛博夜城",
        "file": "cyber-night.jpg",
        "accent": "#00e5ff",
        "focus": (0.25, 0.15, 0.5, 0.7),
        "anchor": "center",
        "dark": True,  # 深墨半透明面板 + 霓虹青
        "scrim": (10, 14, 28, 110),
    },
    "miku": {
        "name": "电子歌姬",
        "file": "miku.jpg",
        "accent": "#2e9bff",
        "focus": (0.25, 0.05, 0.5, 0.9),
        "anchor": "center",
        "dark": False,
        "scrim": (238, 250, 248, 128),
    },
    "summer": {
        "name": "夏沫琉璃",
        "file": "summer.jpg",
        "accent": "#2fa5b8",
        "focus": (0.2, 0.2, 0.6, 0.6),
        "anchor": "center",
        "dark": False,
        "scrim": (244, 250, 250, 122),
    },
}

ANCHOR_RATIO = {"left": 0.0, "center": 0.5, "right": 1.0}

#: 无主题（自定义图片背景）时的兜底取景框：画面中央竖条。
DEFAULT_FOCUS = (0.25, 0.0, 0.5, 1.0)


def get_theme(key: str) -> dict | None:
    return THEMES.get(key)


def theme_names() -> list[tuple[str, str]]:
    """(key, 显示名) 列表，供设置界面填充下拉框。"""
    return [(key, theme["name"]) for key, theme in THEMES.items()]


def _asset_candidates(filename: str) -> list[Path]:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "chat" / filename)
    # petcore/ui/chat/themes.py -> 仓库根
    candidates.append(Path(__file__).resolve().parents[3] / "assets" / "chat" / filename)
    return candidates


def resolve_background_pixmap(config_value: str) -> QPixmap | None:
    """Resolve a built-in theme or an absolute custom image path."""
    value = str(config_value or "").strip()
    if not value:
        return None
    if value.startswith("builtin:"):
        theme = get_theme(value[8:])
        if theme is None:
            return None
        path = next(
            (candidate for candidate in _asset_candidates(theme["file"]) if candidate.is_file()),
            None,
        )
    else:
        path = Path(value).expanduser()
    if path is None or not path.is_file():
        return None
    pixmap = QPixmap(str(path))
    return pixmap if not pixmap.isNull() else None


def build_modern_custom_overlay_qss(
    accent: str, card_opacity: int = 84, *, dark: bool = False
) -> str:
    """Make the modern workspace surfaces translucent over a custom image."""
    alpha = round(255 * max(10, min(100, int(card_opacity))) / 100)
    if dark:
        return f"""
QFrame#phone-shell, QFrame#chat-main {{ background: transparent; }}
QFrame#deepseek-sidebar {{ background: rgba(18, 20, 26, 218); }}
QFrame#chat-main-header {{ background: rgba(22, 24, 29, 218); }}
QScrollArea#message-scroll,
QScrollArea#message-scroll QWidget#qt_scrollarea_viewport,
QWidget#message-view,
QWidget#message-timeline {{ background: transparent; }}
QFrame#floating-composer {{ background: transparent; }}
QFrame#message-bubble {{ background: transparent; }}
QFrame#message-surface {{
    padding: 12px 16px;
    border: 1px solid rgba(70, 78, 92, 150);
    border-radius: 12px;
    background: rgba(30, 34, 43, {alpha});
}}
QFrame#message-surface[role="user"] {{ background: rgba(38, 44, 57, {alpha}); }}
QFrame#message-surface[state="error"] {{
    border-color: rgba(190, 90, 82, 185);
    background: rgba(48, 28, 28, {alpha});
}}
QFrame#chat-composer {{ background: rgba(27, 31, 39, 232); border-color: {accent}; }}
"""
    return f"""
QFrame#phone-shell, QFrame#chat-main {{ background: transparent; }}
QFrame#deepseek-sidebar {{ background: rgba(248, 250, 253, 218); }}
QFrame#chat-main-header {{ background: rgba(255, 255, 255, 218); }}
QScrollArea#message-scroll,
QScrollArea#message-scroll QWidget#qt_scrollarea_viewport,
QWidget#message-view,
QWidget#message-timeline {{ background: transparent; }}
QFrame#floating-composer {{ background: transparent; }}
QFrame#message-bubble {{ background: transparent; }}
QFrame#message-surface {{
    padding: 12px 16px;
    border: 1px solid rgba(255, 255, 255, 150);
    border-radius: 12px;
    background: rgba(255, 255, 255, {alpha});
}}
QFrame#message-surface[role="user"] {{ background: rgba(235, 242, 252, {alpha}); }}
QFrame#message-surface[state="error"] {{
    border-color: rgba(225, 97, 86, 185);
    background: rgba(255, 239, 237, {alpha});
}}
QFrame#chat-composer {{ background: rgba(255, 255, 255, 232); border-color: {accent}; }}
"""


def scale_background_pixmap(
    pixmap: QPixmap,
    width: int,
    height: int,
    fill_mode: str = "cover",
    dpr: float = 1.0,
) -> QPixmap:
    """Scale a wallpaper using browser-like cover/contain/stretch semantics.

    `width`/`height` 是**逻辑**尺寸；按 `dpr` 解到物理像素并写回 DPR，
    否则 150% 屏上会把 1 倍缓冲铺到 1.5 倍设备像素上，整面壁纸发虚（审计 AUD-32）。
    """
    aspect_mode = {
        "contain": Qt.AspectRatioMode.KeepAspectRatio,
        "stretch": Qt.AspectRatioMode.IgnoreAspectRatio,
    }.get(fill_mode, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
    ratio = max(1.0, float(dpr or 1.0))
    scaled = pixmap.scaled(
        max(1, int(round(width * ratio))),
        max(1, int(round(height * ratio))),
        aspect_mode,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(ratio)
    return scaled


def scrim_rgba(theme: dict) -> tuple[int, int, int, int]:
    return theme.get("scrim", (253, 246, 236, 128))


def background_focus_rect(
    theme: dict | None, crops, bg_value: str
) -> tuple[float, float, float, float]:
    """解析某张背景当前生效的取景框（归一化 x/y/w/h）。

    用户在裁切编辑器里保存的自定义取景框（config['chat_bg_crops'][bg_value]）
    优先于主题默认 focus；条目缺失或手改损坏时回退主题 focus，无主题回退
    DEFAULT_FOCUS。
    """
    custom = crops.get(bg_value) if isinstance(crops, dict) else None
    if isinstance(custom, (list, tuple)) and len(custom) == 4:
        try:
            return tuple(float(value) for value in custom)
        except (TypeError, ValueError):
            pass  # 手改坏的配置：回退主题默认取景
    return tuple((theme or {}).get("focus", DEFAULT_FOCUS))


def background_draw_offset(
    target_x: float,
    target_y: float,
    target_w: float,
    target_h: float,
    scaled_w: float,
    scaled_h: float,
    focus: tuple[float, float, float, float],
    fill_mode: str = "cover",
) -> tuple[int, int]:
    """计算背景缩放图的绘制偏移，让取景框完整可见。

    cover：满铺后平移，使 focus 框整体落在窗口内；框比窗口大则居中于主体，
    并始终钳制在 cover 边界内（不留白边）。contain/stretch 无取景语义，居中。
    """
    if fill_mode != "cover":
        return (
            int(target_x + (target_w - scaled_w) // 2),
            int(target_y + (target_h - scaled_h) // 2),
        )
    fx, fy, fw, fh = focus
    sw, sh = scaled_w, scaled_h
    x = target_x + target_w / 2.0 - (fx + fw / 2.0) * sw
    y = target_y + target_h / 2.0 - (fy + fh / 2.0) * sh
    if fw * sw <= target_w:
        x = min(max(x, target_x + target_w - (fx + fw) * sw), target_x - fx * sw)
    if fh * sh <= target_h:
        y = min(max(y, target_y + target_h - (fy + fh) * sh), target_y - fy * sh)
    x = min(max(x, target_x + target_w - sw), float(target_x))
    y = min(max(y, target_y + target_h - sh), float(target_y))
    return int(round(x)), int(round(y))
