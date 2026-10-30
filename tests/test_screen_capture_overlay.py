# -*- coding: utf-8 -*-
"""「快速识屏」P0 框选覆盖层（真实 QWidget，offscreen）。

覆盖层是本片唯一的交互面，四条契约：

1. 铺满目标屏且无边框置顶（否则冻结帧与真实屏幕错位）；
2. 拖框 → 归一化选区 → **松手即钉住并浮出动作条**；选动作（或 Enter = 默认的
   提取文字）才上报 ``action_requested(动作, 选区)``；Esc 或动作条「取消」上报
   ``selection_cancelled(理由)``；**没框住任何东西时两条路都无效**——空选区进
   OCR 是纯浪费，门槛必须卡在交互层；
3. 冻结帧画成底图，非选区压暗、选区保持原样——"真截屏体验"的视觉契约，
   用真实 render() 采样像素验证，不看描述（含尺寸标签与描边两层）；
4. 预选（记住上次动作过的那块）命中时动作条**直接待命**，不用先拖一下才能点。

动作 id 与编排层（``actions.ACTIONS``）共用同一组常量（定义在本模块）：两条真值
漂了就是"点了没反应"，所以另有一条 seam 用例把两边钉在一起。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from pet.screen_capture.overlay import (  # noqa: E402
    ACTION_ASK,
    ACTION_COPY,
    ACTION_OCR,
    ACTION_TRANSLATE,
    DIM_ALPHA,
    MIN_SELECTION_SIDE,
    SelectionMemory,
    SelectionOverlay,
)

from pet.screen_capture.session import SelectionRect  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _fresh_shared_memory(monkeypatch):
    """每个用例一份干净的「上次选区」进程级记忆。

    记忆是跨覆盖层实例的进程状态（这正是它的用处），用例之间若不隔离，"上一个
    用例确认过一块"会变成"下一个用例一开局就预选着"——本文件里大量断言
    ``current_selection() is None`` 的用例会因此莫名其妙地红。隔离的是**进程级
    那一份**，用例自己注入 ``SelectionMemory()`` 的路径不受影响。
    """
    from pet.screen_capture import overlay as overlay_mod

    monkeypatch.setattr(overlay_mod, "_SHARED_MEMORY", overlay_mod.SelectionMemory())


class _FakeScreen:
    """只暴露覆盖层真正使用的两个读口（几何 + DPR），避免依赖真屏幕尺寸。"""

    def __init__(self, width: int = 200, height: int = 120, dpr: float = 1.0) -> None:
        self._rect = QRect(0, 0, width, height)
        self._dpr = dpr

    def geometry(self) -> QRect:  # noqa: D102
        return QRect(self._rect)

    def devicePixelRatio(self) -> float:  # noqa: N802 - 对齐 Qt 命名
        return self._dpr


def _drag(overlay, start, end, *, button=Qt.MouseButton.LeftButton):
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*start), QPointF(*start),
                        button, button, Qt.KeyboardModifier.NoModifier)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(*end), QPointF(*end),
                       Qt.MouseButton.NoButton, button, Qt.KeyboardModifier.NoModifier)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(*end), QPointF(*end),
                          button, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    overlay.mousePressEvent(press)
    overlay.mouseMoveEvent(move)
    overlay.mouseReleaseEvent(release)


def _press(overlay, at):
    """只按下（不松手）：用来验"重新在空白处按下 = 丢弃旧选区"。"""
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*at), QPointF(*at),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    overlay.mousePressEvent(press)


def _key(overlay, key):
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def _record(overlay):
    """接住两条上报口：``[(动作 id, 选区)]`` 与 ``[取消理由]``。"""
    actions: list[tuple] = []
    cancelled: list[str] = []
    overlay.action_requested.connect(lambda action, rect: actions.append((action, rect)))
    overlay.selection_cancelled.connect(cancelled.append)
    return actions, cancelled


def _click(overlay, action_id) -> None:
    """点动作条上某个按钮（按 objectName 找，不靠下标）。"""
    button = overlay.action_bar().findChild(
        QPushButton, f"screen-capture-action-{action_id}")
    assert button is not None, f"动作条上没有 {action_id} 按钮"
    button.click()


def _bar_shown(overlay) -> bool:
    """动作条露出来没有。

    用 ``isVisibleTo(overlay)`` 而不是 ``isVisible()``：后者在覆盖层自身没 show()
    时恒为 False（子控件的可见性取决于父窗口），而这里要问的是"动作条自己有没有
    被藏起来"——覆盖层在真实运行时永远是 show 着的。
    """
    return overlay.action_bar().isVisibleTo(overlay)


# ------------------------------------------------------------------ 覆盖整屏


def test_overlay_fills_the_target_screen_and_is_frameless_on_top(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen(320, 240))

    assert overlay.geometry() == QRect(0, 0, 320, 240)
    flags = overlay.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    overlay.close()


def test_overlay_keeps_focus_for_keyboard(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen())
    assert overlay.focusPolicy() != Qt.FocusPolicy.NoFocus
    overlay.close()


# -------------------------------------------------------------------- 框选


def test_drag_produces_normalized_selection(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen())
    actions, _cancelled = _record(overlay)

    _drag(overlay, (150, 100), (60, 40))

    assert overlay.current_selection() == SelectionRect(60, 40, 90, 60)
    _key(overlay, Qt.Key.Key_Return)

    assert actions == [(ACTION_OCR, SelectionRect(60, 40, 90, 60))]
    overlay.close()


def test_drag_outside_screen_is_clamped(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen(200, 120))

    _drag(overlay, (10, 10), (500, 500))

    assert overlay.current_selection() == SelectionRect(10, 10, 190, 110)
    overlay.close()


def test_escape_cancels_and_never_confirms(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen())
    actions, cancelled = _record(overlay)

    _drag(overlay, (10, 10), (60, 60))
    _key(overlay, Qt.Key.Key_Escape)

    assert cancelled == ["esc"]
    assert actions == []
    overlay.close()


def test_enter_without_selection_is_ignored(qapp):
    """空选区不许进下游：Enter 无效、动作条不出现、覆盖层不关、不算取消。"""
    overlay = SelectionOverlay(screen=_FakeScreen())
    actions, cancelled = _record(overlay)

    _key(overlay, Qt.Key.Key_Return)

    assert actions == []
    assert cancelled == []
    assert overlay.current_selection() is None
    assert _bar_shown(overlay) is False
    overlay.close()


def test_zero_size_drag_then_enter_is_ignored(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen())
    actions, _cancelled = _record(overlay)

    _drag(overlay, (50, 50), (50, 50))
    _key(overlay, Qt.Key.Key_Return)

    assert actions == []
    overlay.close()


def test_right_button_drag_does_not_select(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen())

    _drag(overlay, (10, 10), (80, 80), button=Qt.MouseButton.RightButton)

    assert overlay.current_selection() is None
    overlay.close()


def test_repainting_after_a_second_drag_pins_the_new_selection(qapp):
    """第二次框选替换第一次：钉住的是新的一块（旧的那块不许留在画面上）。"""
    overlay = SelectionOverlay(screen=_FakeScreen())
    actions, _cancelled = _record(overlay)

    _drag(overlay, (10, 10), (60, 60))
    _drag(overlay, (100, 20), (150, 70))
    _key(overlay, Qt.Key.Key_Return)

    assert actions == [(ACTION_OCR, SelectionRect(100, 20, 50, 50))]
    overlay.close()


def test_a_drag_below_the_minimum_size_is_not_a_selection(qapp):
    """1 px 级抖动不算选区：清成 None（画面如实显示"没选"），两条确认路都无效。"""
    overlay = SelectionOverlay(screen=_FakeScreen(), memory=SelectionMemory())
    actions, _cancelled = _record(overlay)

    _drag(overlay, (50, 50), (52, 51))            # 2x1，两个方向都远小于门槛

    assert overlay.current_selection() is None
    assert _bar_shown(overlay) is False
    _key(overlay, Qt.Key.Key_Return)
    assert actions == [], "过小选区进了下游就是一次白跑的会话"
    overlay.close()


def test_a_drag_at_the_minimum_size_is_accepted(qapp):
    """门槛的另一半：正好够大的拖拽必须照常确认（别把正常用小区域也挡掉）。"""
    overlay = SelectionOverlay(screen=_FakeScreen(), memory=SelectionMemory())
    actions, _cancelled = _record(overlay)

    _drag(overlay, (50, 50), (50 + MIN_SELECTION_SIDE, 50 + MIN_SELECTION_SIDE))
    _key(overlay, Qt.Key.Key_Return)

    assert actions == [(ACTION_OCR,
                        SelectionRect(50, 50, MIN_SELECTION_SIDE, MIN_SELECTION_SIDE))]
    overlay.close()


def test_only_one_side_below_the_minimum_is_refused(qapp):
    """门槛是两个方向都要够：一条 100x2 的细缝进 OCR 一样读不出东西。"""
    overlay = SelectionOverlay(screen=_FakeScreen(), memory=SelectionMemory())
    actions, _cancelled = _record(overlay)

    _drag(overlay, (10, 10), (110, 12))
    _key(overlay, Qt.Key.Key_Return)

    assert overlay.current_selection() is None
    assert actions == []
    overlay.close()


# ------------------------------------------------------------------ 动作条


def test_release_pins_the_selection_and_reveals_the_action_bar(qapp):
    """松手 → 选区钉住 + 动作条出现（对齐主流截图工具，不用再按一次 Enter）。"""
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    assert _bar_shown(overlay) is False

    _drag(overlay, (100, 100), (300, 220))

    assert overlay.current_selection() == SelectionRect(100, 100, 200, 120)
    assert _bar_shown(overlay) is True
    overlay.close()


def test_action_bar_offers_every_action_with_the_shared_ids(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    _drag(overlay, (100, 100), (300, 220))

    bar = overlay.action_bar()
    assert {button.objectName() for button in bar.findChildren(QPushButton)} == {
        "screen-capture-action-ocr",
        "screen-capture-action-translate",
        "screen-capture-action-ask",
        "screen-capture-action-copy",
        "screen-capture-action-cancel",
    }
    # ``action_bar()`` 拿到的是那个子控件本身（不是复制品）：点在它上面才有效。
    assert bar.parent() is overlay
    overlay.close()


def test_all_action_buttons_are_enabled_and_report_their_action(qapp):
    """翻译已接入（文本互译走聊天模型）：动作条不再有置灰按钮，点了就上报动作。"""
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    _drag(overlay, (100, 100), (300, 220))
    actions, cancelled = _record(overlay)

    bar = overlay.action_bar()
    for name in ("ocr", "translate", "ask", "copy"):
        button = bar.findChild(QPushButton, f"screen-capture-action-{name}")
        assert button.isEnabled() is True, f"{name} 不该置灰"

    bar.findChild(QPushButton, "screen-capture-action-translate").click()
    assert actions == [(ACTION_TRANSLATE, SelectionRect(100, 100, 200, 120))]
    assert cancelled == []
    overlay.close()


def test_picking_a_button_reports_that_action_with_the_selection(qapp):
    for action_id in (ACTION_OCR, ACTION_ASK, ACTION_COPY):
        overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
        _drag(overlay, (100, 100), (300, 220))
        actions, cancelled = _record(overlay)

        _click(overlay, action_id)

        assert actions == [(action_id, SelectionRect(100, 100, 200, 120))]
        assert cancelled == []
        overlay.close()


def test_the_cancel_button_reports_a_toolbar_cancellation(qapp):
    """动作条「取消」与 Esc 是两条不同的理由（排查时能区分用户从哪一条退出）。"""
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    _drag(overlay, (100, 100), (300, 220))
    actions, cancelled = _record(overlay)

    _click(overlay, "cancel")

    assert cancelled == ["toolbar"]
    assert actions == []
    overlay.close()


def test_enter_is_the_default_ocr_action(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    _drag(overlay, (100, 100), (300, 220))
    actions, _cancelled = _record(overlay)

    _key(overlay, Qt.Key.Key_Return)

    assert actions == [(ACTION_OCR, SelectionRect(100, 100, 200, 120))]
    overlay.close()


def test_pressing_on_empty_space_drops_the_selection_and_hides_the_bar(qapp):
    """重新在空白处按下 = 丢弃旧选区重框，动作条收起（旧选区不许被当成"选好的"）。"""
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    _drag(overlay, (100, 100), (300, 220))
    assert _bar_shown(overlay) is True
    actions, _cancelled = _record(overlay)

    _press(overlay, (500, 500))

    assert overlay.current_selection() is None
    assert _bar_shown(overlay) is False
    _key(overlay, Qt.Key.Key_Return)
    assert actions == []
    overlay.close()


def test_the_action_bar_sits_below_the_selection_and_stays_inside_the_screen(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    _drag(overlay, (100, 100), (300, 220))

    bar = overlay.action_bar().geometry()
    assert bar.top() >= 220, "动作条压在选区上会挡住用户刚框的内容"
    assert bar.right() <= 800 and bar.bottom() <= 600
    overlay.close()


def test_the_action_bar_flips_above_a_selection_on_the_bottom_edge(qapp):
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    _drag(overlay, (100, 540), (300, 599))

    bar = overlay.action_bar().geometry()
    assert bar.bottom() <= 540, "贴底的选区没有把动作条翻到上方"
    assert bar.top() >= 0
    overlay.close()


def test_action_ids_are_exactly_what_the_orchestrator_dispatches(qapp):
    """动作 id 是跨模块契约：动作条上有什么，编排层就必须认什么（反之亦然）。

    按**渲染出来的按钮**（objectName）读这一组 id，不按私有元组的下标读——
    元组的形状（现在带了图标名）是会变的，objectName 才是与编排层对齐的那一面。
    """
    from pet.screen_capture import actions as actions_mod

    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory())
    _drag(overlay, (100, 100), (300, 220))
    button_ids = {button.objectName().removeprefix("screen-capture-action-")
                  for button in overlay.action_bar().findChildren(QPushButton)}

    assert button_ids - {"cancel"} == set(actions_mod.ACTIONS)
    assert actions_mod.ACTIONS == (ACTION_OCR, ACTION_TRANSLATE, ACTION_ASK, ACTION_COPY)
    # 同一组对象（编排层从本模块 import），不是两份同文字面量。
    assert actions_mod.ACTION_OCR is ACTION_OCR and actions_mod.ACTION_COPY is ACTION_COPY
    overlay.close()


# ---------------------------------------------------------------- 记住上次


def test_the_next_overlay_preselects_the_last_confirmed_area(qapp):
    """同一块屏上再次框选：上次**用过动作**的那块预选出来，动作条直接待命。

    记忆口径是"任何动作确认"，不止 Enter——这里第一条走动作条上的「截图问答」，
    第二条开局就该预选着同一块，且不用先拖一下才出动作条。
    """
    memory = SelectionMemory()

    first = SelectionOverlay(screen=_FakeScreen(200, 120), memory=memory)
    _drag(first, (40, 20), (140, 80))
    _click(first, ACTION_ASK)
    first.close()

    second = SelectionOverlay(screen=_FakeScreen(200, 120), memory=memory)
    assert second.current_selection() == SelectionRect(40, 20, 100, 60)
    assert _bar_shown(second) is True, "预选命中时动作条就该直接待命"

    actions, _cancelled = _record(second)
    _key(second, Qt.Key.Key_Return)
    assert actions == [(ACTION_OCR, SelectionRect(40, 20, 100, 60))], "预选的区域要能直接确认"
    second.close()


def test_the_preselection_is_actually_painted(qapp):
    """预选必须看得见：只是内部状态的话，用户不知道 Enter 会截哪一块。"""
    memory = SelectionMemory()
    memory.remember((200, 120), SelectionRect(40, 20, 100, 60))

    overlay = SelectionOverlay(frame=_frozen_frame(), screen=_FakeScreen(200, 120),
                               memory=memory)
    overlay.show()

    canvas = _render(overlay)

    # 探针点选在选区下缘内侧：这个小屏（200×120）上动作条会翻到选区上方
    # （下方放不下），盖住选区中部；左上还有尺寸标签。(90, 76) 两处都避开了。
    dimmed_white = round(255 * (1 - DIM_ALPHA / 255))
    assert canvas.pixelColor(90, 76).red() >= 250                # 预选区内是原样的亮
    assert abs(canvas.pixelColor(10, 10).red() - dimmed_white) <= 6
    overlay.close()


def test_a_different_screen_size_does_not_reuse_the_old_selection(qapp):
    """换屏就作废：另一块屏的逻辑坐标套过来会框到完全不同的东西。"""
    memory = SelectionMemory()
    memory.remember((200, 120), SelectionRect(40, 20, 100, 60))

    overlay = SelectionOverlay(screen=_FakeScreen(400, 300), memory=memory)

    assert overlay.current_selection() is None
    overlay.close()


def test_a_selection_outside_the_screen_is_not_preselected(qapp):
    """换分辨率后旧坐标可能落在新屏外：越界的预选一律不作数。"""
    memory = SelectionMemory()
    memory.remember((200, 120), SelectionRect(500, 500, 100, 60))

    overlay = SelectionOverlay(screen=_FakeScreen(200, 120), memory=memory)

    assert overlay.current_selection() is None
    overlay.close()


def test_cancelling_does_not_remember_anything(qapp):
    """没确认过的东西不该被当成默认值。"""
    memory = SelectionMemory()

    first = SelectionOverlay(screen=_FakeScreen(200, 120), memory=memory)
    _drag(first, (40, 20), (140, 80))
    _key(first, Qt.Key.Key_Escape)
    first.close()

    second = SelectionOverlay(screen=_FakeScreen(200, 120), memory=memory)
    assert second.current_selection() is None
    second.close()


def test_the_toolbar_cancel_does_not_remember_anything_either(qapp):
    memory = SelectionMemory()

    first = SelectionOverlay(screen=_FakeScreen(200, 120), memory=memory)
    _drag(first, (40, 20), (140, 80))
    _click(first, "cancel")
    first.close()

    second = SelectionOverlay(screen=_FakeScreen(200, 120), memory=memory)
    assert second.current_selection() is None
    second.close()


def test_clicking_after_a_preselection_drops_it_instead_of_capturing_it(qapp):
    """预选之后随手点一下 = 开始新的一次框选：绝不许把旧区域当成"用户选好的"。"""
    memory = SelectionMemory()
    memory.remember((200, 120), SelectionRect(40, 20, 100, 60))
    overlay = SelectionOverlay(screen=_FakeScreen(200, 120), memory=memory)
    assert _bar_shown(overlay) is True
    actions, _cancelled = _record(overlay)

    _drag(overlay, (10, 10), (12, 11))            # 误触

    assert overlay.current_selection() is None
    assert _bar_shown(overlay) is False
    _key(overlay, Qt.Key.Key_Return)
    assert actions == []
    overlay.close()


def test_default_overlays_share_the_process_wide_memory(qapp):
    """不注入 memory 的默认路径必须真的共享那一份（否则"记住上次"等于没接上）。"""
    first = SelectionOverlay(screen=_FakeScreen(200, 120))
    _drag(first, (30, 30), (130, 90))
    _key(first, Qt.Key.Key_Return)
    first.close()

    second = SelectionOverlay(screen=_FakeScreen(200, 120))

    assert second.current_selection() == SelectionRect(30, 30, 100, 60)
    second.close()


def test_selection_memory_is_pure_and_copies(qapp):
    """记忆本身是纯数据：取出来的是副本，改它不会脏内部（也不要求 Qt）。"""
    memory = SelectionMemory()
    remembered = SelectionRect(10, 20, 30, 40)
    memory.remember((800, 600), remembered)

    got = memory.get((800, 600))
    assert got == remembered and got is not remembered
    assert memory.get((1024, 768)) is None

    memory.clear()
    assert memory.get((800, 600)) is None


# -------------------------------------------------------------------- 绘制


def _frozen_frame(width: int = 200, height: int = 120) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(255, 255, 255))
    return image


def _gray_frame(width: int = 800, height: int = 600) -> QImage:
    """中灰底图：压暗后 88、原样 128——尺寸标签（深色 pill）和白色虚线都能压住。"""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(128, 128, 128))
    return image


def _render(overlay, background: QColor | None = None) -> QImage:
    canvas = QImage(overlay.size(), QImage.Format.Format_RGB32)
    canvas.fill(background if background is not None else QColor(0, 0, 0))
    painter = QPainter(canvas)
    overlay.render(painter, QPoint(0, 0))
    painter.end()
    return canvas


def test_frozen_frame_is_shown_as_background(qapp):
    overlay = SelectionOverlay(frame=_frozen_frame(), screen=_FakeScreen())

    canvas = _render(overlay)

    # 白底图被压暗后仍是亮灰——不是黑屏（黑屏＝抓帧没接上）
    assert canvas.pixelColor(5, 5).red() > 100
    overlay.close()


def test_selection_area_stays_bright_while_the_rest_is_dimmed(qapp):
    overlay = SelectionOverlay(frame=_frozen_frame(), screen=_FakeScreen())
    overlay.show()
    _drag(overlay, (50, 30), (150, 90))

    canvas = _render(overlay)

    dimmed_white = round(255 * (1 - DIM_ALPHA / 255))
    # 探针点选选区右下角：左上会被尺寸标签占、下方边缘可能被动作条占（小屏翻上缘），
    # 这两处"覆盖层 chrome"的占地随字体/屏尺寸变，探针必须落在它们永远够不到的地方。
    inside = canvas.pixelColor(140, 85)
    outside = canvas.pixelColor(10, 10)
    assert inside.red() >= 250                       # 选区内保持原样
    assert abs(outside.red() - dimmed_white) <= 6    # 选区外按 DIM_ALPHA 压暗
    overlay.close()


def test_overlay_without_frozen_frame_shows_hint_instead_of_dim(qapp):
    """无选区时不压暗（无遮罩遮内容），只出提示条。

    带冻结帧断言（不带帧的场景是合成边角：控制器抓不到帧根本不会建覆盖层）；
    不依赖窗口默认底色——全量套件里跑过主题类用例后调色板会变，底色断言会误伤。
    """
    overlay = SelectionOverlay(frame=_frozen_frame(), screen=_FakeScreen())

    canvas = _render(overlay, background=QColor(255, 255, 255))

    assert canvas.pixelColor(10, 10).red() >= 250, "无选区不压暗：角落应是冻结帧原样的白"
    # 顶部提示条在屏中央（200 宽的假屏：chip 约 x25..175, y64..88，让开灵动岛高度）
    center = canvas.pixelColor(100, 76)
    assert center.red() < 120, "无选区时顶部应有提示 chip"
    overlay.close()


def test_dim_alpha_is_the_value_the_hdr_measurement_settled_on(qapp):
    """压暗强度是实测值（不是随手填的）：110 在 HDR 屏上被 SDR 提亮补偿抬成白雾，
    80 之下选区内外仍一眼可分、底下内容照常可读。"""
    assert DIM_ALPHA == 80


# ------------------------------------------------------- 尺寸标签与描边（像素级）


#: 尺寸标签画在选区上方这条带里（逻辑像素余量，含贴顶时收进选区内侧的形态）。
_LABEL_BAND = 30


def _label_run(width: int, height: int):
    """框一块 ``width × height``，返回尺寸标签那次深色 pill 的像素行程。"""
    overlay = SelectionOverlay(frame=_gray_frame(), screen=_FakeScreen(800, 600),
                               memory=SelectionMemory())
    overlay.show()
    _drag(overlay, (100, 200), (100 + width, 200 + height))
    canvas = _render(overlay)
    overlay.close()

    best = None
    for row in range(200 - _LABEL_BAND, 200):
        xs = [x for x in range(94, 380) if canvas.pixelColor(x, row).red() < 60]
        if xs and (best is None or len(xs) > len(best)):
            best = xs
    if best is None:
        return None
    return min(best), max(best)


def test_the_size_label_is_painted_above_the_selection(qapp):
    """尺寸标签是 paintEvent 画的（不是控件），且贴着选区的左上角。

    "不是控件"这条很实：标签若做成控件，它就会参与布局/命中测试，拖框时正好压在
    鼠标轨迹上。
    """
    overlay = SelectionOverlay(frame=_gray_frame(), screen=_FakeScreen(800, 600),
                               memory=SelectionMemory())
    overlay.show()
    _drag(overlay, (100, 200), (400, 400))

    canvas = _render(overlay)
    chip = _label_run(400, 200)

    assert chip is not None, "选区上方没有画出尺寸标签"
    assert abs(chip[0] - 100) <= 4, "标签没对齐在选区左上角"
    assert chip[1] - chip[0] >= 40, "标签太窄，不像画了一段 'W × H'"
    # 同一块底图上，标签之外仍是均匀的压暗（标签是"画上去的一小块"）
    assert abs(canvas.pixelColor(700, 150).red() - 88) <= 6
    assert overlay.findChildren(QLabel) == [], "尺寸标签应该是画出来的，不是控件"
    overlay.close()


def test_the_label_reads_the_actual_dimensions(qapp):
    """标签内容是实测尺寸（"400 × 400" 比 "20 × 20" 长）——不是一块固定宽度的装饰。"""
    small = _label_run(20, 20)
    big = _label_run(400, 400)

    assert small is not None and big is not None
    assert big[1] - big[0] > small[1] - small[0]


def test_the_border_is_a_bright_dash_over_a_dark_underlay(qapp):
    """描边两层：白色虚线（亮内容上也看得见）+ 深色垫底（暗内容上也看得见）。"""
    overlay = SelectionOverlay(frame=_gray_frame(), screen=_FakeScreen(800, 600),
                               memory=SelectionMemory())
    overlay.show()
    _drag(overlay, (100, 200), (400, 400))

    canvas = _render(overlay)

    edge = [canvas.pixelColor(x, 200).red() for x in range(120, 380)]
    assert max(edge) >= 230, "描边缺少白色那一层"
    assert min(edge) <= 100, "描边缺少深色垫底（间隙里露不出来）"
    assert abs(canvas.pixelColor(200, 195).red() - 88) <= 6, "描边外一圈仍是均匀压暗"
    overlay.close()


def test_the_action_bar_gates_chat_dependent_actions_by_capability(qapp):
    """没配 AI 服务的宿主：翻译/问问留位置、置灰、写明原因；本地的提取/复制照常。

    能力由编排层注入（``capabilities``）；缺省/全 True 时全部可点（盲审 m10：
    能点但必炸的按钮不如不让点）。
    """
    overlay = SelectionOverlay(screen=_FakeScreen(800, 600), memory=SelectionMemory(),
                               capabilities={"translate": False, "ask": False})
    _drag(overlay, (100, 100), (300, 220))
    bar = overlay.action_bar()
    for name in ("translate", "ask"):
        button = bar.findChild(QPushButton, f"screen-capture-action-{name}")
        assert button.isEnabled() is False, f"{name} 在无配置宿主上不该能点"
        assert button.toolTip(), "置灰必须写明原因"
    for name in ("ocr", "copy"):
        assert bar.findChild(QPushButton, f"screen-capture-action-{name}").isEnabled() is True
    overlay.close()


def test_show_joins_all_spaces_on_macos(monkeypatch, qapp):
    """覆盖层必须在用户当前 Space 显示：否则 activateWindow 会把用户带到旧桌面。"""
    from pet import platform_mac

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(platform_mac, "_mac_set_window_all_spaces",
                        lambda wid: calls.append(("spaces", wid)) or True)
    monkeypatch.setattr(platform_mac, "_mac_set_hides_on_deactivate",
                        lambda wid: calls.append(("hides", wid)) or True)

    overlay = SelectionOverlay(frame=_frozen_frame(200, 120), screen=_FakeScreen(200, 120),
                               memory=SelectionMemory())
    overlay.show()
    qapp.processEvents()
    try:
        kinds = [kind for kind, _wid in calls]
        assert "spaces" in kinds and "hides" in kinds
    finally:
        overlay.close()
