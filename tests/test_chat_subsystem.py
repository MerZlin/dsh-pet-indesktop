from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pet.chat.models import ChatMessage, ChatSettings, ProviderConfig
from pet.chat.prompt import PromptBuilder, load_character_prompt
from pet.chat.providers import (
    ProviderError,
    SSEParser,
    _is_cert_verify_error,
    _make_ssl_context,
    normalize_chat_endpoint,
)
from pet.chat.session_store import SessionStore
from pet.window import _squash_geometry


def test_provider_endpoint_normalization():
    assert normalize_chat_endpoint("https://api.example.com") == "https://api.example.com/v1/chat/completions"
    assert normalize_chat_endpoint("https://api.example.com/v1/") == "https://api.example.com/v1/chat/completions"
    assert normalize_chat_endpoint("https://api.example.com/v1/chat/completions/") == "https://api.example.com/v1/chat/completions"


def test_sse_parser_handles_fragmented_events_and_done():
    parser = SSEParser()
    first = parser.feed(b'data: {"choices":[{"delta":{"content":"he')
    second = parser.feed(b'llo"}}]}\n\ndata: {"choices":[{"delta":{"content":"!"}}]}\n\n')
    third = parser.feed(b'data: [DONE]\n\n')
    assert first == []
    assert second == ['hello', '!']
    assert third == []
    assert parser.done is True


def test_sse_parser_ignores_keep_alive_and_empty_choices():
    parser = SSEParser()
    assert parser.feed(b': keep-alive\n\n') == []
    assert parser.feed(b'data: {"choices":[]}\n\n') == []


def test_prompt_priority_and_limits(tmp_path: Path):
    character_dir = tmp_path / "assets" / "characters" / "cat"
    character_dir.mkdir(parents=True)
    (character_dir / "manifest.json").write_text(
        json.dumps({"chat": {"system_prompt": "manifest", "theme_color": "#abc"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    settings = ChatSettings(
        default_system_prompt="global",
        history_message_limit=2,
        history_char_limit=20,
    )
    history = [
        ChatMessage("user", "old"),
        ChatMessage("assistant", "older"),
        ChatMessage("user", "new"),
    ]
    builder = PromptBuilder(tmp_path / "assets" / "characters")
    messages = builder.build_messages(settings, "cat", history, "question", role_prompt="override")
    assert messages[0] == {"role": "system", "content": "override"}
    assert messages[-1] == {"role": "user", "content": "question"}
    assert len(messages) <= 4


def test_character_prompt_load(tmp_path: Path):
    root = tmp_path / "characters" / "cat"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"chat": {"system_prompt": "hello"}}), encoding="utf-8")
    assert load_character_prompt(tmp_path / "characters", "cat") == "hello"
    assert load_character_prompt(tmp_path / "characters", "missing") == ""


def test_session_store_atomic_roundtrip_and_corruption(tmp_path: Path):
    store = SessionStore(tmp_path)
    session = store.create("cat", "provider", "system")
    session.messages.append(ChatMessage("user", "hi"))
    store.save(session)
    loaded = store.load(session.session_id)
    assert loaded is not None
    assert loaded.messages[0].content == "hi"
    loaded_path = tmp_path / "sessions" / "cat" / f"{session.session_id}.json"
    loaded_path.write_text("{bad", encoding="utf-8")
    recovered = store.load(session.session_id)
    assert recovered is None
    assert list(loaded_path.parent.glob("*.corrupt-*.json"))


def test_provider_error_is_safe():
    error = ProviderError("bad", status=401)
    assert "401" in str(error)
    assert "api_key" not in str(error).lower()

def test_config_v4_migrates_legacy_chat_fields(tmp_path: Path):
    from pet.config import Config
    root = tmp_path / "appdata"
    cfg_dir = root / "dsh-pet-standalone"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps({
        "version": 2,
        "chat_enabled": True,
        "chat_api_url": "https://deepseek.example/v1/",
        "chat_api_key": "secret-value",
        "chat_model": "deepseek-chat",
        "chat_system_prompt": "legacy prompt",
    }), encoding="utf-8")
    cfg = Config(root)
    settings = cfg.chat_settings()
    assert settings.default_system_prompt == "legacy prompt"
    assert settings.active_config.base_url == "https://deepseek.example/v1/"
    assert settings.active_config.model == "deepseek-chat"
    assert settings.active_config.api_key == "secret-value"
    cfg.save()
    assert json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))["version"] == 4


def test_chat_window_offscreen_smoke(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.chat.widgets import ChatWindow
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window._add("user", "hello")
    window._add("assistant", "hi")
    assert window.title.text().startswith("新会话")
    window.close()
    app.processEvents()

def test_chat_window_has_playful_shell_and_session_controls(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.chat.widgets import ChatWindow

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert window.minimumWidth() >= 520
    assert window.minimumHeight() >= 480
    assert window.phone_shell.objectName() == "phone-shell"
    assert window.title_bar.objectName() == "chat-main-header"
    assert window.session_combo.count() >= 1
    assert window.new_session_button.objectName() == "new-conversation-button"
    assert window.delete_session_button.objectName() == "delete-session-button"
    assert window.clear_button.objectName() == "clear-session-button"
    assert window.composer.objectName() == "chat-composer"
    assert window.send.objectName() == "send-button"
    window.close()
    app.processEvents()


def test_message_bubble_exposes_avatar_body_and_state():
    from pet.chat.widgets import MessageBubble

    bubble = MessageBubble("assistant", "hello", character_id="shenshen")
    assert bubble.objectName() == "message-bubble"
    assert bubble.body.text() == "hello"
    assert bubble.avatar.text() == "S"
    assert bubble.state == "normal"
    bubble.set_state("streaming")
    assert bubble.property("state") == "streaming"
    bubble.set_content("updated")
    assert bubble.body.text() == "updated"


def test_session_sidebar_uses_readable_deepseek_style_list(tmp_path: Path):
    from PySide6.QtWidgets import QApplication, QListWidget
    from pet.config import Config
    from pet.chat.widgets import ChatWindow

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert isinstance(window.session_list, QListWidget)
    assert window.session_list.objectName() == "session-list"
    assert window.session_list.count() >= 1
    assert "QListWidget#session-list" in window.styleSheet()
    window.close()


def test_chat_window_uses_visible_pet_bounds_for_side_placement(tmp_path: Path):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication, QWidget
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    pet = QWidget()
    pet.setGeometry(0, 0, 220, 160)
    visible_rect = QRect(36, 24, 72, 112)
    pet.visible_content_rect = lambda: visible_rect
    window = ChatWindow(Config(tmp_path), "shenshen", pet_window=pet)
    window.show()
    app.processEvents()
    window.position_near_pet(pet, gap=10)
    work_area = app.primaryScreen().availableGeometry()
    assert window.x() >= work_area.left()
    assert window.y() >= work_area.top()
    right_x = visible_rect.right() + 10 + 1
    left_x = visible_rect.left() - 10 - window.width()
    if right_x + window.width() <= work_area.right() + 1:
        assert window.x() == right_x
    elif left_x >= work_area.left():
        assert window.x() == left_x
    else:
        assert window.x() == work_area.left()
    assert window.phone_shell.objectName() == "phone-shell"
    window.close()
    pet.close()
    app.processEvents()


def test_chat_window_moves_to_left_of_pet_at_right_screen_edge(tmp_path: Path):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication, QWidget
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    work_area = app.primaryScreen().availableGeometry()
    pet = QWidget()
    pet.setGeometry(work_area.right() - 140, work_area.top() + 80, 120, 140)
    visible_rect = QRect(work_area.right() - 100, work_area.top() + 100, 80, 100)
    pet.visible_content_rect = lambda: visible_rect
    window = ChatWindow(Config(tmp_path), "shenshen", pet_window=pet)
    window.show()
    app.processEvents()
    window.position_near_pet(pet, gap=10)

    left_x = visible_rect.left() - 10 - window.width()
    if left_x >= work_area.left():
        assert window.x() + window.width() <= visible_rect.left()
    else:
        assert window.x() == work_area.left()
    assert window.y() >= work_area.top()
    window.close()
    pet.close()
    app.processEvents()


def test_pet_window_visible_content_rect_uses_alpha_mask():
    from PySide6.QtCore import QPoint, QRect, QSize
    from PySide6.QtGui import QRegion
    from PySide6.QtWidgets import QApplication
    from pet.window import PetWindow

    class FakePet:
        def frameGeometry(self):
            return QRect(100, 200, 220, 160)

        def character_local_region(self):
            # 无角色轮廓缓存（_mask_bounds 为空）→ 回退 mask 分支
            return QRect()

        def mask(self):
            return QRegion(QRect(36, 24, 72, 112))

    app = QApplication.instance() or QApplication([])
    assert PetWindow.visible_content_rect(FakePet()) == QRect(QPoint(136, 224), QSize(72, 112))
    app.processEvents()


def test_streaming_scroll_only_follows_when_already_near_bottom(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    bar = window.scroll.verticalScrollBar()
    bar.setRange(0, 100)
    bar.setValue(100)
    assert window._is_near_bottom() is True
    bar.setValue(0)
    assert window._is_near_bottom() is False
    window.close()
    app.processEvents()


def test_ai_settings_is_modeless_so_pet_can_still_move(tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from pet.app import PetApp
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    owner = PetApp(app, Config(tmp_path))
    owner.open_chat_settings()
    dialog = owner.chat_settings_dialog
    assert dialog is not None
    assert dialog.isModal() is False
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    dialog.reject()
    app.processEvents()
    assert owner.chat_settings_dialog is None


def test_present_dialog_defers_until_popup_menu_closes(tmp_path: Path, monkeypatch):
    """菜单跟踪会话期间触发的设置弹窗应延迟到菜单关闭后再显示。

    回归 macOS 右键菜单首次点击「AI 设置/桌宠设置」无反应的问题：
    原生 NSMenu 跟踪会话中新建窗口的 show/activate 会被 AppKit 抑制。
    """
    import time

    import pet.app as app_mod
    from PySide6.QtWidgets import QApplication, QDialog
    from pet.app import PetApp
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    owner = PetApp(app, Config(tmp_path))
    state = {"popup": True}

    class FakeQApp:
        @staticmethod
        def activePopupWidget():
            return object() if state["popup"] else None

    monkeypatch.setattr(app_mod, "QApplication", FakeQApp)
    dialog = QDialog()
    owner._present_dialog(dialog)
    app.processEvents()
    assert not dialog.isVisible(), "菜单仍打开时不应立即显示窗口"
    state["popup"] = False
    deadline = time.time() + 3
    while not dialog.isVisible() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert dialog.isVisible(), "菜单关闭后窗口应自动显示"
    dialog.close()
    app.processEvents()


def test_heavy_chat_creation_is_deferred_until_context_menu_closes(tmp_path: Path, monkeypatch):
    import time

    import pet.app as app_mod
    from PySide6.QtWidgets import QApplication
    from pet.app import PetApp
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    owner = PetApp(app, Config(tmp_path))
    state = {"popup": True}
    calls = []

    class FakeQApp:
        @staticmethod
        def activePopupWidget():
            return object() if state["popup"] else None

    monkeypatch.setattr(app_mod, "QApplication", FakeQApp)
    assert owner._defer_while_popup_active("modern-chat", lambda: calls.append("create")) is True
    assert calls == []
    state["popup"] = False
    deadline = time.time() + 1
    while not calls and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert calls == ["create"]


def test_chat_window_session_switch_and_character_refresh(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.chat.widgets import ChatWindow

    app = QApplication.instance() or QApplication([])
    config = Config(tmp_path)
    window = ChatWindow(config, "shenshen")
    first_id = window.session.session_id
    window.new_session()
    assert window.session.session_id != first_id
    assert window.session_combo.count() >= 2
    window.select_session(first_id)
    assert window.session.session_id == first_id
    window.switch_character("another-character")
    assert window.character_id == "another-character"
    assert window.avatar_label.text() == "A"
    assert window.message_stack.currentWidget() is window.empty_page
    window.close()
    app.processEvents()


def test_squash_geometry_uses_logical_frame_size_at_high_dpi():
    # DPR=2 的 QPixmap 物理尺寸不能直接拿来当 QWidget 逻辑绘制尺寸。
    # Q 弹中间帧应与 DPR 无关，并保持脚底在窗口底线。
    logical = _squash_geometry(
        window_width=640,
        window_height=390,
        frame_width=640,
        frame_height=360,
        progress=0.5,
    )
    physical_mistake = _squash_geometry(
        window_width=640,
        window_height=390,
        frame_width=1280,
        frame_height=720,
        progress=0.5,
    )
    assert logical == (-32, 84, 704, 306)
    assert physical_mistake != logical
    assert logical[1] + logical[3] == 390


def test_follow_pet_option_registers_and_unregisters_position_listener(tmp_path: Path):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    class FakePet:
        def __init__(self):
            self.listeners = []

        def add_position_listener(self, listener):
            self.listeners.append(listener)

        def remove_position_listener(self, listener):
            if listener in self.listeners:
                self.listeners.remove(listener)

        def frameGeometry(self):
            return QRect(80, 80, 120, 140)

        def visible_content_rect(self):
            return self.frameGeometry()

    app = QApplication.instance() or QApplication([])
    pet = FakePet()
    config = Config(tmp_path)
    window = ChatWindow(config, "shenshen", pet_window=pet)
    assert window.follow_pet is False
    window.set_follow_pet(True)
    assert window.follow_pet is True
    assert len(pet.listeners) == 1
    assert config.get("chat_follow_pet") is True
    window.set_follow_pet(False)
    assert window.follow_pet is False
    assert pet.listeners == []
    assert config.get("chat_follow_pet") is False
    window.set_follow_pet(True)
    window.show()
    app.processEvents()
    window._on_pet_moved(pet)
    assert window._follow_reposition_timer.isActive()
    window._follow_reposition_timer.stop()
    window.close()
    app.processEvents()


def test_no_chat_packaging_uses_isolated_entrypoint():
    no_chat_entry = Path("packaging/pet_entry_no_chat.py").read_text(encoding="utf-8")
    assert "main(enable_chat=False)" in no_chat_entry
    # spec 是构建产物（*.spec 已 gitignore）；干净检出时不存在则跳过，
    # 存在时（本机构建过）校验无 Chat 变体必须排除 pet.chat。
    for spec_name in (
        "dsh-pet-standalone.spec",
        "dsh-pet-standalone-hd.spec",
        "dsh-pet-standalone-gif.spec",
        "dsh-pet-standalone-webm.spec",
    ):
        spec_path = Path(spec_name)
        if not spec_path.is_file():
            continue
        spec = spec_path.read_text(encoding="utf-8")
        assert "pet_entry_no_chat.py" in spec
        assert "'pet.chat'" in spec
    for spec_name in ("dsh-pet-standalone-gif-chat.spec", "dsh-pet-standalone-webm-chat.spec"):
        spec_path = Path(spec_name)
        if not spec_path.is_file():
            continue
        chat_spec = spec_path.read_text(encoding="utf-8")
        assert "pet_entry.py" in chat_spec
        assert "pet_entry_no_chat.py" not in chat_spec


def test_chat_window_uses_transparent_window_around_opaque_rounded_shell(tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    # 无边框圆角窗：窗口自身透明，只有圆角的 phone-shell 可见（窗外无方形背景）
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    assert "QDialog#chat-window" in window.styleSheet()
    assert "background: transparent" in window.styleSheet()
    assert window.autoFillBackground() is False
    assert window.phone_shell.autoFillBackground() is True
    window.close()
    app.processEvents()


def test_modern_chat_window_icons_are_dark_on_light_surfaces(tmp_path: Path):
    """深色系统下（palette 前景为白）聊天窗图标仍须在浅色表面上可见。

    回归：关闭/最小化/新建/删除等按钮图标曾取 app palette 前景色（深色系统
    下为白色），白底白图不可见。窗口级 menuStyle=modern 应让图标统一为深灰。
    """
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    # 模拟深色系统：palette 前景为白（回归条件——图标曾取 palette 前景色）
    dark_palette = app.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        dark_palette.setColor(group, QPalette.ColorRole.WindowText, QColor("#ffffff"))
        dark_palette.setColor(group, QPalette.ColorRole.Text, QColor("#ffffff"))
        dark_palette.setColor(group, QPalette.ColorRole.ButtonText, QColor("#ffffff"))
        dark_palette.setColor(group, QPalette.ColorRole.Window, QColor("#1e1e1e"))
        dark_palette.setColor(group, QPalette.ColorRole.Base, QColor("#1e1e1e"))
    app.setPalette(dark_palette)
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert window.property("menuStyle") == "modern"
    for name, size, probe in (
        ("minimize_button", 16, (8, 11)),    # 最小化横线
        ("close_button", 16, (8, 8)),        # ✕ 交叉点附近
        ("new_session_button", 16, (8, 8)),  # 圆形加号
        ("delete_session_button", 15, (8, 8)),
    ):
        button = getattr(window, name)
        image = button.icon().pixmap(size, size).toImage()
        x, y = probe
        color = image.pixelColor(x, y)
        assert color.lightness() < 150, f"{name} 图标应为深色，实际 {color.name()}"
    # 强调色/深色底上的图标应为浅色
    window.set_follow_pet(True)  # 勾选后按钮底为强调色 → 图标应变浅
    assert window.follow_button.property("modernDark") is True
    for name, size in (("send", 17), ("follow_button", 14)):
        button = getattr(window, name)
        image = button.icon().pixmap(size, size).toImage()
        dark = light = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() == 0:
                    continue
                if color.lightness() < 100:
                    dark += 1
                elif color.lightness() > 170:
                    light += 1
        assert dark == 0, f"{name} 图标不应有深色像素"
        assert light > 0, f"{name} 图标应含浅色像素"
    window.close()
    app.processEvents()


def test_icon_theme_inherits_from_ancestor_widget():
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication, QWidget
    from pet.context_menus.icons import _icon_theme, vector_widget_icon

    app = QApplication.instance() or QApplication([])
    root = QWidget()
    root.setProperty("menuStyle", "modern")
    child = QWidget(root)
    assert _icon_theme(child) == ("modern", False)
    assert _icon_theme(QWidget()) == ("", False)
    accent = QWidget(child)
    accent.setProperty("modernDark", True)
    assert _icon_theme(accent) == ("modern", True)
    # 渲染出的图标颜色跟随继承的主题，深色 palette 不参与
    palette = root.palette()
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
    root.setPalette(palette)
    icon = vector_widget_icon(child, "minimize", 16)
    color = icon.pixmap(16, 16).toImage().pixelColor(8, 11)
    assert color.lightness() < 150
    app.processEvents()


def test_chat_window_uses_modern_two_pane_ai_chat_layout(tmp_path: Path):
    from PySide6.QtWidgets import QApplication, QWidget
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert window.minimumWidth() >= 520
    assert window.findChild(QWidget, "deepseek-sidebar") is not None
    assert window.findChild(QWidget, "chat-main") is not None
    assert window.findChild(QWidget, "chat-main-header") is not None
    assert window.findChild(QWidget, "new-conversation-button") is not None
    assert window.findChild(QWidget, "floating-composer") is not None
    assert "QFrame#deepseek-sidebar" in window.styleSheet()
    assert "QFrame#chat-main" in window.styleSheet()
    window.close()
    app.processEvents()


def test_legacy_chat_window_renames_current_session(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QInputDialog

    from pet.chat.legacy_widgets import ChatWindow
    from pet.config import Config

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *args, **kwargs: ("新名字", True)))
    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert window.rename_session_button is not None
    window.rename_current_session()
    assert window.session.custom_title == "新名字"
    assert window.session_combo.currentText() == "新名字"
    # 取消不修改
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *args, **kwargs: ("不应生效", False)))
    window.rename_current_session()
    assert window.session.custom_title == "新名字"
    window.close()
    app.processEvents()


def test_legacy_and_modern_chat_windows_use_independent_modules_and_styles(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.legacy_widgets import ChatWindow as LegacyChatWindow
    from pet.chat.widgets import ChatWindow as ModernChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    legacy = LegacyChatWindow(Config(tmp_path), "shenshen")
    modern = ModernChatWindow(Config(tmp_path), "shenshen")
    assert legacy.minimumWidth() == 380
    assert legacy.maximumWidth() == 560
    assert modern.minimumWidth() >= 520
    assert "#1d2634" in legacy.styleSheet()
    assert "QFrame#deepseek-sidebar" in modern.styleSheet()
    assert legacy.styleSheet() != modern.styleSheet()
    legacy.close()
    modern.close()
    app.processEvents()


def test_chat_ui_style_defaults_modern_and_dispatches_classic_on_request(tmp_path: Path):
    from pet.app import PetApp
    from pet.config import Config

    config = Config(tmp_path)
    assert config.get("chat_ui_style") == "modern"
    manager = PetApp(object(), config, enable_chat=True)
    manager.win = object()
    calls = []
    manager.open_modern_chat = lambda: calls.append("modern")
    manager.open_legacy_chat = lambda: calls.append("classic")
    manager.open_chat()
    config.set("chat_ui_style", "classic")
    manager.open_chat()
    assert calls == ["modern", "classic"]


def test_single_modern_menu_routes_to_selected_chat_dispatcher():
    from PySide6.QtWidgets import QApplication, QMenu
    from pet.context_menu import populate_context_menu

    class Config(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class Pet:
        cfg = Config(context_menu_template="legacy", character="shenshen", on_top=False)
        on_open_chat_settings = on_open_legacy_settings = on_open_modern_settings = None
        idles = turns = moves = clicks = acts = []
        playback_speed = scale = 1.0
        drag_physics = no_move = False

        def __init__(self):
            self.calls = []
            self.on_open_chat = lambda: self.calls.append("selected")
            self.on_open_modern_chat = lambda: self.calls.append("unexpected")

        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    app = QApplication.instance() or QApplication([])
    pet = Pet()
    menu = QMenu()
    populate_context_menu(menu, pet)
    next(action for action in menu.actions() if action.text() == "AI 对话").trigger()
    assert pet.calls == ["selected"]
    app.processEvents()


def test_modern_message_rows_hide_identity_and_offer_inline_tools():
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import MessageBubble

    app = QApplication.instance() or QApplication([])
    user = MessageBubble("user", "hello", "shenshen")
    assistant = MessageBubble("assistant", "hi", "shenshen")
    assert user.avatar.isHidden() and user.meta.isHidden()
    assert assistant.avatar.isHidden() and assistant.meta.isHidden()
    assert user.findChild(object, "message-copy-button") is not None
    assert user.findChild(object, "message-edit-button") is None
    assert assistant.findChild(object, "message-retry-button") is None
    assert assistant.findChild(object, "message-like-button") is None
    assert assistant.findChild(object, "message-dislike-button") is None
    app.processEvents()


def test_modern_sidebar_groups_sessions_and_exposes_row_action_menu(tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    headers = [
        window.session_list.item(index).text()
        for index in range(window.session_list.count())
        if window.session_list.item(index).data(Qt.ItemDataRole.UserRole) is None
    ]
    assert "今天" in headers
    session_items = [
        window.session_list.item(index)
        for index in range(window.session_list.count())
        if window.session_list.item(index).data(Qt.ItemDataRole.UserRole)
    ]
    row = window.session_list.itemWidget(session_items[0])
    assert row is not None
    assert row.findChild(object, "session-more-button") is not None
    window.close()
    app.processEvents()


def test_new_conversation_keeps_empty_canvas_on_the_styled_white_surface(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.new_session()
    app.processEvents()
    assert window.message_stack.currentWidget() is window.empty_page
    assert window.empty_page.autoFillBackground() is False
    assert window.scroll.viewport().autoFillBackground() is False
    window.close()
    app.processEvents()


def test_modern_sidebar_multi_select_can_batch_pin_and_delete_sessions(tmp_path: Path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog
    from pet.chat import widgets as chat_widgets
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = chat_widgets.ChatWindow(Config(tmp_path), "shenshen")
    first_id = window.session.session_id
    window.new_session()
    second_id = window.session.session_id

    def selector_for(session_id):
        for index in range(window.session_list.count()):
            item = window.session_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == session_id:
                return window.session_list.itemWidget(item).findChild(
                    object, "session-select-button"
                )
        raise AssertionError(f"missing session row: {session_id}")

    window.multi_select_button.click()
    selector_for(first_id).click()
    selector_for(second_id).click()
    assert window.multi_select_button.objectName() == "session-multi-select-button"
    assert window.batch_action_bar.isHidden() is False
    assert window.session_caption.text() == "已选择 2 个对话"
    session_items = [
        window.session_list.item(index)
        for index in range(window.session_list.count())
        if window.session_list.item(index).data(Qt.ItemDataRole.UserRole)
    ]
    assert all(
        window.session_list.itemWidget(item)
        .findChild(object, "session-select-button")
        .isHidden() is False
        for item in session_items
    )

    window.batch_pin_button.click()
    assert window.store.load(first_id, "shenshen").pinned is True
    assert window.store.load(second_id, "shenshen").pinned is True
    assert window.batch_action_bar.isHidden() is True

    monkeypatch.setattr(
        chat_widgets.DeleteConversationDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    window.multi_select_button.click()
    selector_for(first_id).click()
    selector_for(second_id).click()
    window.batch_delete_button.click()
    assert window.store.load(first_id, "shenshen") is None
    assert window.store.load(second_id, "shenshen") is None
    assert len(window.store.list("shenshen")) == 1
    window.close()
    app.processEvents()


def test_modern_delete_uses_custom_confirmation_and_respects_cancel(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog
    from pet.chat import widgets as chat_widgets
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = chat_widgets.ChatWindow(Config(tmp_path), "shenshen")
    doomed_id = window.session.session_id
    window.new_session()

    seen = []

    def cancel(dialog):
        seen.append(dialog)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(chat_widgets.DeleteConversationDialog, "exec", cancel)
    window._session_action(doomed_id, "delete")
    assert window.store.load(doomed_id, "shenshen") is not None
    assert seen[0].objectName() == "delete-conversation-dialog"
    assert seen[0].findChild(object, "confirm-delete-button") is not None

    monkeypatch.setattr(
        chat_widgets.DeleteConversationDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    window._session_action(doomed_id, "delete")
    assert window.store.load(doomed_id, "shenshen") is None
    window.close()
    app.processEvents()


def test_modern_header_avatar_uses_pet_image_without_colored_round_backplate(tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    avatar = QPixmap(34, 34)
    avatar.fill(Qt.GlobalColor.red)

    class Pet:
        def icon_pixmap(self, size):
            assert size == 34
            return avatar

    window = ChatWindow(Config(tmp_path), "shenshen", pet_window=Pet())
    assert window.avatar_label.text() == ""
    assert window.avatar_label.pixmap() is not None
    assert window.avatar_label.pixmap().isNull() is False
    avatar_style = window.avatar_label.styleSheet()
    assert "background-color" not in avatar_style
    assert "border-radius" not in avatar_style
    window.close()
    app.processEvents()


def test_batch_delete_style_outranks_generic_batch_button_color(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert (
        "QFrame#session-batch-action-bar QPushButton#batch-delete-button"
        in window.styleSheet()
    )
    window.close()
    app.processEvents()


def test_delete_confirmation_uses_visible_rounded_card_surface():
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import DeleteConversationDialog

    app = QApplication.instance() or QApplication([])
    dialog = DeleteConversationDialog(3)
    card = dialog.findChild(object, "delete-dialog-card")
    assert card is not None
    assert "QFrame#delete-dialog-card" in dialog.styleSheet()
    assert "QDialog#delete-conversation-dialog{background:transparent" in dialog.styleSheet()
    assert card.graphicsEffect() is not None
    dialog.close()
    app.processEvents()


def test_chat_session_custom_title_and_pin_round_trip():
    from pet.chat.models import ChatSession

    session = ChatSession.create("shenshen", "provider", "prompt")
    session.custom_title = "置顶会话"
    session.pinned = True
    restored = ChatSession.from_dict(session.to_dict())
    assert restored.custom_title == "置顶会话"
    assert restored.pinned is True


def test_modern_composer_is_single_rounded_card_with_inline_send(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert window.composer.findChild(object, "composer-toolbar") is not None
    parent = window.send.parentWidget()
    assert parent is not None
    assert window.composer.isAncestorOf(window.send)
    style = window.styleSheet()
    assert "QFrame#chat-composer" in style
    assert "border-radius: 18px" in style
    assert "QToolButton#send-button" in style
    window.close()
    app.processEvents()


def test_modern_sidebar_footer_only_keeps_status_and_follow(tmp_path: Path):
    from PySide6.QtWidgets import QApplication, QToolButton
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    footer = window.findChild(object, "sidebar-footer")
    assert footer is not None
    assert window.provider_label.isVisibleTo(footer) is False
    visible_tools = [
        button for button in footer.findChildren(QToolButton)
        if not button.isHidden()
    ]
    # 跟随桌宠 + 删除当前会话 + 清空当前会话（此前两个按钮是孤儿控件）
    assert visible_tools == [
        window.follow_button, window.delete_session_button, window.clear_button,
    ]
    assert window.follow_button.icon().isNull() is False
    assert window.follow_button.minimumHeight() >= 28
    assert window.delete_session_button.icon().isNull() is False
    assert window.clear_button.icon().isNull() is False
    window.close()
    app.processEvents()


def test_modern_new_conversation_button_is_compact_and_elevated(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    button = window.new_session_button
    assert button.maximumHeight() <= 36
    assert button.graphicsEffect() is not None
    assert button.icon().isNull() is False
    window.close()
    app.processEvents()


def test_modern_message_surface_and_toolbar_are_separate_and_copy_only():
    from PySide6.QtWidgets import QToolButton
    from pet.chat.widgets import MessageBubble

    user = MessageBubble("user", "hello", character_id="shenshen")
    assistant = MessageBubble("assistant", "hi", character_id="shenshen")
    assert user.findChild(object, "message-surface") is not None
    assert assistant.findChild(object, "message-surface") is not None
    assert [button.objectName() for button in user.tools.findChildren(QToolButton)] == [
        "message-copy-button"
    ]
    assert [button.objectName() for button in assistant.tools.findChildren(QToolButton)] == [
        "message-copy-button"
    ]


def test_modern_composer_supports_file_picker_state_and_drop_payload(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatComposer

    app = QApplication.instance() or QApplication([])
    document = tmp_path / "notes.txt"
    document.write_text("附件正文", encoding="utf-8")
    image = tmp_path / "preview.png"
    image.write_bytes(b"small-image-payload")
    composer = ChatComposer()
    composer.add_attachments([document, image])
    assert composer.acceptDrops() is True
    assert composer.mode_button is None
    assert composer.input.maximumHeight() <= 72
    assert composer.attachment_paths == [document.resolve(), image.resolve()]
    assert composer.attachment_strip.isHidden() is False
    prompt = composer.attachment_prompt()
    assert "notes.txt" in prompt
    assert "附件正文" in prompt
    image_payloads = composer.image_payloads()
    assert image_payloads[0]["type"] == "image_url"
    assert image_payloads[0]["image_url"]["url"].startswith("data:image/png;base64,")
    composer.clear_attachments()
    assert composer.attachment_paths == []
    app.processEvents()


def test_modern_sidebar_becomes_overlay_drawer_on_compact_width(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(760, 600)
    window.show()
    app.processEvents()
    assert window.property("compactLayout") is True
    assert window.sidebar.isHidden()
    assert window.sidebar_toggle_button.isVisible()
    window.toggle_sidebar()
    app.processEvents()
    assert window.sidebar.isVisible()
    assert window.sidebar.property("overlayDrawer") is True
    assert window.sidebar_scrim.isVisible()
    window.toggle_sidebar()
    assert window.sidebar.isHidden()
    window.close()
    app.processEvents()


def test_compact_sidebar_scrim_starts_at_actual_drawer_edge(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(620, 600)
    window.show()
    app.processEvents()
    window.toggle_sidebar()
    app.processEvents()
    assert window.sidebar_scrim.x() == window.sidebar.geometry().right() + 1
    assert window.sidebar_scrim.width() == window.phone_shell.width() - window.sidebar.width()
    window.close()
    app.processEvents()


def test_error_message_has_rounded_surface_and_single_error_row():
    from PySide6.QtWidgets import QApplication, QHBoxLayout
    from pet.chat.widgets import MessageBubble

    app = QApplication.instance() or QApplication([])
    bubble = MessageBubble("assistant", "请求失败：HTTP 400", "shenshen")
    bubble.set_state("error")
    assert bubble.surface.property("state") == "error"
    assert bubble.error_actions.isVisibleTo(bubble)
    assert isinstance(bubble.error_actions.layout(), QHBoxLayout)
    assert bubble.status_label.parentWidget() is bubble.error_actions
    assert bubble.retry_button.parentWidget() is bubble.error_actions
    assert bubble.tools.isHidden()
    margins = bubble.surface.layout().contentsMargins()
    assert margins.left() >= 10
    assert margins.right() >= 10
    assert margins.top() >= 8
    assert margins.bottom() >= 8
    bubble.deleteLater()
    app.processEvents()


def test_attachment_chip_has_thumbnail_and_hover_remove(tmp_path: Path):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import AttachmentChip, ChatComposer

    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "preview.png"
    image = QImage(24, 24, QImage.Format.Format_ARGB32)
    image.fill(0xFF55AAEE)
    assert image.save(str(image_path))
    composer = ChatComposer()
    composer.add_attachments([image_path])
    chip = composer.attachment_strip.findChild(AttachmentChip)
    assert chip is not None
    assert chip.preview.pixmap().isNull() is False
    assert chip.remove_button.isHidden()
    chip.enterEvent(None)
    assert chip.remove_button.isHidden() is False
    chip.remove_button.click()
    assert composer.attachment_paths == []
    app.processEvents()


def test_file_drop_is_intercepted_by_input_instead_of_inserting_file_url(tmp_path: Path):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatComposer

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "test.log"
    path.write_text("hello", encoding="utf-8")
    composer = ChatComposer()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDropEvent(
        QPointF(5, 5), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    composer.input.dropEvent(event)
    assert composer.attachment_paths == [path.resolve()]
    assert "file://" not in composer.input.toPlainText()
    app.processEvents()


def test_assistant_message_expands_to_available_timeline_width(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(1180, 700)
    bubble = window._add("assistant", "很长的回复" * 30)
    window.show()
    app.processEvents()
    available = window.scroll.viewport().width() - 2 * window.message_horizontal_margin
    assert bubble.width() >= available - 8
    window.close()
    app.processEvents()


def test_status_and_model_are_in_header_not_sidebar(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert window.status.parentWidget().objectName() == "header-status"
    assert window.status_dot.parentWidget().objectName() == "header-status"
    assert window.provider_label.parentWidget().objectName() == "header-status"
    assert window.provider_label.text() == window.settings.active_config.model
    assert window.status.isVisibleTo(window.title_bar)
    window.close()
    app.processEvents()


def test_empty_provider_response_becomes_visible_error(tmp_path: Path):
    import time

    from PySide6.QtWidgets import QApplication
    from pet.chat.models import ProviderConfig
    from pet.chat.service import ChatService

    class EmptyProvider:
        def stream(self, messages, config, cancel):
            if False:
                yield ""

    app = QApplication.instance() or QApplication([])
    service = ChatService(provider=EmptyProvider())
    errors = []
    finished = []
    service.error.connect(lambda _rid, text: errors.append(text))
    service.finished.connect(lambda _rid, text: finished.append(text))
    service.send([], ProviderConfig(
        provider_id="test", name="test", base_url="http://invalid", model="test"
    ))
    deadline = time.time() + 2
    while service.busy and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert finished == []
    assert errors and "未返回" in errors[0]


def test_streaming_reply_uses_typewriter_and_finishes_after_buffer_drains(tmp_path: Path):
    import time
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window._bubble = window._add("assistant", "")
    window._active_request_id = "request"
    text = "这是一段用于确认逐字输出效果的流式回复。"
    window._delta("request", text)
    assert window._bubble.body.text() != text
    window._finished("request", text)
    deadline = time.time() + 5
    while window._bubble.body.text() != text and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window._bubble.body.text() == text
    assert window.session.messages[-1].content == text
    window.close()
    app.processEvents()


def test_session_switch_during_typewriter_drain_does_not_cross_write(tmp_path: Path):
    """模型已完成、打字机仍在排空时切换会话：不得把上一轮回复写进新会话。

    回归背景：_reset 此前不停止 _typewriter_timer、不清 _pending_output /
    _pending_finish_text，切会话后迟到的 tick 会把旧回复 append+save 到
    新会话（原会话丢回复、新会话多幻影消息）。
    """
    import time
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.chat.models import ChatMessage
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    # 模拟：service 已不忙（模型返回完毕），打字机仍在逐字排空
    window._active_request_id = "request"
    window._pending_output = "残留输出"
    window._pending_finish_text = "完整的旧回复"
    window._typewriter_timer.start()
    window.session.messages.append(ChatMessage("user", "旧提问"))
    old_session = window.session

    window.new_session()

    assert not window._typewriter_timer.isActive()
    assert window._pending_output == ""
    assert window._pending_finish_text is None
    assert window.session is not old_session
    assert all(m.role != "assistant" for m in window.session.messages)
    # 事件循环再转一会儿，确认没有迟到的 tick 把旧回复写进新会话
    deadline = time.time() + 0.5
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert all(m.role != "assistant" for m in window.session.messages)
    window.close()
    app.processEvents()


def test_image_payloads_skips_deleted_attachments(tmp_path: Path):
    """附件文件已删除时发送图片不得抛 FileNotFoundError。"""
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatComposer

    app = QApplication.instance() or QApplication([])
    composer = ChatComposer()
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    composer.add_attachments([str(image)])
    assert len(composer.attachment_paths) == 1
    image.unlink()
    assert composer.image_payloads() == []
    composer.deleteLater()
    app.processEvents()


def test_enter_while_ime_composing_does_not_send():
    """输入法组合中回车用于上屏候选，不得触发发送（现代+经典两套）。"""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QInputMethodEvent, QKeyEvent
    from PySide6.QtWidgets import QApplication

    from pet.chat.legacy_widgets import ChatComposer as LegacyComposer
    from pet.chat.widgets import ChatComposer as ModernComposer

    app = QApplication.instance() or QApplication([])
    for composer_type in (ModernComposer, LegacyComposer):
        composer = composer_type()
        sent = []
        composer.send_requested.connect(lambda: sent.append(1))
        enter = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
        )

        # 未组合：回车直接发送
        assert composer.eventFilter(composer.input, enter) is True
        assert sent == [1]

        # 组合中：回车不发送，交给输入法
        im = QInputMethodEvent("拼音候选", [])
        composer.eventFilter(composer.input, im)
        assert composer._ime_composing is True
        assert composer.eventFilter(composer.input, enter) is False
        assert sent == [1]

        # 提交后：恢复发送
        commit = QInputMethodEvent()
        commit.setCommitString("候选")
        composer.eventFilter(composer.input, commit)
        assert composer._ime_composing is False
        assert composer.eventFilter(composer.input, enter) is True
        assert sent == [1, 1]
        composer.deleteLater()
    app.processEvents()


def test_modern_chat_pauses_follow_when_user_scrolls_up(tmp_path: Path):
    """上翻阅读历史时暂停自动滚底；回到顶部/底部时按位置更新跟随状态。"""
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(700, 500)
    for index in range(40):
        window._add("assistant", f"第 {index} 条消息。" * 40)
    window.show()
    app.processEvents()
    app.processEvents()
    bar = window.scroll.verticalScrollBar()
    if bar.maximum() <= 0:
        window.close()
        app.processEvents()
        pytest.skip("offscreen 下内容高度不足以产生滚动条")
    # 用户滚动到底部 → 跟随
    bar.setValue(bar.maximum())
    app.processEvents()
    assert window._stream_follow_output is True
    # 用户上翻 → 暂停跟随
    bar.setValue(0)
    app.processEvents()
    assert window._stream_follow_output is False
    window.close()
    app.processEvents()


def test_session_switch_lands_at_bottom(tmp_path: Path):
    """切换会话再切回长会话，必须落在最新消息底部。

    回归背景：切会话时 _bottom 的 singleShot(0) 早于布局重算（maximum 仍为
    0），切回长会话后停在顶部；加 80ms 兜底拍修复。
    """
    import time
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.chat.models import ChatMessage
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(700, 500)
    long_a = window._new_session()
    for index in range(30):
        long_a.messages.append(ChatMessage("assistant", f"长会话 A 第 {index} 条。" * 20))
    window.store.save(long_a)
    short_b = window._new_session()
    for _ in range(2):
        short_b.messages.append(ChatMessage("assistant", "短会话 B 消息。" * 20))
    window.store.save(short_b)

    window.show()
    app.processEvents()
    window.select_session(long_a.session_id)
    app.processEvents()
    window.select_session(short_b.session_id)
    app.processEvents()
    window.select_session(long_a.session_id)
    deadline = time.time() + 1
    bar = window.scroll.verticalScrollBar()
    while not (bar.value() >= bar.maximum() - 24) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert bar.value() >= bar.maximum() - 24, (
        f"切回长会话应落在底部（max={bar.maximum()} value={bar.value()}）"
    )
    window.close()
    app.processEvents()


def test_append_look_sync_persists_to_session(tmp_path: Path):
    """「看看屏幕」结果必须同步进当前会话并持久化（PR#11 重写后丢失）。"""
    from PySide6.QtWidgets import QApplication

    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    before = len(window.session.messages)
    window.append_look_sync("[看看屏幕] 前台窗口：测试", "这是屏幕分析结果")
    after = len(window.session.messages)
    assert after == before + 2
    reloaded = window.store.load(window.session.session_id, "shenshen")
    assert reloaded is not None and len(reloaded.messages) == after
    window.close()
    app.processEvents()


def test_quit_closes_active_context_menu_before_leaving_event_loop(monkeypatch):
    import time
    from PySide6.QtWidgets import QApplication
    import pet.window as window_mod
    from pet.window import PetWindow

    events = []

    class FakeMenu:
        def close(self):
            events.append("close")

    class FakeApp:
        def quit(self):
            events.append("quit")

    class FakeQApplication:
        @staticmethod
        def instance():
            return FakeApp()

    class FakePet:
        _active_context_menu = FakeMenu()

        def _save_position(self):
            events.append("save")

    monkeypatch.setattr(window_mod, "QApplication", FakeQApplication)
    PetWindow._request_quit(FakePet())
    # 退出不再保存当前位置（自动移动/抛掷后的位置不覆盖手动放置记忆）
    assert events == ["close"]
    app = QApplication.instance() or QApplication([])
    deadline = time.time() + 0.2
    while events[-1:] != ["quit"] and time.time() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert events == ["close", "quit"]


def test_short_conversation_starts_at_timeline_top_while_composer_stays_bottom(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(1180, 720)
    bubble = window._add("assistant", "一段高度有限的回复。" * 8)
    window.show()
    app.processEvents()
    app.processEvents()
    bubble_top = bubble.mapTo(window.scroll.viewport(), bubble.rect().topLeft()).y()
    composer_bottom = window.composer_card.mapTo(
        window.chat_main, window.composer_card.rect().bottomLeft()
    ).y()
    assert 0 <= bubble_top <= 60
    assert window.chat_main.height() - composer_bottom <= 32
    window.close()
    app.processEvents()


def test_close_required_menu_callback_runs_only_after_exec_returns(monkeypatch):
    from PySide6.QtCore import QPoint
    import pet.window as window_mod
    from pet.window import PetWindow

    state = {"inside_exec": False}
    calls = []

    class FakeSignal:
        def connect(self, callback):
            self.callback = callback

        def emit(self):
            self.callback()

    class FakeMenu:
        def __init__(self, _parent):
            self.aboutToShow = FakeSignal()
            self.aboutToHide = FakeSignal()
            self.destroyed = FakeSignal()

        def exec(self, _pos):
            state["inside_exec"] = True
            self._deferred_callbacks = [lambda: calls.append(state["inside_exec"])]
            state["inside_exec"] = False

        def findChildren(self, _type):
            return []

        def sizeHint(self):
            from PySide6.QtCore import QSize

            return QSize(120, 180)

        def setLayoutDirection(self, _direction):
            pass

        def deleteLater(self):
            self.destroyed.emit()

    class FakePet:
        def _restore_on_top_after_context_menu(self):
            pass

        def visible_content_rect(self):
            from PySide6.QtCore import QRect

            return QRect(300, 300, 200, 150)

        def _screen_available(self, _screen_name=None):
            from PySide6.QtCore import QRect

            class _Screen:
                @staticmethod
                def availableGeometry():
                    return QRect(0, 0, 1920, 1080)

            return _Screen()

    class _BubbleStub:
        def hide(self):
            pass

    FakePet._speech_bubble = _BubbleStub()

    monkeypatch.setattr(window_mod, "QMenu", FakeMenu)
    monkeypatch.setattr(window_mod, "_populate_context_menu", lambda _menu, _pet: None)
    monkeypatch.setattr(window_mod.QTimer, "singleShot", lambda *args: args[-1]())
    pet = FakePet()
    PetWindow._show_context_menu(pet, QPoint(12, 18))
    assert calls == [False]
    assert pet._active_context_menu is None


def test_context_menu_window_callback_waits_until_old_menu_is_destroyed(monkeypatch):
    from PySide6.QtCore import QPoint

    import pet.window as window_mod
    from pet.window import PetWindow

    calls = []
    timers = []
    created = []

    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def emit(self):
            for callback in list(self.callbacks):
                callback()

    class FakeMenu:
        def __init__(self, _parent):
            self.aboutToShow = FakeSignal()
            self.aboutToHide = FakeSignal()
            self.destroyed = FakeSignal()
            self.delete_requested = False
            created.append(self)

        def exec(self, _pos):
            self._deferred_callbacks = [lambda: calls.append("settings")]

        def findChildren(self, _type):
            return []

        def sizeHint(self):
            from PySide6.QtCore import QSize

            return QSize(120, 180)

        def setLayoutDirection(self, _direction):
            pass

        def deleteLater(self):
            self.delete_requested = True

    class FakePet:
        def _restore_on_top_after_context_menu(self):
            pass

        def visible_content_rect(self):
            from PySide6.QtCore import QRect

            return QRect(300, 300, 200, 150)

        def _screen_available(self, _screen_name=None):
            from PySide6.QtCore import QRect

            class _Screen:
                @staticmethod
                def availableGeometry():
                    return QRect(0, 0, 1920, 1080)

            return _Screen()

    class _BubbleStub:
        def hide(self):
            pass

    FakePet._speech_bubble = _BubbleStub()

    monkeypatch.setattr(window_mod, "QMenu", FakeMenu)
    monkeypatch.setattr(window_mod, "_populate_context_menu", lambda _menu, _pet: None)
    monkeypatch.setattr(
        window_mod.QTimer,
        "singleShot",
        lambda *args: timers.append(args[-1]),
    )

    pet = FakePet()
    PetWindow._show_context_menu(pet, QPoint(12, 18))
    menu = created[0]
    assert menu.delete_requested is True
    assert calls == []
    assert timers == []

    menu.destroyed.emit()
    assert calls == []
    assert len(timers) == 1
    timers.pop()()
    assert calls == ["settings"]


def test_context_menu_drops_callbacks_when_owning_pet_is_already_destroyed(monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import QPoint

    import pet.window as window_mod
    from pet.window import PetWindow

    timers = []
    created = []

    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def emit(self):
            for callback in list(self.callbacks):
                callback()

    class FakeMenu:
        def __init__(self, _parent):
            self.aboutToShow = FakeSignal()
            self.aboutToHide = FakeSignal()
            self.destroyed = FakeSignal()
            created.append(self)

        def exec(self, _pos):
            self._deferred_callbacks = [lambda: None]

        def findChildren(self, _type):
            return []

        def sizeHint(self):
            from PySide6.QtCore import QSize

            return QSize(120, 180)

        def setLayoutDirection(self, _direction):
            pass

        def deleteLater(self):
            pass

    class FakePet:
        def _restore_on_top_after_context_menu(self):
            pass

        def visible_content_rect(self):
            from PySide6.QtCore import QRect

            return QRect(300, 300, 200, 150)

        def _screen_available(self, _screen_name=None):
            from PySide6.QtCore import QRect

            class _Screen:
                @staticmethod
                def availableGeometry():
                    return QRect(0, 0, 1920, 1080)

            return _Screen()

    class _BubbleStub:
        def hide(self):
            pass

    FakePet._speech_bubble = _BubbleStub()

    monkeypatch.setattr(window_mod, "QMenu", FakeMenu)
    monkeypatch.setattr(window_mod, "_populate_context_menu", lambda _menu, _pet: None)
    monkeypatch.setattr(
        window_mod, "shiboken6", SimpleNamespace(isValid=lambda _obj: False), raising=False
    )
    monkeypatch.setattr(
        window_mod.QTimer, "singleShot", lambda *args: timers.append(args)
    )

    pet = FakePet()
    PetWindow._show_context_menu(pet, QPoint(12, 18))
    created[0].destroyed.emit()
    assert timers == []


def test_close_required_actions_are_queued_while_menu_is_visible():
    from PySide6.QtWidgets import QApplication, QMenu
    from pet.context_menus.shared import add_action, take_deferred_menu_callbacks

    app = QApplication.instance() or QApplication([])
    calls = []
    menu = QMenu()
    action = add_action(menu, "AI 对话", None, lambda: calls.append("open"), close_on_trigger=True)
    menu.show()
    app.processEvents()
    action.trigger()
    assert calls == []
    callbacks = take_deferred_menu_callbacks(menu)
    assert len(callbacks) == 1
    callbacks[0]()
    assert calls == ["open"]
    menu.close()
    app.processEvents()


def test_conversation_mode_pins_composer_to_bottom_and_top_aligns_short_messages(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(1180, 720)
    window._add("user", "你好")
    window._add("assistant", "一条比较短的回复。")
    window.show()
    app.processEvents()
    app.processEvents()
    composer_bottom = window.composer_card.mapTo(
        window.chat_main, window.composer_card.rect().bottomLeft()
    ).y()
    first_bubble = window._bubbles[0]
    bubble_top = first_bubble.mapTo(window.scroll.viewport(), first_bubble.rect().topLeft()).y()
    assert window.chat_main.height() - composer_bottom <= 32
    assert 0 <= bubble_top <= 60
    assert window.scroll.verticalScrollBar().maximum() == 0
    window.close()
    app.processEvents()


def test_empty_conversation_centers_prompt_and_composer_as_one_group(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(1180, 720)
    window.show()
    app.processEvents()
    app.processEvents()
    group_top = window.scroll.mapTo(window.chat_main, window.scroll.rect().topLeft()).y()
    group_bottom = window.composer_card.mapTo(
        window.chat_main, window.composer_card.rect().bottomLeft()
    ).y()
    group_center = (group_top + group_bottom) / 2
    assert abs(group_center - window.chat_main.height() / 2) <= 40
    assert window.scroll.verticalScrollBar().maximum() == 0
    window.close()
    app.processEvents()


def test_long_conversation_scrolls_only_after_timeline_overflows(tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window.resize(1180, 720)
    for index in range(8):
        window._add("assistant", f"第 {index + 1} 段：" + "这是一段足够长的回复。" * 18)
    window.show()
    app.processEvents()
    app.processEvents()
    assert window.scroll.verticalScrollBar().maximum() > 0
    assert (
        window.scroll.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert window.scroll.horizontalScrollBar().isVisible() is False
    composer_bottom = window.composer_card.mapTo(
        window.chat_main, window.composer_card.rect().bottomLeft()
    ).y()
    assert window.chat_main.height() - composer_bottom <= 32
    window.close()
    app.processEvents()


def test_present_dialog_restores_a_minimized_chat_window(tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog
    from pet.app import PetApp
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    owner = PetApp(app, Config(tmp_path))
    dialog = QDialog()
    dialog.showMinimized()
    app.processEvents()
    assert dialog.windowState() & Qt.WindowState.WindowMinimized
    owner._present_dialog(dialog)
    app.processEvents()
    assert not dialog.windowState() & Qt.WindowState.WindowMinimized
    assert dialog.isVisible()
    dialog.close()
    app.processEvents()


def test_quit_runs_immediately_when_native_menu_has_already_returned(monkeypatch):
    import pet.window as window_mod
    from pet.window import PetWindow

    events = []

    class FakeApp:
        def quit(self):
            events.append("quit")

    class FakeQApplication:
        @staticmethod
        def instance():
            return FakeApp()

    class FakePet:
        _active_context_menu = None

        def _save_position(self):
            events.append("save")

    monkeypatch.setattr(window_mod, "QApplication", FakeQApplication)
    PetWindow._request_quit(FakePet())
    # 退出不再保存当前位置
    assert events == ["quit"]


def test_pet_animation_and_self_talk_defaults_are_persisted(tmp_path: Path):
    from pet.config import (
        DEFAULT_SELF_TALK_TEXTS,
        Config,
    )

    cfg = Config(tmp_path)
    assert cfg.get("animation_gap_seconds") == 0.0
    assert cfg.get("self_talk_enabled") is False
    assert cfg.get("self_talk_texts") == DEFAULT_SELF_TALK_TEXTS
    cfg.set("animation_gap_seconds", 2.5)
    cfg.set("self_talk_enabled", True)
    cfg.set("self_talk_min_interval", 12.0)
    cfg.set("self_talk_max_interval", 30.0)
    cfg.set("self_talk_texts", ["one", "two"])
    cfg.save()
    loaded = Config(tmp_path)
    assert loaded.get("animation_gap_seconds") == 2.5
    assert loaded.get("self_talk_enabled") is True
    assert loaded.get("self_talk_texts") == ["one", "two"]



def test_pet_settings_dialog_saves_animation_gap_and_self_talk(tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.settings_dialog import PetSettingsDialog

    app = QApplication.instance() or QApplication([])
    config = Config(tmp_path)
    dialog = PetSettingsDialog(config)
    assert dialog.isModal() is False
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    dialog.gap_spin.setValue(4.5)
    dialog.self_talk_check.setChecked(True)
    dialog.min_spin.setValue(7)
    dialog.max_spin.setValue(12)
    dialog.texts_edit.setPlainText("自定义一\n自定义二")
    dialog._save()
    assert config.get("animation_gap_seconds") == 4.5
    assert config.get("self_talk_enabled") is True
    assert config.get("self_talk_min_interval") == 7.0
    assert config.get("self_talk_max_interval") == 12.0
    assert config.get("self_talk_texts") == ["自定义一", "自定义二"]
    app.processEvents()


def test_reference_animation_materials_are_folder_classified():
    from pet import catalog

    root = Path("assets/characters/shenshen/videos")
    files = list(root.rglob("*.webm"))
    names = [path.stem for path in files]
    folder_map = {path.stem: path.parent.name for path in files}
    folder_files = {}
    for path in files:
        folder_files.setdefault(path.parent.name, []).append(path.stem)
    categories = catalog.build_categories(
        names,
        folder_map=folder_map,
        folder_files=folder_files,
    )
    assert len(files) >= 90
    assert categories["idle"] == "待机呼吸休闲"
    assert categories["turn"] == "东张西望"
    assert "点击回应-元气挥手" in categories["clicks"]
    assert "小幅度原地360度旋转展示" in categories["acts"]

def test_pet_speech_bubble_prefers_centered_position_above_character():
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication
    from pet.speech_bubble import PetSpeechBubble

    app = QApplication.instance() or QApplication([])
    bubble = PetSpeechBubble()
    anchor = QRect(300, 280, 120, 140)
    bubble.show_text("好模型……", anchor, duration_ms=500)
    app.processEvents()
    assert abs(bubble.geometry().center().x() - anchor.center().x()) <= 2
    assert bubble.geometry().bottom() < anchor.top()
    bubble.close()


def test_webm_playback_speed_updates_timer_before_and_after_start():
    from PySide6.QtWidgets import QApplication
    from pet.webm_clip import WebMClip

    app = QApplication.instance() or QApplication([])
    clip = WebMClip(Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm"))
    clip.warm_meta()
    clip.set_playback_speed(2.0)
    assert clip._timer.interval() <= 22
    clip.start()
    app.processEvents()
    assert clip._timer.interval() <= 22
    clip.stop()
    clip.set_playback_speed(0.5)
    assert clip._timer.interval() >= 80

@pytest.mark.skipif(
    not Path("assets/characters_gif").is_dir(),
    reason="GIF 派生素材未生成（本地/CI 可选生成，不作为必需检查）",
)
def test_webm_and_gif_animation_sets_are_in_sync():
    webm_root = Path("assets/characters")
    gif_root = Path("assets/characters_gif")
    webm_rel = {
        path.relative_to(webm_root).with_suffix(".gif")
        for path in webm_root.rglob("*.webm")
    }
    gif_rel = {
        path.relative_to(gif_root)
        for path in gif_root.rglob("*.gif")
    }
    assert webm_rel
    assert webm_rel == gif_rel


def test_config_variant_dir_and_legacy_migration(tmp_path, monkeypatch):
    """变体使用独立配置目录，并从旧共享目录一次性迁移配置与会话。"""
    from pet import config as config_mod

    legacy = tmp_path / "dsh-pet-standalone"
    legacy.mkdir()
    (legacy / "config.json").write_text('{"version": 3, "scale": 0.85}', encoding="utf-8")
    (legacy / "sessions").mkdir()

    monkeypatch.setattr(config_mod, "APP_DIR_NAME", "dsh-pet-standalone-webm-chat")
    cfg = config_mod.Config(tmp_path)
    assert cfg.dir == tmp_path / "dsh-pet-standalone-webm-chat"
    assert cfg.path.is_file()
    assert cfg.get("scale") == 0.85
    assert (cfg.dir / "sessions").is_dir()

    # 新目录已存在时不再重复迁移，且直接读取迁移后的配置
    cfg2 = config_mod.Config(tmp_path)
    assert cfg2.get("scale") == 0.85


def test_config_shared_dir_when_no_variant_marker(tmp_path):
    """源码运行（无 build_variant 标识）时仍使用共享目录。"""
    from pet import config as config_mod

    cfg = config_mod.Config(tmp_path)
    assert cfg.dir == tmp_path / "dsh-pet-standalone"


def test_provider_config_verify_ssl_roundtrip():
    p = ProviderConfig("t", verify_ssl=False)
    assert p.to_dict()["verify_ssl"] is False
    p2 = ProviderConfig.from_dict("t", p.to_dict())
    assert p2.verify_ssl is False
    # 旧配置没有该字段时默认开启校验
    p3 = ProviderConfig.from_dict("t", {"name": "old"})
    assert p3.verify_ssl is True


def test_ssl_context_selection():
    assert _make_ssl_context(False).verify_mode == ssl.CERT_NONE
    assert _make_ssl_context(True).verify_mode == ssl.CERT_REQUIRED


def test_cert_error_detection():
    assert _is_cert_verify_error(
        ssl.SSLError("certificate verify failed: self-signed certificate in certificate chain")
    ) is True
    assert _is_cert_verify_error(TimeoutError("timed out")) is False


def test_connection_test_happy_path(tmp_path):
    import http.server

    from pet.chat.providers import OpenAICompatibleProvider, test_connection

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            if '"stream": true' in body.decode("utf-8", "replace"):
                out = b'data: {"choices":[{"delta":{"content":"pong"}}]}\n\ndata: [DONE]\n\n'
            else:
                out = json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        cfg = ProviderConfig("t", base_url=f"http://127.0.0.1:{port}", model="x")
        ok, msg = test_connection(cfg)
        assert ok is True
        assert "200" in msg
        # 同一配置走 stream 也应成功
        provider = OpenAICompatibleProvider()
        chunks = list(provider.stream([{"role": "user", "content": "hi"}], cfg, threading.Event()))
        assert "".join(chunks) == "pong"
    finally:
        server.shutdown()
        server.server_close()


def test_stream_surfaces_certificate_hint(monkeypatch):
    from pet.chat.providers import OpenAICompatibleProvider

    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError(
            ssl.SSLError("certificate verify failed: self-signed certificate in certificate chain")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider()
    with pytest.raises(ProviderError) as excinfo:
        list(provider.stream([{"role": "user", "content": "hi"}], ProviderConfig("t"), threading.Event()))
    assert "网络连接失败" in str(excinfo.value)
    assert "跳过 SSL 证书验证" in str(excinfo.value)


def test_connection_test_reports_certificate_hint(monkeypatch):
    from pet.chat.providers import test_connection

    def fake_urlopen(*args, **kwargs):
        raise urllib.error.URLError(
            ssl.SSLError("certificate verify failed: self-signed certificate in certificate chain")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    ok, msg = test_connection(ProviderConfig("t"))
    assert ok is False
    assert "跳过 SSL 证书验证" in msg


def test_connection_test_reentrant_clicks_do_not_duplicate_requests(tmp_path, monkeypatch):
    """多次点击测试连接：进行中重复调用被忽略，完成后可再次发起；不产生线程崩溃。"""
    import time

    import pet.chat.settings_dialog as sd
    from PySide6.QtWidgets import QApplication
    from pet.chat.settings_dialog import ChatSettingsDialog
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    dialog = ChatSettingsDialog(Config(tmp_path))
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_test(cfg, timeout=10.0):
        calls.append(1)
        started.set()
        release.wait(10)
        return True, "连接成功（HTTP 200）"

    monkeypatch.setattr(sd, "test_connection", fake_test)
    try:
        dialog._run_test()
        assert started.wait(2), "第一次测试未启动"
        dialog._run_test()
        dialog._run_test()
        release.set()
        deadline = time.time() + 5
        while dialog._test_thread is not None and dialog._test_thread.is_alive() and time.time() < deadline:
            time.sleep(0.05)
        assert len(calls) == 1, f"进行中重复点击不应发起新请求，实际 {len(calls)} 次"
        app.processEvents()
        assert dialog.test.isEnabled()
        assert dialog.test.text() == "测试连接"
        # 第二轮：全新阻塞事件，验证"完成后可再次发起 + 进行中重复点击仍被忽略"。
        # 注意必须换新 release：上一轮的 release 已 set，旧事件会让新线程瞬间完成，
        # 使 is_alive() 为 False 而误启动第三个线程（macOS 上时序更快，必然触发）。
        release = threading.Event()
        dialog._run_test()
        assert started.wait(2), "第二轮测试未启动"
        dialog._run_test()
        dialog._run_test()
        release.set()
        deadline = time.time() + 5
        while dialog._test_thread is not None and dialog._test_thread.is_alive() and time.time() < deadline:
            time.sleep(0.05)
        assert len(calls) == 2, f"第二轮进行中重复点击不应发起新请求，实际 {len(calls)} 次"
        app.processEvents()
    finally:
        release.set()
        dialog.close()
        app.processEvents()


def test_chat_settings_dialog_warns_when_keyring_unavailable(tmp_path, monkeypatch):
    """AI 设置对话框在 keyring 不可用时须提示，且 key 仅保留内存（不落盘明文）。"""
    from PySide6.QtWidgets import QApplication

    import pet.chat.settings_dialog as sd
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    warnings = []

    class FakeStore:
        def get(self, _ref):
            return ""

        def set(self, _ref, _value):
            return False

    monkeypatch.setattr(sd, "SecretStore", FakeStore)
    monkeypatch.setattr(sd.QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    dialog = sd.ChatSettingsDialog(Config(tmp_path))
    dialog.key.setText("sk-new")
    dialog.save()
    assert len(warnings) == 1
    assert "系统安全存储" in str(warnings[0][2])
    assert dialog.settings.active_config.api_key == "sk-new"
    app.processEvents()
