from __future__ import annotations

from .menu_dispatch import connect_action

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtWidgets import QMenu

from .menu_style import install_check_layer

#: 纯模板子菜单（没有注册 `MenuItem`）的图标：老 layout 里没有 icon 键，靠它兜底。
SUBMENU_ICONS = {
    "pet_controls": "pet",
    "tools_help": "tools",
    "extensions_hub": "application",
}


def add_submenu(parent: QMenu, title: str) -> QMenu:
    """Give each submenu explicit QObject ownership, independent of wrapper GC."""
    submenu = QMenu(title, parent)
    parent.addMenu(submenu)
    return submenu


@dataclass
class MenuItem:
    id: str
    label: str
    callback: Callable[[], None] | None = None
    owner: str = "core"
    order: int = 100
    checked: Callable[[], bool] | None = None
    populate: Callable[[QMenu], None] | None = None
    enabled: bool = True
    icon: str = ""
    children: tuple["MenuItem", ...] = field(default_factory=tuple)
    #: 自绘行（如彩蛋头像行）：收到菜单自行 addAction，替代普通文本项。
    builder: Callable[[QMenu], None] | None = None

    @property
    def checkable(self) -> bool:
        return self.checked is not None


def _resolve_icon(spec):
    if not spec:
        return None
    try:
        from .icons import icon_from_spec

        return icon_from_spec(spec)
    except Exception:
        return None


class MenuModel:
    def __init__(self):
        self._items: dict[str, MenuItem] = {}
        self._nodes: tuple[dict, ...] = ()
        self._availability: dict[str, bool] = {}
        self._hidden: set[str] = set()
        self._order: list[str] = []
        self._font_size = 13
        self._owner_labels: dict[str, str] = {}
        self._extension_skip_owners: set[str] = set()

    def set_owner_labels(self, labels: dict[str, str]) -> None:
        """扩展子菜单的显示名（owner → 中文名）。"""
        self._owner_labels = {
            str(owner): str(label or owner) for owner, label in dict(labels or {}).items()
        }

    def set_extension_skip_owners(self, owners) -> None:
        """已内嵌到 Core 页的内置特性：不进「扩展」子菜单。"""
        self._extension_skip_owners = {str(owner) for owner in (owners or ())}

    def set_font_size(self, size: int) -> None:
        """菜单字号（px）：下一次 ``build()`` 生效，非法值回退 13。"""
        try:
            value = int(size)
        except (TypeError, ValueError, OverflowError):
            value = 13
        self._font_size = max(9, min(32, value))

    def register(self, item: MenuItem) -> None:
        self._items[item.id] = item

    def unregister(self, item_id: str) -> None:
        self._items.pop(item_id, None)

    def unregister_owner(self, owner: str) -> None:
        for item_id in [key for key, item in self._items.items() if item.owner == owner]:
            self._items.pop(item_id, None)

    def set_availability(self, flags: dict[str, bool]) -> None:
        self._availability = dict(flags)

    def registered_ids(self) -> set[str]:
        return set(self._items)

    def available_ids(self) -> set[str]:
        return {
            item_id
            for item_id in self._items
            if self._availability.get(item_id, True)
        }

    def build_layout(self, nodes, parent=None) -> QMenu:
        previous = self._nodes
        try:
            self._nodes = tuple(nodes)
            return self.build(parent)
        finally:
            self._nodes = previous

    def apply_layout(self, nodes) -> None:
        self._nodes = tuple(nodes)
        self._hidden = set()
        self._order = []

    def apply_preferences(self, hidden: list[str] | None, order: list[str] | None) -> None:
        self._hidden = {str(item) for item in (hidden or [])}
        self._order = [str(item) for item in (order or [])]
        priority = {item_id: index for index, item_id in enumerate(self._order)}
        items = sorted(
            self._items.values(),
            key=lambda item: (priority.get(item.id, len(priority)), item.order, item.id),
        )
        self._nodes = tuple(
            {"type": "action", "id": item.id, "visible": item.id not in self._hidden}
            for item in items
        )

    def set_hidden(self, item_id: str, hidden: bool) -> None:
        if hidden:
            self._hidden.add(item_id)
            return
        self._hidden.discard(item_id)

    def is_hidden(self, item_id: str) -> bool:
        return item_id in self._hidden

    def ordered(self) -> list[MenuItem]:
        if self._nodes:
            ordered: list[MenuItem] = []
            for node in self._nodes:
                if node.get("type") == "action" and node.get("id") in self._items:
                    ordered.append(self._items[node["id"]])
                elif node.get("type") == "submenu":
                    for child in node.get("children", ()):
                        if child.get("type") == "action" and child.get("id") in self._items:
                            ordered.append(self._items[child["id"]])
            return ordered
        priority = {item_id: index for index, item_id in enumerate(self._order)}
        return sorted(
            self._items.values(),
            key=lambda item: (priority.get(item.id, len(priority)), item.order, item.id),
        )

    def visible_items(self) -> list[MenuItem]:
        return [item for item in self.ordered() if item.id not in self._hidden]

    def snapshot(self) -> list[str]:
        if self._nodes:
            return self._flat_action_ids(self._nodes)
        return [item.id for item in self.visible_items()]

    def _flat_action_ids(self, nodes) -> list[str]:
        found: list[str] = []
        for node in nodes:
            if node.get("type") == "action":
                found.append(str(node.get("id") or ""))
            elif node.get("type") == "submenu":
                found.extend(self._flat_action_ids(node.get("children", ())))
        return found

    def build(self, parent=None) -> QMenu:
        menu = QMenu(parent)
        self._apply_style(menu)
        if self._nodes:
            placed: set[str] = set()
            for node in self._nodes:
                self._append_node(menu, node, placed)
        else:
            for item in self.visible_items():
                self._append_item(menu, item)
        # 勾选标记由行尾层画（QSS 的 indicator 只剩 1px）；这里失败就让异常冒出来，
        # 否则「所有勾选静默消失」只会被用户发现。
        install_check_layer(menu)
        return menu

    def _apply_style(self, menu: QMenu) -> None:
        from PySide6.QtGui import QColor, QPalette

        from .menu_style import accent_color, menu_stylesheet

        menu.setStyleSheet(menu_stylesheet(font_size=self._font_size))
        palette = menu.palette()
        if hasattr(QPalette.ColorRole, "Accent"):
            palette.setColor(QPalette.ColorRole.Accent, QColor(accent_color()))
            menu.setPalette(palette)

    def _submenu_icon_spec(self, item_id: str, node: dict) -> str:
        """子菜单图标：节点声明 → 已注册项 → 纯模板子菜单的固定字形。"""
        declared = node.get("icon")
        if declared:
            return declared
        item = self._items.get(item_id)
        if item is not None and item.icon:
            return item.icon
        return SUBMENU_ICONS.get(item_id, "")

    def _append_node(self, menu: QMenu, node: dict, placed: set[str]) -> None:
        node_type = node.get("type")
        if node_type == "separator":
            menu.addSeparator()
            return
        item_id = str(node.get("id") or "")
        if node_type == "submenu":
            submenu = add_submenu(menu, str(node.get("alias") or node.get("label") or item_id))
            icon = _resolve_icon(self._submenu_icon_spec(item_id, node))
            if icon is not None:
                submenu.setIcon(icon)
            if item_id == "extensions_hub":
                # hub 的 children 只表达「哪些 mod 项可见」，渲染一律交给分组器；
                # 平铺 children 会让分组永远只剩一两条（旧 layout 的实测缺陷）。
                allowed = None
                if "children" in node:
                    allowed = [
                        str(child.get("id") or "")
                        for child in node.get("children", ())
                        if isinstance(child, dict) and child.get("type") == "action"
                    ]
                self._populate_extensions(submenu, placed, allowed)
                return
            for child in node.get("children", ()):
                self._append_node(submenu, child, placed)
            return
        if node_type != "action" or item_id in placed:
            return
        item = self._items.get(item_id)
        if item is None:
            return
        placed.add(item_id)
        if item.populate is not None:
            label = str(node.get("alias") or item.label)
            submenu = add_submenu(menu, label)
            icon = _resolve_icon(node.get("icon") or item.icon)
            if icon is not None:
                submenu.setIcon(icon)
            item.populate(submenu)
            return
        self._append_item(menu, item, node)

    def _append_item(self, menu: QMenu, item: MenuItem, node: dict | None = None) -> None:
        if item.builder is not None:
            # builder 自己往菜单里加（通常是 QWidgetAction），这里不再 addAction。
            item.builder(menu)
            return
        label = str((node or {}).get("alias") or item.label)
        action = menu.addAction(label)
        # 勾选项**也要**有图标：QMenu 的勾选列与图标列是同一个位置，勾选标记已挪到行尾
        # （`menu_style.MenuCheckLayer`），图标列因此空了出来（主分支同口径）。
        icon = _resolve_icon((node or {}).get("icon") or item.icon)
        if icon is not None:
            action.setIcon(icon)
        action.setEnabled(item.enabled and self._availability.get(item.id, True))
        if item.checkable:
            action.setCheckable(True)
            action.setChecked(bool(item.checked()))
        if item.callback is not None:
            connect_action(action, lambda _checked=False, cb=item.callback: cb())

    def _populate_extensions(
        self, menu: QMenu, placed: set[str], allowed: list[str] | None = None
    ) -> None:
        """「扩展」子菜单：按 mod（owner）分组，每组一个二级子菜单。

        ``allowed`` 是 layout 里 hub 的可见 action 列表：非空时只渲染这些项
        （保留菜单编辑器的隐藏/裁剪语义）；为空或缺失时回退到全部可见项，
        避免旧 layout 把整个扩展菜单变成「（暂无扩展）」。
        """
        allowed_order = {
            action_id: index for index, action_id in enumerate(allowed or [])
        }
        allowed_set = set(allowed_order)
        groups: dict[str, list[MenuItem]] = {}
        for item in self._items.values():
            if item.owner == "core" or item.id in placed:
                continue
            if item.owner in self._extension_skip_owners:
                continue
            if not self._availability.get(item.id, True):
                continue
            if allowed_set and item.id not in allowed_set:
                continue
            groups.setdefault(item.owner, []).append(item)

        def group_key(owner: str) -> tuple:
            items = groups[owner]
            hints = [
                allowed_order[item.id] for item in items if item.id in allowed_order
            ]
            if hints:
                return (0, min(hints), owner)
            return (1, min(item.order for item in items), owner)

        for owner in sorted(groups, key=group_key):
            items = sorted(groups[owner], key=lambda entry: (entry.order, entry.label))
            submenu = add_submenu(menu, self._owner_labels.get(owner) or owner)
            icon = _resolve_icon(next((item.icon for item in items if item.icon), ""))
            if icon is not None:
                submenu.setIcon(icon)
            for item in items:
                placed.add(item.id)
                if item.populate is not None:
                    child = add_submenu(submenu, item.label)
                    item.populate(child)
                else:
                    self._append_item(submenu, item)
        if not menu.actions():
            empty = menu.addAction("（暂无扩展）")
            empty.setEnabled(False)
