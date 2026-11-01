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
QToolButton#window-maximize-button, QToolButton#window-pin-button, QToolButton#window-dark-button {
    width: 29px; height: 29px; border: none; border-radius: 8px; background: transparent;
    color: #252a32; /* 深色系统下无 QSS color 会落到 palette 白字，白底上看不清 */
}
QToolButton#sidebar-toggle-button:hover { background: #f0f2f5; }
QToolButton#window-dark-button:hover { background: #f0f2f5; }
QToolButton#window-dark-button:checked { color: #ffffff; background: @ACCENT@; }
QToolButton#view-toggle-button {
    min-height: 29px; padding: 0 9px; border: none; border-radius: 8px;
    background: transparent; color: #626a76; font-size: @F_11@; font-weight: 600;
}
QToolButton#view-toggle-button:hover { background: #f0f2f5; }
QToolButton#view-toggle-button:checked { color: #ffffff; background: @ACCENT@; }
/* 扩展视图（`chat_view`）的标题栏按钮：与 DSH 按钮同一语言，多一个图标。 */
QToolButton#chat-view-button {
    min-height: 29px; padding: 0 9px; border: none; border-radius: 8px;
    background: transparent; color: #626a76; font-size: @F_11@; font-weight: 600;
}
QToolButton#chat-view-button:hover { background: #f0f2f5; }
QToolButton#chat-view-button:checked { color: #ffffff; background: @ACCENT@; }
QToolButton#chat-view-more-button {
    width: 29px; height: 29px; border: none; border-radius: 8px; background: transparent;
    color: #626a76;
}
QToolButton#chat-view-more-button:hover { background: #f0f2f5; }
QToolButton#chat-view-more-button::menu-indicator { image: none; width: 0; }
/* 圆角只由 phone-shell 画：QSS 从不裁剪子控件，方形白底会盖掉右下角
   （实测圆角外有 30 个不透明纯白像素的楔形）。栈页与 DSH 表面一律透明。 */
QStackedWidget#chat-view-stack { background: transparent; }
QWidget#chat-ai-view, QWidget#dsh-view { background: transparent; }
/* 扩展视图槽位：内缩由布局给（见 window.CHAT_VIEW_INSET），表面保持透明，
   mod 自己决定里面画什么。 */
QFrame#chat-view-host { background: transparent; border: none; }
QLabel#chat-view-error { color: #8b93a1; font-size: @F_12@; padding: 24px; }
QFrame#dsh-header { background: transparent; border: none; border-bottom: 1px solid #f0f1f4; }
QLabel#dsh-status { color: #53606d; font-size: @F_11@; }
QLabel#dsh-status[state="error"] { color: #cf3f38; }
QScrollArea#dsh-scroll, QWidget#dsh-timeline { background: transparent; border: none; }
QLabel#dsh-user {
    background: #f0f3f8; border-radius: 12px; padding: 8px 12px;
    color: #252a32; font-size: @FONT_SIZE@px;
}
QLabel#dsh-assistant { color: #252a32; font-size: @FONT_SIZE@px; padding: 6px 2px; }
QFrame#dsh-tool, QFrame#dsh-approval {
    background: #f7f8fa; border: 1px solid #e6e9ef; border-radius: 8px;
}
QLabel#dsh-tool-title { color: #252a32; font-size: @F_11@; font-weight: 600; }
QLabel#dsh-tool-title[state="error"] { color: #b3261e; }
QLabel#dsh-tool-detail { color: #626a76; font-size: @F_10@; }
QLabel#dsh-error { color: #b3261e; font-size: @F_11@; padding: 4px 2px; }
QLabel#dsh-thinking { color: #626a76; font-size: @F_10@; }
QFrame#dsh-composer { background: #ffffff; border: none; border-top: 1px solid #f0f1f4; }
QPlainTextEdit#dsh-input {
    border: 1px solid #dfe3e9; border-radius: 10px; padding: 6px 8px;
    color: #252a32; background: #ffffff; selection-background-color: @ACCENT@;
}
QPushButton#dsh-send {
    background: @ACCENT@; color: #ffffff; border: none; border-radius: 8px;
    padding: 6px 14px; font-weight: 600;
}
QPushButton#dsh-retry, QPushButton#dsh-stop, QPushButton#dsh-context, QPushButton#dsh-new,
QPushButton#dsh-deny {
    background: #eef0f3; color: #252a32; border: none; border-radius: 8px;
    padding: 6px 12px;
}
QPushButton#dsh-retry:hover, QPushButton#dsh-stop:hover, QPushButton#dsh-context:hover,
QPushButton#dsh-new:hover, QPushButton#dsh-deny:hover { background: #e3e7ee; }
/* 审批卡的允许/拒绝：没有这两条时用的是 palette 默认色，深色系统下实测白字白底 */
QPushButton#dsh-allow {
    background: @ACCENT@; color: #ffffff; border: none; border-radius: 8px;
    padding: 6px 16px; font-weight: 600;
}
QPushButton#dsh-allow:hover { background: @ACCENT@; }
QPushButton#dsh-allow:disabled, QPushButton#dsh-deny:disabled {
    background: #eef0f3; color: #9aa1ab;
}
QToolButton#window-minimize-button:hover, QToolButton#window-maximize-button:hover { background: #f0f2f5; }
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

#: 深色覆盖表：**追加**在 `MODERN_QSS` 之后（同选择器后者胜），所以浅色那条
#: 路径一个字节都没动。只覆盖对话窗自己的表面，设置窗/桌宠/快捷气泡不受影响。
DARK_OVERRIDES = """
QDialog#chat-window { color: #e6e8ec; }
QFrame#phone-shell { background: #16181d; border: 1px solid #2a2f39; }
QFrame#deepseek-sidebar { background: #12141a; border-right: 1px solid #262b36; }
QFrame#deepseek-sidebar[overlayDrawer="true"] { border: 1px solid #2a2f39; border-left: none; }
QFrame#sidebar-scrim { background: rgba(0, 0, 0, 150); }
QLabel#brand-label { color: @ACCENT@; }
QPushButton#new-conversation-button {
    background: #1e222b; border: 1px solid #333947; color: #e6e8ec;
}
QPushButton#new-conversation-button:hover { background: #252b36; border-color: #3f4757; color: @ACCENT@; }
QLabel#session-section-title { color: #7b8494; }
QListWidget#session-list { color: #c9d0da; }
QListWidget#session-list::item:hover { background: #232833; }
QListWidget#session-list::item:selected { background: #26314a; color: #9dc0ff; }
QLabel#session-row-title { color: #c9d0da; }
QLabel#session-row-title[archived="true"] { color: #6b7382; }
QToolButton#session-more-button { color: #9aa3b0; }
QToolButton#session-more-button:hover { background: #262b36; }
QToolButton#archive-toggle-button { color: #9aa3b0; }
QToolButton#archive-toggle-button:hover { background: #262b36; }
QToolButton#follow-pet-button, QToolButton#delete-session-button, QToolButton#clear-session-button { color: #9aa3b0; }
QToolButton#follow-pet-button:hover, QToolButton#delete-session-button:hover,
QToolButton#clear-session-button:hover { background: #262b36; }
QLabel#status-label { color: #aab2c0; }
QLabel#status-label[state="error"] { color: #ff7a72; }
QLabel#status-dot { color: #4cc98a; }
QLabel#status-dot[state="busy"] { color: #e0a14a; }
QLabel#provider-label { color: #7b8494; }
QLabel#subtitle-label { color: #93abe0; }
QFrame#chat-main { background: #16181d; }
QFrame#chat-main-header { background: #16181d; border-bottom: 1px solid #262b36; }
QLabel#title-label { color: #e6e8ec; }
QToolButton#sidebar-toggle-button, QToolButton#window-minimize-button,
QToolButton#window-close-button, QToolButton#window-maximize-button,
QToolButton#window-pin-button, QToolButton#window-dark-button { color: #d7dce6; }
QToolButton#sidebar-toggle-button:hover, QToolButton#window-minimize-button:hover,
QToolButton#window-maximize-button:hover, QToolButton#window-pin-button:hover,
QToolButton#window-dark-button:hover { background: #262b36; }
QToolButton#window-close-button:hover { background: #5a2727; }
QToolButton#view-toggle-button, QToolButton#chat-view-button, QToolButton#chat-view-more-button { color: #aab2c0; }
QToolButton#view-toggle-button:hover, QToolButton#chat-view-button:hover,
QToolButton#chat-view-more-button:hover { background: #262b36; }
QToolButton#view-toggle-button:checked, QToolButton#chat-view-button:checked { color: #ffffff; background: @ACCENT@; }
QLabel#chat-view-error { color: #8b93a1; }
QFrame#dsh-header { border-bottom: 1px solid #262b36; }
QLabel#dsh-status { color: #aab2c0; }
QLabel#dsh-status[state="error"] { color: #ff7a72; }
QLabel#dsh-user { background: #232833; color: #e6e8ec; }
QLabel#dsh-assistant { color: #e6e8ec; }
QFrame#dsh-tool, QFrame#dsh-approval { background: #1c2029; border: 1px solid #2c323d; }
QLabel#dsh-tool-title { color: #e6e8ec; }
QLabel#dsh-tool-title[state="error"] { color: #ff7a72; }
QLabel#dsh-tool-detail { color: #a3abb8; }
QLabel#dsh-thinking { color: #a3abb8; }
QLabel#dsh-error { color: #ff7a72; }
QFrame#dsh-composer { background: #16181d; border-top: 1px solid #262b36; }
QPlainTextEdit#dsh-input {
    color: #e6e8ec; background: #1b1f27; border: 1px solid #333947;
}
QPushButton#dsh-retry, QPushButton#dsh-stop, QPushButton#dsh-context, QPushButton#dsh-new,
QPushButton#dsh-deny { background: #262b36; color: #e6e8ec; }
QPushButton#dsh-retry:hover, QPushButton#dsh-stop:hover, QPushButton#dsh-context:hover,
QPushButton#dsh-new:hover, QPushButton#dsh-deny:hover { background: #2f3644; }
QPushButton#dsh-allow:disabled, QPushButton#dsh-deny:disabled { background: #262b36; color: #6b7382; }
QScrollArea#message-scroll, QScrollArea#message-scroll QWidget#qt_scrollarea_viewport,
QWidget#message-view, QWidget#message-timeline { background: #16181d; }
QLabel#empty-state { color: #6f7887; }
QFrame#message-surface[role="user"] { background: #232833; }
QFrame#message-bubble[state="stopped"] { background: #1c2029; }
QFrame#message-surface[state="error"] {
    border: 1px solid #5a2a2a; background: #2a1c1c;
}
QFrame#message-surface[state="error"] QLabel#bubble-body { color: #ff9b93; }
QLabel#bubble-body { color: #e6e8ec; }
QLabel#bubble-status { color: #e0a14a; }
QFrame#message-tools QToolButton { color: #c9d0da; }
QFrame#message-tools QToolButton:hover { background: #262b36; }
QFrame#floating-composer { background: #16181d; }
QFrame#chat-composer { background: #1b1f27; border: 1px solid #333947; }
QFrame#attachment-chip { background: #1e222b; border: 1px solid #333947; }
QFrame#attachment-chip:hover { background: #252b36; border-color: #3f4757; }
QLabel#attachment-preview { background: #11131a; border: 1px solid #333947; }
QLabel#attachment-name { color: #d7dce6; }
QLabel#attachment-meta { color: #8b93a1; }
QPlainTextEdit#chat-input { color: #e6e8ec; }
QLabel#composer-hint { color: #7b8494; }
QLabel#composer-partial { color: #9aa3b4; }
QToolButton#composer-attach-button, QToolButton#composer-record-button { color: #c9d0da; }
QToolButton#composer-attach-button:hover, QToolButton#composer-record-button:hover { background: #262b36; }
QToolButton#send-button:disabled { background: #33405c; }
QScrollBar::handle:vertical { background: #3a414f; }
QScrollBar::handle:vertical:hover { background: #4b5364; }
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
    dark: bool = False,
) -> str:
    """Accent/font-substituted modern QSS plus an optional translucent overlay.

    ``dark=True`` 只是在浅色表后面追加 :data:`DARK_OVERRIDES`：浅色路径的行为与
    字节都不变，深色出错时删掉那一段就回到原样。
    """
    color = safe_accent(accent)
    size = clamp_font_size(font_size)
    sheet = MODERN_QSS.replace("@ACCENT@", color).replace("@FONT_SIZE@", str(size))
    sheet = _FONT_TOKEN_RE.sub(
        lambda match: str(_scaled_font_size(size, int(match.group(1)))), sheet
    )
    sheet = sheet.replace("@FONT_FAMILY@", safe_font_family(font_family))
    if dark:
        sheet += DARK_OVERRIDES.replace("@ACCENT@", color)
    return sheet + (overlay or "")
