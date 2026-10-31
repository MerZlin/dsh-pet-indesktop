"""Modern chat window QSS as a Python constant.

移植自主分支 ``pet/chat/modern_styles.qss``：嵌在模块里而不是读 ``.qss`` 数据文件，
打包时不需要额外的 ``--add-data``。``@ACCENT@`` 由窗口按主题/角色强调色替换，
``@FONT_SIZE@`` 由 ``ui_font_size`` 替换。
"""

from __future__ import annotations

import re

DEFAULT_ACCENT = "#326de6"
DEFAULT_FONT_SIZE = 13
#: 对话窗自己的字号范围（与全局 ``ui_font_size`` 的 10–18 分开，允许更大）。
FONT_SIZE_RANGE = (10, 24)
DEFAULT_FONT_FAMILY = (
    '"SF Pro Text", ".AppleSystemUIFont", "PingFang SC", "Segoe UI", sans-serif'
)

_ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONT_TOKEN_RE = re.compile(r"@F_(\d+)@")
#: 字体族里不允许出现 QSS 控制字符（引号/反斜杠/分号/花括号）。
_FONT_FAMILY_BAD = set("\"'\\;{}")

MODERN_QSS = """
QDialog#chat-window {
    /* 窗口自身透明：无边框圆角窗只显示圆角 phone-shell，不留窗外方形背景 */
    background: transparent; color: #20242b; font-size: @FONT_SIZE@px;
    font-family: @FONT_FAMILY@;
}
QFrame#phone-shell { background: #ffffff; border: 1px solid #e1e4e9; border-radius: 14px; }
QFrame#deepseek-sidebar {
    background: #f5f7fb; border: none; border-right: 1px solid #e7eaf0;
    border-top-left-radius: 13px; border-bottom-left-radius: 13px;
}
QFrame#deepseek-sidebar[overlayDrawer="true"] {
    border: 1px solid #e1e5eb; border-left: none;
    border-top-right-radius: 14px; border-bottom-right-radius: 14px;
}
QFrame#sidebar-scrim { background: rgba(26, 31, 40, 105); border: none; }
QLabel#avatar-label { background: transparent; border: none; color: #326de6; font-size: @F_12@; font-weight: 700; }
QLabel#brand-label { color: #326de6; font-size: @F_17@; font-weight: 700; }
QPushButton#new-conversation-button {
    min-height: 32px; max-height: 34px; border: 1px solid #e0e4eb; border-radius: 17px;
    background: #ffffff; color: #252a32; font-size: @F_13@; font-weight: 600;
}
QPushButton#new-conversation-button:hover { background: #f8faff; border-color: #ccd7ee; color: #326de6; }
QLabel#session-section-title { color: #9299a5; font-size: @F_11@; padding: 8px 7px 1px 7px; }
QListWidget#session-list { background: transparent; color: #303640; border: none; outline: none; padding: 0; }
QListWidget#session-list::item { min-height: 30px; border: none; border-radius: 9px; }
QListWidget#session-list::item:hover { background: #edf1f7; }
QListWidget#session-list::item:selected { background: #e4ebfc; color: #326de6; }
QFrame#session-row { background: transparent; border: none; }
QLabel#session-row-title { color: #343a44; background: transparent; }
QLabel#session-row-title[archived="true"] { color: #9aa1ab; }
QToolButton#session-more-button {
    width: 24px; height: 24px; border: none; border-radius: 6px;
    background: transparent; color: #626a76;
}
QToolButton#session-more-button:hover { background: #e8ecf2; }
QToolButton#archive-toggle-button {
    min-height: 30px; padding: 0 8px; border: none; border-radius: 8px;
    background: transparent; color: #626a76; font-size: @F_10@;
}
QToolButton#archive-toggle-button:hover { background: #e8ecf2; }
QToolButton#archive-toggle-button:checked { color: #ffffff; background: @ACCENT@; }
QFrame#sidebar-footer { background: transparent; border: none; }
QFrame#header-status { background: transparent; border: none; }
QLabel#status-dot { color: #28a56b; font-size: @F_9@; }
QLabel#status-dot[state="busy"] { color: #dc8a24; }
QLabel#status-label { color: #53606d; font-size: @F_10@; font-weight: 600; }
QLabel#status-label[state="error"] { color: #cf3f38; }
QLabel#provider-label { color: #9299a5; font-size: @F_10@; }
QToolButton#follow-pet-button, QToolButton#delete-session-button, QToolButton#clear-session-button {
    min-height: 30px; padding: 0 9px; border: none; border-radius: 8px;
    background: transparent; color: #626a76; font-size: @F_11@; font-weight: 500;
}
QToolButton#follow-pet-button:hover, QToolButton#delete-session-button:hover,
QToolButton#clear-session-button:hover { background: #e8ecf2; }
QToolButton#follow-pet-button:checked { color: #ffffff; background: @ACCENT@; }
QFrame#chat-main { background: #ffffff; border: none; border-top-right-radius: 13px; border-bottom-right-radius: 13px; }
QFrame#chat-main-header { min-height: 55px; background: #ffffff; border: none; border-bottom: 1px solid #f0f1f4; border-top-right-radius: 13px; }
QLabel#title-label { color: #252a32; font-size: @F_14@; font-weight: 600; }
QLabel#subtitle-label { color: #7189c7; font-size: @F_10@; }
QToolButton#sidebar-toggle-button, QToolButton#window-minimize-button, QToolButton#window-close-button,
QToolButton#window-pin-button {
    width: 29px; height: 29px; border: none; border-radius: 8px; background: transparent;
    color: #252a32; /* 深色系统下无 QSS color 会落到 palette 白字，白底上看不清 */
}
QToolButton#sidebar-toggle-button:hover { background: #f0f2f5; }
QToolButton#view-toggle-button {
    min-height: 29px; padding: 0 9px; border: none; border-radius: 8px;
    background: transparent; color: #626a76; font-size: @F_11@; font-weight: 600;
}
QToolButton#view-toggle-button:hover { background: #f0f2f5; }
QToolButton#view-toggle-button:checked { color: #ffffff; background: @ACCENT@; }
QStackedWidget#chat-view-stack { background: #ffffff; }
QWidget#chat-ai-view, QWidget#dsh-view { background: #ffffff; }
QFrame#dsh-header { background: #ffffff; border: none; border-bottom: 1px solid #f0f1f4; }
QLabel#dsh-status { color: #53606d; font-size: @F_11@; }
QLabel#dsh-status[state="error"] { color: #cf3f38; }
QScrollArea#dsh-scroll, QWidget#dsh-timeline { background: #ffffff; border: none; }
QLabel#dsh-user {
    background: #f0f3f8; border-radius: 12px; padding: 8px 12px;
    color: #252a32; font-size: @FONT_SIZE@px;
}
QLabel#dsh-assistant { color: #252a32; font-size: @FONT_SIZE@px; padding: 6px 2px; }
QFrame#dsh-tool, QFrame#dsh-approval {
    background: #f7f8fa; border: 1px solid #e6e9ef; border-radius: 8px;
}
QLabel#dsh-tool-title { color: #252a32; font-size: @F_11@; font-weight: 600; }
QLabel#dsh-tool-detail { color: #626a76; font-size: @F_10@; }
QFrame#dsh-composer { background: #ffffff; border: none; border-top: 1px solid #f0f1f4; }
QPlainTextEdit#dsh-input {
    border: 1px solid #dfe3e9; border-radius: 10px; padding: 6px 8px;
    color: #252a32; background: #ffffff; selection-background-color: @ACCENT@;
}
QPushButton#dsh-send {
    background: @ACCENT@; color: #ffffff; border: none; border-radius: 8px;
    padding: 6px 14px; font-weight: 600;
}
QPushButton#dsh-retry, QPushButton#dsh-stop, QPushButton#dsh-context {
    background: #eef0f3; color: #252a32; border: none; border-radius: 8px;
    padding: 6px 12px;
}
QToolButton#window-minimize-button:hover { background: #f0f2f5; }
QToolButton#window-close-button:hover { background: #fbe8e8; }
QToolButton#window-pin-button:hover { background: #f0f2f5; }
QToolButton#window-pin-button:checked { color: #ffffff; background: @ACCENT@; }
QScrollArea#message-scroll, QScrollArea#message-scroll QWidget#qt_scrollarea_viewport,
QWidget#message-view, QWidget#message-timeline { background: #ffffff; border: none; }
QLabel#empty-state { color: #969da8; font-size: @F_14@; }
QFrame#message-bubble { padding: 0; border: none; background: transparent; }
QFrame#message-surface { padding: 0; border: none; background: transparent; }
QFrame#message-surface[role="user"] { padding: 8px 12px; border-radius: 12px; background: #f0f3f8; }
QFrame#message-bubble[state="streaming"] { background: transparent; }
QFrame#message-bubble[state="error"] { background: transparent; }
QFrame#message-bubble[state="stopped"] { background: #f6f7f9; }
QFrame#message-surface[state="error"] {
    padding: 0; border: 1px solid #f0c8c3; border-radius: 12px; background: #fff3f1;
}
QFrame#message-surface[state="error"] QLabel#bubble-body { color: #7f2e29; font-weight: 600; }
QLabel#bubble-avatar, QLabel#bubble-meta { background: transparent; border: none; }
QLabel#bubble-body { color: #252a32; font-size: @FONT_SIZE@px; line-height: 1.55; }
QLabel#bubble-status { color: #a56c1c; font-size: @F_10@; }
QFrame#error-actions { background: transparent; border: none; }
QFrame#message-tools { background: transparent; border: none; }
QFrame#message-tools QToolButton { width: 25px; height: 25px; border: none; border-radius: 6px; background: transparent; color: #252a32; }
QFrame#message-tools QToolButton:hover { background: #f0f2f5; }
QFrame#floating-composer { background: #ffffff; border: none; }
QFrame#chat-composer { background: #ffffff; border: 1px solid #dfe3e9; border-radius: 18px; }
QFrame#attachment-strip { background: transparent; border: none; }
QFrame#attachment-chip {
    min-height: 42px; max-height: 44px; border: 1px solid #dfe3e9;
    border-radius: 10px; background: #f5f7fb;
}
QFrame#attachment-chip:hover { background: #edf2fa; border-color: #cbd5e5; }
QLabel#attachment-preview { background: #ffffff; border: 1px solid #e1e5ec; border-radius: 7px; }
QLabel#attachment-name { color: #343b45; font-size: @F_10@; font-weight: 600; background: transparent; }
QLabel#attachment-meta { color: #929aa6; font-size: @F_8@; background: transparent; }
QToolButton#attachment-remove-button {
    width: 20px; height: 20px; border: none; border-radius: 10px; background: #626a75;
    color: #ffffff;
}
QToolButton#attachment-remove-button:hover { background: #d34f48; }
QPlainTextEdit#chat-input {
    min-height: 36px; max-height: 68px; padding: 2px 2px; color: #252a32; background: transparent;
    border: none; selection-background-color: @ACCENT@;
}
QPlainTextEdit#chat-input:focus { border: none; }
QFrame#composer-toolbar { background: transparent; border: none; }
QLabel#composer-hint { color: #a0a6b0; font-size: @F_9@; }
QLabel#composer-partial {
    color: #6f7782; font-size: @F_9@; padding: 0 2px;
}
QToolButton#composer-attach-button {
    width: 29px; height: 29px; border: none; border-radius: 14px; background: transparent;
    color: #252a32;
}
QToolButton#composer-attach-button:hover { background: #f0f2f5; }
QToolButton#composer-record-button {
    width: 29px; height: 29px; border: none; border-radius: 14px; background: transparent;
    color: #252a32;
}
QToolButton#composer-record-button:hover { background: #f0f2f5; }
QToolButton#composer-record-button[recording="true"] { background: #d34f48; }
QToolButton#send-button {
    width: 34px; height: 34px; border: none; border-radius: 17px;
    color: #ffffff; background: @ACCENT@;
}
QToolButton#send-button:hover { background: #286fda; }
QToolButton#send-button:disabled { background: #c8d4f2; }
QToolButton#send-button[busy="true"] { background: #d88932; }
QScrollBar:vertical { width: 7px; margin: 3px 1px; background: transparent; }
QScrollBar::handle:vertical { min-height: 28px; border-radius: 3px; background: #ccd0d7; }
QScrollBar::handle:vertical:hover { background: #adb3bd; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def safe_accent(value: object, default: str = DEFAULT_ACCENT) -> str:
    """Theme accents come from config/manifests: only ``#RRGGBB`` is accepted."""
    text = str(value or "").strip()
    return text if _ACCENT_RE.fullmatch(text) else default


def safe_font_family(value: object) -> str:
    """字体族来自设置/系统字体列表：只接受安全字符，空值回系统默认。"""
    text = str(value or "").strip()
    if not text or len(text) > 64:
        return DEFAULT_FONT_FAMILY
    if any(char in _FONT_FAMILY_BAD for char in text):
        return DEFAULT_FONT_FAMILY
    return f'"{text}", sans-serif'


def clamp_font_size(value: object) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FONT_SIZE
    low, high = FONT_SIZE_RANGE
    return max(low, min(high, size))


def _scaled_font_size(base: int, original: int) -> int:
    """按 ``base / 13`` 等比缩放一个原始 px 字号，下限 8px。"""
    return max(8, round(original * base / DEFAULT_FONT_SIZE))


def modern_stylesheet(
    accent: str = DEFAULT_ACCENT,
    font_size: int = DEFAULT_FONT_SIZE,
    overlay: str = "",
    font_family: str = "",
) -> str:
    """Accent/font-substituted modern QSS plus an optional translucent overlay."""
    color = safe_accent(accent)
    size = clamp_font_size(font_size)
    sheet = MODERN_QSS.replace("@ACCENT@", color).replace("@FONT_SIZE@", str(size))
    sheet = _FONT_TOKEN_RE.sub(
        lambda match: str(_scaled_font_size(size, int(match.group(1)))), sheet
    )
    sheet = sheet.replace("@FONT_FAMILY@", safe_font_family(font_family))
    return sheet + (overlay or "")
