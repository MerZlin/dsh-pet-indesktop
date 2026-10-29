"""鼠标模式状态机：经典左键 / 穿透模式（左键穿透 + 右键操作）。

界面层只负责把鼠标事件与长按计时喂进来，把返回的动作落到窗口上。这里不碰
Qt，也不读配置，便于穷举时序（单击/长按/拖拽中右键）而不依赖真实等待。
"""

from __future__ import annotations

from enum import Enum

#: 穿透模式下，左键按住多久才进入拖动（毫秒）。
POINTER_DRAG_HOLD_MS = 250
#: 穿透模式下，右键按住多久才弹出菜单（毫秒）。
POINTER_MENU_HOLD_MS = 350


class PointerAction(str, Enum):
    """一次鼠标事件应触发的动作。"""

    NONE = "none"
    PENDING = "pending"
    DRAG = "drag"
    DRAG_RELEASE = "drag_release"
    FORWARD_CLICK = "forward_click"
    CLICK = "click"
    MENU = "menu"
    SLINGSHOT = "slingshot"


class PointerArbiter:
    """穿透模式的鼠标状态机。

    经典模式不使用本类（窗口保留原有的左键点击/右键菜单路径）；只有
    ``click_through=True`` 时才创建。状态只记“按下了哪个键、是否已越过
    长按阈值”，不做时间计算（长按由窗口的 QTimer 驱动）。
    """

    def __init__(self) -> None:
        self._left_down = False
        self._left_dragging = False
        self._right_down = False
        self._right_menu = False

    @property
    def left_down(self) -> bool:
        return self._left_down

    @property
    def left_dragging(self) -> bool:
        return self._left_dragging

    @property
    def right_down(self) -> bool:
        return self._right_down

    @property
    def right_menu_open(self) -> bool:
        return self._right_menu

    def reset(self) -> None:
        self._left_down = False
        self._left_dragging = False
        self._right_down = False
        self._right_menu = False

    def left_press(self) -> PointerAction:
        """左键按下：先等长按阈值，不立即拖动。"""
        if self._left_down:
            return PointerAction.NONE
        self._left_down = True
        self._left_dragging = False
        return PointerAction.PENDING

    def left_hold(self) -> PointerAction:
        """长按阈值到点：进入拖动；鼠标已经松开时返回 NONE。"""
        if not self._left_down or self._left_dragging:
            return PointerAction.NONE
        self._left_dragging = True
        return PointerAction.DRAG

    def left_release(self) -> PointerAction:
        """左键松开：拖动走落点，未拖动则把这一击转发给下层窗口。"""
        if not self._left_down:
            return PointerAction.NONE
        dragging = self._left_dragging
        self._left_down = False
        self._left_dragging = False
        return PointerAction.DRAG_RELEASE if dragging else PointerAction.FORWARD_CLICK

    def right_press(self, *, dragging: bool) -> PointerAction:
        """右键按下：拖拽中进弹弓瞄准，否则等菜单长按阈值。"""
        if self._right_down or self._right_menu:
            return PointerAction.NONE
        if dragging:
            return PointerAction.SLINGSHOT
        self._right_down = True
        return PointerAction.PENDING

    def right_hold(self) -> PointerAction:
        """右键长按阈值到点：弹菜单（只返回一次）。"""
        if not self._right_down or self._right_menu:
            return PointerAction.NONE
        self._right_menu = True
        return PointerAction.MENU

    def right_release(self) -> PointerAction:
        """右键松开：菜单已弹则吞掉；否则就是一次右键单击。"""
        if self._right_menu:
            self._right_menu = False
            self._right_down = False
            return PointerAction.NONE
        if not self._right_down:
            return PointerAction.NONE
        self._right_down = False
        return PointerAction.CLICK

    def right_cancel(self) -> None:
        """瞄准会话被取消/窗口隐藏：清右键按下态，不产生单击。"""
        self._right_down = False
        self._right_menu = False
