# -*- coding: utf-8 -*-
"""双击桌宠打开快速对话气泡（QuickChatBubble）。

为什么是事件过滤器，而不是覆写 ``mouseDoubleClickEvent``：

1. ``PetWindow`` 的 MRO 是 ``[PetWindow, QWidget, QObject, QPaintDevice, Object,
   WindowFeatureGateMixin, object]`` —— ``QWidget`` 排在 ``WindowFeatureGateMixin``
   之前，混入类里定义 Qt 虚函数会被 ``QWidget`` 的实现**静默遮蔽**（实测
   ``PetWindow.mouseDoubleClickEvent`` 解析到 ``QWidget.mouseDoubleClickEvent``）；
2. ``window.py`` 行数预算已顶满（4616/4616，见 ``tests/test_architecture.py``），
   往主窗口加处理器必然越线。

过滤器安装在桌宠窗口上，在窗口自身的事件处理之前拿到事件，因此既能开栏，
也能吞掉双击的第二段 ``Release``。

事件序（Windows 真实消息映射 ``WM_LBUTTONDOWN``/``UP``/``DBLCLK``/``UP``）::

    Press  → PetWindow.mousePressEvent     （正常按下）
    Release→ PetWindow.mouseReleaseEvent   （第一次点击回应 _on_click）
    DblClick→ 本过滤器：开栏并吞掉事件
    Release→ 本过滤器：吞掉（否则因 _press_global 已清空、位移为 0，
             会再落进 window.py 的点击分支，多出第二次点击回应）
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt

__all__ = ["DoubleClickChatFilter", "install_double_click_chat"]


class DoubleClickChatFilter(QObject):
    """把桌宠左键双击路由到快速对话栏，并抑制尾随 Release 的重复点击。"""

    def __init__(self, win, parent=None):
        super().__init__(parent if parent is not None else win)
        self._win = win
        self._swallow_release = False

    # ------------------------------------------------------------ 状态探针
    def _enabled(self) -> bool:
        cfg = getattr(self._win, "cfg", None)
        getter = getattr(cfg, "get", None)
        if not callable(getter):
            return False
        try:
            return bool(getter("double_click_chat", True))
        except Exception:
            return False

    def _callback(self):
        callback = getattr(self._win, "on_open_quick_chat", None)
        return callback if callable(callback) else None

    def _in_interactive_area(self, event) -> bool:
        """与 window.py 的按下路径同口径：左右留白区域不参与交互。"""
        check = getattr(self._win, "_is_in_interactive_area", None)
        if not callable(check):
            return True
        try:
            return bool(check(event.position().toPoint()))
        except Exception:
            return True

    # ------------------------------------------------------------ 事件入口
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if obj is not self._win:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            # 新的按下作废上一次双击留下的抑制标记，避免状态泄漏到后续交互
            self._swallow_release = False
            return False
        if kind == QEvent.Type.MouseButtonDblClick:
            return self._on_double_click(event)
        if kind == QEvent.Type.MouseButtonRelease and self._swallow_release:
            self._swallow_release = False
            return True
        return False

    def _on_double_click(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        callback = self._callback()
        # 无对话入口（no-chat 打包变体）或开关关闭：完整回落到原有双击行为
        if callback is None or not self._enabled():
            return False
        if not self._in_interactive_area(event):
            return False
        self._swallow_release = True
        callback()
        return True


def install_double_click_chat(win) -> DoubleClickChatFilter | None:
    """给桌宠窗口安装双击开栏过滤器（幂等）。

    无 ``cfg`` 的宿主、以及非 QWidget 的测试替身（``_wire_window`` 会喂
    ``_FakeWindow``）都返回 None——没有事件过滤器可装，也不该因此报错。
    """
    if getattr(win, "cfg", None) is None:
        return None
    children = getattr(win, "children", None)
    install = getattr(win, "installEventFilter", None)
    if not callable(children) or not callable(install):
        return None
    for child in children():
        if isinstance(child, DoubleClickChatFilter):
            return child
    flt = DoubleClickChatFilter(win)
    install(flt)
    return flt
