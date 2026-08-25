# -*- coding: utf-8 -*-
"""聊天窗背景主题注册表：每套主题 = 壁纸 + 强调色 + 裁剪锚点 + 明/暗面板 + 纱罩。

皮肤资产移植自 dsh 皮肤中心（@linxin666/dsh-client-ui-skin-center）的对应皮肤包，
仅取背景画，配色按其 skin.json 的 accent 适配。
anchor: 画面主体所在侧（left/center/right），竖窗裁剪时保住主体。
"""

from __future__ import annotations

THEMES: dict[str, dict] = {
    'whale': {
        'name': '蓝色幻想 · 鲸鱼娘',
        'file': 'whale.jpg',
        'accent': '#4a5fa8',
        'focus': (0.56, 0.04, 0.38, 0.94),  # 主体框（归一化）
        'anchor': 'right',      # Q版鲸鱼娘在画面右侧
        'dark': False,
        'scrim': (253, 246, 236, 128),
    },
    'whale-v2': {
        'name': '星海鲸裙',
        'file': 'whale-v2.jpg',
        'accent': '#5a7fc8',
        'focus': (0.04, 0.05, 0.42, 0.9),  # 主体框（归一化）
        'anchor': 'left',       # 少女在画面左侧
        'dark': False,
        'scrim': (240, 244, 252, 118),
    },
    'whale-mom': {
        'name': '鲸鱼妈妈',
        'file': 'whale-mom.jpg',
        'accent': '#d9a53c',
        'focus': (0.25, 0.05, 0.5, 0.95),  # 主体框（归一化）：取景偏左，保住对白气泡
        'anchor': 'center',     # 人物居中偏右，左侧对白气泡入镜也无妨
        'dark': False,
        'scrim': (247, 244, 252, 128),
    },
    'whale-song': {
        'name': '鲸吟',
        'file': 'whale-song.jpg',
        'accent': '#4d8fd4',
        'focus': (0.02, 0.05, 0.45, 0.9),  # 主体框（归一化）
        'anchor': 'left',       # 少女与鲸群在画面左半
        'dark': False,
        'scrim': (244, 248, 253, 118),
    },
    'furina': {
        'name': '芙宁娜',
        'file': 'furina.jpg',
        'accent': '#4a5fb5',
        'focus': (0.55, 0.1, 0.42, 0.85),  # 主体框（归一化）
        'anchor': 'right',      # 芙宁娜在画面右侧
        'dark': False,
        'scrim': (240, 246, 252, 128),
    },
    'harbor': {
        'name': '夕港',
        'file': 'harbor.jpg',
        'accent': '#ff9d5c',
        'focus': (0.45, 0.05, 0.55, 0.95),  # 主体框（归一化）
        'anchor': 'right',      # 女仆在画面右侧
        'dark': False,
        'scrim': (253, 244, 236, 138),  # 暮色图偏暗，纱罩略厚
    },
    'cyber-night': {
        'name': '赛博夜城',
        'file': 'cyber-night.jpg',
        'accent': '#00e5ff',
        'focus': (0.25, 0.15, 0.5, 0.7),  # 主体框（归一化）
        'anchor': 'center',
        'dark': True,           # 深墨半透明面板 + 霓虹青
        'scrim': (10, 14, 28, 110),
    },
    'miku': {
        'name': '电子歌姬',
        'file': 'miku.jpg',
        'accent': '#2e9bff',
        'focus': (0.25, 0.05, 0.5, 0.9),  # 主体框（归一化）
        'anchor': 'center',     # 人物居中
        'dark': False,
        'scrim': (238, 250, 248, 128),
    },
    'summer': {
        'name': '夏沫琉璃',
        'file': 'summer.jpg',
        'accent': '#2fa5b8',
        'focus': (0.2, 0.2, 0.6, 0.6),  # 主体框（归一化）
        'anchor': 'center',
        'dark': False,
        'scrim': (244, 250, 250, 122),
    },
}

ANCHOR_RATIO = {'left': 0.0, 'center': 0.5, 'right': 1.0}

# 明/暗两套面板叠加层模板；{accent} 由主题 accent 替换
_OVERLAY_LIGHT = """
QDialog#chat-window { background: transparent; }
QFrame#phone-shell { background: transparent; border: none; }
QFrame#chat-title-bar { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {title0}, stop:1 {title1}); border-bottom: 3px solid {accent}; }
QFrame#chat-context-bar { background: rgba(255, 250, 242, 200); border-bottom: 1px solid rgba(240, 226, 208, 160); }
QScrollArea#message-scroll,
QScrollArea#message-scroll QWidget#qt_scrollarea_viewport,
QWidget#message-view,
QWidget#message-timeline { background: transparent; }
QFrame#chat-composer { background: rgba(255, 250, 242, 200); border-top: 1px solid rgba(240, 226, 208, 160); }
QFrame#message-bubble { background: rgba(255, 255, 255, 216); }
QPlainTextEdit#chat-input { background: rgba(255, 255, 255, 226); }
"""

_OVERLAY_DARK = """
QDialog#chat-window { background: transparent; }
QFrame#phone-shell { background: transparent; border: none; }
QFrame#chat-context-bar { background: rgba(16, 21, 38, 205); border-bottom: 1px solid rgba(90, 110, 160, 90); }
QScrollArea#message-scroll,
QScrollArea#message-scroll QWidget#qt_scrollarea_viewport,
QWidget#message-view,
QWidget#message-timeline { background: transparent; }
QFrame#chat-composer { background: rgba(16, 21, 38, 205); border-top: 1px solid rgba(90, 110, 160, 90); }
QFrame#message-bubble { background: rgba(28, 34, 56, 220); }
QFrame#chat-title-bar { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #161c34, stop:1 #232c4e); border-bottom: 3px solid {accent}; }
QPlainTextEdit#chat-input { background: rgba(22, 27, 46, 225); color: #e8ecf8; border: 1px solid rgba(90, 110, 160, 110); }
QLabel#bubble-body { color: #e8ecf8; }
QLabel#bubble-meta { color: #8a94b8; }
QLabel#provider-label, QLabel#context-caption, QLabel#composer-hint { color: #8a94b8; }
QLabel#empty-state { color: #9aa5c4; }
QLabel#status-label { color: #7fe0b8; }
QComboBox#session-combo { color: #e8ecf8; border-bottom: 1px solid rgba(90, 110, 160, 110); }
QToolButton#follow-pet-button, QToolButton#new-session-button,
QToolButton#delete-session-button, QToolButton#clear-session-button { color: #9aa5c4; }
QToolButton#follow-pet-button:hover, QToolButton#new-session-button:hover,
QToolButton#delete-session-button:hover, QToolButton#clear-session-button:hover { color: {accent}; background: rgba(255, 255, 255, 30); }
"""


def get_theme(key: str) -> dict | None:
    return THEMES.get(key)


def theme_names() -> list[tuple[str, str]]:
    """(key, 显示名) 列表，供设置界面填充下拉框。"""
    return [(key, t['name']) for key, t in THEMES.items()]


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix_white(rgb: tuple[int, int, int], t: float) -> str:
    return '#%02x%02x%02x' % tuple(round(c + (255 - c) * t) for c in rgb)


def build_overlay_qss(theme: dict) -> str:
    '''按主题的明/暗模式生成面板叠加层 QSS；标题栏渐变由主题 accent 派生。'''
    tpl = _OVERLAY_DARK if theme.get('dark') else _OVERLAY_LIGHT
    base = _hex_to_rgb(theme['accent'])
    return (tpl.replace('{accent}', theme['accent'])
               .replace('{title0}', _mix_white(base, 0.18))
               .replace('{title1}', _mix_white(base, 0.48)))


def scrim_rgba(theme: dict) -> tuple[int, int, int, int]:
    return theme.get('scrim', (253, 246, 236, 128))
