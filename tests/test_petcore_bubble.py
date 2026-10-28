from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from petcore.config import Config
from petcore.contracts.content import CharacterSpec
from petcore.ui.animated_image import AnimatedImage
from petcore.ui.bubble import (
    BUBBLE_TEXT_COLUMN,
    BUBBLE_TEXT_COLUMN_MAX,
    BUBBLE_TEXT_MIN_WIDTH,
    SpeechBubble,
    column_for_text,
)
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


class FakeHost:
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


def fallback() -> CharacterSpec:
    return CharacterSpec(id="fallback", name="Fallback", root=Path("."), media_kind="none", animations=())


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path: Path):
    config = Config(tmp_path)
    instance = PetWindow(config, tmp_path, FakeHost([fallback()]))
    instance.start(fallback())
    instance.move(200, 420)
    yield instance
    instance.shutdown()


def _write_image(path: Path, width: int = 400, height: int = 200) -> Path:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#30a0ff"))
    assert image.save(str(path))
    return path


def test_bubble_emits_closed_only_when_it_was_visible(app, window) -> None:
    """气泡收起要能被上层观察到（点击台词排队在余额气泡之后靠它）。"""
    bubble = window.bubble
    seen: list[int] = []
    bubble.closed.connect(lambda: seen.append(1))
    bubble.hide()  # 本来就没显示：不发
    assert seen == []

    window.say("余额 ¥1.00")
    assert bubble.isVisible() is True
    bubble.hide()
    assert seen == [1]

    bubble.hide()  # 已经收起：不重复发
    assert seen == [1]


def test_bubble_follows_the_pet_when_the_window_moves(app, window) -> None:
    window.say("跟着我")
    bubble = window.bubble
    assert bubble.isVisible() is True
    assert bubble.is_showing_image() is False
    before = bubble.pos()

    window.move(window.x() + 60, window.y())
    app.processEvents()

    anchor = window.bubble_anchor_rect()
    assert bubble.pos() != before
    assert bubble.x() == pytest.approx(before.x() + 60, abs=2)
    assert bubble.y() == pytest.approx(before.y(), abs=2)
    assert not bubble.geometry().intersects(anchor), "气泡不得压住头框"


def test_reposition_is_skipped_when_nothing_changed(app, window) -> None:
    window.say("不动")
    bubble = window.bubble
    second_anchor = window.bubble_anchor_rect()
    bubble.move(bubble.x() + 7, bubble.y() + 7)
    moved = bubble.pos()
    bubble.reposition(second_anchor)
    assert bubble.pos() == moved, "锚点与尺寸都没变时不应重排"


def test_show_image_switches_content_and_scales(app, window, tmp_path: Path) -> None:
    image_path = _write_image(tmp_path / "egg.png", width=400, height=200)
    bubble = window.bubble
    anchor = window.bubble_anchor_rect()

    bubble.show_image(image_path, anchor, caption="厉害了我的鲸")

    assert bubble.isVisible() is True
    assert bubble.is_showing_image() is True
    assert bubble.caption_label.isVisible() is True
    assert bubble.caption_label.text() == "厉害了我的鲸"
    pixmap = bubble.image_label.pixmap()
    assert pixmap.width() == 320
    assert pixmap.height() == 160
    assert not bubble.geometry().intersects(anchor), "图片气泡不得压住头框"

    bubble.show_text("回到文字", anchor)
    assert bubble.is_showing_image() is False
    assert bubble.label.isVisible() is True
    assert bubble.image_label.isVisible() is False


def test_show_image_accepts_a_pixmap(app, window) -> None:
    pixmap = QPixmap(500, 500)
    pixmap.fill(QColor("#ff8800"))
    bubble = window.bubble
    bubble.show_image(pixmap, window.bubble_anchor_rect())
    assert bubble.is_showing_image() is True
    shown = bubble.image_label.pixmap()
    assert max(shown.width(), shown.height()) <= 320


def test_bubble_repositions_after_scale_change(app, window) -> None:
    window.say("放大也要跟住")
    bubble = window.bubble
    before = bubble.pos()
    window.set_scale(1.1, persist=False)
    app.processEvents()
    assert bubble.isVisible() is True
    assert bubble.pos() != before
    assert not bubble.geometry().intersects(window.bubble_anchor_rect())


def test_standalone_bubble_defaults_to_text(app) -> None:
    bubble = SpeechBubble()
    try:
        assert bubble.is_showing_image() is False
        assert bubble.text_scale() == pytest.approx(1.0)
    finally:
        bubble.close()


def test_set_text_scale_enlarges_the_text_style(app) -> None:
    bubble = SpeechBubble()
    try:
        bubble.show_text("放大一点", QRect(200, 400, 80, 80))
        before = bubble.label.styleSheet()
        assert "font-size:13px" in before
        assert "border-radius:12px" in bubble.text_panel.styleSheet()
        assert bubble.text_panel.layout().contentsMargins().left() == 14
        height_before = bubble.height()

        bubble.set_text_scale(2.0)

        assert bubble.text_scale() == pytest.approx(2.0)
        after = bubble.label.styleSheet()
        assert "font-size:26px" in after
        assert "border-radius:24px" in bubble.text_panel.styleSheet()
        assert bubble.text_panel.layout().contentsMargins().left() == 28
        assert "font-size:22px" in bubble.subtitle_label.styleSheet()
        assert "font-size:24px" in bubble.caption_label.styleSheet()
        assert bubble.height() > height_before, "字号变大后气泡要重新 adjustSize"
        assert bubble.isVisible() is True
        assert not bubble.geometry().intersects(bubble.anchor())
    finally:
        bubble.close()


def test_set_text_scale_is_clamped(app) -> None:
    bubble = SpeechBubble()
    try:
        bubble.set_text_scale(10.0)
        assert bubble.text_scale() == pytest.approx(3.0)
        assert "font-size:39px" in bubble.label.styleSheet()
        bubble.set_text_scale(float("nan"))
        assert bubble.text_scale() == pytest.approx(1.0)
        assert "font-size:13px" in bubble.label.styleSheet()
    finally:
        bubble.close()


def test_show_image_respects_the_image_scale(app, window) -> None:
    bubble = window.bubble
    anchor = window.bubble_anchor_rect()
    pixmap = QPixmap(1280, 640)
    pixmap.fill(QColor("#30a0ff"))

    assert bubble.show_image(pixmap, anchor) is True
    assert bubble.image_label.pixmap().size().toTuple() == (320, 160)

    assert bubble.show_image(pixmap, anchor, image_scale=2.0) is True
    assert bubble.image_label.pixmap().size().toTuple() == (640, 320)
    assert not bubble.geometry().intersects(anchor)

    # 超过 3.0 的系数被钳位：最大边 = 320 × 3
    assert bubble.show_image(pixmap, anchor, image_scale=10.0) is True
    assert bubble.image_label.pixmap().size().toTuple() == (960, 480)


def test_text_and_image_content_do_not_leak_into_each_other(app, window, tmp_path: Path) -> None:
    image_path = _write_image(tmp_path / "egg.png", width=400, height=200)
    bubble = window.bubble
    anchor = window.bubble_anchor_rect()

    assert bubble.show_image(image_path, anchor, caption="带说明") is True
    bubble.show_text("回到文字", anchor)
    assert bubble.is_showing_image() is False
    assert bubble.image_label.isVisible() is False
    assert bubble.image_label.pixmap().isNull()
    assert bubble.caption_label.isVisible() is False
    assert bubble.caption_label.text() == ""

    bubble.show_image(image_path, anchor)
    assert bubble.is_showing_image() is True
    assert bubble.label.isVisible() is False
    assert bubble.label.text() == ""


def test_window_applies_and_persists_text_scale(app, tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set("bubble_text_scale", 150)
    instance = PetWindow(config, tmp_path, FakeHost([fallback()]))
    instance.start(fallback())
    try:
        assert instance.bubble.text_scale() == pytest.approx(1.5)
        assert "font-size:20px" in instance.bubble.label.styleSheet()

        instance.set_text_scale(200, persist=False)
        assert config.get("bubble_text_scale") == 200
        assert instance.bubble.text_scale() == pytest.approx(2.0)

        # 设置页只写配置：下一条气泡要读到新字号
        config.set("bubble_text_scale", 75)
        instance.say("配置改了")
        assert instance.bubble.text_scale() == pytest.approx(0.75)
    finally:
        instance.shutdown()


def test_hidden_pet_does_not_show_a_bubble(app, window) -> None:
    window.say("先冒一个泡")
    assert window.bubble.isVisible()
    assert window.bubble.label.text() == "先冒一个泡"

    window.set_visible(False)
    assert window.isVisible() is False
    assert window.bubble.isVisible() is False

    window.say("隐藏后不该冒泡")
    assert window.bubble.isVisible() is False
    assert window.bubble.label.text() == "先冒一个泡"


def test_bubble_keeps_the_pet_layer(app, window) -> None:
    """置顶必须跟着宿主走：图片气泡比文字气泡大，掉层会直接被桌宠盖住。"""
    bubble = window.bubble
    assert bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    window.set_always_on_top(False, persist=False)
    assert bubble.host_on_top() is False
    assert not (bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    window.set_always_on_top(True, persist=False)
    assert bubble.host_on_top() is True
    assert bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_bubble_is_owned_by_the_pet_window(app, window) -> None:
    """气泡必须是桌宠窗口的从属窗口（owned window），而不只是「碰巧同层」。

    只靠 ``WindowStaysOnTopHint`` 时，气泡是独立顶层窗口：非置顶状态下它自己
    ``raise_()`` 会排到所有普通窗口（别的应用、QMenu）之上，用户看到的就是
    「桌宠不在最上层，气泡却在最上面，还把右键菜单压住了」。
    从属窗口的 z 序由宿主决定：宿主退它就退，``raise_()`` 只在宿主之上生效。
    """
    bubble = window.bubble
    assert bubble.parent() is window, "气泡必须挂在桌宠窗口下，层级才跟得上"
    assert bubble.isWindow(), "挂父窗口之后它仍然要是独立窗口（能伸出桌宠边界）"



def test_text_and_image_bubbles_share_the_same_window(app, window, tmp_path) -> None:
    image = _write_image(tmp_path / "egg.png", 640, 480)
    window.say("文字气泡")
    text_window = id(window.bubble)
    flags_before = window.bubble.windowFlags()

    assert window.show_image(image, "配图") is True
    assert id(window.bubble) == text_window
    assert window.bubble.windowFlags() == flags_before
    assert window.bubble.is_showing_image() is True


def test_column_grows_with_text_length_and_scale() -> None:
    assert column_for_text("短句") == BUBBLE_TEXT_COLUMN
    assert column_for_text("长" * 400) == BUBBLE_TEXT_COLUMN_MAX
    assert column_for_text("长" * 400, 2.0) == BUBBLE_TEXT_COLUMN_MAX * 2


def test_long_text_paginates_and_wheel_pages(app) -> None:
    bubble = SpeechBubble()
    try:
        text = "这是一句很长的台词。" * 120
        bubble.show_text(text, QRect(600, 600, 80, 80))
        assert bubble.page_count() > 1
        assert bubble.pager.isVisible() is True
        first = bubble.label.text()
        assert "●" in bubble.pager.text()

        assert bubble.next_page() is True
        assert bubble.label.text() != first
        assert bubble.page_index() == 1

        bubble._timer.stop()
        bubble.previous_page()
        assert bubble.page_index() == 0
    finally:
        bubble.close()


def test_subtitle_is_rendered_in_the_same_panel(app) -> None:
    bubble = SpeechBubble()
    try:
        bubble.show_text(
            "第一句歌词",
            QRect(600, 600, 80, 80),
            subtitle="我在唱《测试曲》",
        )
        assert bubble.subtitle_text() == "我在唱《测试曲》"
        assert bubble.subtitle_label.isVisible() is True
        assert bubble.subtitle_label.parent() is bubble.text_panel
        assert bubble.label.wordWrap() is False
        assert bubble.subtitle_label.width() == bubble.label.width()
        assert bubble.subtitle_label.width() >= BUBBLE_TEXT_MIN_WIDTH
        assert (
            bubble.subtitle_label.heightForWidth(bubble.subtitle_label.width())
            <= bubble.subtitle_label.height()
        )
    finally:
        bubble.close()


def _assert_text_fits(bubble) -> None:
    metrics = QFontMetrics(bubble.label.font())
    assert bubble.label.wordWrap() is False
    for line in bubble.label.text().split("\n"):
        assert metrics.horizontalAdvance(line) <= bubble.label.width()
    assert bubble.label.heightForWidth(bubble.label.width()) <= bubble.label.height()


def test_short_text_stays_single_line_across_consecutive_shows(app) -> None:
    bubble = SpeechBubble()
    try:
        for text in ("今天天气", "今天天气", "好的没问题", "一二三四五六七八九十"):
            bubble.show_text(text, QRect(600, 600, 80, 80))
            app.processEvents()
            assert "\n" not in bubble.label.text(), text
            assert bubble.label.width() >= BUBBLE_TEXT_MIN_WIDTH
            _assert_text_fits(bubble)
    finally:
        bubble.close()


def test_long_subtitle_wraps_inside_the_text_column(app) -> None:
    bubble = SpeechBubble()
    try:
        bubble.show_text(
            "歌词",
            QRect(600, 600, 80, 80),
            subtitle="我在唱《" + "歌" * 80 + "》",
        )
        app.processEvents()
        assert bubble.subtitle_label.width() == bubble.label.width()
        assert bubble.subtitle_label.width() <= column_for_text("歌词")
        assert (
            bubble.subtitle_label.heightForWidth(bubble.subtitle_label.width())
            <= bubble.subtitle_label.height()
        )
    finally:
        bubble.close()


def _write_gif(path: Path) -> Path:
    path.write_bytes(_ANIMATED_GIF)
    return path


def test_show_image_plays_a_gif(app, window, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    bubble = window.bubble
    anchor = window.bubble_anchor_rect()

    assert bubble.show_image(gif, anchor, caption="动图") is True
    animated = bubble.animated_image
    assert isinstance(animated, AnimatedImage)
    assert animated.movie_path == str(gif)
    assert animated.is_running is True
    assert animated.isVisible() is True
    assert bubble.is_showing_image() is True
    assert bubble.caption_label.text() == "动图"
    # 白色圆角底板仍是 image_label（保留 8px 内边距），动图控件贴在它里面。
    assert bubble.image_label.isVisible() is True
    assert bubble.image_label.pixmap().isNull()
    assert bubble.image_label.size().toTuple() == (8 + 2 * 8, 4 + 2 * 8)
    assert (animated.x(), animated.y()) == (8, 8)

    bubble.show_text("回到文字", anchor)
    assert animated.is_running is False
    assert animated.isVisible() is False


def test_static_image_hides_the_previous_animation(app, window, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    png = _write_image(tmp_path / "egg.png", width=400, height=200)
    bubble = window.bubble
    anchor = window.bubble_anchor_rect()

    assert bubble.show_image(gif, anchor) is True
    animated = bubble.animated_image
    assert bubble.show_image(png, anchor) is True

    assert animated.is_running is False
    assert animated.isVisible() is False
    assert bubble.image_label.pixmap().size().toTuple() == (320, 160)


def test_showing_a_second_gif_reuses_the_animated_widget(app, window, tmp_path: Path) -> None:
    first = _write_gif(tmp_path / "a.gif")
    second = _write_gif(tmp_path / "b.gif")
    bubble = window.bubble
    anchor = window.bubble_anchor_rect()

    assert bubble.show_image(first, anchor) is True
    animated = bubble.animated_image
    assert bubble.show_image(second, anchor) is True

    assert bubble.animated_image is animated
    assert animated.movie_path == str(second)
    assert animated.is_running is True


def test_hiding_the_bubble_stops_the_gif(app, window, tmp_path: Path) -> None:
    gif = _write_gif(tmp_path / "anim.gif")
    bubble = window.bubble
    anchor = window.bubble_anchor_rect()

    assert bubble.show_image(gif, anchor) is True
    animated = bubble.animated_image
    assert animated.is_running is True

    bubble.hide()

    assert animated.is_running is False
