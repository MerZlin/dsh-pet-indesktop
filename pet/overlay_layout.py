"""Keep native pet overlays readable without changing focus or content."""

from __future__ import annotations
from PySide6.QtCore import QObject, QEvent, QTimer, QRect, QPoint, QAbstractAnimation
from PySide6.QtWidgets import QApplication, QWidget

CHAT_NAMES = frozenset(("chat-window", "quick-chat-bubble"))
QUOTA_NAMES = frozenset(("codexUsageTrendWindow", "codexQuotaBubble"))
_coordinator = None


def windows(names):
    return [w for w in QApplication.topLevelWidgets() if w.isVisible() and not w.isMinimized() and w.objectName() in names]


def avoid_chat(bubble):
    if getattr(bubble, "_capture_compat", False):
        return
    chats = windows(CHAT_NAMES)
    if not chats:
        return
    anchor = bubble._anchor_rect
    avail = bubble._available_geometry(anchor)
    if avail is None:
        return
    desired = QRect(bubble.geometry())
    anim = getattr(bubble, "_pos_anim", None)
    if anim is not None and anim.state() == QAbstractAnimation.State.Running:
        end = anim.endValue()
        if isinstance(end, QPoint):
            desired.moveTopLeft(end)
    obstacles = [w.frameGeometry().adjusted(-12, -12, 12, 12) for w in chats]
    if not any(desired.intersects(r) for r in obstacles):
        if anim is not None and anim.state() == QAbstractAnimation.State.Running and any(bubble.geometry().united(desired).intersects(r) for r in obstacles):
            anim.stop()
            bubble.move(desired.topLeft())
            bubble._update_surface_geometry(desired)
        return
    candidates = []
    width, height = desired.width(), desired.height()
    for r in obstacles:
        candidates.extend(
            (
                QPoint(desired.x(), r.top() - height),
                QPoint(desired.x(), r.bottom() + 1),
                QPoint(r.left() - width, desired.y()),
                QPoint(r.right() + 1, desired.y()),
                QPoint(r.center().x() - width // 2, r.top() - height),
                QPoint(r.center().x() - width // 2, r.bottom() + 1),
            )
        )
    candidates.extend(
        (
            avail.topLeft(),
            QPoint(avail.right() - width + 1, avail.top()),
            QPoint(avail.left(), avail.bottom() - height + 1),
            QPoint(avail.right() - width + 1, avail.bottom() - height + 1),
        )
    )
    valid = []
    for point in candidates:
        rect = QRect(point, bubble.size())
        rect.moveLeft(max(avail.left(), min(rect.left(), avail.right() - width + 1)))
        rect.moveTop(max(avail.top(), min(rect.top(), avail.bottom() - height + 1)))
        if avail.contains(rect) and not any(rect.intersects(r) for r in obstacles):
            valid.append(rect)
    if not valid:
        # A maximized chat can occupy all available space; keep its inputs accessible.
        for chat in chats:
            chat.raise_()
        return
    target = min(valid, key=lambda r: (r.x() - desired.x()) ** 2 + (r.y() - desired.y()) ** 2)
    if anim is not None:
        anim.stop()
    if bubble.pos() != target.topLeft():
        bubble.move(target.topLeft())
    bubble._update_surface_geometry(target)


def sync():
    for bubble in windows(frozenset(("pet-speech-bubble",))):
        avoid_chat(bubble)
    # Raising never activates a window, so typing in chat retains keyboard focus.
    for quota in windows(QUOTA_NAMES):
        quota.raise_()


class OverlayCoordinator(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.pending = False
        app.installEventFilter(self)

    def schedule(self):
        if self.pending:
            return
        self.pending = True
        QTimer.singleShot(0, self.flush)

    def flush(self):
        self.pending = False
        sync()

    def eventFilter(self, watched, event):
        if (
            isinstance(watched, QWidget)
            and watched.isWindow()
            and watched.objectName() in CHAT_NAMES | QUOTA_NAMES | {"pet-speech-bubble"}
            and event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Move, QEvent.Type.Resize)
        ):
            self.schedule()
        return False


def install():
    global _coordinator
    app = QApplication.instance()
    if app is not None and _coordinator is None:
        _coordinator = OverlayCoordinator(app)
    return _coordinator
