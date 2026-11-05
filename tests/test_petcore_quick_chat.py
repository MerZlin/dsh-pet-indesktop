"""快速对话气泡：点气泡开窗、提交/流式/落会话、去聊天窗、按宠隔离。"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.ui.bubble import SpeechBubble
from petcore.ui.chat import ChatController, QuickChatController
from petcore.ui.pet_window import PetWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeHost:
    def __init__(self, root: Path) -> None:
        self.config = Config(root)
        self.root = root
        self.pets = None
        self.window = None
        self.chat = ChatController(self, root)
        self.quick_chat = QuickChatController(self)
        self.quick_opens: list[int] = []

    def open_quick_chat(self, slot: int = 0) -> bool:
        self.quick_opens.append(int(slot))
        return True

    # PetWindow 需要的宿主面
    def characters(self):
        return []

    def build_menu(self, parent=None):
        return None

    def open_settings(self, initial_page=None):
        return None

    def reload_mods(self):
        return None

    def open_data_dir(self):
        return None

    def quit_app(self):
        return None

    def run_feature_command(self, *args, **kwargs):
        return False


@pytest.fixture()
def host(app, tmp_path: Path):
    instance = FakeHost(tmp_path)
    yield instance
    for state in instance.quick_chat._states.values():
        state.bubble.close()
    for state in instance.chat.states().values():
        if state.window is not None:
            state.window.deleteLater()


def _wait(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return bool(predicate())


def test_open_shows_the_bubble(host) -> None:
    assert host.quick_chat.open("ai.chat", pet_slot=0) is True
    bubble = host.quick_chat.bubble_for("ai.chat", 0)
    assert bubble is not None and bubble.isVisible()
    assert host.quick_chat.is_open("ai.chat") is True


def test_submit_streams_and_persists_to_the_shared_session(host) -> None:
    received: list[tuple[str, int]] = []
    host.quick_chat.on_submit("ai.chat", lambda text, slot: received.append((text, slot)))
    host.quick_chat.open("ai.chat", pet_slot=0)
    bubble = host.quick_chat.bubble_for("ai.chat", 0)
    assert bubble is not None

    bubble.input.setText("在吗")
    QTest.keyClick(bubble.input, Qt.Key.Key_Return)
    assert received == [("在吗", 0)]
    assert _wait(lambda: host.chat.history("ai.chat")[-1]["content"] == "在吗")

    host.quick_chat.token("ai.chat", "你")
    host.quick_chat.token("ai.chat", "好")
    host.quick_chat.done("ai.chat", "你好", pet_slot=0)
    assert _wait(lambda: bubble.text() == "你好")
    assert bubble.is_streaming() is False

    history = host.chat.history("ai.chat")
    assert [item["content"] for item in history[-2:]] == ["在吗", "你好"]
    # 同一份会话：完整聊天窗打开时能看到这两条。
    host.chat.open("ai.chat", pet_slot=0)
    window = host.chat.window_for("ai.chat")
    assert window is not None
    assert "在吗" in window.transcript_text()
    assert "你好" in window.transcript_text()


def test_submit_without_handler_shows_an_error(host) -> None:
    host.quick_chat.open("ai.chat", pet_slot=0)
    bubble = host.quick_chat.bubble_for("ai.chat", 0)
    assert bubble is not None
    bubble.input.setText("在吗")
    QTest.keyClick(bubble.input, Qt.Key.Key_Return)
    assert "没有接入" in bubble.text()


def test_error_and_status_are_visible(host) -> None:
    host.quick_chat.open("ai.chat", pet_slot=0)
    bubble = host.quick_chat.bubble_for("ai.chat", 0)
    assert bubble is not None
    host.quick_chat.status("ai.chat", "🔍 正在搜索：天气", pet_slot=0)
    assert _wait(lambda: "正在搜索" in bubble.hint.text())
    host.quick_chat.error("ai.chat", "network", "网络不可达", pet_slot=0)
    assert _wait(lambda: "网络不可达" in bubble.text())


def test_open_full_chat_hides_the_bubble(host) -> None:
    host.quick_chat.open("ai.chat", pet_slot=0)
    bubble = host.quick_chat.bubble_for("ai.chat", 0)
    assert bubble is not None
    host.quick_chat.open_full_chat("ai.chat", pet_slot=0)
    assert bubble.isVisible() is False
    window = host.chat.window_for("ai.chat")
    assert window is not None and window.isVisible()


def test_bubbles_are_isolated_per_pet(host) -> None:
    host.quick_chat.open("ai.chat", pet_slot=0)
    host.quick_chat.open("ai.chat", pet_slot=1)
    first = host.quick_chat.bubble_for("ai.chat", 0)
    second = host.quick_chat.bubble_for("ai.chat", 1)
    assert first is not None and second is not None and first is not second


def test_speech_bubble_emits_clicked(app) -> None:
    bubble = SpeechBubble()
    seen: list[bool] = []
    bubble.clicked.connect(lambda: seen.append(True))
    bubble.show_text("点我", bubble.rect(), 4000)
    bubble.show()
    try:
        QTest.mouseClick(bubble, Qt.MouseButton.LeftButton, pos=QPoint(4, 4))
        assert seen == [True]
    finally:
        bubble.close()


def test_pet_window_routes_the_bubble_click_to_the_host(app, host) -> None:
    from petcore.contracts.content import CharacterSpec

    spec = CharacterSpec(
        id="fallback",
        name="Fallback",
        root=Path("."),
        media_kind="none",
        animations=(),
    )
    window = PetWindow(host.config, host.root, host, slot=0)
    window.start(spec)
    try:
        window.bubble.clicked.emit()
        assert host.quick_opens == [0]
    finally:
        window.shutdown()


def test_done_renders_a_local_image_sticker(host, tmp_path: Path) -> None:
    from PySide6.QtGui import QColor, QImage

    path = tmp_path / "meme.png"
    image = QImage(64, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3d8ef0"))
    assert image.save(str(path))

    host.quick_chat.open("ai.chat", pet_slot=0)
    bubble = host.quick_chat.bubble_for("ai.chat", 0)
    assert bubble is not None
    host.quick_chat.done("ai.chat", f"看图\n\n![表情包](<{path}>)", pet_slot=0)

    assert _wait(lambda: "看图" in bubble.text())
    assert "![" not in bubble.output.text()
    widgets = bubble.inline_image_widgets()
    assert len(widgets) == 1
    assert 0 < widgets[0].width() <= 160
    assert 0 < widgets[0].height() <= 160


def test_bubble_stays_readable_under_a_dark_system_palette(app) -> None:
    """系统深色模式：输出区不能跟着变黑（用户截图：黑底黑字）。

    QSS 把外观写死成浅色，但 QScrollArea 的 viewport / QWidget 子控件会按系统
    ``QPalette`` 涂底。这里把应用调色板换成深色，断言输出区仍然是浅底、并且
    文字像素仍然是深色（不是只断言某个属性）。
    """
    from PySide6.QtGui import QColor, QPalette

    from petcore.ui.chat.quick import QuickChatBubble

    original = app.palette()
    dark = QPalette(original)
    for role, color in (
        (QPalette.ColorRole.Window, "#1e1e1e"),
        (QPalette.ColorRole.WindowText, "#e6e6e6"),
        (QPalette.ColorRole.Base, "#141414"),
        (QPalette.ColorRole.Text, "#e6e6e6"),
        (QPalette.ColorRole.Button, "#2b2b2b"),
        (QPalette.ColorRole.ButtonText, "#e6e6e6"),
        (QPalette.ColorRole.PlaceholderText, "#c8c8c8"),
    ):
        dark.setColor(role, QColor(color))

    app.setPalette(dark)
    bubble = QuickChatBubble()
    try:
        bubble.resize(380, 260)
        bubble.finish("你好呀，这是测试回答。第二句。第三句。")
        bubble.show()
        app.processEvents()

        image = bubble.grab().toImage()
        background = image.pixelColor(bubble.scroll.mapTo(bubble, QPoint(20, 20)))
        assert background.lightness() > 200, background.name()

        geometry = bubble.output.geometry()
        origin = bubble.output.mapTo(bubble, QPoint(0, 0))
        darkest = min(
            image.pixelColor(origin.x() + x, origin.y() + y).lightness()
            for x in range(0, max(1, geometry.width()), 3)
            for y in range(0, max(1, geometry.height()), 3)
        )
        assert darkest < 120, darkest
    finally:
        app.setPalette(original)
        bubble.close()
        bubble.deleteLater()
