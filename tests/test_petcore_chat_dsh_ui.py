# -*- coding: utf-8 -*-
"""DSH 视图的排版/折叠/窗口几何回归（2026-11-01 用户报障）。

覆盖四类实测缺陷：

1. 一行含不可断词 token 的 tool-call 把 ``timeline.minimumSizeHint()`` 抬到
   1186px，而横向滚动条被关掉 → 每行右侧永久裁掉 450px；
2. 右下角圆角被平方白底的子控件盖掉（实测 30 个不透明纯白像素）；
3. 工具过程/思考必须一行折叠，点开才看正文；
4. 最大化必须真铺满（7px 透明外环与圆角都要去掉），且跟随桌宠/拖边缘
   不能把最大化静默取消。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from petcore.ui.chat.dsh_view import DshView, _Collapsible
from petcore.ui.chat.window import ChatWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, rounds: int = 3) -> None:
    for _ in range(rounds):
        app.processEvents()


def _long_frames(view: DshView) -> None:
    view._on_frame({"kind": "user", "text": "帮我看看这个"})
    view._on_frame(
        {
            "kind": "assistant",
            "text": "三样都在，而且刚才做了实测。" * 12,
            "reasoning": "先看桥的状态，再核对工具卡。" * 6,
        }
    )
    view._on_frame(
        {
            "kind": "tool-call",
            "callId": "c1",
            "tool": "pwsh",
            "args": '{"command":"Get-Item C:/Users/someone/AppData/Local/Temp/'
            '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}',
        }
    )
    view._on_frame({"kind": "tool-result", "callId": "c1", "ok": True, "text": "ok"})


def _open_dsh_window(app) -> ChatWindow:
    window = ChatWindow(dsh=None)
    window.resize(960, 700)
    window.show()
    window.set_view("dsh")
    _pump(app)
    return window


def test_long_tokens_do_not_widen_the_dsh_timeline(app) -> None:
    window = _open_dsh_window(app)
    view = window.dsh_view
    try:
        _long_frames(view)
        _pump(app)
        viewport = view.scroll.viewport()
        assert view.timeline.width() <= viewport.width(), (
            f"timeline {view.timeline.width()} > viewport {viewport.width()}"
        )
        assert view.scroll.horizontalScrollBar().maximum() == 0
        assert view.timeline.minimumSizeHint().width() <= viewport.width()
    finally:
        window.close()
        window.deleteLater()


def test_text_reflows_when_the_window_is_resized(app) -> None:
    window = _open_dsh_window(app)
    view = window.dsh_view
    try:
        _long_frames(view)
        _pump(app)
        assistant = next(
            child
            for child in view.timeline.findChildren(QWidget)
            if child.objectName() == "dsh-assistant"
        )
        wide = assistant.height()
        window.resize(620, 700)
        _pump(app, 6)
        view._reflow()
        _pump(app)
        assert assistant.width() <= view.scroll.viewport().width()
        assert assistant.height() > wide, "窗口变窄后折行高度必须增加"
    finally:
        window.close()
        window.deleteLater()


def test_tool_cards_and_thinking_start_collapsed(app) -> None:
    view = DshView(None)
    try:
        _long_frames(view)
        cards = {
            row.objectName(): row for row in view.timeline.findChildren(_Collapsible)
        }
        tool = cards["dsh-tool"]
        thinking = cards["dsh-thinking"]
        # 视图本身没 show()：用 isHidden() 看显式折叠状态，不用 isVisible()。
        assert tool.body.isHidden() is True
        assert tool.is_expanded() is False
        assert "完成" in tool.header.text()
        assert "已思考" in thinking.header.text()
        assert thinking.body.isHidden() is True
        tool.toggle()
        assert tool.is_expanded() is True
        assert tool.body.isHidden() is False
    finally:
        view.deleteLater()


def test_failed_tool_result_expands_its_details(app) -> None:
    view = DshView(None)
    try:
        view._on_frame(
            {"kind": "tool-call", "callId": "c9", "tool": "pwsh", "args": "{}"}
        )
        view._on_frame(
            {"kind": "tool-result", "callId": "c9", "ok": False, "text": "boom"}
        )
        card = view._tool_cards["c9"]
        assert card.is_expanded() is True
        assert "失败" in card.header.text()
        assert card.header.property("state") == "error"
    finally:
        view.deleteLater()


def _render(window: ChatWindow) -> QImage:
    image = QImage(window.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        window.render(painter, QPoint(0, 0))
    finally:
        painter.end()
    return image


def test_shell_corner_is_transparent_for_every_surface(app) -> None:
    # 采样点必须在 phone-shell **内部**的角上（窗口最外 2px 是刻意的透明外环，
    # 拿它断言恒真、测不出子控件盖住圆角）。SHELL_MARGIN=7、圆角 14：
    # (w-9, h-9) 落在弧外、壳体方形范围内，旧实现这里是纯白。
    for background in ("", "builtin:whale"):
        window = ChatWindow(dsh=None, background=background)
        window.resize(960, 700)
        window.show()
        _pump(app)
        try:
            image = _render(window)
            corner = image.pixelColor(window.width() - 9, window.height() - 9)
            assert corner.alpha() == 0, f"background={background!r} corner={corner}"
            # 同一张图里壳体中段必须不透明：否则「全透明」也能骗过上面那条。
            solid = image.pixelColor(window.width() // 2, window.height() // 2)
            assert solid.alpha() == 255
        finally:
            window.close()
            window.deleteLater()


def test_maximized_window_fills_the_screen_without_shell_margin(app) -> None:
    window = ChatWindow(dsh=None)
    window.resize(960, 700)
    window.show()
    _pump(app)
    try:
        window.toggle_maximized()
        _pump(app)
        assert window.isMaximized() is True
        assert window.layout().contentsMargins().left() == 0
        assert window.phone_shell.geometry() == window.rect()
        window.toggle_maximized()
        _pump(app)
        assert window.isMaximized() is False
        assert window.layout().contentsMargins().left() > 0
    finally:
        window.close()
        window.deleteLater()


class _FakePet(QWidget):
    pet_moved = Signal()

    def body_rect(self):
        # 真窗口返回 ``(x, y, w, h)`` 元组（``QRect(*rect)`` 只吃可迭代对象）。
        return (400, 300, 160, 200)


def test_follow_pet_does_not_cancel_maximized(app) -> None:
    pet = _FakePet()
    pet.show()
    window = ChatWindow(dsh=None, pet_window=pet, follow_pet=True)
    window.show()
    _pump(app)
    try:
        window.toggle_maximized()
        _pump(app)
        window.position_near_pet()
        _pump(app)
        assert window.isMaximized() is True
    finally:
        window.close()
        window.deleteLater()
        pet.close()
        pet.deleteLater()


def test_maximized_window_has_no_resize_edges(app) -> None:
    window = ChatWindow(dsh=None)
    window.resize(960, 700)
    window.show()
    _pump(app)
    try:
        window.toggle_maximized()
        _pump(app)
        assert window._edge_at(QPoint(1, 1)) == set()
        assert window._edge_at(QPoint(window.width() - 1, window.height() - 1)) == set()
    finally:
        window.close()
        window.deleteLater()


def test_dsh_sidebar_section_hidden_until_enabled(app) -> None:
    window = ChatWindow(dsh=None)
    window.resize(960, 700)
    window.show()
    _pump(app)
    try:
        assert window.dsh_enabled() is False
        window.set_dsh_enabled(True)
        assert window.dsh_enabled() is True
    finally:
        window.close()
        window.deleteLater()


def test_thinking_row_absent_without_reasoning(app) -> None:
    view = DshView(None)
    try:
        view._on_frame({"kind": "assistant", "text": "只有正文"})
        names = [row.objectName() for row in view.timeline.findChildren(_Collapsible)]
        assert "dsh-thinking" not in names
    finally:
        view.deleteLater()


def test_many_long_rows_do_not_widen_the_timeline(app) -> None:
    """行数很多时也不许把时间线撑宽（回归：tool-call 的长参数）。"""
    view = DshView(None)
    view.resize(QSize(700, 500))
    view.show()
    _pump(app)
    try:
        for index in range(30):
            view._on_frame(
                {
                    "kind": "tool-call",
                    "callId": f"c{index}",
                    "tool": "pwsh",
                    "args": "x" * 400,
                }
            )
        _pump(app)
        view._reflow()
        _pump(app)
        assert (
            view.timeline.minimumSizeHint().width()
            <= view.scroll.viewport().width()
        )
        assert view.scroll.horizontalScrollBar().maximum() == 0
    finally:
        view.close()
        view.deleteLater()


def test_application_event_loop_smoke(app) -> None:
    """最小事件循环冒烟：建窗、切视图、关窗都不许抛。"""
    window = ChatWindow(dsh=None)
    window.show()
    QApplication.processEvents()
    window.set_view("dsh")
    window.set_view("ai")
    window.close()
    window.deleteLater()
