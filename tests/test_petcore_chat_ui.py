"""Core 聊天窗口与 ChatPort：流式、输入约定、按宠隔离、会话落盘、现代布局。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QThread, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from petcore.chat.session_store import ChatSession
from petcore.config import Config
from petcore.contracts.contributions import ChatViewSpec
from petcore.ui.chat import ChatController, themes
from petcore.ui.chat.window import ChatWindow, FOLLOW_GAP, GROUP_ROLE


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
    session_items = [
        window.session_list.item(index)
        for index in range(window.session_list.count())
        if window.session_list.item(index).data(Qt.ItemDataRole.UserRole)
    ]
    assert len(session_items) == 2
    titles = [
        window.session_list.itemWidget(item).title.text() for item in session_items
    ]
    assert titles == ["第二个会话", "第一个会话"], "同一分区内按更新时间倒序"

    seen: list[str] = []
    window.session_selected.connect(seen.append)
    window.session_list.setCurrentRow(window.session_list.row(session_items[0]))
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


def test_composer_mic_button_emits_record_signals(controller, monkeypatch) -> None:
    from petcore.platform import desktop
    original = desktop.capabilities
    monkeypatch.setattr(desktop, "capabilities", lambda **_: original(platform="win32"))
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


def test_ime_preedit_hides_the_placeholder(controller) -> None:
    window = _open(controller)
    original = window.input.placeholderText()
    assert original.startswith("和桌宠说点什么")

    QApplication.sendEvent(window.input, QInputMethodEvent("nihao", []))
    assert window.input.placeholderText() == ""

    commit = QInputMethodEvent()
    commit.setCommitString("你好")
    QApplication.sendEvent(window.input, commit)
    assert window.input.placeholderText() == original
    assert window.input.toPlainText() == "你好"

    # 取消组合（空 preedit）也要恢复提示。
    QApplication.sendEvent(window.input, QInputMethodEvent("", []))
    assert window.input.placeholderText() == original


def test_composer_stays_compact_and_short_chat_sits_above_it(controller) -> None:
    window = _open(controller)
    window.resize(1100, 800)
    window.load_messages(
        [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀，今天想聊什么？"},
        ]
    )

    assert _wait_until(lambda: window.scroll.height() > 300)
    assert window.composer.height() <= 140
    assert window.composer.toolbar.height() <= 40
    assert window.composer_card.y() >= window.scroll.y() + window.scroll.height() - 2
    assert window.scroll.height() > 300

    first = window.bubbles()[0]
    top = first.mapTo(window.timeline_host, QPoint(0, 0)).y()
    assert top > 100, "短会话应贴到底部，而不是堆在标题栏下面"


def test_clearing_messages_keeps_both_spacers(controller) -> None:
    window = _open(controller)
    window.load_messages([{"role": "user", "content": "你好"}])
    window.load_messages([])
    assert window.message_host_layout.count() == 2
    assert window.message_top_spacer is not None
    assert window.message_bottom_spacer is not None


def test_partial_transcript_mirrors_then_replaces_with_final(controller) -> None:
    window = _open(controller)
    window.set_partial_transcript("你好")
    assert window.input.toPlainText() == "你好"
    window.set_partial_transcript("你好呀")
    assert window.input.toPlainText() == "你好呀"

    window.insert_transcript("你好呀，今天想聊什么？")
    assert window.input.toPlainText() == "你好呀，今天想聊什么？"
    assert window.composer._partial_mirror == ""


def test_partial_transcript_never_overwrites_user_text(controller) -> None:
    window = _open(controller)
    window.input.setPlainText("我先打的字")
    window.set_partial_transcript("识别到的内容")
    assert window.input.toPlainText() == "我先打的字"
    assert "识别到的内容" in window.composer.partial_label.text()
    assert window.composer.partial_label.isVisible() is True

    window.set_partial_transcript("")
    assert window.input.toPlainText() == "我先打的字"
    assert window.composer.partial_label.isVisible() is False


def test_history_is_grouped_by_date_and_groups_collapse(controller) -> None:
    window = _open(controller)
    now = time.time()
    sessions = [
        ChatSession("today", "今天会话", [], now),
        ChatSession("old", "更早会话", [], now - 60 * 86400),
    ]
    window.set_history(sessions, "today")

    headers = [
        window.session_list.item(index)
        for index in range(window.session_list.count())
        if window.session_list.item(index).data(GROUP_ROLE)
    ]
    assert any(item.text().endswith("AI 对话") for item in headers)
    date_headers = [
        item
        for item in headers
        if not str(item.data(GROUP_ROLE)).startswith("channel:")
    ]
    assert [item.text() for item in date_headers][0].endswith("今天")
    assert any(item.text().endswith("更早") for item in date_headers)

    old_row = window._row_by_session["old"]
    assert window.session_list.isRowHidden(old_row) is True, "更早默认折叠"
    older_header = next(item for item in date_headers if item.text().endswith("更早"))
    window.session_list.itemClicked.emit(older_header)
    assert window.session_list.isRowHidden(old_row) is False
    window.session_list.itemClicked.emit(older_header)
    assert window.session_list.isRowHidden(old_row) is True


def test_archived_rows_and_toggle_emit_signals(controller) -> None:
    from petcore.chat.session_store import ChatSession

    window = _open(controller)
    session = ChatSession("archived", "归档会话", [], time.time(), archived_at=time.time())
    window.set_history([session], "archived")

    row_item = next(
        window.session_list.item(index)
        for index in range(window.session_list.count())
        if window.session_list.item(index).data(Qt.ItemDataRole.UserRole) == "archived"
    )
    row = window.session_list.itemWidget(row_item)
    assert row.archived is True
    assert row.title.property("archived") is True

    archived: list[tuple[str, bool]] = []
    deleted: list[str] = []
    toggled: list[bool] = []
    window.session_archive_requested.connect(lambda sid, value: archived.append((sid, value)))
    window.session_delete_requested.connect(deleted.append)
    window.archived_toggled.connect(toggled.append)

    row.archive_requested.emit("archived", False)
    row.delete_requested.emit("archived")
    window.archive_button.click()
    assert archived == [("archived", False)]
    assert deleted == ["archived"]
    assert toggled == [True]
    assert window.is_showing_archived() is True


def test_late_stream_answer_does_not_land_in_archived_fallback_session(controller) -> None:
    window = _open(controller)
    state = controller.states()[("ai.chat", 0)]
    controller.token("ai.chat", "半")
    assert state.active is True
    old_id = state.session.session_id

    controller._on_archive(state, old_id, True)
    assert state.session.session_id != old_id
    new_session = state.session

    controller.done("ai.chat", "迟到的完整回答")
    assert new_session.session_id == state.session.session_id
    assert all(
        "迟到的完整回答" not in str(item.get("content") or "")
        for item in new_session.messages
    )


def test_channel_sections_and_qq_submit_route(controller) -> None:
    window = _open(controller)
    seen: list[tuple[str, int, str]] = []
    controller.register_channel(
        "qq", lambda text, slot, conversation: seen.append((text, slot, conversation))
    )
    session_id = controller.open_channel(
        "qq", "private:42", "QQ 私聊", owner="ai.chat"
    )
    state = controller.states()[("ai.chat", 0)]
    assert state.session.session_id == session_id
    assert state.session.channel == "qq"
    controller._on_submit(("ai.chat", 0), "在吗")
    assert seen == [("在吗", 0, "private:42")]
    assert state.session.messages[-1]["channel"] == "qq"

    ai = state.store.new_session(title="AI 会话")
    dsh = state.store.new_session(title="DSH 会话", channel="dsh", conversation_id="s1")
    qq = state.store.load(session_id)
    # DSH 分组只在 DSH 模式打开时出现（默认关闭）。
    window.set_dsh_enabled(True)
    window.set_history([ai, dsh, qq], session_id)
    headers = [
        window.session_list.item(index).text()
        for index in range(window.session_list.count())
        if str(window.session_list.item(index).data(GROUP_ROLE) or "").startswith("channel:")
    ]
    assert any("AI 对话" in text for text in headers)
    assert any("DSH 对话" in text for text in headers)
    assert any("QQ 消息" in text for text in headers)


def test_ingest_is_idempotent_and_does_not_hijack_ai_session(controller) -> None:
    controller.open("ai.chat")
    state = controller.states()[("ai.chat", 0)]
    ai_id = state.session.session_id
    record = {
        "channel": "qq",
        "conversation_id": "group:7",
        "role": "user",
        "text": "群里有人吗",
        "sender": "小明",
        "external_id": "m-100",
    }
    first = controller.ingest(record, owner="ai.chat")
    second = controller.ingest(record, owner="ai.chat")
    assert first == second
    assert state.session.session_id == ai_id
    messages = controller.messages(first, owner="ai.chat")
    assert [item["content"] for item in messages] == ["群里有人吗"]
    assert messages[0]["sender"] == "小明"
    assert state.store.find_by_conversation("qq", "group:7").session_id == first


def test_dsh_frame_survives_later_state_save(controller) -> None:
    controller.open("ai.chat")
    window = controller.window_for("ai.chat")
    session_id = controller.open_channel(
        "dsh", "dsh-1", "DSH 会话", owner="ai.chat"
    )
    state = controller.states()[("ai.chat", 0)]
    controller._persist_dsh_frame(
        state,
        {"kind": "assistant", "text": "来自 DSH 的回答", "sessionId": "dsh-1", "seq": 1},
    )
    # 关闭/归档等路径会走 _save(state)：它必须看到刚写入的帧，而不是打开时的快照。
    controller._save(state)
    loaded = state.store.load(session_id)
    assert any(
        item.get("content") == "来自 DSH 的回答" for item in loaded.messages
    )


def test_collapsed_channel_hides_date_rows(controller) -> None:
    window = _open(controller)
    ai = ChatSession("ai-1", "AI", [], time.time())
    qq = ChatSession("qq-1", "QQ", [], time.time(), channel="qq")
    window.set_history([ai, qq], "ai-1")
    channel_key = "channel:qq"
    qq_row = window._row_by_session["qq-1"]
    assert window.session_list.isRowHidden(qq_row) is False
    window._toggle_group(channel_key)
    assert window.session_list.isRowHidden(qq_row) is True


def test_dsh_sessions_hidden_until_the_mode_is_enabled(controller) -> None:
    """没开 DSH 的人不该在侧栏看到 DSH 分组（用户明确要求的默认）。"""
    window = _open(controller)
    ai = ChatSession("ai-1", "AI", [], time.time())
    dsh = ChatSession("dsh-1", "DSH", [], time.time(), channel="dsh")
    window.set_history([ai, dsh], "ai-1")
    dsh_row = window._row_by_session["dsh-1"]
    assert window.session_list.isRowHidden(dsh_row) is True
    assert window._channel_headers["channel:dsh"].isHidden() is True

    window.set_dsh_enabled(True)
    assert window.session_list.isRowHidden(dsh_row) is False
    assert window._channel_headers["channel:dsh"].isHidden() is False

    window.set_dsh_enabled(False)
    assert window.session_list.isRowHidden(dsh_row) is True


def test_date_headers_use_a_lighter_font_than_session_rows(controller) -> None:
    window = _open(controller)
    ai = ChatSession("ai-1", "今天的会话", [], time.time())
    dsh = ChatSession("dsh-1", "DSH", [], time.time(), channel="dsh")
    window.set_dsh_enabled(True)
    window.set_history([ai, dsh], "ai-1")
    date_header = next(iter(window._group_headers.values()))
    channel_header = window._channel_headers["channel:ai"]
    assert date_header.font().pixelSize() < channel_header.font().pixelSize()
    assert date_header.font().weight() == 400
    assert channel_header.font().weight() == 600
    assert date_header.font().pixelSize() < window._font_size


def test_closing_dsh_mode_falls_back_to_the_latest_ai_session(controller) -> None:
    """关掉 DSH 后在 AI 输入框打字必须发给 AI：会话通道要跟着视图切回来。"""
    window = _open(controller)
    state = controller.states()[("ai.chat", 0)]
    ai = state.store.new_session(title="AI 会话")
    dsh = state.store.ensure_conversation("dsh", "pet", title="DSH 会话")
    assert dsh.messages == []
    state.session = dsh
    window.set_dsh_enabled(True)
    window.set_view("dsh")
    window._on_view_toggled(False)
    QApplication.processEvents()
    assert window.current_view() == "ai"
    assert state.session.channel == "ai"
    assert state.session.session_id == ai.session_id
    assert controller._config_get("chat_dsh_mode", True) is False


def test_opening_dsh_mode_selects_a_dsh_session(controller) -> None:
    window = _open(controller)
    state = controller.states()[("ai.chat", 0)]
    state.store.new_session(title="AI 会话")
    window._on_view_toggled(True)
    QApplication.processEvents()
    assert window.current_view() == "dsh"
    assert state.session.channel == "dsh"
    assert window.dsh_enabled() is True
    assert controller._config_get("chat_dsh_mode", False) is True


def test_append_from_worker_thread_builds_the_window_on_the_gui_thread(
    app, controller
) -> None:
    """后台线程调 ChatPort.append 不许在后台建窗（旧 dsh.channel 的 SSE 线程）。

    旧实现第一次 append 会在调用线程里 `ChatWindow(...)`，随后与 GUI 线程
    争 Qt 内部锁；现场表现是审批卡弹出时整个桌宠 AppHang。这里把「建窗线程」
    钉死成 GUI 线程。
    """
    created: list = []
    original = controller._create_window

    def tracked(state):
        created.append(QThread.currentThread())
        return original(state)

    controller._create_window = tracked
    thread = threading.Thread(
        target=lambda: controller.append("dsh.channel", "assistant", "审批来了"),
        name="legacy-dsh-sse",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=2.0)
    assert _wait_until(lambda: controller.window_for("dsh.channel") is not None)
    assert created and created[0] == app.thread()
    state = controller.states()[("dsh.channel", 0)]
    assert any(item.get("content") == "审批来了" for item in state.session.messages)


def test_open_from_worker_thread_defers_to_the_gui_thread(app, controller) -> None:
    thread = threading.Thread(
        target=lambda: controller.open("ai.chat", pet_slot=0),
        name="legacy-mod-worker",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=2.0)
    assert _wait_until(lambda: controller.window_for("ai.chat") is not None)
    window = controller.window_for("ai.chat")
    assert window is not None
    assert window.isVisible() is True


def _settle(app) -> None:
    for _ in range(4):
        app.processEvents()


def test_user_bubble_uses_the_wide_column_for_long_messages(app) -> None:
    window = ChatWindow(title="bubble", character_id="shenshen")
    window.resize(1180, 700)
    window.show()
    _settle(app)
    try:
        text = "我在其他几个对话说了啊，不记得我什么时候上课了吗？" * 3
        bubble = window._add_bubble("user", text)
        _settle(app)
        available = window.scroll.viewport().width() - 2 * window.message_horizontal_margin
        cap = max(240, int(available * 0.86))
        assert bubble.width() >= cap - 4
        assert bubble.body.width() >= cap - 30
        # 同样的文字在旧实现里只剩约 120px 的正文宽（一行十来个字）。
        assert bubble.body.width() > 400
    finally:
        window.close()
        _settle(app)


def test_short_user_bubble_stays_compact_and_assistant_fills_the_column(app) -> None:
    window = ChatWindow(title="bubble", character_id="shenshen")
    window.resize(1180, 700)
    window.show()
    _settle(app)
    try:
        short = window._add_bubble("user", "好")
        assistant = window._add_bubble("assistant", "很长的回复" * 30)
        _settle(app)
        available = window.scroll.viewport().width() - 2 * window.message_horizontal_margin
        assert short.width() < available * 0.5
        assert assistant.width() >= available - 4
    finally:
        window.close()
        _settle(app)


def test_chat_view_receives_the_window_pet_slot(app) -> None:
    class Probe(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.slot = None

        def set_pet_slot(self, slot) -> None:
            self.slot = int(slot)

    probe = Probe()
    spec = ChatViewSpec(
        id="ai.chat:memory",
        owner="ai.chat",
        view_id="memory",
        label="记忆",
        factory=lambda: probe,
    )
    window = ChatWindow(title="views", views=[spec], pet_slot=2)
    window.show()
    _settle(app)
    try:
        assert window.show_view("ai.chat:memory") is True
        _settle(app)
        assert probe.slot == 2
    finally:
        window.close()
        _settle(app)


# ---------------------------------------------------------------- Markdown 图片


def _write_png(path: Path, width: int = 64, height: int = 32) -> Path:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#3d8ef0"))
    assert image.save(str(path))
    return path


def test_assistant_message_renders_a_local_image_as_a_sticker(controller, tmp_path: Path) -> None:
    path = _write_png(tmp_path / "meme.png")
    controller.open("ai.chat", pet_slot=0)
    window = controller.window_for("ai.chat")
    assert window is not None

    controller.append("ai.chat", "assistant", f"看看这个\n\n![表情包](<{path}>)")
    bubble = window.bubbles()[-1]
    assert "看看这个" in bubble.body.text()
    assert "![" not in bubble.body.text()
    widgets = bubble.inline_image_widgets()
    assert len(widgets) == 1
    assert 0 < widgets[0].width() <= 240
    assert 0 < widgets[0].height() <= 240


def test_gif_sticker_is_animated_only_for_the_latest_message(controller, tmp_path: Path) -> None:
    from PySide6.QtTest import QTest

    from petcore.ui.animated_image import AnimatedImage
    from tests.test_petcore_animated_image import _ANIMATED_GIF

    gif = tmp_path / "meme.gif"
    gif.write_bytes(_ANIMATED_GIF)
    controller.open("ai.chat", pet_slot=0)
    window = controller.window_for("ai.chat")
    assert window is not None

    controller.append("ai.chat", "assistant", f"![表情包](<{gif}>)")
    bubble = window.bubbles()[-1]
    widgets = bubble.inline_image_widgets()
    assert len(widgets) == 1
    assert isinstance(widgets[0], AnimatedImage)
    assert widgets[0].movie_path == str(gif)
    QTest.qWait(50)
    assert widgets[0].is_running is True

    controller.append("ai.chat", "assistant", "下一条")
    old = bubble.inline_image_widgets()
    assert old, "历史消息仍要保留首帧"
    assert not isinstance(old[0], AnimatedImage)


def test_image_only_fenced_block_is_unwrapped(controller, tmp_path: Path) -> None:
    path = _write_png(tmp_path / "meme.png")
    controller.open("ai.chat", pet_slot=0)
    window = controller.window_for("ai.chat")
    assert window is not None

    controller.append(
        "ai.chat", "assistant", f"```markdown\n![表情包](<{path}>)\n```"
    )
    bubble = window.bubbles()[-1]
    assert len(bubble.inline_image_widgets()) == 1
    assert "```" not in bubble.body.text()


def test_remote_markdown_image_shows_a_placeholder(controller) -> None:
    controller.open("ai.chat", pet_slot=0)
    window = controller.window_for("ai.chat")
    assert window is not None

    controller.append("ai.chat", "assistant", "![猫](https://example.com/cat.png)")
    bubble = window.bubbles()[-1]
    assert "[图片：猫]" in bubble.body.text()
    assert bubble.inline_image_widgets() == []


def test_image_inside_a_code_block_with_other_content_stays_code(
    controller, tmp_path: Path
) -> None:
    path = _write_png(tmp_path / "meme.png")
    controller.open("ai.chat", pet_slot=0)
    window = controller.window_for("ai.chat")
    assert window is not None

    controller.append(
        "ai.chat",
        "assistant",
        f"示例：\n\n```markdown\n![x](<{path}>)\nprint(1)\n```",
    )
    bubble = window.bubbles()[-1]
    assert bubble.inline_image_widgets() == []
    assert "print(1)" in bubble.body.text()
