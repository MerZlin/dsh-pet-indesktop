"""Core-owned, nonblocking Qt quit barrier for owner-scoped background work.

Participants retain their own work; this gate neither stops processes nor waits
on threads. The event loop remains available while real drain evidence is pending.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class _Participant:
    owner: str
    prepare: Callable[[], object]
    ready: Callable[[], bool]


class ApplicationExitGate(QObject):
    def __init__(self, app: QApplication):
        super().__init__(app)
        self._app = app
        self._participants: dict[str, _Participant] = {}
        self._prepared: set[str] = set()
        self._errors: dict[str, str] = {}
        self._waiting = False
        self._allow = False
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._advance)
        app.installEventFilter(self)

    def register(self, owner: str, prepare: Callable[[], object], ready: Callable[[], bool]) -> str:
        token = uuid.uuid4().hex
        self._participants[token] = _Participant(owner, prepare, ready)
        if self._waiting:
            self._allow = False
            self._timer.start()
        return token

    def unregister(self, token: str) -> None:
        self._participants.pop(token, None)
        self._prepared.discard(token)
        self._errors.pop(token, None)

    def defer(self, callback: Callable[[], object], milliseconds: int = 10) -> None:
        # The timer belongs to Core, not a potentially deleted feature widget.
        timer = QTimer(self)
        timer.setSingleShot(True)

        def run():
            try:
                callback()
            finally:
                timer.deleteLater()

        timer.timeout.connect(run)
        timer.start(milliseconds)

    @property
    def blockers(self) -> tuple[dict[str, str], ...]:
        return tuple({"owner": p.owner, "reason": self._errors.get(t, "background_drain_pending")} for t, p in self._participants.items())

    def eventFilter(self, watched, event):
        if watched is self._app and event.type() == QEvent.Type.Quit:
            if self._allow:
                self._allow = False
                self._waiting = False
                self._timer.stop()
                return False
            if not self._participants:
                return False
            self._waiting = True
            self._advance()
            return True
        return False

    def _advance(self):
        for token, participant in list(self._participants.items()):
            if token not in self._prepared:
                self._prepared.add(token)
                try:
                    participant.prepare()
                except Exception:
                    self._errors[token] = "drain_prepare_failed"
            if token in self._errors:
                continue
            try:
                if participant.ready():
                    self.unregister(token)
            except Exception:
                self._errors[token] = "drain_evidence_failed"
        if not self._participants:
            self._timer.stop()
            self._allow = True
            QTimer.singleShot(0, self._app.quit)
        elif not self._timer.isActive():
            self._timer.start()


def application_exit_gate(app: QApplication | None = None) -> ApplicationExitGate:
    app = app or QApplication.instance()
    if app is None:
        raise RuntimeError("application_required_for_async_exit")
    gate = getattr(app, "_owner_exit_gate", None)
    if gate is None:
        gate = ApplicationExitGate(app)
        app._owner_exit_gate = gate
    return gate
