"""Interactive hidden-pet chat hosted inside the Dynamic Island surface."""

from __future__ import annotations
import logging

logging.info("Hidden-pet embedded Island chat loaded")
from PySide6.QtCore import QObject, QEvent, QRect, QSize, Qt
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QSizePolicy


def controller(island):
    return getattr(island, "_dsh_chat_controller", None)


def embedded(bubble):
    value = getattr(bubble, "_dsh_embedded_controller", None)
    return value if value is not None and value.opened else None


class IslandChatController(QObject):
    def __init__(self, island):
        super().__init__(island)
        self.island = island
        self.bubble = None
        self.opened = False
        self.changing_flags = False
        self.host = QFrame(island)
        self.host.setObjectName("island-chat-host")
        self.host.setStyleSheet("QFrame#island-chat-host { background: transparent; border: none; }")
        self.layout = QVBoxLayout(self.host)
        self.layout.setContentsMargins(18, 9, 18, 15)
        self.layout.setSpacing(0)
        self.host.hide()
        island.installEventFilter(self)

    def focusable(self, enabled):
        island = self.island
        flags = island.windowFlags()
        changed = flags & ~Qt.WindowType.WindowDoesNotAcceptFocus if enabled else self.saved_flags
        if flags == changed:
            return
        visible, rect = island.isVisible(), island.geometry()
        self.changing_flags = True
        try:
            island.setWindowFlags(changed)
            island.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not enabled)
            island.setGeometry(rect)
            if visible:
                island.show()
        finally:
            self.changing_flags = False

    def open(self, bubble):
        island = self.island
        if self.opened:
            self.refresh()
            island.raise_()
            island.activateWindow()
            QApplication.setActiveWindow(island)
            bubble.input.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.bubble = bubble
        self.saved_flags = island.windowFlags()
        self.opened = True
        bubble._auto_collapse.stop()
        bubble.hide()
        bubble._anchor = island
        bubble._dsh_embedded_controller = self
        bubble.setParent(self.host, Qt.WindowType.Widget)
        bubble.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        bubble.setAutoFillBackground(False)
        bubble.setMinimumWidth(0)
        bubble.setMaximumWidth(16777215)
        bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.layout.addWidget(bubble)
        bubble.installEventFilter(self)
        bubble.input.installEventFilter(self)
        bubble.output_scroll.setMinimumHeight(80)
        bubble.output_scroll.setMaximumHeight(16777215)
        bubble.output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.focusable(True)
        if island._mode != "expanded":
            island.expand_card()
        island._card_collapse_timer.stop()
        island._dock_back_timer.stop()
        island._hover_scale_target = 1.0
        island._card_box.hide()
        work = getattr(island, "_codex_work_controller", None)
        if work is not None:
            work.seen_timer.stop()
        self.refresh()
        island._geo_from = island._geo_to = None
        island._set_free_geometry(island._target_rect())
        island._apply_fixed_size()
        self.place()
        self.host.show()
        bubble.show()
        self.host.raise_()
        island.raise_()
        island.activateWindow()
        QApplication.setActiveWindow(island)
        bubble.input.setFocus(Qt.FocusReason.OtherFocusReason)
        # A quick-chat reply previously shown in a floating preview becomes
        # fully readable in the embedded scroll area without changing storage.
        bubble._set_reply_text(bubble._reply_full)
        bubble._render_reply()
        island._emit_geometry_changed()

    def place(self):
        self.host.setGeometry(0, 44, self.island.width(), max(0, self.island.height() - 44))
        self.layout.activate()

    def refresh(self):
        if not self.opened:
            return
        from .ui_polish import colors, common_qss, apply_palette
        from .language_ui import language

        bubble, island = self.bubble, self.island
        style = island._cfg.get("style", "dark")
        c = colors("light" if style in ("light", "glass") else "dark")
        apply_palette(bubble, c)
        bubble.layout().setContentsMargins(0, 0, 0, 0)
        bubble.layout().setSpacing(10)
        bubble.page_widget.hide()
        bubble.title_label.setText({"en": "Island chat", "zh_CN": "灵动岛对话"}.get(language(), "靈動島對話"))
        bubble.close_btn.setToolTip({"en": "Back to status", "zh_CN": "返回工作状态"}.get(language(), "返回工作狀態"))
        bubble.show_pet_btn.setText({"en": "Show pet", "zh_CN": "显示桌宠"}.get(language(), "顯示桌寵"))
        bubble.input.setMinimumHeight(34)
        bubble.input.setMinimumWidth(0)
        bubble.send_btn.setMinimumHeight(34)
        bubble.show_pet_btn.setMinimumHeight(30)
        qss = """
        QFrame#quick-chat-bubble { background: transparent; border: none; }
        QLabel { color: TEXT; background: transparent; border: none; }
        QLabel#quick-chat-hint { color: MUTED; font-size: 11px; }
        QLabel#quick-chat-title { font-size: 14px; font-weight: 600; }
        QLabel#quick-chat-output { font-size: 14px; padding: 2px; }
        QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
        QPushButton { color: TEXT; background: PILL; border: 1px solid BORDER; border-radius: 9px; padding: 3px 10px; }
        QPushButton:hover { background: HOVER; border-color: BLUE; }
        QPushButton#quick-chat-send { color: #ffffff; background: PRIMARY; border: none; padding: 4px 12px; }
        QPushButton#quick-chat-send:hover { background: #5595ff; }
        QPushButton#quick-chat-close { color: MUTED; background: transparent; border: none; padding: 0; font-size: 19px; }
        QPushButton#quick-chat-close:hover { color: TEXT; background: HOVER; }
        QLineEdit { color: TEXT; background: FIELD; border: 1px solid BORDER; border-radius: 11px; padding: 4px 10px; selection-background-color: PRIMARY; }
        QLineEdit:focus { border-color: BLUE; }
        """
        for token in sorted(c, key=len, reverse=True):
            qss = qss.replace(token.upper(), c[token])
        bubble.setStyleSheet(qss + common_qss(c))
        bubble.setProperty("dshEmbeddedChatTheme", style)
        island._card_box.hide()
        island._card_collapse_timer.stop()
        self.place()
        bubble.update()

    def closed(self):
        if not self.opened:
            return
        self.opened = False
        self.host.hide()
        self.bubble._auto_collapse.stop()
        self.focusable(False)
        island = self.island
        island._sync_card_labels()
        if island._mode == "expanded":
            island._card_box.show()
            island._geo_from = island._geo_to = None
            island._set_free_geometry(island._target_rect())
            island._apply_fixed_size()
            island._card_collapse_timer.start()
            work = getattr(island, "_codex_work_controller", None)
            if work is not None:
                work.viewed()
        island._emit_geometry_changed()

    def close(self):
        if self.opened:
            self.bubble.close()

    def eventFilter(self, watched, event):
        if watched is self.island and event.type() == QEvent.Type.Resize and self.opened:
            self.place()
        if self.opened and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close()
            return True
        return super().eventFilter(watched, event)


def show(bubble, island, activate, reply_text, original):
    if not activate:
        if embedded(bubble):
            return  # Keep an interactive request and draft intact.
        detach(bubble)
        return original(bubble, island, activate=False, reply_text=reply_text)
    value = controller(island)
    if value is None:
        value = IslandChatController(island)
        island._dsh_chat_controller = value
    if reply_text is not None and not bubble.service.busy:
        bubble.show_reply(reply_text)
    value.open(bubble)


def detach(bubble):
    value = getattr(bubble, "_dsh_embedded_controller", None)
    if value is None:
        return
    value.layout.removeWidget(bubble)
    bubble._dsh_embedded_controller = None
    bubble.setParent(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
    bubble.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    bubble.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    bubble.setMinimumWidth(320)
    bubble.setMaximumWidth(460)
    bubble.output_scroll.setMaximumHeight(220)
    from .ui_polish import quick_style

    quick_style(bubble)


def rest_size(island, size):
    value = controller(island)
    if value is None or not value.opened or island._mode != "expanded":
        return size
    screen = island._current_screen()
    available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
    return QSize(min(max(340, size.width()), available.width()), min(380, max(260, available.height() - 16)))


def refresh(island):
    value = controller(island)
    if value is not None:
        value.refresh()


def close(island):
    value = controller(island)
    if value is not None:
        value.close()


def closed(bubble):
    value = embedded(bubble)
    if value is not None:
        value.closed()


def render(bubble):
    if embedded(bubble) is None:
        return False
    bubble._pages = []
    bubble.page_widget.hide()
    bubble.output.setText(bubble._reply_full)
    return True
