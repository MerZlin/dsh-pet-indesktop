"""右键菜单的统一入口（层级、弹出与会话）。

菜单是 ``Qt.Popup``：Windows 上 Qt 建的弹出菜单本身就带 ``WS_EX_TOPMOST``（本机
实测，``prepare_menu`` 加不加 ``WindowStaysOnTopHint`` 都一样），所以它一弹出就压住
普通窗口；但**置顶的桌宠与气泡再 ``raise_()`` 一次就会翻到它上面**，气泡又是桌宠的
从属窗口、``show()`` 会把整条 owner 链一起抬走——于是「菜单开着时冒泡」就把菜单压了。

层级只能靠「会话」保证：菜单弹出期间，桌宠与气泡的任何一次 ``raise_()``（或
``show()`` 带来的 owner 链上浮）都会把它压下去，所以弹出菜单要登记一个会话
（``menu_session``），桌宠与气泡在抬起前先查 ``menu_open()``。菜单自己仍按
``topmost`` 决定是否加置顶旗标，不因为这条修复被永久置顶。

所有会弹出「属于桌宠的」菜单的地方（桌宠右键、扩展的快捷启动菜单）都走这两个函数，
各自写一份 ``exec`` 只会把这套层级规则漏掉一半。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from .menu_actions import availability, core_items, install_core_menu

__all__ = [
    "active_menu",
    "availability",
    "core_items",
    "install_core_menu",
    "menu_open",
    "menu_session",
    "prepare_menu",
    "show_context_menu",
]

#: 正在弹出的菜单会话层数（0 = 没有菜单）。会话期间桌宠与气泡都不许抢到菜单上面：
#: 它们和菜单同属普通窗口层，任何一次抬起都会把菜单压下去。
_menu_sessions = 0
#: 当前会话的菜单（嵌套时是最内层那只）。
_active_menu: QMenu | None = None


def menu_open() -> bool:
    """当前是否有「属于桌宠的」菜单正在弹出。"""
    return _menu_sessions > 0


def active_menu() -> QMenu | None:
    """当前会话的菜单；底层对象已销毁时返回 ``None``。"""
    menu = _active_menu
    if menu is None:
        return None
    try:
        menu.objectName()
    except RuntimeError:  # 底层 C++ 对象已被释放
        return None
    return menu


@contextmanager
def menu_session(menu: QMenu) -> Iterator[QMenu]:
    """登记一次「菜单正在弹出」的会话（嵌套安全）。

    会话期间桌宠与气泡要先查 :func:`menu_open` 再决定是否 ``raise_()``；退出会话
    （包括异常）后计数归零，旧行为自动恢复。
    """
    global _menu_sessions, _active_menu
    previous = _active_menu
    _menu_sessions += 1
    _active_menu = menu
    try:
        yield menu
    finally:
        _menu_sessions = max(0, _menu_sessions - 1)
        _active_menu = previous if _menu_sessions else None


def prepare_menu(menu: QMenu, *, topmost: bool = False) -> QMenu:
    """把菜单放进桌宠所在的层（幂等；不影响 QMenu 的点击外部关闭行为）。"""
    if topmost:
        if not (menu.windowFlags() & Qt.WindowType.WindowStaysOnTopHint):
            menu.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        menu.raise_()
    return menu


def show_context_menu(menu: QMenu, pos, *, topmost: bool = False) -> None:
    """弹出菜单：先按桌宠的层级摆好，再在会话里 ``exec``（阻塞到菜单关闭）。

    会话包住整个 ``exec``：菜单开着的时候桌宠冒泡（自言自语、余额、歌词）也不会把
    菜单压下去，会话结束后立刻恢复原行为。
    """
    if menu is None:
        return
    with menu_session(menu):
        prepare_menu(menu, topmost=topmost)
        menu.exec(pos)
