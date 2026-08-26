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

def test_config_v3_migrates_legacy_chat_fields(tmp_path: Path):
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
    assert json.loads((cfg_dir / "config.json").read_text(encoding="utf-8"))["version"] == 3


def test_chat_window_offscreen_smoke(tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.chat.widgets import ChatWindow
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    window._add("user", "hello")
    window._add("assistant", "hi")
    assert window.title.text().startswith("shenshen")
    window.close()
    app.processEvents()

def test_chat_window_has_playful_shell_and_session_controls(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.chat.widgets import ChatWindow

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert 380 <= window.minimumWidth() <= 560
    assert window.minimumHeight() >= 620
    assert window.phone_shell.objectName() == "phone-shell"
    assert window.title_bar.objectName() == "chat-title-bar"
    assert window.session_combo.count() >= 1
    assert window.new_session_button.objectName() == "new-session-button"
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


def test_session_popup_uses_readable_dark_text_on_light_surface(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.config import Config
    from pet.chat.widgets import ChatWindow

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    popup = window.session_combo.view()
    assert popup.objectName() == "session-list"
    assert "QAbstractItemView#session-list" in window.styleSheet()
    assert "selection-color: #3a4552" in window.styleSheet()
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
    assert window.x() == visible_rect.right() + 10 + 1
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

    assert window.x() + window.width() <= visible_rect.left()
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
    # Q 弹中间帧应与 DPR 无关，并保持脚底在窗口底线；
    # 宽度不放大（sx=1.0），避免角色伸出窗口/mask 边界被裁剪成透明边缘。
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
    assert logical == (0, 84, 640, 306)
    assert physical_mistake != logical
    assert logical[1] + logical[3] == 390
    # 中间帧宽度不应超过窗口宽度（无宽度膨胀）
    assert logical[2] == 640


def test_placeholder_pixmap_generated():
    """解码失败降级用的占位帧应非空且尺寸正确（None 防御的兜底画面）。"""
    from PySide6.QtWidgets import QApplication
    from pet.window import _make_placeholder_pixmap

    app = QApplication.instance() or QApplication([])
    pm = _make_placeholder_pixmap("shenshen", 1.0)
    assert pm.isNull() is False
    assert pm.width() == 640
    assert pm.height() == 360
    small = _make_placeholder_pixmap("", 0.5)
    assert small.isNull() is False
    assert small.width() == 320


def test_look_sync_appends_to_current_session(tmp_path: Path):
    """「看看屏幕」结果应写入 AI 对话当前会话（UI + 持久化）。"""
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    before = len(window.session.messages)
    window.append_look_sync("[看看屏幕] 前台窗口：chrome.exe | 哔哩哔哩", "主人又在看视频啦~")
    assert len(window.session.messages) == before + 2
    assert window.session.messages[-2].role == "user"
    assert window.session.messages[-2].content.startswith("[看看屏幕]")
    assert window.session.messages[-1].role == "assistant"
    assert window.session.messages[-1].content == "主人又在看视频啦~"
    # 持久化：重新加载会话能看到
    reloaded = window.store.load(window.session.session_id, "shenshen")
    assert reloaded is not None
    assert reloaded.messages[-1].content == "主人又在看视频啦~"
    # 空参数应被忽略
    before = len(window.session.messages)
    window.append_look_sync("", "")
    assert len(window.session.messages) == before
    window.close()
    app.processEvents()


def test_check_ffmpeg_detects_missing_binary(monkeypatch):
    """ffmpeg 二进制缺失（被杀软隔离）时启动自检应返回 False。"""
    import imageio_ffmpeg

    from pet import app as app_mod

    monkeypatch.setattr(
        imageio_ffmpeg,
        "get_ffmpeg_exe",
        lambda: str(Path("E:/definitely/not/exist/ffmpeg.exe")),
    )
    assert app_mod._check_ffmpeg_available() is False


def test_check_ffmpeg_accepts_existing_binary(monkeypatch):
    import imageio_ffmpeg

    from pet import app as app_mod

    monkeypatch.setattr(
        imageio_ffmpeg,
        "get_ffmpeg_exe",
        lambda: str(Path(__file__).resolve()),
    )
    assert app_mod._check_ffmpeg_available() is True


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


def test_chat_window_uses_translucent_shell_for_rounded_corners(tmp_path: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    assert "QDialog#chat-window" in window.styleSheet()
    assert "background: #fbf3e9" in window.styleSheet()
    assert window.phone_shell.autoFillBackground() is True
    window.close()
    app.processEvents()


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


def test_warm_first_frame_caches_qimage_for_zero_block_jump():
    """后台预热只缓存 QImage；jumpToFrame(0) 走缓存时主线程零阻塞转 QPixmap。

    回归：点击 Q 弹残留上一动画帧——首次播放某动画时 jumpToFrame(0)
    同步 ffmpeg 解码导致 _current_pixmap 短暂为 None，窗口层 rebuild
    拿不到新帧而继续画旧帧。
    """
    from PySide6.QtWidgets import QApplication
    from pet.webm_clip import WebMClip

    if not WebMClip.available:
        pytest.skip("imageio-ffmpeg 不可用")
    app = QApplication.instance() or QApplication([])
    clip = WebMClip(Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm"))
    assert clip._first_image is None
    clip.warm_first_frame()
    assert clip._first_image is not None, "后台预热应缓存首帧 QImage"
    assert clip._first_pixmap is None, "后台预热不应触碰 QPixmap（非主线程）"
    clip.jumpToFrame(0)
    assert clip._current_pixmap is not None, "jumpToFrame(0) 应基于缓存立即生成 QPixmap"
    assert not clip._current_pixmap.isNull()

def test_webm_and_gif_animation_sets_are_in_sync():
    webm_root = Path("assets/characters")
    gif_root = Path("assets/characters_gif")
    # gif 镜像资产可选（不入库）；缺目录时跳过，不阻塞 CI
    if not gif_root.is_dir():
        import pytest
        pytest.skip("gif 镜像目录 assets/characters_gif 不存在（可选资产）")
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


def test_chat_window_builtin_background_resolves(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    cfg = Config(tmp_path)
    cfg.set("chat_background", "builtin:whale")
    window = ChatWindow(cfg, "shenshen")
    assert window._bg_pixmap is not None and not window._bg_pixmap.isNull()
    assert "rgba(255, 250, 242" in window.styleSheet()  # 半透明面板叠加层生效
    window.close()


def test_chat_window_no_background_by_default(tmp_path: Path):
    from PySide6.QtWidgets import QApplication
    from pet.chat.widgets import ChatWindow
    from pet.config import Config

    app = QApplication.instance() or QApplication([])
    window = ChatWindow(Config(tmp_path), "shenshen")
    assert window._bg_pixmap is None
    window.close()


def test_chat_background_config_roundtrip(tmp_path: Path):
    """chat_background 必须能穿过 config 的加载白名单存取往返。"""
    from pet.config import Config

    cfg = Config(tmp_path)
    cfg.set("chat_background", "builtin:whale")
    cfg.save()
    assert Config(tmp_path).get("chat_background") == "builtin:whale"

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


def test_stream_capture_mode_config_persisted(tmp_path: Path):
    """stream_capture_mode 必须能穿过 config 的加载白名单存取往返。"""
    from pet.config import Config

    cfg = Config(tmp_path)
    assert cfg.get("stream_capture_mode", False) is False
    cfg.set("stream_capture_mode", True)
    cfg.save()
    assert Config(tmp_path).get("stream_capture_mode", False) is True


def test_build_window_flags_stream_capture_mode():
    """默认 Tool（捕获软件过滤），开启兼容模式后为普通顶层窗口。"""
    from PySide6.QtCore import Qt

    from pet.window import build_window_flags

    base = {"on_top": True}
    normal = build_window_flags(base)
    # Tool=11 含 Window=1 位，须用完整值比较判定 Tool 形态
    assert normal & Qt.WindowType.Tool == Qt.WindowType.Tool

    compat = build_window_flags(base, stream_capture_mode=True)
    assert not (compat & Qt.WindowType.Tool == Qt.WindowType.Tool)  # 关键差异：去掉 Tool 形态
    assert compat & Qt.WindowType.Window
    # 置顶/穿透标志在两种模式下都保留
    for flags in (normal, compat):
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert build_window_flags(base, mouse_through=True) & Qt.WindowType.WindowTransparentForInput
    assert not (build_window_flags({"on_top": False}) & Qt.WindowType.WindowStaysOnTopHint)


def test_enforce_topmost_darwin_skips_raise(monkeypatch):
    """macOS 看门狗不得 raise_（置顶由原生 setLevel 保证，raise_ 会抢焦点）。"""
    from types import SimpleNamespace

    from pet.window import PetWindow

    win = PetWindow.__new__(PetWindow)
    win.cfg = SimpleNamespace(get=lambda key, default=None: default if key != "on_top" else True)
    win._auto_hidden = False
    win.isVisible = lambda: True
    win.winId = lambda: 12345
    raised = []
    win.raise_ = lambda: raised.append(1)
    monkeypatch.setattr("sys.platform", "darwin")
    win._enforce_topmost()
    assert raised == []


def test_enforce_topmost_win32_uses_native_reset(monkeypatch):
    """Windows 看门狗仍走原生 SetWindowPos 重设（不回归）。"""
    from types import SimpleNamespace

    from pet.window import PetWindow

    win = PetWindow.__new__(PetWindow)
    win.cfg = SimpleNamespace(get=lambda key, default=None: default if key != "on_top" else True)
    win._auto_hidden = False
    win.isVisible = lambda: True
    win.winId = lambda: 12345
    calls = []
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("pet.window._win_is_topmost", lambda hwnd: calls.append(("check", hwnd)) or False)
    monkeypatch.setattr("pet.window._win_set_topmost", lambda hwnd, on: calls.append(("set", hwnd, on)) or True)
    win._enforce_topmost()
    assert ("check", 12345) in calls
    assert ("set", 12345, True) in calls


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


def test_win_topmost_helpers_safe_with_invalid_hwnd():
    """无效句柄下 Win32 置顶辅助函数应安全返回 False（不崩溃）。"""
    from pet.window import _win_is_topmost, _win_set_topmost

    assert _win_is_topmost(0) is False
    assert _win_set_topmost(0, True) is False


def test_enforce_topmost_resets_lost_topmost(monkeypatch, tmp_path):
    """置顶丢失时 watchdog 应调用原生 SetWindowPos 重设。"""
    import types

    from pet import window as window_mod
    from pet.config import Config

    monkeypatch.setattr("sys.platform", "win32")
    calls = []
    monkeypatch.setattr(window_mod, "_win_is_topmost", lambda hwnd: False)
    monkeypatch.setattr(window_mod, "_win_set_topmost", lambda hwnd, on: calls.append((hwnd, on)) or True)
    cfg = Config(tmp_path)
    fake = types.SimpleNamespace(cfg=cfg, winId=lambda: 12345,
                                 isVisible=lambda: True, _auto_hidden=False)
    window_mod.PetWindow._enforce_topmost(fake)
    assert calls == [(12345, True)]


def test_enforce_topmost_skips_when_on_top_disabled(monkeypatch, tmp_path):
    """关闭置顶时 watchdog 不应重设。"""
    import types

    from pet import window as window_mod
    from pet.config import Config

    monkeypatch.setattr("sys.platform", "win32")
    calls = []
    monkeypatch.setattr(window_mod, "_win_is_topmost", lambda hwnd: False)
    monkeypatch.setattr(window_mod, "_win_set_topmost", lambda hwnd, on: calls.append((hwnd, on)) or True)
    cfg = Config(tmp_path)
    cfg.set("on_top", False)
    fake = types.SimpleNamespace(cfg=cfg, winId=lambda: 12345,
                                 isVisible=lambda: True, _auto_hidden=False)
    window_mod.PetWindow._enforce_topmost(fake)
    assert calls == []


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
