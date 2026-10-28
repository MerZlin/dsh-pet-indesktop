from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from petcore.config import Config
from petcore.contracts.content import CharacterSpec
from petcore.ui.animated_image import AnimatedImage
from petcore.ui.easter_egg import EasterEggController, EasterEggPopup
from petcore.ui.pet_window import PetWindow

# 8x4 双帧 GIF89a（首帧红、次帧蓝，100ms/帧），字节内联以避免外部素材。
_ANIMATED_GIF = (
    b'GIF89a\x08\x00\x04\x00\x81\x00\x00\xff\x00\x00\x00\x00\x00\x00'
    b'\x00\x00\x00\x00\x00!\xff\x0bNETSCAPE2.0\x03'
    b'\x01\x00\x00\x00!\xf9\x04\x00\n\x00\x00\x00,\x00\x00\x00\x00\x08\x00\x04'
    b'\x00\x00\x08\x0c\x00\x01\x08\x1cH\xb0\xa0\xc1\x83\x04\x03\x02\x00!\xf9\x04'
    b'\x01\n\x00\x01\x00,\x00\x00\x00\x00\x08\x00\x04\x00\x81\x00\x00\xff\x00\x00'
    b'\x00\x00\x00\x00\x00\x00\x00\x08\x0c\x00\x01\x08\x1cH\xb0\xa0\xc1\x83\x04\x03'
    b'\x02\x00;'
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakePetWindow(QWidget):
    def __init__(self, root: Path):
        super().__init__()
        self.config = Config(root)
        self.messages: list[tuple[str, bool]] = []
        self.resize(140, 140)

    def say(self, text, duration_ms: int = 4000, force: bool = False) -> None:
        del duration_ms
        self.messages.append((str(text), bool(force)))


def _write_image(path: Path, width: int = 200, height: int = 120) -> Path:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#30a0ff"))
    assert image.save(str(path))
    return path


def _write_gif(path: Path) -> Path:
    path.write_bytes(_ANIMATED_GIF)
    return path


@pytest.fixture
def window(tmp_path: Path):
    window = FakePetWindow(tmp_path)
    yield window
    window.close()


def test_show_opens_a_popup_with_the_configured_title(app, window, tmp_path: Path) -> None:
    folder = tmp_path / "eggs"
    folder.mkdir()
    _write_image(folder / "a.png")
    _write_image(folder / "b.png")
    window.config.set("easter_egg_image_dir", str(folder))
    window.config.set("easter_egg_title", "秘密彩蛋")
    controller = EasterEggController(window)

    assert controller.show() is True
    popups = controller.live_popups()
    assert len(popups) == 1
    popup = popups[0]
    assert isinstance(popup, EasterEggPopup)
    assert popup.isVisible() is True
    assert popup.windowTitle() == "秘密彩蛋"
    assert popup.title_label.text() == "秘密彩蛋"
    assert popup.image_path.parent == folder
    controller.close_all()
    assert controller.live_popups() == []
    assert popup.isVisible() is False


def test_images_are_scaled_to_the_maximum_side(app, window, tmp_path: Path) -> None:
    folder = tmp_path / "big"
    folder.mkdir()
    _write_image(folder / "wide.png", width=1280, height=320)
    window.config.set("easter_egg_image_dir", str(folder))
    controller = EasterEggController(window)

    assert controller.show() is True
    popup = controller.live_popups()[0]
    pixmap = popup.image_label.pixmap()
    assert pixmap.width() == 520
    assert pixmap.height() == 130
    controller.close_all()


def test_easter_egg_image_scale_grows_the_popup(
    app, window, tmp_path: Path
) -> None:
    folder = tmp_path / "big"
    folder.mkdir()
    _write_image(folder / "wide.png", width=1280, height=320)
    window.config.set("easter_egg_image_dir", str(folder))

    window.config.set("easter_egg_image_scale", 200)
    controller = EasterEggController(window)
    assert controller.show() is True
    popup = controller.live_popups()[0]
    pixmap = popup.image_label.pixmap()
    assert (pixmap.width(), pixmap.height()) == (1040, 260)
    assert popup.width() > 1040

    # 3 倍会被钳到 1200 上限
    window.config.set("easter_egg_image_scale", 300)
    assert controller.show() is True
    scaled = controller.live_popups()[-1]
    assert scaled.image_label.pixmap().width() == 1200
    controller.close_all()


def test_empty_directory_returns_false_and_reports(app, window, tmp_path: Path) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    window.config.set("easter_egg_image_dir", str(folder))
    controller = EasterEggController(window)

    assert controller.show() is False
    assert controller.live_popups() == []
    assert window.messages
    text, force = window.messages[-1]
    assert "没有可读取的图片" in text
    assert force is True


def test_disabled_easter_egg_returns_false_and_reports(app, window, tmp_path: Path) -> None:
    folder = tmp_path / "eggs"
    folder.mkdir()
    _write_image(folder / "a.png")
    window.config.set("easter_egg_image_dir", str(folder))
    window.config.set("easter_egg_enabled", False)
    controller = EasterEggController(window)

    assert controller.show() is False
    assert controller.live_popups() == []
    text, force = window.messages[-1]
    assert "彩蛋已关闭" in text
    assert force is True


def test_close_all_closes_every_popup(app, window, tmp_path: Path) -> None:
    folder = tmp_path / "eggs"
    folder.mkdir()
    _write_image(folder / "a.png")
    window.config.set("easter_egg_image_dir", str(folder))
    controller = EasterEggController(window)

    assert controller.show() is True
    first = controller.live_popups()[0]
    assert controller.show() is True
    second = controller.live_popups()[1]
    controller.close_all()
    assert controller.live_popups() == []
    assert first.isVisible() is False
    assert second.isVisible() is False


def test_easter_egg_row_follows_the_menu_font(app, window) -> None:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QMenu

    from petcore.ui.easter_egg_entry import build_easter_egg_entry

    menu = QMenu()
    font = QFont(menu.font())
    font.setPointSize(18)
    menu.setFont(font)
    row = build_easter_egg_entry(menu, window).defaultWidget()

    assert row.title_label.font().pointSizeF() == pytest.approx(18.0)
    assert row.hint_label.font().pointSizeF() == pytest.approx(16.0)
    assert "font-size" not in row.title_label.styleSheet()


def test_interaction_page_writes_and_discards_egg_settings(app, tmp_path: Path) -> None:
    from petcore.ui.settings.page_interaction import InteractionPage

    class Host:
        def __init__(self, root: Path):
            self.config = Config(root)
            self.window = None

    host = Host(tmp_path)
    page = InteractionPage(host)
    assert page.dirty() is False
    assert page.easter_dir_edit.text() == ""
    assert page.easter_dir_edit.placeholderText() == "留空使用内置图片目录"

    page.easter_enabled.setChecked(False)
    page.easter_title.setText("秘密")
    page.easter_dir_edit.setText(str(tmp_path / "pics"))
    page._on_changed()
    assert host.config.get("easter_egg_enabled") is False
    assert host.config.get("easter_egg_title") == "秘密"
    assert host.config.get("easter_egg_image_dir") == str(tmp_path / "pics")
    assert page.dirty() is True

    page.discard_changes()
    assert host.config.get("easter_egg_enabled") is True
    assert host.config.get("easter_egg_title") == "鲸鱼娘彩蛋"
    assert host.config.get("easter_egg_image_dir") == ""
    assert page.easter_enabled.isChecked() is True
    assert page.easter_title.text() == "鲸鱼娘彩蛋"
    assert page.easter_dir_edit.text() == ""
    assert page.dirty() is False


class _PetHost:
    def __init__(self, characters):
        self._characters = characters
        self.pets = None
        self.tray = None

    def characters(self):
        return list(self._characters)

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


def _fallback_character() -> CharacterSpec:
    return CharacterSpec(id="fallback", name="Fallback", root=Path("."), media_kind="none", animations=())


@pytest.fixture
def pet_window(app, tmp_path: Path):
    config = Config(tmp_path)
    window = PetWindow(config, tmp_path, _PetHost([_fallback_character()]))
    window.start(_fallback_character())
    window.move(200, 420)
    yield window
    window.shutdown()


def test_bubble_mode_shows_the_image_inside_the_speech_bubble(
    app, pet_window, tmp_path: Path
) -> None:
    folder = tmp_path / "eggs"
    folder.mkdir()
    _write_image(folder / "a.png")
    pet_window.config.set("easter_egg_image_dir", str(folder))
    pet_window.config.set("easter_egg_title", "秘密彩蛋")
    assert pet_window.config.get("easter_egg_display") in (None, "bubble")

    assert pet_window.show_easter_egg() is True
    assert pet_window.bubble.is_showing_image() is True
    assert pet_window.bubble.caption_label.text() == "秘密彩蛋"
    assert pet_window.easter.live_popups() == []


def test_bubble_mode_applies_the_configured_image_scale(
    app, pet_window, tmp_path: Path
) -> None:
    folder = tmp_path / "eggs"
    folder.mkdir()
    _write_image(folder / "wide.png", width=1280, height=320)
    pet_window.config.set("easter_egg_image_dir", str(folder))
    pet_window.config.set("easter_egg_image_scale", 200)

    assert pet_window.show_easter_egg() is True
    assert pet_window.bubble.image_label.pixmap().size().toTuple() == (640, 160)


def test_window_mode_opens_the_popup(app, pet_window, tmp_path: Path) -> None:
    folder = tmp_path / "eggs"
    folder.mkdir()
    _write_image(folder / "a.png")
    pet_window.config.set("easter_egg_image_dir", str(folder))
    # 该键还在等设置页/Config.DEFAULTS 登记（别的 agent 负责），先直接写数据
    # 验证 show_easter_egg 的读取分支。
    pet_window.config.data["easter_egg_display"] = "window"

    assert pet_window.show_easter_egg() is True
    assert len(pet_window.easter.live_popups()) == 1
    assert pet_window.bubble.is_showing_image() is False
    pet_window.easter.close_all()


def test_bubble_mode_without_images_falls_back_to_a_message(
    app, pet_window, tmp_path: Path
) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    pet_window.config.set("easter_egg_image_dir", str(folder))

    assert pet_window.show_easter_egg() is False
    assert pet_window.easter.live_popups() == []
    assert "没有可读取的图片" in pet_window.bubble.label.text()


def test_popup_follows_the_host_topmost_flag(app, window, tmp_path: Path) -> None:
    """弹窗与桌宠同层：桌宠不置顶时彩蛋窗也不置顶。"""
    folder = tmp_path / "eggs"
    folder.mkdir()
    _write_image(folder / "a.png")
    window.config.set("easter_egg_image_dir", str(folder))

    window.always_on_top = False
    controller = EasterEggController(window)
    assert controller.show() is True
    popup = controller.live_popups()[0]
    assert not (popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    controller.close_all()

    window.always_on_top = True
    assert controller.show() is True
    popup = controller.live_popups()[0]
    assert popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    controller.close_all()


def test_animated_popup_keeps_the_logical_size_on_hidpi(app, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    popup = EasterEggPopup(gif, "动图", dpr=2.0)
    try:
        animated = popup.animated_image
        assert isinstance(animated, AnimatedImage)
        assert popup.device_pixel_ratio == pytest.approx(2.0)
        assert animated.device_pixel_ratio() == pytest.approx(2.0)
        # 动图与静态图同一套逻辑尺寸口径（QLabel 位置留给静态图）。
        assert popup.image_label.size().toTuple() == (8, 4)
        assert animated.size().toTuple() == (8, 4)
        assert animated.movie().scaledSize().toTuple() == (16, 8)
    finally:
        popup.close()


def test_window_mode_shows_a_gif_as_an_animated_image(
    app, pet_window, tmp_path: Path
) -> None:
    folder = tmp_path / "eggs"
    folder.mkdir()
    gif = _write_gif(folder / "anim.gif")
    pet_window.config.set("easter_egg_image_dir", str(folder))
    pet_window.config.data["easter_egg_display"] = "window"

    assert pet_window.show_easter_egg() is True
    popup = pet_window.easter.live_popups()[0]
    animated = popup.animated_image
    assert isinstance(animated, AnimatedImage)
    assert animated.movie_path == str(gif)
    assert animated.is_running is True
    assert animated.isVisible() is True
    # 静态图的那条 QLabel 让位：没有 pixmap 也不占版面。
    assert popup.image_label.isVisible() is False
    assert popup.image_label.pixmap().isNull()
    assert popup.image_label.size().toTuple() == (8, 4)
    # 窗口总尺寸仍用同一套公式，只是图片尺寸来自动图的逻辑尺寸（8x4），
    # 标题/hint 的最小尺寸会把它撑大，所以只断言不比图片小。
    assert popup.width() >= 8 + 24
    assert popup.height() >= 4 + 18 + 8 + 10 + 12

    popup.close()
    assert animated.is_running is False
    assert animated.movie() is None


def test_default_bubble_mode_plays_a_gif(app, pet_window, tmp_path: Path) -> None:
    folder = tmp_path / "eggs"
    folder.mkdir()
    gif = _write_gif(folder / "anim.gif")
    pet_window.config.set("easter_egg_image_dir", str(folder))
    assert pet_window.config.get("easter_egg_display") in (None, "bubble")

    assert pet_window.show_easter_egg() is True
    bubble = pet_window.bubble
    assert bubble.is_showing_image() is True
    animated = bubble.animated_image
    assert isinstance(animated, AnimatedImage)
    assert animated.movie_path == str(gif)
    assert animated.is_running is True

    bubble.hide()
    assert animated.is_running is False
