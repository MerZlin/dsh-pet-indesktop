"""逐条回复朗读：按钮、文本清洗、停止与无语音端口时的回退。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.ui.chat.controller import ChatController


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Speech:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, int | None]] = []
        self.cancelled: list[str] = []
        self.callbacks = {}

    def cancel_owner(self, owner: str) -> None:
        self.cancelled.append(str(owner))

    def speak(
        self,
        request,
        *,
        on_done=None,
        on_error=None,
        on_finished=None,
        owner: str = "",
        slot=None,
        guard=None,
    ):
        self.requests.append((str(request.text), str(owner), slot))
        self.callbacks = {
            "done": on_done,
            "error": on_error,
            "finished": on_finished,
        }
        return "token"


class _Host:
    def __init__(self, root: Path, speech=None) -> None:
        self.config = Config(root)
        self.ports = SimpleNamespace(speech=speech) if speech is not None else None


def _open(root: Path, speech=None):
    host = _Host(root, speech)
    control = ChatController(host, root)
    assert control.open("ai.chat", pet_slot=0) is True
    window = control.window_for("ai.chat", 0)
    assert window is not None
    return control, window


def _cleanup(control) -> None:
    for state in control.states().values():
        if state.window is not None:
            state.window.deleteLater()


def test_assistant_bubble_speaks_cleaned_text_and_stops(app, tmp_path: Path) -> None:
    speech = _Speech()
    control, window = _open(tmp_path, speech)
    try:
        control.append("ai.chat", "assistant", "**你好** `代码` [链接](https://x.test)")
        app.processEvents()
        bubble = window.bubbles()[-1]
        assert bubble.role == "assistant"
        assert bubble.speak_button is not None

        bubble.speak_button.click()
        app.processEvents()
        assert speech.requests
        text, owner, slot = speech.requests[-1]
        assert text == "你好 代码 链接"
        assert owner == "chat:ai.chat:0" and slot == 0
        assert bubble.speaking is True

        speech.callbacks["done"](None)
        speech.callbacks["finished"](None)
        app.processEvents()
        assert bubble.speaking is False

        bubble.speak_button.click()
        app.processEvents()
        assert bubble.speaking is True
        bubble.speak_button.click()
        app.processEvents()
        assert bubble.speaking is False
        assert "chat:ai.chat:0" in speech.cancelled
    finally:
        _cleanup(control)


def test_speech_unavailable_is_reported_without_breaking_the_bubble(
    app, tmp_path: Path
) -> None:
    control, window = _open(tmp_path, None)
    try:
        control.append("ai.chat", "assistant", "读不出来也没关系")
        app.processEvents()
        bubble = window.bubbles()[-1]
        bubble.speak_button.click()
        app.processEvents()
        assert bubble.speaking is False
        assert "语音不可用" in bubble.speech_status.text()
    finally:
        _cleanup(control)


def test_user_bubble_has_no_speak_button(app, tmp_path: Path) -> None:
    control, window = _open(tmp_path, _Speech())
    try:
        control.append("ai.chat", "user", "用户消息")
        app.processEvents()
        bubble = window.bubbles()[-1]
        assert bubble.role == "user"
        assert bubble.speak_button is None
    finally:
        _cleanup(control)


def test_stopping_clears_the_bubble_even_without_a_callback(app, tmp_path: Path) -> None:
    speech = _Speech()
    control, window = _open(tmp_path, speech)
    try:
        control.append("ai.chat", "assistant", "会被中途停止的回复")
        app.processEvents()
        bubble = window.bubbles()[-1]
        bubble.speak_button.click()
        app.processEvents()
        assert bubble.speaking is True
        control._stop_speech(("ai.chat", 0))
        app.processEvents()
        assert bubble.speaking is False
        assert bubble.speech_status.text() == ""
    finally:
        _cleanup(control)
