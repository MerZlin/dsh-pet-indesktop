from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..icons import file_icon, icon as menu_icon, icon_from_spec, image_error
from ..menu_layout import DEFAULT_LAYOUT_ID, load_default, resolve
from ..menu_model import MenuModel
from .base import make_button

NODE_ROLE = Qt.ItemDataRole.UserRole
ICON_CHOICES = (
    "默认图标",
    "character",
    "size",
    "hide",
    "pin",
    "pointer",
    "autostart",
    "corner",
    "screen",
    "refresh",
    "folder",
    "application",
    "settings",
    "info",
    "quit",
    "pet",
    "tools",
    "web",
    "todo",
    "download",
    "sparkles",
    "capture",
    "eye",
)
PICK_IMAGE = "选择图片文件…"


class MenuEditor(QWidget):
    def __init__(self, host, model: MenuModel, parent=None):
        super().__init__(parent)
        self.host = host
        self.model = model
        self._preview: QMenu | None = None
        self._initial = self._editor_nodes()
        # 基准要等树建好才拿得到；与 `value()` 同结构、同类型（dict），否则
        # `is_dirty()` 恒为 True（审计 AUD-28）。`is_dirty()` 首次调用时惰性定基准。
        self._initial_value: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("menuEditorTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(18)
        self.tree.setMinimumHeight(300)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._sync_buttons)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.up_button = make_button("上移", self)
        self.down_button = make_button("下移", self)
        self.separator_button = make_button("分割线", self)
        self.submenu_button = make_button("新建子菜单", self)
        self.remove_button = make_button("删除", self)
        self.rename_button = make_button("重命名", self)
        self.icon_button = make_button("图标", self)
        self.reset_button = make_button("重置默认", self)
        self.preview_button = make_button("预览", self)
        for button in (
            self.up_button,
            self.down_button,
            self.separator_button,
            self.submenu_button,
            self.remove_button,
            self.rename_button,
            self.icon_button,
            self.reset_button,
            self.preview_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.separator_button.clicked.connect(self.insert_separator)
        self.submenu_button.clicked.connect(self.add_submenu)
        self.remove_button.clicked.connect(self.remove_selected)
        self.rename_button.clicked.connect(self.rename_selected)
        self.icon_button.clicked.connect(self.set_selected_icon)
        self.reset_button.clicked.connect(self.reset_default)
        self.preview_button.clicked.connect(self.show_preview)

        self._populate(self._initial)
        self._sync_buttons()
        # 基准必须在树建好、填充完之后取：与 `value()` 同结构同类型（dict），
        # 否则 `is_dirty()` 恒为 True（审计 AUD-28）。
        self._initial_value = self.value()

    # ------------------------------------------------------------ data
    def _editor_nodes(self) -> list[dict]:
        raw = self.host.config.get("menu_layout")
        if not isinstance(raw, dict):
            raw = load_default()
        resolved = resolve(
            raw,
            registered=self.model.registered_ids(),
            available=self.model.registered_ids(),
            include_hidden=True,
        )
        return [deepcopy(node) for node in resolved.nodes]

    def _registered(self) -> set[str]:
        return self.model.registered_ids()

    def value(self) -> dict:
        return {
            "schema_version": 1,
            "layout_id": DEFAULT_LAYOUT_ID,
            "nodes": self._encode(self._tree_nodes()),
        }

    def is_dirty(self) -> bool:
        return self.value() != self._initial_value

    def confirm_save(self) -> None:
        # 只写内存，**不落盘**：落盘由 `SettingsDialog._commit()` 在所有页都通过后
        # 统一做。这里自己 save 会让「API 页校验失败 → 取消」也留下已保存的菜单。
        self.host.config.set("menu_layout", self.value())
        self._initial_value = self.value()
        if hasattr(self.host, "reload_menu"):
            self.host.reload_menu()
        if getattr(self.host, "tray", None) is not None:
            self.host.tray.refresh()

    def discard(self) -> None:
        self._initial = self._editor_nodes()
        self._populate(self._initial)

    # ------------------------------------------------------------ tree
    def _populate(self, nodes) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        for node in nodes:
            self._add_node(None, node)
        self.tree.blockSignals(False)
        self.tree.expandAll()

    def _add_node(self, parent: QTreeWidgetItem | None, node: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        node_type = str(node.get("type") or "action")
        node_id = str(node.get("id") or "")
        if node_type == "separator":
            item.setText(0, "— 分割线")
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled
            )
        else:
            label = self._label(node)
            item.setText(0, label)
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled
            if node_type == "submenu":
                flags |= Qt.ItemFlag.ItemIsDropEnabled
            else:
                flags &= ~Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(flags)
            item.setCheckState(
                0,
                Qt.CheckState.Checked if node.get("visible", True) else Qt.CheckState.Unchecked,
            )
            icon_spec = node.get("icon")
            if not icon_spec and node_type == "action":
                registered = self.model._items.get(node_id)
                icon_spec = registered.icon if registered else ""
            if icon_spec:
                item.setIcon(0, icon_from_spec(icon_spec))
            if node.get("available") is False:
                item.setForeground(0, Qt.GlobalColor.gray)
                item.setToolTip(0, "当前环境不可用；重新可用时会自动恢复")
        item.setData(0, NODE_ROLE, self._node_payload(node))
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in node.get("children", ()) or ():
            self._add_node(item, child)
        return item

    def _node_payload(self, node: dict) -> dict:
        payload = {
            "type": str(node.get("type") or "action"),
            "id": str(node.get("id") or ""),
            "visible": bool(node.get("visible", True)),
        }
        for key in ("label", "alias", "icon", "section"):
            if key in node and node[key] is not None:
                payload[key] = node[key]
        return payload

    def _label(self, node: dict) -> str:
        node_type = node.get("type")
        node_id = str(node.get("id") or "")
        original = str(node.get("label") or "")
        if node_type == "action":
            registered = self.model._items.get(node_id)
            original = registered.label if registered else node_id
        elif node_type == "submenu":
            original = original or "子菜单"
        alias = str(node.get("alias") or "").strip()
        return f"{alias}（{original}）" if alias else original

    def _tree_nodes(self) -> list[dict]:
        def encode(item: QTreeWidgetItem) -> dict:
            payload = dict(item.data(0, NODE_ROLE) or {})
            node_type = payload.get("type")
            node = {
                "type": node_type,
                "id": payload.get("id"),
                "visible": True
                if node_type == "separator"
                else item.checkState(0) == Qt.CheckState.Checked,
            }
            for key in ("label", "alias", "icon", "section"):
                if payload.get(key):
                    node[key] = payload[key]
            if node_type == "submenu":
                node["children"] = [encode(item.child(i)) for i in range(item.childCount())]
            return node

        return [encode(self.tree.topLevelItem(i)) for i in range(self.tree.topLevelItemCount())]

    def _encode(self, nodes) -> list[dict]:
        return [
            {
                key: value
                for key, value in node.items()
                if key in {"type", "id", "visible", "label", "alias", "icon", "section", "children"}
            }
            for node in nodes
        ]

    # ------------------------------------------------------------ editing
    def _current(self) -> QTreeWidgetItem | None:
        return self.tree.currentItem()

    def _sync_buttons(self, *_args) -> None:
        item = self._current()
        payload = dict(item.data(0, NODE_ROLE) or {}) if item is not None else {}
        node_type = payload.get("type")
        parent = item.parent() if item is not None else None
        self.up_button.setEnabled(item is not None and parent is not None and parent.indexOfChild(item) > 0
                                 or (item is not None and parent is None and self.tree.indexOfTopLevelItem(item) > 0))
        self.down_button.setEnabled(item is not None and parent is not None
                                   and parent.indexOfChild(item) < parent.childCount() - 1
                                   or (item is not None and parent is None
                                       and self.tree.indexOfTopLevelItem(item) < self.tree.topLevelItemCount() - 1))
        self.remove_button.setEnabled(node_type in {"separator", "submenu"})
        self.rename_button.setEnabled(node_type in {"action", "submenu"})
        self.icon_button.setEnabled(node_type in {"action", "submenu"})
        self.submenu_button.setEnabled(True)
        self.separator_button.setEnabled(True)

    def _on_item_changed(self, *_args) -> None:
        self._sync_buttons()

    def move_selected(self, delta: int) -> None:
        item = self._current()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            index = self.tree.indexOfTopLevelItem(item)
            target = index + delta
            if 0 <= target < self.tree.topLevelItemCount():
                taken = self.tree.takeTopLevelItem(index)
                self.tree.insertTopLevelItem(target, taken)
                self.tree.setCurrentItem(taken)
        else:
            index = parent.indexOfChild(item)
            target = index + delta
            if 0 <= target < parent.childCount():
                taken = parent.takeChild(index)
                parent.insertChild(target, taken)
                self.tree.setCurrentItem(taken)
        self._sync_buttons()

    def insert_separator(self) -> None:
        existing = {node.get("id") for node in self._tree_nodes()}
        number = 1
        while f"user.separator-{number}" in existing:
            number += 1
        item = QTreeWidgetItem()
        item.setText(0, "— 分割线")
        item.setData(0, NODE_ROLE, {"type": "separator", "id": f"user.separator-{number}", "visible": True})
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
        self._insert_after_current(item)
        self.tree.setCurrentItem(item)

    def add_submenu(self, label: str | None = None) -> None:
        if label is None:
            label, ok = QInputDialog.getText(self, "新建子菜单", "名称")
            if not ok or not label.strip():
                return
        existing = {node.get("id") for node in self._tree_nodes()}
        number = 1
        while f"user.submenu-{number}" in existing:
            number += 1
        item = QTreeWidgetItem()
        item.setText(0, label.strip())
        item.setData(0, NODE_ROLE, {
            "type": "submenu",
            "id": f"user.submenu-{number}",
            "label": label.strip(),
            "visible": True,
        })
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsDropEnabled
        )
        item.setCheckState(0, Qt.CheckState.Checked)
        self._insert_after_current(item)
        self.tree.setCurrentItem(item)

    def _insert_after_current(self, item: QTreeWidgetItem) -> None:
        current = self._current()
        if current is not None:
            parent = current.parent()
            if parent is not None:
                parent.insertChild(parent.indexOfChild(current) + 1, item)
            else:
                self.tree.insertTopLevelItem(self.tree.indexOfTopLevelItem(current) + 1, item)
        else:
            self.tree.addTopLevelItem(item)

    def remove_selected(self) -> None:
        item = self._current()
        if item is None:
            return
        payload = dict(item.data(0, NODE_ROLE) or {})
        if payload.get("type") not in {"separator", "submenu"}:
            return
        answer = QMessageBox.question(
            self,
            "删除菜单项",
            f"确定删除「{item.text(0)}」？该操作只改菜单布局，之后仍可重新添加。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        parent = item.parent()
        if parent is None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        else:
            parent.takeChild(parent.indexOfChild(item))
        self._sync_buttons()

    def rename_selected(self, alias: str | None = None) -> None:
        item = self._current()
        if item is None:
            return
        payload = dict(item.data(0, NODE_ROLE) or {})
        if payload.get("type") not in {"action", "submenu"}:
            return
        if alias is None:
            alias, ok = QInputDialog.getText(
                self, "重命名", "显示名称（留空恢复原名）", text=str(payload.get("alias") or "")
            )
            if not ok:
                return
        alias = str(alias or "").strip()[:40]
        if alias:
            payload["alias"] = alias
        else:
            payload.pop("alias", None)
        item.setData(0, NODE_ROLE, payload)
        item.setText(0, self._label(payload))

    def set_selected_icon(self, icon_name: str | None = None) -> None:
        item = self._current()
        if item is None:
            return
        payload = dict(item.data(0, NODE_ROLE) or {})
        if payload.get("type") not in {"action", "submenu"}:
            return
        if icon_name is None:
            choices = [*ICON_CHOICES, PICK_IMAGE]
            icon_name, ok = QInputDialog.getItem(self, "选择图标", "图标", choices, 0, False)
            if not ok:
                return
            if icon_name == PICK_IMAGE:
                path, _ = QFileDialog.getOpenFileName(
                    self, "选择图片图标", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)"
                )
                if not path:
                    return
                error = image_error(path)
                if error:
                    QMessageBox.warning(self, "图标", error)
                    return
                display, ok = QInputDialog.getItem(
                    self, "图片显示方式", "显示方式", ["完整显示", "裁切填满"], 0, False
                )
                if not ok:
                    return
                cover = display == "裁切填满"
                payload["icon"] = {
                    "kind": "file",
                    "path": str(Path(path).resolve()),
                    "display": "cover" if cover else "contain",
                }
                item.setData(0, NODE_ROLE, payload)
                item.setIcon(0, file_icon(path, display="cover" if cover else "contain"))
                return
        if icon_name in {"", "默认图标"}:
            payload.pop("icon", None)
            registered = self.model._items.get(str(payload.get("id") or ""))
            default_icon = registered.icon if registered else ""
            if default_icon:
                item.setIcon(0, icon_from_spec(default_icon))
            else:
                item.setIcon(0, QIcon())
        else:
            payload["icon"] = icon_name
            item.setIcon(0, menu_icon(icon_name))
        item.setData(0, NODE_ROLE, payload)

    def reset_default(self) -> None:
        self._populate([deepcopy(node) for node in resolve(
            load_default(),
            registered=self.model.registered_ids(),
            available=self.model.registered_ids(),
            include_hidden=True,
        ).nodes])

    def show_preview(self) -> None:
        resolved = resolve(
            self.value(),
            registered=self.model.registered_ids(),
            available=self.model.available_ids(),
        )
        self._preview = self.model.build_layout(resolved.nodes, self)
        button = self.preview_button
        self._preview.popup(button.mapToGlobal(button.rect().bottomLeft()))

    # ------------------------------------------------------------ Qt
    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self._normalize_depth()
        self._sync_buttons()

    def _normalize_depth(self) -> None:
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            for child_index in range(top.childCount()):
                child = top.child(child_index)
                while child.childCount():
                    self.tree.addTopLevelItem(child.takeChild(0))
        self.tree.expandAll()
