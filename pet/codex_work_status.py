"""Native Codex status card and short input reminders; one background reader."""

from __future__ import annotations
import json, logging, os, time
from pathlib import Path
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from .codex_work_reader import CodexWorkReader, NoticeLedger

COPY = {
    "heading": ("Codex 工作狀態", "Codex work status", "Codex 工作状态"),
    "active_count": ("{count} 進行中", "{count} active", "{count} 进行中"),
    "empty": ("目前沒有新的工作通知", "No new work notifications", "目前没有新的工作通知"),
    "loading": ("正在讀取 Codex 工作狀態…", "Reading Codex work status…", "正在读取 Codex 工作状态…"),
    "disabled": ("Codex 工作狀態已關閉", "Codex work status is off", "Codex 工作状态已关闭"),
    "unavailable": ("暫時無法讀取 Codex 狀態", "Codex status temporarily unavailable", "暂时无法读取 Codex 状态"),
    "thinking": ("思考中", "Thinking", "思考中"),
    "coding": ("大肥魚敲代碼", "Big Fish is coding", "大肥鱼敲代码"),
    "long_thinking": ("雷霆大思考", "Thunder Thinking", "雷霆大思考"),
    "completed": ("已完成", "Completed", "已完成"),
    "attention": ("需要你操作", "Needs input", "需要你操作"),
    "failed": ("需要查看", "Check error", "需要查看"),
    "notice": (
        "Codex 需要你操作\n{title}\n請回到 Codex 查看。",
        "Codex needs your input\n{title}\nPlease check Codex.",
        "Codex 需要你操作\n{title}\n请回到 Codex 查看。",
    ),
}


def text(key):
    from .language_ui import language

    return COPY[key][{"zh_TW": 0, "en": 1, "zh_CN": 2}.get(language(), 0)]


def notification_scroll_style(style, c):
    # A six-pixel capsule with a small gap from the notification cards.
    thumb = {"dark": "#718fb6", "light": "#b1c5e2", "glass": "#a8c0df"}.get(style, "#718fb6")
    return (
        """
 QScrollArea, QScrollArea QWidget { background: transparent; border: none; }
 QScrollBar:vertical { background: transparent; border: none; width: 10px; margin: 3px 0 3px 4px; }
 QScrollBar::handle:vertical { background: THUMB; border: none; border-radius: 3px; min-height: 28px; }
 QScrollBar::handle:vertical:hover { background: HOVER; }
 QScrollBar::handle:vertical:pressed { background: PRESSED; }
 QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { width: 0; height: 0; border: none; background: transparent; }
 QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical { width: 0; height: 0; background: transparent; }
 QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; border: none; }
 """.replace("THUMB", thumb)
        .replace("HOVER", c["blue"])
        .replace("PRESSED", c["primary"])
    )


class ReaderWorker(QObject):
    finished = Signal(object)

    def __init__(self, hook_root):
        super().__init__()
        self.reader = CodexWorkReader(hook_root=hook_root)

    def read(self):
        try:
            self.finished.emit(self.reader.read())
        except Exception:
            self.finished.emit({"ok": False, "rows": []})


class WorkController(QObject):
    request = Signal()

    def __init__(self, island):
        super().__init__(island)
        self.island = island
        self.snapshot = {"ok": True, "rows": []}
        self._loaded = False
        self._clock = time.monotonic
        self._phase_started = {}
        profile = Path(island.config.path).parent
        self.ledger = NoticeLedger(profile / "codex-work-seen.json")
        self.cache = profile / "codex-work-status.json"
        self.visible = []
        self._key = None
        self._busy = False
        self.notified = set()
        self.pending = []
        self._shown = []
        self._ack_rows = []
        self._pet = None
        box = QWidget(island._card_box)
        box.setObjectName("codexWorkBox")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(5)
        self.heading = QLabel(box)
        self.heading.setFont(QFont("Microsoft JhengHei UI", 9))
        layout.addWidget(self.heading)
        self.scroll = QScrollArea(box)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setObjectName("codexWorkScroll")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().setObjectName("codexWorkScrollBar")
        self.content = QWidget()
        self.rows_layout = QVBoxLayout(self.content)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(5)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll)
        island._card_box.layout().insertWidget(2, box)
        self.box = box
        island._card_message_label.hide()
        self.thread = QThread(self)
        self.worker = ReaderWorker(
            Path(os.environ["DSH_CODEX_BRIDGE_CONFIG"]).parent / "events" if os.environ.get("DSH_CODEX_BRIDGE_CONFIG") else profile / "codex-events"
        )
        self.worker.moveToThread(self.thread)
        self.request.connect(self.worker.read)
        self.worker.finished.connect(self.accept)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()
        self.poll = QTimer(self)
        self.poll.setInterval(1500)
        self.poll.timeout.connect(self.tick)
        self.poll.start()
        self.seen_timer = QTimer(self)
        self.seen_timer.setSingleShot(True)
        self.seen_timer.timeout.connect(self.acknowledge)
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.restore)
        island.card_expanded.connect(self.viewed)
        self.scroll.verticalScrollBar().valueChanged.connect(self.viewed)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.refresh()
        QTimer.singleShot(150, self.tick)
        island.installEventFilter(self)
        logging.info("Codex local work status and viewed-notice tracking loaded")

    def enabled(self):
        return bool(self.island.config.get("codex_work_status_enabled", True))

    def tick(self):
        if not self.enabled():
            self.pending = []
            self.restore()
            self.visible = []
            self.refresh()
            return
        if not self._busy:
            self._busy = True
            self.request.emit()
        self.show_notice()

    def accept(self, snapshot):
        self._busy = False
        # Reading and notification delivery continue while the island is collapsed.
        # Visibility is relevant only when acknowledging a notice as viewed.
        if not self.enabled():
            return
        if not snapshot.get("ok"):
            self.snapshot["ok"] = False
            self.refresh()
            return
        self._loaded = True
        self.snapshot = snapshot
        clock = self._clock()
        updated = float(snapshot.get("updatedAt", time.time()))
        phases = {}
        for row in snapshot["rows"]:
            phase = (row["turnId"], row["state"])
            previous = self._phase_started.get(row["threadId"])
            # Repeated reasoning updates keep the same timer; a new turn/state resets it.
            if previous is not None and previous[:2] == phase:
                phases[row["threadId"]] = previous
            else:
                age = max(0, min(12 * 3600, updated - float(row.get("timestamp", updated)))) if row["state"] == "thinking" else 0
                phases[row["threadId"]] = (*phase, clock - age)
        self._phase_started = phases
        key = tuple((r["threadId"], r["title"], r["signature"]) for r in snapshot["rows"])
        self.visible = self.ledger.visible(snapshot["rows"])
        if key != self._key:
            self._key = key
            try:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                temp = self.cache.with_suffix(".tmp")
                temp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
                os.replace(temp, self.cache)
            except OSError:
                pass
        for row in self.visible:
            if row["state"] == "attention" and row["signature"] not in self.notified:
                self.notified.add(row["signature"])
                self.pending.append(dict(row))
        self.pending = [r for r in self.pending if any(v["signature"] == r["signature"] for v in self.visible)]
        self.refresh()
        self.show_notice()

    def refresh(self):
        island = self.island
        island._card_message_label.hide()
        from .ui_polish import colors

        style = island._cfg.get("style", "dark")
        c = colors("light" if style in ("light", "glass") else "dark")
        active_count = sum(row["state"] in ("thinking", "coding") for row in self.visible) if self.enabled() else 0
        heading = text("heading")
        if active_count > 1:
            heading += " · " + text("active_count").format(count=active_count)
        self.heading.setText(heading)
        self.heading.setStyleSheet("color: %s; font-size: 11px;" % c["muted"])
        rows = self.visible if self.enabled() else []
        render_key = (
            tuple((r["title"], r["state"], r["signature"], self.state_caption(r)) for r in rows),
            style,
            text("heading"),
            self.enabled(),
            self.snapshot.get("ok"),
            self._loaded,
        )
        if render_key == getattr(self, "_render_key", None):
            return
        self._render_key = render_key
        self._shown = list(rows)
        self._record_frames = []
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not rows:
            empty = QLabel(
                text("disabled")
                if not self.enabled()
                else text("unavailable")
                if not self.snapshot.get("ok")
                else text("empty")
                if self._loaded
                else text("loading")
            )
            empty.setStyleSheet("color: %s; font-size: 12px; padding: 8px 0;" % c["muted"])
            self.rows_layout.addWidget(empty)
        for row in rows:
            frame = QFrame(self.content)
            frame.setObjectName("codexWorkRow")
            frame.setFixedHeight(37)
            line = QHBoxLayout(frame)
            line.setContentsMargins(9, 0, 9, 0)
            line.setSpacing(5)
            title = QLabel(frame)
            title.setMinimumWidth(0)
            caption = self.state_caption(row)
            badge = QLabel(caption, frame)
            badge.setObjectName("codexWorkState")
            badge.setFont(QFont("Microsoft JhengHei UI", 8))
            badge.ensurePolished()
            badge_width = badge.fontMetrics().horizontalAdvance(badge.text()) + 15
            badge.setFixedWidth(badge_width)
            maxwidth = 284 - badge_width
            title.setText(title.fontMetrics().elidedText(row["title"], Qt.TextElideMode.ElideRight, maxwidth))
            title.setToolTip(row["title"])
            title.setStyleSheet("color: %s; font-size: 12px;" % c["text"])
            line.addWidget(title, 1)
            line.addWidget(badge)
            highlight = "#e5ad57" if row["state"] in ("attention", "failed") else "#45ba8b" if row["state"] == "completed" else c["blue"]
            frame.setStyleSheet(
                "QFrame#codexWorkRow { background: %s; border-radius: 8px; } QLabel#codexWorkState { color: %s; background: transparent; }"
                % (c["field"], highlight)
            )
            frame.setAccessibleName(row["title"] + " (" + caption + ")")
            self.rows_layout.addWidget(frame)
            self._record_frames.append((frame, row))
        self.rows_layout.addStretch(1)
        count = max(1, min(3, len(rows)))
        height = count * 42 + 2
        self.scroll.setFixedHeight(height)
        self.box.setFixedHeight(height + 24)
        self.scroll.setStyleSheet(notification_scroll_style(style, c))
        self.rows_layout.activate()
        island._card_box.layout().activate()
        if island._mode == "expanded":
            island._apply_fixed_size()
            island._card_box.setGeometry(0, 44, island.width(), island.height() - 44)
            if not self.seen_timer.isActive():
                self.viewed()
        island._content_cache_key = None
        island._refresh()
        island.set_agent_active(any(r["state"] in ("thinking", "coding") for r in rows))
        island.update()

    def state_caption(self, row):
        phase = self._phase_started.get(row["threadId"])
        if row["state"] == "thinking" and phase is not None and phase[:2] == (row["turnId"], "thinking") and self._clock() - phase[2] > 60:
            return text("long_thinking")
        return text(row["state"])

    def viewed(self, *_args):
        if not self.enabled() or not self.box.isVisible():
            return
        # Only rows actually inside the visible viewport count as read.
        self._ack_rows = []
        viewport = self.scroll.viewport().rect()
        for frame, row in getattr(self, "_record_frames", []):
            y = frame.mapTo(self.scroll.viewport(), frame.rect().topLeft()).y()
            if min(viewport.bottom(), y + frame.height()) - max(viewport.top(), y) > frame.height() * 0.5:
                self._ack_rows.append(row)
        if self._ack_rows:
            self.seen_timer.start(2800)

    def acknowledge(self):
        if self.island._mode != "expanded" or not self.box.isVisible():
            return
        if self.ledger.mark(self._ack_rows):
            self.visible = self.ledger.visible(self.snapshot.get("rows", []))
            self.refresh()

    def find_pet(self):
        from shiboken6 import isValid

        if self._pet is not None and isValid(self._pet):
            return self._pet
        self._pet = next((w for w in QApplication.topLevelWidgets() if hasattr(w, "_speech_bubble") and callable(getattr(w, "show_bubble", None))), None)
        return self._pet

    def show_notice(self):
        if self.notice_timer.isActive() or not self.pending or not self.enabled():
            return
        pet = self.find_pet()
        if (
            pet is None
            or not pet.isVisible()
            or getattr(pet, "_sticky_bubble_active", False)
            or getattr(pet, "_alert_current", None)
            or getattr(pet, "_bubble_suppressed", False)
        ):
            return
        quota = getattr(pet, "_codex_quota_controller", None)
        if quota and quota.popup.isVisible():
            return
        row = self.pending.pop(0)
        if self.ledger.seen.get(row["threadId"]) == row["signature"]:
            return
        self._old_busy = getattr(pet, "_bubble_busy_until", 0)
        pet._codex_work_notice_active = True
        pet.show_bubble(text("notice").format(title=row["title"][:100]), duration_ms=5000)
        pet._bubble_busy_until = time.monotonic() + 5
        self.notice_timer.start(5000)

    def restore(self):
        pet = self.find_pet()
        if pet is not None:
            pet._codex_work_notice_active = False
            pet._bubble_busy_until = max(getattr(self, "_old_busy", 0), time.monotonic())
            # The native music controller resumes its current lyric on the next sample.

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent

        if watched is self.island and event.type() == QEvent.Type.Close:
            self.shutdown()
        return super().eventFilter(watched, event)

    def shutdown(self):
        if getattr(self, "_shutdown", False):
            return
        self._shutdown = True
        self.poll.stop()
        self.seen_timer.stop()
        self.notice_timer.stop()
        self.restore()
        self.thread.quit()
        self.thread.wait(2000)


def attach(island):
    if not hasattr(island, "_codex_work_controller"):
        island._codex_work_controller = WorkController(island)


def refresh(island):
    controller = getattr(island, "_codex_work_controller", None)
    if controller is not None:
        controller.refresh()


def notice_active(pet):
    return bool(getattr(pet, "_codex_work_notice_active", False))


def capsule_info(island, original):
    controller = getattr(island, "_codex_work_controller", None)
    if controller is None or not controller.enabled():
        return original
    if any(r["state"] == "attention" for r in controller.visible):
        return text("attention")
    active_count = sum(r["state"] in ("thinking", "coding") for r in controller.visible)
    if active_count > 1:
        return text("active_count").format(count=active_count)
    complete = sum(r["state"] == "completed" for r in controller.visible)
    if complete:
        from .language_ui import language

        return str(complete) + (" done" if language() == "en" else " 完成")
    return original


def status_dot(island, original):
    from PySide6.QtGui import QColor

    controller = getattr(island, "_codex_work_controller", None)
    if controller is not None and controller.enabled() and any(r["state"] == "attention" for r in controller.visible):
        return QColor("#efa83b")
    return original
