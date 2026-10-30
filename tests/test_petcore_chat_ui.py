"""Core 聊天窗口与 ChatPort：流式、输入约定、按宠隔离、会话落盘、现代布局。"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QRect, Qt, QThread, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from petcore.chat.session_store import ChatSession
from petcore.config import Config
from petcore.ui.chat import ChatController, themes
from petcore.ui.chat.window import ChatWindow, FOLLOW_GAP


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeHost:
    def __init__(self, root: Path) -> None:
        self.config = Config(root)
        self.pets = None


@pytest.fixture()
def controller(app, tmp_path: Path):
    host = FakeHost(tmp_path)
    control = ChatController(host, tmp_path)
    yield control
    for state in control.states().values():
        if state.window is not None:
            state.window.deleteLater()


def _key(window, *, shift: bool = False) -> None:
    modifier = (
        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    )
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, modifier, "\r")
    QApplication.sendEvent(window.input, event)


def _open(controller, *, slot: int = 0):
    assert controller.open("ai.chat", pet_slot=slot) is True
    window = controller.window_for("ai.chat", slot)
    assert window is not None and window.isVisible()
    return window


class FakePetWindow(QWidget):
    """最小的桌宠替身：只有跟随定位需要的 ``body_rect``/``pet_moved``。"""

    pet_moved = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._body = QRect(0, 0, 120, 120)

    def set_body_rect(self, rect: QRect) -> None:
        self._body = QRect(rect)

    def body_rect(self) -> tuple[int, int, int, int]:
        return self._body.x(), self._body.y(), self._body.width(), self._body.height()

    def move_body(self, x: int, y: int) -> None:
        self.set_body_rect(QRect(x, y, self._body.width(), self._body.height()))
        self.pet_moved.emit()


def _wait_until(predicate, timeout_ms: int = 2000) -> bool:
    """事件循环里等条件成立（宽预算，不靠固定 sleep 猜时序）。"""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QThread.msleep(5)
    QApplication.processEvents()
    return bool(predicate())


def _available() -> QRect:
    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    return screen.availableGeometry()


def test_reasoning_and_status_feed_the_thinking_panel(controller) -> None:
    window = _open(controller)

    controller.status("ai.chat", "🔍 正在搜索：deepseek 定价")
    controller.reasoning("ai.chat", "先确认一下最新价格。")
    controller.token("ai.chat", "答案")
    controller.done("ai.chat", "答案")

    bubble = window.bubbles()[-1]
    assert bubble.has_thinking is True
    reasoning, activity = bubble.thinking_record
    assert reasoning == "先确认一下最新价格。"
    assert activity == ["🔍 正在搜索：deepseek 定价"]
    assert "先确认" not in bubble.body.text()


def test_thinking_is_persisted_and_restored_from_disk(controller) -> None:
    window = _open(controller)
    controller.reasoning("ai.chat", "先想一下。")
    controller.status("ai.chat", "🌐 联网检索过程")
    controller.token("ai.chat", "答案")
    controller.done("ai.chat", "答案")

    state = controller.states()[("ai.chat", 0)]
    stored = state.session.messages[-1]
    assert stored["reasoning"] == "先想一下。"
    assert stored["activity"] == ["🌐 联网检索过程"]

    reloaded = state.store.load(state.session.session_id)
    assert reloaded is not None
    assert reloaded.messages[-1]["reasoning"] == "先想一下。"
    assert reloaded.messages[-1]["activity"] == ["🌐 联网检索过程"]

    window.load_messages(reloaded.messages)
    bubble = window.bubbles()[-1]
    assert bubble.has_thinking is True
    assert bubble.thinking_record[0] == "先想一下。"


def test_reasoning_never_opens_a_chat_window(controller) -> None:
    controller.reasoning("ai.chat", "只更新已存在的窗口")
    controller.status("ai.chat", "联网检索")

    assert controller.window_for("ai.chat") is None
    state = controller.states().get(("ai.chat", 0))
    assert state is not None and state.session.messages == []


def test_open_and_stream_render_markdown(controller) -> None:
    assert controller.open("ai.chat", pet_slot=0, title="测试对话") is True
    window = controller.window_for("ai.chat")
    assert window is not None and window.isVisible()
    assert window.windowTitle() == "测试对话"

    controller.append("ai.chat", "user", "在吗")
    controller.token("ai.chat", "**你")
    controller.token("ai.chat", "好**")
    controller.done("ai.chat", "**你好**")

    assert window.is_streaming() is False
    assert "在吗" in window.transcript_text()
    assert "你好" in window.transcript_text()
    assert window.send_button.toolTip() == "发送"

    bubble = window.bubbles()[-1]
    assert bubble.role == "assistant"
    assert bubble.body.textFormat() == Qt.TextFormat.RichText
    assert "你好" in bubble.body.text()
    assert "**" not in bubble.body.text()


def test_enter_submits_and_shift_enter_keeps_newline(controller) -> None:
    window = _open(controller)
    received: list[tuple[str, int]] = []
    controller.on_submit("ai.chat", lambda text, slot: received.append((text, slot)))

    window.input.setPlainText("第一行")
    _key(window, shift=True)
    assert "\n" in window.input.toPlainText()
    assert received == []

    _key(window)
    assert received == [("第一行", 0)]
    assert window.input.toPlainText() == ""


def test_ime_composition_enter_does_not_send(controller) -> None:
    window = _open(controller)
    received: list[tuple[str, int]] = []
    controller.on_submit("ai.chat", lambda text, slot: received.append((text, slot)))

    composing = QInputMethodEvent("nihao", [])
    QApplication.sendEvent(window.input, composing)
    _key(window)
    assert received == []

    commit = QInputMethodEvent()
    commit.setCommitString("你好")
    QApplication.sendEvent(window.input, commit)
    _key(window)
    assert received == [("你好", 0)]


def test_stop_button_tracks_streaming_state(controller) -> None:
    window = _open(controller)
    stops: list[bool] = []
    window.stop_requested.connect(lambda: stops.append(True))

    controller.token("ai.chat", "半")
    assert window.is_streaming() is True
    assert window.send_button.toolTip() == "停止"
    assert window.send_button.isEnabled() is True

    window.send_button.click()
    assert stops == [True]
    assert window.is_streaming() is False
    assert window.send_button.toolTip() == "发送"

    controller.token("ai.chat", "再接一句")
    assert window.is_streaming() is True
    controller.done("ai.chat", "半句")
    assert window.is_streaming() is False


def test_each_pet_slot_gets_its_own_window_and_session(controller) -> None:
    main = _open(controller, slot=0)
    child = _open(controller, slot=1)
    assert main is not child

    controller.append("ai.chat", "user", "主宠的话", pet_slot=0)
    controller.append("ai.chat", "user", "子宠的话", pet_slot=1)
    assert "主宠的话" in controller.history("ai.chat", pet_slot=0)[-1]["content"]
    assert "子宠的话" in controller.history("ai.chat", pet_slot=1)[-1]["content"]
    assert "子宠的话" not in main.transcript_text()
    assert "主宠的话" not in child.transcript_text()

    files = list(Path(controller.root).rglob("*.json"))
    assert files, "会话必须落盘"
    assert any("sessions-slot-1" in str(path) for path in files)


def test_clear_starts_a_new_session(controller) -> None:
    window = _open(controller)
    controller.append("ai.chat", "user", "旧消息")
    first = controller.states()[("ai.chat", 0)].session.session_id
    window.clear_button.click()
    second = controller.states()[("ai.chat", 0)].session.session_id
    assert first != second
    assert controller.history("ai.chat") == []
    assert window.transcript_text() == ""


def test_error_is_shown_and_clears_the_pending_bubble(controller) -> None:
    window = _open(controller)
    controller.append("ai.chat", "user", "问题")
    controller.token("ai.chat", "半句")
    controller.error("ai.chat", "network", "网络不可达")
    assert "网络不可达" in window.status_label.text()
    assert window.status_label.property("state") == "error"
    assert window.is_streaming() is False
    assert "问题" in window.transcript_text()
    assert controller.history("ai.chat")[-1]["role"] == "user"


def test_modern_layout_and_styles(controller) -> None:
    controller.host.config.set("character", "鲸鱼娘")
    controller.host.config.set("ai_model", "deepseek-chat")
    window = _open(controller)
    for name in (
        "deepseek-sidebar",
        "chat-main",
        "chat-main-header",
        "new-conversation-button",
        "floating-composer",
    ):
        assert window.findChild(QWidget, name) is not None, name

    assert "QFrame#deepseek-sidebar" in window.styleSheet()
    assert "QFrame#chat-main" in window.styleSheet()
    assert window.send_button.objectName() == "send-button"
    assert window.minimumWidth() == 560
    assert window.width() >= 560
    assert window.has_background() is False
    assert window.avatar_label.text() == "鲸"  # 角色首字
    assert window.provider_label.text() == "deepseek-chat"


def test_sidebar_collapses_into_an_overlay_when_compact(controller) -> None:
    window = _open(controller)
    assert window.sidebar.isVisible() is True
    window.resize(700, 620)
    QApplication.processEvents()
    assert window.sidebar.isHidden() is True
    window.sidebar_toggle_button.click()
    assert window.sidebar.isVisible() is True
    assert window.sidebar_scrim.isVisible() is True
    window.sidebar_scrim.clicked.emit()
    assert window.sidebar.isHidden() is True


def test_session_list_and_new_conversation(controller) -> None:
    window = _open(controller)
    sessions = [
        ChatSession("s1", "第一个会话", [], 1.0),
        ChatSession("s2", "第二个会话", [], 2.0),
    ]
    window.set_history(sessions, "s1")
    assert window.session_list.count() == 2
    titles = [
        window.session_list.itemWidget(window.session_list.item(index)).title.text()
        for index in range(2)
    ]
    assert titles == ["第一个会话", "第二个会话"]

    seen: list[str] = []
    window.session_selected.connect(seen.append)
    window.session_list.setCurrentRow(1)
    assert seen == ["s2"]
    window.new_session_button.click()
    assert seen == ["s2", ""]


def test_attachments_are_captured_per_slot(controller, tmp_path: Path) -> None:
    text_file = tmp_path / "note.txt"
    text_file.write_text("附件里的内容", encoding="utf-8")
    image_file = tmp_path / "shot.png"
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ff0000"))
    assert image.save(str(image_file), "PNG")

    window = _open(controller, slot=0)
    _open(controller, slot=1)
    received: list[tuple[str, int]] = []
    controller.on_submit("ai.chat", lambda text, slot: received.append((text, slot)))

    window.composer.add_attachments([str(text_file), str(image_file)])
    assert len(window.composer.attachment_paths) == 2
    window.input.setPlainText("看附件")
    _key(window)
    assert received == [("看附件", 0)]

    attachments = controller.take_attachments("ai.chat", pet_slot=0)
    assert [item["name"] for item in attachments] == ["note.txt", "shot.png"]
    assert attachments[0]["text"] == "附件里的内容"
    assert attachments[0]["mime"].startswith("text/")
    assert attachments[1]["image_data_url"].startswith("data:image/png;base64,")
    assert attachments[1]["text"] is None
    assert controller.take_attachments("ai.chat", pet_slot=0) == []
    assert controller.take_attachments("ai.chat", pet_slot=1) == []
    assert window.pending_attachments() == []


def test_record_writes_history_without_a_window(controller) -> None:
    controller.record("ai.chat", "user", "看看屏幕：桌面", pet_slot=0)
    controller.record("ai.chat", "assistant", "桌面上有一个窗口", pet_slot=0)
    assert controller.window_for("ai.chat", 0) is None
    history = controller.history("ai.chat", pet_slot=0)
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "看看屏幕：桌面"

    window = _open(controller, slot=0)
    assert "看看屏幕：桌面" in window.transcript_text()
    assert "桌面上有一个窗口" in window.transcript_text()

    controller.record("ai.chat", "user", "再记一条", pet_slot=0)
    assert "再记一条" in window.transcript_text()
    assert controller.history("ai.chat", pet_slot=0)[-1]["content"] == "再记一条"


def test_font_size_and_background_follow_config(controller) -> None:
    controller.host.config.set("ui_font_size", 16)
    controller.host.config.set("chat_background", "builtin:whale")
    window = _open(controller)

    assert window.has_background() is True
    assert "#20242b; font-size: 16px;" in window.styleSheet()
    # 自定义/内置壁纸会追加半透明面板叠加层
    assert "rgba(248, 250, 253, 218)" in window.styleSheet()

    window.set_font_size(99)
    assert "#20242b; font-size: 24px;" in window.styleSheet()
    window.set_font_size(1)
    assert "#20242b; font-size: 10px;" in window.styleSheet()


def test_chat_font_override_is_chat_only_and_live(controller) -> None:
    controller.host.config.set("ui_font_size", 13)
    controller.host.config.set("chat_font_size", 20)
    controller.host.config.set("chat_font_family", "Microsoft YaHei")
    window = _open(controller)

    assert "#20242b; font-size: 20px;" in window.styleSheet()
    assert 'font-family: "Microsoft YaHei", sans-serif;' in window.styleSheet()
    assert controller.host.config.get("ui_font_size") == 13

    controller.host.config.set("chat_font_size", 16)
    controller.host.config.set("chat_font_family", "")
    controller.apply_appearance()
    assert "#20242b; font-size: 16px;" in window.styleSheet()
    assert 'font-family: "SF Pro Text"' in window.styleSheet()


def test_pin_button_toggles_always_on_top(controller) -> None:
    window = _open(controller)
    assert window.is_always_on_top() is False
    window.pin_button.click()
    assert window.is_always_on_top() is True
    assert controller.host.config.get("chat_always_on_top") is True
    window.pin_button.click()
    assert window.is_always_on_top() is False
    assert controller.host.config.get("chat_always_on_top") is False


def test_theme_registry_and_asset_resolution(app) -> None:
    names = dict(themes.theme_names())
    assert "whale" in names
    assert themes.resolve_background_pixmap("") is None
    assert themes.resolve_background_pixmap("builtin:missing") is None
    pixmap = themes.resolve_background_pixmap("builtin:whale")
    assert pixmap is not None and not pixmap.isNull()
    assert themes.background_focus_rect(None, {}, "builtin:whale") == themes.DEFAULT_FOCUS


def test_chat_window_follows_the_pet_until_disabled(app) -> None:
    area = _available()
    pet = FakePetWindow()
    pet.set_body_rect(QRect(area.x() + 10, area.y() + 40, 80, 80))
    pet.show()
    window = ChatWindow(font_size=13)
    try:
        window.resize(560, 480)
        window.set_pet_window(pet)
        window.set_follow_pet(True)
        window.show()
        QApplication.processEvents()

        body_x, body_y, body_w, _body_h = pet.body_rect()
        assert window.x() == body_x + body_w + FOLLOW_GAP
        assert area.contains(window.geometry())

        pet.move_body(body_x + 40, body_y + 10)
        assert window._follow_timer.isActive() is True, "移动信号只排 40ms 合并刷新"
        assert _wait_until(
            lambda: window.x() == pet.body_rect()[0] + pet.body_rect()[2] + FOLLOW_GAP
        ), "40ms 合并刷新后聊天窗应跟上桌宠"
        assert area.contains(window.geometry())

        resting = window.geometry()
        window.set_follow_pet(False)
        pet.move_body(area.x() + 120, area.y() + 200)
        assert window._follow_timer.isActive() is False
        _wait_until(lambda: False, timeout_ms=120)
        assert window.geometry() == resting
    finally:
        window.deleteLater()
        pet.deleteLater()


def test_follow_stays_inside_available_geometry_near_the_edge(app) -> None:
    area = _available()
    pet = FakePetWindow()
    pet.set_body_rect(QRect(area.x() + area.width() - 60, area.y() + 20, 50, 60))
    pet.show()
    window = ChatWindow(font_size=13)
    try:
        window.resize(560, 480)
        window.set_follow_pet(True)
        window.set_pet_window(pet)
        window.show()
        QApplication.processEvents()

        geometry = window.geometry()
        assert area.contains(geometry)
        assert geometry.x() < pet.body_rect()[0], "右侧放不下时要换到桌宠左边"
    finally:
        window.deleteLater()
        pet.deleteLater()


def test_follow_falls_below_when_neither_side_fits(app) -> None:
    area = _available()
    pet = FakePetWindow()
    pet.set_body_rect(QRect(area.x() + 20, area.y() + 10, area.width() - 40, 90))
    pet.show()
    window = ChatWindow(font_size=13)
    try:
        window.resize(560, 480)
        window.set_pet_window(pet)
        window.set_follow_pet(True)
        window.show()
        QApplication.processEvents()

        _, body_y, _, body_h = pet.body_rect()
        assert window.y() == body_y + body_h + FOLLOW_GAP
        assert area.contains(window.geometry())
    finally:
        window.deleteLater()
        pet.deleteLater()


def test_controller_wires_follow_from_config(controller) -> None:
    controller.host.config.set("chat_follow_pet", True)
    window = _open(controller)
    assert window.follows_pet() is True


def test_apply_appearance_updates_existing_windows(controller) -> None:
    window = _open(controller)
    assert window.follows_pet() is False
    assert window.background_value() == ""

    controller.apply_appearance(
        background="builtin:whale",
        accent="#ff8800",
        always_on_top=True,
        follow=True,
    )

    assert window.background_value() == "builtin:whale"
    assert window.accent_color.lower() == "#ff8800"
    assert window.is_always_on_top() is True
    assert window.follows_pet() is True

    controller.host.config.set("chat_accent", "#123456")
    controller.host.config.set("chat_background", "")
    controller.host.config.set("chat_always_on_top", False)
    controller.host.config.set("chat_follow_pet", False)
    controller.apply_appearance()

    assert window.background_value() == ""
    assert window.accent_color.lower() == "#123456"
    assert window.is_always_on_top() is False
    assert window.follows_pet() is False


def test_apply_appearance_never_opens_a_window(controller) -> None:
    controller.apply_appearance(background="builtin:whale", follow=True)
    assert controller.states() == {}


def test_apply_appearance_rebinds_a_recreated_pet_window(controller) -> None:
    pet = FakePetWindow()
    pet.show()
    controller.host.pets = SimpleNamespace(window_for_slot=lambda _slot: pet)
    window = _open(controller)
    assert window._pet_window is pet

    replacement = FakePetWindow()
    replacement.show()
    controller.host.pets = SimpleNamespace(window_for_slot=lambda _slot: replacement)
    try:
        controller.apply_appearance()
        assert window._pet_window is replacement, "桌宠收回重建后跟随要换绑到新窗口"
    finally:
        pet.deleteLater()
        replacement.deleteLater()


def test_composer_mic_button_emits_record_signals(controller) -> None:
    window = _open(controller)
    requested: list[bool] = []
    stopped: list[bool] = []
    window.record_requested.connect(lambda: requested.append(True))
    window.record_stopped.connect(lambda: stopped.append(True))

    window.composer.record_button.click()
    assert requested == [True]

    window.set_recording(True, "正在录音…")
    assert window.composer.record_button.isChecked() is True
    window.composer.record_button.click()
    assert stopped == [True]
    window.set_recording(False)
    assert window.composer.record_button.isChecked() is False


def test_view_toggle_switches_to_dsh_and_back(controller) -> None:
    window = _open(controller)
    assert window.current_view() == "ai"
    window.set_view("dsh")
    assert window.current_view() == "dsh"
    assert window.view_button.text() == "AI"
    window.set_view("ai")
    assert window.current_view() == "ai"
    assert window.view_button.text() == "DSH"


def test_dsh_context_is_inserted_into_ai_composer(controller) -> None:
    window = _open(controller)
    window.set_view("dsh")
    window._use_dsh_context("DSH：你好")
    assert window.current_view() == "ai"
    assert "DSH：你好" in window.composer.input.toPlainText()
