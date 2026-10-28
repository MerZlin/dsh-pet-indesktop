"""窗口层级：置顶关着时气泡不许抢前台，右键菜单永远高于桌宠与气泡。

三条规则：

1. 气泡是桌宠的从属窗口，``show()`` 会把整条 owner 链一起抬到普通窗口层最上面——
   所以「非置顶」时气泡的显示/换页/旗标重建都不许再 ``raise_()``，还要把桌宠塞回
   它原来的位置（真实层级证据见 ``.scratch/probe_layers.py``）。
2. 菜单会话（``ui.menu.menu_session`` / ``menu_open``）期间，桌宠与气泡的任何抬升
   都要被抑制：菜单必须留在它们上面。
3. 置顶时行为与以前完全一致（``raise_()`` 照旧）。
"""

from __future__ import annotations

import ctypes
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from petcore.config import Config
from petcore.contracts.content import CharacterSpec
from petcore.ui.bubble import SpeechBubble
from petcore.ui.menu import (
    active_menu,
    menu_open,
    menu_session,
    show_context_menu,
)
from petcore.ui.pet_window import PetWindow


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
    return CharacterSpec(
        id="fallback",
        name="Fallback",
        root=Path("."),
        media_kind="none",
        animations=(),
    )


def _make_window(app, root: Path, *, on_top: bool) -> PetWindow:
    config = Config(root)
    config.set("always_on_top", on_top)
    instance = PetWindow(config, root, FakeHost([fallback()]))
    instance.start(fallback())
    instance.move(200, 420)
    app.processEvents()
    return instance


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path: Path):
    instance = _make_window(app, tmp_path, on_top=True)
    yield instance
    instance.shutdown()


@pytest.fixture
def plain_window(app, tmp_path: Path):
    """置顶关着的桌宠（用户报的「鱼还会突然跳出来」就是这个状态）。"""
    instance = _make_window(app, tmp_path / "plain", on_top=False)
    yield instance
    instance.shutdown()


@pytest.fixture
def raises(monkeypatch):
    """记录 ``SpeechBubble.raise_()`` 的真实调用（仍然是 Qt 原实现）。"""
    calls: list[SpeechBubble] = []
    original = SpeechBubble.raise_

    def spy(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(SpeechBubble, "raise_", spy)
    return calls


@pytest.fixture
def pet_raises(monkeypatch):
    calls: list[PetWindow] = []
    original = PetWindow.raise_

    def spy(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(PetWindow, "raise_", spy)
    return calls


def _image() -> QPixmap:
    pixmap = QPixmap(120, 60)
    pixmap.fill(QColor("#30a0ff"))
    return pixmap


# ---------------------------------------------------------------- ① 非置顶不抬
def test_non_top_bubble_never_raises_on_show(app, plain_window, raises) -> None:
    bubble = plain_window.bubble
    assert bubble.host_on_top() is False

    plain_window.say("置顶关着，冒个泡")
    bubble.show_text("再换一句", plain_window.bubble_anchor_rect())
    assert bubble.show_image(_image(), plain_window.bubble_anchor_rect()) is True
    bubble.show_text("回到文字", plain_window.bubble_anchor_rect())

    assert raises == [], "置顶关着时气泡显示不许 raise_（会连带把桌宠抬到别的窗口前面）"
    assert bubble.isVisible() is True, "不抬升不等于不显示"


def test_non_top_bubble_never_raises_on_flag_rebuild(
    app, plain_window, raises
) -> None:
    bubble = plain_window.bubble
    plain_window.say("先把气泡显示出来")
    raises.clear()

    bubble.set_text_scale(1.4)
    assert raises == [], "换字号只重排，不抬窗"

    bubble._apply_window_flags()
    assert bubble.isVisible() is True, "旗标重建后要原地恢复显示"
    assert raises == [], "非置顶时旗标重建不许 raise_"


def test_stream_mode_bubble_still_raises(app, plain_window, raises) -> None:
    """直播模式下气泡是普通置顶窗（捕获软件要能枚举它），抬升照旧。"""
    bubble = plain_window.bubble
    plain_window.say("直播前")
    raises.clear()

    bubble.set_stream_mode(True)
    try:
        assert bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert raises, "直播模式的气泡是置顶窗，允许照旧抬到最前"
    finally:
        bubble.set_stream_mode(False)


def test_top_bubble_still_raises(app, window, raises) -> None:
    """置顶时行为不变：气泡照旧抬到最前（图片气泡要压住桌宠）。"""
    assert window.bubble.host_on_top() is True
    window.say("置顶时冒泡")
    assert raises, "置顶时气泡必须照旧 raise_"
    assert window.bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_standalone_bubble_has_nothing_to_own(app, raises) -> None:
    """没有宿主（parent 为空）时也不能炸：只显示，不碰层级。"""
    bubble = SpeechBubble()
    try:
        bubble.show_text("孤儿气泡", QRect(200, 400, 80, 80))
        assert bubble.isVisible() is True
    finally:
        bubble.close()


# ---------------------------------------------------------------- ② 菜单会话
def test_menu_session_is_nested_and_balanced(app) -> None:
    outer, inner = QMenu(), QMenu()
    assert menu_open() is False
    assert active_menu() is None

    with menu_session(outer):
        assert menu_open() is True
        assert active_menu() is outer
        with menu_session(inner):
            assert active_menu() is inner
        assert menu_open() is True, "内层会话结束不许把外层一起关掉"
        assert active_menu() is outer

    assert menu_open() is False
    assert active_menu() is None


def test_menu_session_is_released_on_error(app) -> None:
    menu = QMenu()
    with pytest.raises(RuntimeError):
        with menu_session(menu):
            raise RuntimeError("boom")
    assert menu_open() is False


def test_bubble_does_not_raise_while_a_menu_is_open(app, window, raises) -> None:
    bubble = window.bubble
    menu = QMenu()
    with menu_session(menu):
        bubble.show_text("菜单开着的时候冒泡", window.bubble_anchor_rect())
        assert bubble.isVisible() is True
        assert raises == [], "菜单会话期间气泡不许抬：那会把菜单压住"

    bubble.hide()
    bubble.show_text("菜单关了就恢复", window.bubble_anchor_rect())
    assert raises, "会话结束后必须恢复原来的抬升行为"


def test_show_context_menu_opens_a_session_for_the_whole_exec(
    app, window, raises, monkeypatch
) -> None:
    """``show_context_menu`` 用会话包住整个 ``exec``（真实 ``exec`` 由定时器关掉）。"""
    from PySide6.QtCore import QTimer

    menu = QMenu()
    menu.addAction("菜单项")
    seen: dict = {}

    def during() -> None:
        seen["open"] = menu_open()
        seen["active"] = active_menu() is menu
        window.bubble.show_text("菜单里冒泡", window.bubble_anchor_rect())
        seen["visible"] = window.bubble.isVisible()
        menu.close()

    QTimer.singleShot(120, during)
    QTimer.singleShot(2000, menu.close)
    show_context_menu(menu, QPoint(300, 300), topmost=window.always_on_top)

    assert seen.get("open") is True, "菜单弹出期间必须是会话状态"
    assert seen.get("active") is True
    assert seen.get("visible") is True, "会话期间也要冒泡，只是不许压到菜单上"
    assert raises == []
    assert menu_open() is False, "菜单关闭后会话必须收掉"


def test_hidden_pet_does_not_raise_over_an_open_menu(
    app, window, pet_raises
) -> None:
    window.set_visible(False)
    pet_raises.clear()
    menu = QMenu()
    with menu_session(menu):
        window.set_visible(True)
        assert window.isVisible() is True
        assert pet_raises == [], "菜单开着时把桌宠叫出来也不许压到菜单上"

    window.set_visible(False)
    pet_raises.clear()
    window.set_visible(True)
    assert pet_raises, "没有菜单时必须恢复原来的抬起行为"


# ---------------------------------------------------------------- ③ 真机层级
def _user32():
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.GetTopWindow.restype = ctypes.c_void_p
    lib.GetTopWindow.argtypes = [ctypes.c_void_p]
    lib.GetWindow.restype = ctypes.c_void_p
    lib.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    lib.SetWindowPos.restype = ctypes.c_int
    lib.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    return lib


def _z_index(order: list[int], hwnd: int) -> int | None:
    try:
        return order.index(hwnd)
    except ValueError:
        return None


def _top_level_order(user32) -> list[int]:
    order: list[int] = []
    hwnd = user32.GetTopWindow(None)
    while hwnd and len(order) < 400:
        order.append(int(hwnd))
        hwnd = user32.GetWindow(ctypes.c_void_p(hwnd), 2)  # GW_HWNDNEXT
    return order


@pytest.mark.skipif(
    sys.platform != "win32"
    or os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen",
    reason="需要真实 Windows 桌面（CI/offscreen 跳过；证据见 .scratch/probe_layers.py）",
)
def test_real_desktop_bubble_stays_behind_the_other_app_window(
    app, tmp_path: Path
) -> None:
    """真机：别的窗口在最前面时，冒泡不许把桌宠/气泡抬到它上面。"""
    if app.platformName() != "windows":
        pytest.skip("当前 QApplication 不是真实 windows 平台")
    user32 = _user32()

    other = QWidget()
    other.resize(420, 300)
    other.move(120, 120)
    other.show()
    app.processEvents()

    pet = _make_window(app, tmp_path / "real", on_top=False)
    try:
        other_hwnd = int(other.winId())
        # 把「别的应用窗口」放到普通层最上面（等价于它刚被打开）
        user32.SetWindowPos(
            ctypes.c_void_p(other_hwnd),
            None,
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0010,  # NOSIZE | NOMOVE | NOACTIVATE
        )
        for _ in range(20):
            app.processEvents()

        pet_hwnd = int(pet.winId())
        order = _top_level_order(user32)
        assert _z_index(order, other_hwnd) < _z_index(order, pet_hwnd), (
            "前置条件：别的窗口本来就在桌宠上面"
        )

        pet.say("置顶关着的时候冒个泡")
        for _ in range(30):
            app.processEvents()

        bubble_hwnd = int(pet.bubble.winId())
        order = _top_level_order(user32)
        z_other = _z_index(order, other_hwnd)
        z_pet = _z_index(order, pet_hwnd)
        z_bubble = _z_index(order, bubble_hwnd)
        assert pet.bubble.isVisible() is True
        assert z_other < z_pet, f"桌宠被抬到别的窗口之上了：{z_other=} {z_pet=}"
        assert z_other < z_bubble, f"气泡被抬到别的窗口之上了：{z_other=} {z_bubble=}"
    finally:
        pet.shutdown()
        other.close()
