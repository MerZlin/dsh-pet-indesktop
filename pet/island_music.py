"""Theme-aware round music controls revealed by the native island's hover motion."""

from __future__ import annotations
import asyncio
import logging
import queue
import threading
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractButton, QWidget

EXTRA_WIDTH = 120


def music_open(island):
    return getattr(island, "_music_requested", False) and island._mode != "expanded" and not (island._mode == "docked" and not island._hover_peek)


def labels(island, playing=False):
    locale = island.config.get("ui_language", "zh_TW")
    if locale == "en":
        return ("Previous track", "Pause" if playing else "Play", "Next track", "Open YouTube Music and start a song to connect")
    if locale == "zh_CN":
        return ("上一首", "暂停" if playing else "播放", "下一首", "请先在 YouTube Music 播放歌曲")
    return ("上一首", "暫停" if playing else "播放", "下一首", "請先在 YouTube Music 播放歌曲")


class RoundMusicButton(QAbstractButton):
    def __init__(self, bar, kind):
        super().__init__(bar)
        self.bar, self.kind = bar, kind
        self.playing = False
        self.setFixedSize(32, 32)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setObjectName("island-music-" + kind)

    def enterEvent(self, event):
        self.bar.island._music_close_timer.stop()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        island = self.bar.island
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _, primary, _ = island._style_palette()
        accent = QColor(island._accent_color())
        foreground = QColor(primary)
        edge = QColor(island._keyline_color())
        fill = QColor(primary)
        center = self.kind == "toggle"
        if self.isDown():
            fill = QColor(accent)
            fill.setAlpha(85)
            edge = QColor(accent)
            edge.setAlpha(180)
        elif self.underMouse() and self.isEnabled():
            fill = QColor(accent)
            fill.setAlpha(48)
            edge = QColor(accent)
            edge.setAlpha(145)
        elif center:
            fill = QColor(accent)
            fill.setAlpha(24)
            edge = QColor(accent)
            edge.setAlpha(82)
        else:
            fill.setAlpha(9)
            edge.setAlpha(36 if island._cfg.get("style", "dark") == "dark" else 25)
        if not self.isEnabled():
            foreground.setAlpha(80)
            fill.setAlpha(7)
            edge.setAlpha(22)
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(fill)
        painter.drawEllipse(QRectF(2.5, 2.5, 27, 27))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(foreground)
        if center:
            if self.playing:
                painter.drawRoundedRect(QRectF(11, 10, 3.5, 12), 1.1, 1.1)
                painter.drawRoundedRect(QRectF(17.5, 10, 3.5, 12), 1.1, 1.1)
            else:
                path = QPainterPath()
                path.moveTo(13, 10)
                path.lineTo(22, 16)
                path.lineTo(13, 22)
                path.closeSubpath()
                painter.drawPath(path)
        else:
            if self.kind == "previous":
                painter.drawRoundedRect(QRectF(9, 10, 2, 12), 0.8, 0.8)
                points = (QPointF(22, 10), QPointF(12, 16), QPointF(22, 22))
            else:
                painter.drawRoundedRect(QRectF(21, 10, 2, 12), 0.8, 0.8)
                points = (QPointF(10, 10), QPointF(20, 16), QPointF(10, 22))
            painter.drawPolygon(QPolygonF(points))
        painter.end()


class MusicBar(QWidget):
    snapshot_ready = Signal(object)

    def __init__(self, island):
        super().__init__(island)
        self.island = island
        self.setObjectName("island-music-controls")
        self.setFixedSize(116, 36)
        self._busy = False
        self._last_state = None
        self._pending_action = None
        self._jobs = queue.Queue()
        self._thread = None
        self.destroyed.connect(lambda unused=None, jobs=self._jobs: jobs.put("__quit__"))
        self.buttons = {}
        for index, kind in enumerate(("previous", "toggle", "next")):
            button = RoundMusicButton(self, kind)
            button.move(8 + index * 36, 2)
            button.clicked.connect(lambda checked=False, action=kind: self.command(action))
            self.buttons[kind] = button
        self.snapshot_ready.connect(self.received)
        self.poll = QTimer(self)
        self.poll.setInterval(1000)
        self.poll.timeout.connect(self.refresh)
        self.received({"available": False, "playing": False, "previous": False, "next": False})
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        color = QColor(self.island._keyline_color())
        color.setAlpha(42 if self.island._cfg.get("style", "dark") == "dark" else 25)
        painter.setPen(QPen(color, 1))
        painter.drawLine(QPointF(0.5, 10), QPointF(0.5, 26))
        painter.end()

    def refresh(self):
        if not self._busy and self.isVisible():
            self._worker(None)

    def command(self, action):
        if self._busy:
            self._pending_action = action
            return
        self.island._music_close_timer.stop()
        self._worker(action)

    def _worker(self, action):
        self._busy = True
        if action:
            for button in self.buttons.values():
                button.setEnabled(False)

        def run():
            while True:
                action = self._jobs.get()
                if action == "__quit__":
                    return

                async def operation():
                    from . import now_playing
                    from .ytmusic import pick_control_session

                    success = None
                    if action == "toggle":
                        success = await now_playing._play_pause_async()
                    elif action:
                        success = await now_playing._skip_async(action == "previous")
                    if action:
                        logging.info("Island music control: %s, success=%s", action, success)
                        # Let the player publish its new status before updating the icon.
                        await asyncio.sleep(0.16)
                    session = await pick_control_session()
                    if session is None:
                        return {"available": False, "playing": False, "previous": False, "next": False, "success": success}
                    info = session.get_playback_info()
                    controls = info.controls
                    status = int(info.playback_status)
                    return {
                        "available": True,
                        "playing": status == 4,
                        "toggle": bool(getattr(controls, "is_play_pause_toggle_enabled", True)),
                        "previous": bool(getattr(controls, "is_previous_enabled", False)),
                        "next": bool(getattr(controls, "is_next_enabled", False)),
                        "success": success,
                    }

                try:
                    payload = asyncio.run(asyncio.wait_for(operation(), timeout=5))
                except Exception as error:
                    logging.info("Island music session unavailable: %s", type(error).__name__)
                    payload = {"available": False, "playing": False, "previous": False, "next": False}
                try:
                    self.snapshot_ready.emit(payload)
                except RuntimeError:
                    return

        self._jobs.put(action)
        if self._thread is None:
            self._thread = threading.Thread(target=run, name="island-music-control", daemon=True)
            self._thread.start()

    def received(self, state):
        self._busy = False
        self._last_state = state
        self.buttons["toggle"].playing = bool(state.get("playing"))
        texts = labels(self.island, bool(state.get("playing")))
        for index, kind in enumerate(("previous", "toggle", "next")):
            button = self.buttons[kind]
            enabled = bool(state.get("available") and state.get(kind, kind == "toggle"))
            button.setEnabled(enabled)
            button.setAccessibleName(texts[index])
            button.setToolTip(texts[index] if state.get("available") else texts[3])
            button.update()
        if self._pending_action is not None:
            action, self._pending_action = self._pending_action, None
            self._worker(action)


def attach(island):
    island._music_requested = False
    island._music_anchor = None
    island._music_bar = MusicBar(island)
    island._music_close_timer = QTimer(island)
    island._music_close_timer.setSingleShot(True)
    island._music_close_timer.setInterval(220)
    island._music_close_timer.timeout.connect(lambda: close_if_outside(island))
    logging.info("Island hover music controls loaded: three round buttons, native theme")


def rest_size(island, original_size):
    if music_open(island):
        return QSize(original_size.width() + EXTRA_WIDTH, original_size.height())
    return original_size


def target_rect(island, original_rect):
    anchor = getattr(island, "_music_anchor", None)
    if anchor is not None and island._mode == "normal":
        return island._clamp_rect(QRect(anchor.topLeft(), original_rect.size()))
    return original_rect


def layout(island):
    bar = getattr(island, "_music_bar", None)
    if bar is None:
        return
    bar.move(island._capsule_width() - 8, 4)
    active = music_open(island)
    bar.setVisible(active)
    if active:
        bar.raise_()
        if not bar.poll.isActive():
            bar.poll.start()
            bar.refresh()
    else:
        bar.poll.stop()


def enter(island):
    if not hasattr(island, "_music_bar") or island._mode == "expanded":
        return
    island._music_close_timer.stop()
    if not island._music_requested and island._mode == "normal":
        if island._music_anchor is None:
            island._music_anchor = QRect(island.geometry())
    island._music_requested = True
    island._hover_scale_target = 1.0
    island._animate_to(island._target_rect())
    layout(island)


def leave(island):
    if hasattr(island, "_music_close_timer"):
        island._music_close_timer.start()


def close_if_outside(island):
    if island.rect().contains(island.mapFromGlobal(QCursor.pos())):
        return
    close(island)


def close(island):
    if not hasattr(island, "_music_bar"):
        return
    island._music_requested = False
    island._music_bar.hide()
    island._music_bar.poll.stop()
    island._animate_to(island._target_rect())


def suspend(island):
    if not hasattr(island, "_music_bar"):
        return
    island._music_close_timer.stop()
    island._music_requested = False
    island._music_bar.hide()
    island._music_bar.poll.stop()


def reset(island):
    suspend(island)
    island._music_anchor = None


def drag(island):
    if getattr(island, "_dragging", False) and hasattr(island, "_music_bar"):
        suspend(island)
        island._music_anchor = None
        island._apply_fixed_size()
        island._emit_geometry_changed()
