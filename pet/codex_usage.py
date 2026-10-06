"""Native, non-activating Codex quota blood bars attached to a DSH PetWindow.

This controller is activated by the optional Codex companion entry point.
An optional read-only monitor checks quota once a minute and queues threshold
notices. Drag releases keep their upstream behavior. Network work runs outside
the Qt GUI thread.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import threading
import time
import urllib.request

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget, QPushButton
from .language_ui import text, quota_row_label
from .codex_quota_state import QuotaThresholdState
from .codex_usage_history import UsageHistory
from .codex_usage_chart import UsageTrendWindow, row_label, tr, palette
from .ui_polish import ui_text

DISPLAY_MS = 12000
POLL_MS = 60000
SETTING_KEY = "codex_usage_enabled"


def reminder_text(events):
    lines = []
    for event in events:
        row = event["row"]
        remaining = row["remainingPercent"]
        thresholds = event["thresholds"]
        label = quota_row_label(row)
        if len(thresholds) == 1:
            template = text("{label}已降至 {threshold}% 以下，目前 {remaining}%。")
            lines.append(template.format(label=label, threshold=thresholds[0], remaining="%g" % remaining))
        else:
            template = text("{label}已低於 {thresholds}，目前 {remaining}%。")
            lines.append(template.format(label=label, thresholds=" / ".join("%s%%" % value for value in thresholds), remaining="%g" % remaining))
    return "\n".join(lines)


class QuotaBubble(QWidget):
    row_clicked = Signal(object)
    hover_changed = Signal(bool)

    def __init__(self, owner=None):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
        super().__init__(owner, flags)
        self.setWindowTitle("Codex 剩餘用量")
        self.setObjectName("codexQuotaBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.rows = []
        self.message = "讀取用量中…"
        self.updated_at = None
        self.blocked = False
        self.pointer_x = 150
        self._animation_start = 0
        self._from = {}
        self._animation = QTimer(self)
        self._animation.setInterval(20)
        self._animation.timeout.connect(self._animate)
        self.set_loading()

    def _resize_rows(self):
        self.resize(328, 48 + max(1, len(self.rows)) * 57 + 30)
        self._sync_row_buttons()

    def _sync_row_buttons(self):
        if not hasattr(self, "_row_buttons"):
            self._row_buttons = []
        while len(self._row_buttons) < len(self.rows):
            index = len(self._row_buttons)
            button = QPushButton(self)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { background: transparent; border: none; border-radius: 8px; } QPushButton:hover { background: rgba(57,126,247,18); }"
            )
            button.clicked.connect(lambda checked=False, i=index: self._click_row(i))
            self._row_buttons.append(button)
        for index, button in enumerate(self._row_buttons):
            row = self.rows[index] if index < len(self.rows) else {}
            active = row.get("windowDurationMins") in (300, 10080)
            button.setGeometry(13, 40 + index * 57, self.width() - 26, 54)
            button.setVisible(active)
            button.setEnabled(active)
            button.setAccessibleName((row_label(row) + " · " + tr("open")) if active else "")
            button.setToolTip(tr("open"))
            button.raise_()

    def _click_row(self, index):
        if index < len(self.rows) and self.rows[index].get("windowDurationMins") in (300, 10080):
            self.row_clicked.emit(dict(self.rows[index]))

    def enterEvent(self, event):
        self.hover_changed.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_changed.emit(False)
        super().leaveEvent(event)

    def set_loading(self):
        self.rows = [{"label": "短期", "remainingPercent": None}, {"label": "長期", "remainingPercent": None}]
        self.message = "讀取用量中…"
        self.updated_at = None
        self._resize_rows()
        self.update()

    def set_error(self):
        self._animation.stop()
        self.rows = [{"label": "Codex", "remainingPercent": None}]
        self.message = "暫時無法讀取，點魚可重試"
        self.updated_at = None
        self._resize_rows()
        self.update()

    def set_snapshot(self, snapshot):
        self._from = {(row.get("limitId"), row.get("window")): row.get("remainingPercent") for row in self.rows}
        self.rows = snapshot.get("windows") or [{"label": "Codex", "remainingPercent": None}]
        self.updated_at = snapshot.get("updatedAt")
        self.blocked = bool(snapshot.get("usageBlocked"))
        self.message = "目前用量受限" if self.blocked else "帳號共用額度"
        if not any(row.get("remainingPercent") is not None for row in self.rows):
            self.message = "帳號尚未提供用量資料"
        self._animation_start = time.monotonic()
        self._resize_rows()
        self._animation.start()
        labels = ["%s 剩餘 %s%%" % (row["label"], row.get("remainingPercent")) for row in self.rows]
        self.setAccessibleName("Codex 剩餘用量：" + "、".join(labels))
        self.update()

    def _animate(self):
        if time.monotonic() - self._animation_start >= 0.28:
            self._animation.stop()
        self.update()

    @staticmethod
    def _font(size, bold=False):
        font = QFont("Microsoft JhengHei UI", size)
        font.setBold(bold)
        return font

    def paintEvent(self, event):
        owner = self.parentWidget()
        cfg = getattr(owner, "cfg", None)
        from .independent_ui import quota_style

        style = quota_style(cfg) if cfg is not None else "dark"
        c = palette(style)
        self.setProperty("dshQuotaTheme", style)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(5, 4, self.width() - 10, self.height() - 15)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(5, 12, 23, 35))
        painter.drawRoundedRect(body.translated(0, 3), 18, 18)
        painter.setPen(QPen(QColor(c["border"]), 1))
        if style == "glass":
            gradient = QLinearGradient(body.topLeft(), body.bottomLeft())
            gradient.setColorAt(0, QColor(249, 252, 255, 235))
            gradient.setColorAt(0.5, QColor(227, 239, 253, 215))
            gradient.setColorAt(1, QColor(247, 251, 255, 235))
            painter.setBrush(gradient)
        else:
            painter.setBrush(QColor(c["bg"]))
        painter.drawRoundedRect(body, 18, 18)
        px = max(22, min(self.width() - 22, self.pointer_x))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon(QPolygonF([QPointF(px - 7, body.bottom() - 1), QPointF(px + 7, body.bottom() - 1), QPointF(px, body.bottom() + 7)]))
        painter.setFont(self._font(10, True))
        painter.setPen(QColor(c["text"]))
        painter.drawText(QRectF(18, 11, self.width() - 36, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text("Codex 剩餘用量"))
        for index, row in enumerate(self.rows):
            y = 40 + index * 57
            remaining = row.get("remainingPercent")
            known = isinstance(remaining, (int, float)) and math.isfinite(remaining)
            color = QColor("#f07887" if known and remaining <= 20 else "#e6b268" if known and remaining <= 40 else c["blue"])
            card = QRectF(14, y, self.width() - 28, 52)
            surface = QColor(c["surface"])
            if style == "glass":
                surface.setAlpha(215)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(surface)
            painter.drawRoundedRect(card, 11, 11)
            label = row_label(row) if row.get("windowDurationMins") in (300, 10080) else quota_row_label(row)
            painter.setFont(self._font(9))
            painter.setPen(QColor(c["muted"]))
            label = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, self.width() - 122)
            painter.drawText(QRectF(24, y + 6, self.width() - 122, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            painter.setFont(self._font(14, True))
            painter.setPen(color if known else QColor(c["muted"]))
            painter.drawText(
                QRectF(self.width() - 96, y + 2, 70, 26), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "%g%%" % remaining if known else "—"
            )
            track = QRectF(24, y + 33, self.width() - 48, 9)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c["track"]))
            painter.drawRoundedRect(track, 4.5, 4.5)
            if known:
                elapsed = min(1, max(0, (time.monotonic() - self._animation_start) / 0.28))
                start = self._from.get((row.get("limitId"), row.get("window")))
                start = remaining if start is None else start
                value = start + (remaining - start) * (1 - (1 - elapsed) ** 3)
                tip = track.left() + 4.5 + (track.width() - 9) * max(0, min(100, value)) / 100
                clip = QPainterPath()
                clip.addRoundedRect(track, 4.5, 4.5)
                painter.save()
                painter.setClipPath(clip)
                gradient = QLinearGradient(track.topLeft(), track.topRight())
                gradient.setColorAt(0, color.darker(120))
                gradient.setColorAt(1, color)
                painter.setBrush(gradient)
                if value > 0:
                    painter.drawRect(QRectF(track.left(), track.top(), tip - track.left(), track.height()))
                painter.restore()
                painter.setBrush(QColor(c["text"]))
                painter.drawEllipse(QPointF(tip, track.center().y()), 4.5, 4.5)
        painter.setFont(self._font(8))
        painter.setPen(QColor(c["muted"]))
        footer = text(self.message)
        if self.updated_at and not self.blocked:
            footer = ui_text("updated") + " " + time.strftime("%H:%M", time.localtime(self.updated_at)) + " · " + ui_text("trend_hint")
        footer = painter.fontMetrics().elidedText(footer, Qt.TextElideMode.ElideRight, self.width() - 36)
        painter.drawText(QRectF(18, 44 + len(self.rows) * 57, self.width() - 36, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, footer)
        painter.end()


class QuotaController(QObject):
    received = Signal(object)

    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.popup = QuotaBubble(pet)
        self.snapshot = None
        self.pending = False
        self.enabled = False
        self._monitor_ready = False
        self.config_path = Path(pet.cfg.path)
        self._config_stamp = None
        self.state = QuotaThresholdState(self.config_path.parent / "codex-quota-reminders.json")
        self.history = UsageHistory(self.config_path.parent / "codex-usage-history.json")
        self.trends = None
        self._read_failed = False
        logging.info("Codex usage history and clickable trends loaded")
        self.popup.row_clicked.connect(self.open_history)
        self.popup.hover_changed.connect(self._popup_hover)
        self._next_notice = 0
        self.dismiss = QTimer(self)
        self.dismiss.setSingleShot(True)
        self.dismiss.timeout.connect(self.hide)
        self.follow = QTimer(self)
        self.follow.setInterval(40)
        self.follow.timeout.connect(self._position)
        self.received.connect(self._accept)
        pet.installEventFilter(self)
        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self._request)
        self.settings_watch = QTimer(self)
        self.settings_watch.setInterval(1000)
        self.settings_watch.timeout.connect(self._sync_settings)
        self.settings_watch.start()
        self.notices = QTimer(self)
        self.notices.setInterval(2000)
        self.notices.timeout.connect(self._drain_notices)
        self._sync_settings(force=True)

    def _sync_settings(self, force=False):
        try:
            stamp = self.config_path.stat().st_mtime_ns
            if not force and stamp == self._config_stamp:
                return
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            self._config_stamp = stamp
            enabled = data.get(SETTING_KEY, True)
        except (OSError, ValueError):
            enabled = self.pet.cfg.get(SETTING_KEY, True) if force else self.enabled
        enabled = enabled if isinstance(enabled, bool) else True
        if enabled == self.enabled:
            return
        self.enabled = enabled
        self._monitor_ready = False
        if enabled:
            self.poll.start()
            self.notices.start()
            # Give the startup animations and language manager time to settle.
            QTimer.singleShot(2000, self._request)
        else:
            self.poll.stop()
            self.notices.stop()
            self.hide()
            if self.trends is not None:
                self.trends.hide()
        logging.info("Codex quota display and reminders %s", "enabled" if enabled else "disabled")

    def toggle(self):
        self._sync_settings()
        if not self.enabled:
            return
        bubble = getattr(self.pet, "_speech_bubble", None)
        if not (
            getattr(self.pet, "_sticky_bubble_active", False) or getattr(self.pet, "_alert_current", None) or getattr(bubble, "_interactive_active", False)
        ):
            self.pet.hide_speech_bubble()
        if self.popup.isVisible():
            self.hide()
            return
        if self.snapshot:
            self.popup.set_snapshot(self.snapshot)
        else:
            self.popup.set_loading()
        self._position()
        self.popup.show()
        self.popup.raise_()
        self.follow.start()
        self.dismiss.start(DISPLAY_MS)
        logging.info("Codex quota bars opened by pet click")
        self._request()

    def _popup_hover(self, hovered):
        if hovered:
            self.dismiss.stop()
        elif self.popup.isVisible():
            self.dismiss.start(DISPLAY_MS)

    def open_history(self, row):
        if not self.enabled:
            return
        if self.trends is None:
            self.trends = UsageTrendWindow(self)
        self.hide()
        self.trends.open_row(row)
        logging.info("Codex local usage trend opened: %s", row.get("windowDurationMins"))

    def _request(self):
        self._sync_settings()
        if not self.enabled or self.pending:
            return
        self.pending = True
        threading.Thread(target=self._fetch, name="codex-quota-read", daemon=True).start()

    def _fetch(self):
        try:
            config_path = os.environ.get("DSH_CODEX_BRIDGE_CONFIG")
            path = Path(config_path) if config_path else self.config_path.parent / "codex-bridge.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            request = urllib.request.Request("http://127.0.0.1:%d/v1/usage" % config["port"], headers={"Authorization": "Bearer " + config["localToken"]})
            # Do not send local authorization through a configured HTTP proxy.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=18) as response:
                value = json.load(response)
            if not value.get("ok"):
                raise RuntimeError("Usage unavailable")
        except Exception:
            value = {"ok": False}
        try:
            self.received.emit(value)
        except RuntimeError:
            pass  # Pet was closed while the bounded read was in progress.

    def _accept(self, value):
        self._sync_settings()
        self.pending = False
        if not self.enabled:
            return
        if value.get("ok"):
            self._read_failed = False
            self._monitor_ready = True
            self.snapshot = value
            self.popup.set_snapshot(value)
            self.state.observe(value)
            self.history.observe(value)
            if self.trends is not None and self.trends.isVisible():
                self.trends.refresh()
            logging.info("Codex quota bars loaded: %s", [(row["label"], row.get("remainingPercent")) for row in value.get("windows", [])])
        else:
            self._read_failed = True
            if self.trends is not None and self.trends.isVisible():
                self.trends.refresh()
            self.popup.set_error()
            logging.warning("Codex quota read unavailable")
        if self.popup.isVisible():
            self._position()
            if not self.popup.underMouse():
                self.dismiss.start(DISPLAY_MS)
        self._drain_notices()

    def _drain_notices(self):
        if not self.enabled or not self._monitor_ready or not self.state.pending or time.monotonic() < self._next_notice:
            return
        bubble = getattr(self.pet, "_speech_bubble", None)
        if (
            not self.pet.isVisible()
            or self.popup.isVisible()
            or (self.trends is not None and self.trends.isVisible())
            or getattr(self.pet, "_bubble_suppressed", False)
            or getattr(self.pet, "_sticky_bubble_active", False)
            or getattr(self.pet, "_alert_current", None)
            or getattr(bubble, "_interactive_active", False)
        ):
            return
        events = self.state.pending[:2]
        self.pet.show_bubble(reminder_text(events), duration_ms=DISPLAY_MS, subtitle=text("Codex 額度提醒"))
        self.state.acknowledge(len(events))
        self._next_notice = time.monotonic() + DISPLAY_MS / 1000 + 2
        logging.info("Codex quota threshold notice displayed: %s", [(event["row"].get("label"), event["thresholds"]) for event in events])

    def _position(self):
        if not self.pet.isVisible():
            if self.popup.isVisible():
                self.hide()
            return
        body = self.pet.collision_content_rect()
        screen = QGuiApplication.screenAt(body.center()) or self.pet.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = body.center().x() - self.popup.width() // 2
        y = body.top() - self.popup.height() - 6
        x = max(available.left() + 3, min(x, available.right() - self.popup.width() - 3))
        y = max(available.top() + 3, min(y, available.bottom() - self.popup.height() - 3))
        pointer_x = body.center().x() - x
        changed = self.popup.pointer_x != pointer_x or self.popup.pos() != QPoint(x, y)
        self.popup.pointer_x = pointer_x
        if self.popup.pos() != QPoint(x, y):
            self.popup.move(x, y)
        if changed:
            self.popup.update()

    def hide(self):
        self.dismiss.stop()
        self.follow.stop()
        self.popup.hide()
        self.popup._animation.stop()

    def eventFilter(self, watched, event):
        if watched is self.pet:
            if event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
                self.hide()
                if self.trends is not None:
                    self.trends.hide()
                if event.type() == QEvent.Type.Close:
                    self.enabled = False
                    self.poll.stop()
                    self.settings_watch.stop()
                    self.notices.stop()
            elif self.popup.isVisible() and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
                self._position()
        return False


def toggle_for(pet):
    if getattr(pet, "_just_dragged", False):
        return
    try:
        controller = getattr(pet, "_codex_quota_controller", None)
        if controller is None:
            controller = QuotaController(pet)
            pet._codex_quota_controller = controller
        controller.toggle()
    except Exception:
        logging.exception("Cannot show Codex quota bars")


def install_for(pet):
    if getattr(pet, "_codex_quota_controller", None) is None:
        pet._codex_quota_controller = QuotaController(pet)


def add_settings_control(dialog):
    from PySide6.QtWidgets import QScrollArea
    from .settings_widgets import ToggleSwitch, SettingRow, SettingsSection
    from .language_ui import install

    switch = ToggleSwitch(dialog)
    switch.setObjectName("dshCodexUsageEnabled")
    switch.setAccessibleName("Codex 剩餘用量")
    switch.setChecked(dialog.config.get(SETTING_KEY, True))
    dialog.codex_usage_switch = switch
    row = SettingRow(SETTING_KEY, "Codex 剩餘用量", "點擊顯示額度血條；每分鐘檢查，在剩餘 75%、50%、25% 時提醒。關閉後停止查詢與提醒。", switch)
    scroll = dialog.pages.widget(0).findChild(QScrollArea, "settingsScroll")
    content = scroll.widget()
    section = SettingsSection("Codex 用量", [row], content)
    content.layout().insertWidget(1, section)
    dialog._search_rows.append(row)

    def fit_settings(_index=None):
        # English labels need more room with the wider navigation pane.
        dialog.setMinimumWidth(720)

    dialog.ui_language_select.currentIndexChanged.connect(fit_settings)
    fit_settings()
    manager = install(dialog.config)
    if manager is not None:
        manager.translate_tree(dialog)
