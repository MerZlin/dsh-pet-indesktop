# -*- coding: utf-8 -*-
"""灵动岛精简右键菜单：快速切换信息槽内容与外观风格。"""
from __future__ import annotations

from typing import Any

from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QMenu

from .context_menus.menu_styles.modern import apply_modern_menu_style

_INFO_OPTIONS = (
    ("时间", "time"),
    ("余额", "balance"),
    ("余额峰谷", "balance_tier"),
    ("自定义短文本", "custom"),
)

_STYLE_OPTIONS = (
    ("深色", "dark"),
    ("浅色", "light"),
    ("玻璃", "glass"),
)


def _add_radio_menu(
    menu: QMenu, title: str, current: str, options: tuple[tuple[str, str], ...],
) -> QActionGroup:
    sub = menu.addMenu(title)
    group = QActionGroup(sub)
    group.setExclusive(True)
    for label, value in options:
        action = sub.addAction(label)
        action.setCheckable(True)
        action.setChecked(str(value) == str(current))
        action.setData(value)
        group.addAction(action)
    return group


def _commit_config(island: Any, key: str, value: str) -> None:
    existing = dict(getattr(island, "_cfg", {}) or {})
    existing[key] = str(value)
    island._cfg = existing
    island.config.set("dynamic_island", existing)
    island.config.save()
    island._update_size()
    island.update()


def _apply_island_menu(island: Any, group: QActionGroup, key: str) -> None:
    action = group.checkedAction()
    if action is None:
        return
    _commit_config(island, key, str(action.data() or ""))


def show_island_context_menu(island: Any, global_pos) -> None:
    """右键弹出内容/风格两组单选菜单；选择立即写配置并刷新。"""
    cfg = dict(getattr(island, "_cfg", {}) or {})
    menu = QMenu(island)
    apply_modern_menu_style(menu, {})
    content_group = _add_radio_menu(
        menu, "显示内容", str(cfg.get("info_mode") or "time"), _INFO_OPTIONS,
    )
    style_group = _add_radio_menu(
        menu, "外观", str(cfg.get("style") or "dark"), _STYLE_OPTIONS,
    )
    for group, key in ((content_group, "info_mode"), (style_group, "style")):
        group.triggered.connect(
            lambda _action=None, g=group, k=key: _apply_island_menu(island, g, k)
        )
    menu.exec(global_pos)
    menu.deleteLater()
