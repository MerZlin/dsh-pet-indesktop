# -*- coding: utf-8 -*-
"""Modern settings 菜单布局编辑器 MenuLayoutEditor。

从 modern_settings_dialog.py 纯机械搬移。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QSplitter,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .context_menus.icons import (
    custom_icon_file_error,
    vector_widget_icon,
)
from .context_menus.registry import CUSTOM_ICON_CHOICES, MENU_ACTIONS
from .menu_layout import (
    load_default_menu_layout,
    materialize_implicit_separators,
    merge_default_menu_actions,
    resolve_menu_layout,
)
from .settings_widgets import (
    SettingsMenuButton,
    configure_settings_action_popup,
    SettingsPopupMenu,
    IMAGE_NAME_FILTER,
)


class MenuLayoutEditor(QWidget):
    """Draft tree editor with a preview derived from the same nodes."""

    changed = Signal()

    def __init__(
        self, layout: dict | None, parent=None, *,
        available_actions=None, enabled_actions=None,
    ):
        super().__init__(parent)
        self.available_actions = frozenset(available_actions or MENU_ACTIONS.ids)
        self.enabled_actions = frozenset(
            enabled_actions if enabled_actions is not None else self.available_actions
        )
        self.tree = QTreeWidget(self)
        self.tree.setObjectName("menuLayoutTree")
        self.tree.setHeaderLabels(["菜单项", "状态", "位置"])
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(72)
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 92)
        self.tree.setColumnWidth(2, 92)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(18)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setAccessibleName("右键菜单内容与布局")
        self.editor_label = QLabel("菜单结构", self)
        self.editor_label.setObjectName("menuLayoutEditorLabel")
        self.editor_hint = QLabel("拖动表头分隔线调整列宽", self)
        self.editor_hint.setObjectName("menuLayoutEditorHint")
        editor_panel = QFrame(self)
        editor_panel.setObjectName("menuLayoutEditorPanel")
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(10, 10, 10, 10)
        editor_layout.setSpacing(6)
        editor_heading = QHBoxLayout()
        editor_heading.addWidget(self.editor_label)
        editor_heading.addStretch(1)
        editor_heading.addWidget(self.editor_hint)
        editor_layout.addLayout(editor_heading)
        editor_layout.addWidget(self.tree)
        self.preview = QTreeWidget(self)
        self.preview.setObjectName("menuLayoutPreview")
        self.preview.setHeaderHidden(True)
        self.preview.setUniformRowHeights(True)
        self.preview.setIndentation(18)
        self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preview.setAccessibleName("右键菜单实时预览")
        self.preview_label = QLabel("实时菜单预览", self)
        self.preview_label.setObjectName("menuLayoutPreviewLabel")
        preview_panel = QFrame(self)
        preview_panel.setObjectName("menuLayoutPreviewPanel")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        preview_layout.setSpacing(6)
        preview_layout.addWidget(self.preview_label)
        preview_layout.addWidget(self.preview)

        self.order_button = SettingsMenuButton("排序", self)
        self.order_button.setIcon(vector_widget_icon(self, "edit", 14))
        self.order_menu = configure_settings_action_popup(SettingsPopupMenu(self.order_button))
        self.move_up_action = self.order_menu.addAction("上移")
        self.move_down_action = self.order_menu.addAction("下移")
        self.move_up_action.triggered.connect(lambda: self._move_selected(-1))
        self.move_down_action.triggered.connect(lambda: self._move_selected(1))
        self.order_button.setPopupMenu(self.order_menu)

        self.move_button = SettingsMenuButton("移动到", self)
        self.move_button.setIcon(vector_widget_icon(self, "multi_select", 14))
        self.move_menu = configure_settings_action_popup(SettingsPopupMenu(self.move_button))
        self.move_menu.aboutToShow.connect(self._rebuild_move_menu)
        self.move_button.setPopupMenu(self.move_menu)

        self.submenu_button = SettingsMenuButton("插入", self)
        self.submenu_button.setIcon(vector_widget_icon(self, "add", 14))
        self.submenu_menu = configure_settings_action_popup(SettingsPopupMenu(self.submenu_button))
        self.new_submenu_action = self.submenu_menu.addAction("新建子菜单…")
        self.insert_separator_action = self.submenu_menu.addAction("插入分割线")
        self.new_submenu_action.triggered.connect(self._create_submenu)
        self.insert_separator_action.triggered.connect(self._insert_separator_after_selected)
        self.submenu_button.setPopupMenu(self.submenu_menu)

        self.customize_button = SettingsMenuButton("自定义", self)
        self.customize_button.setIcon(vector_widget_icon(self, "edit", 14))
        self.customize_menu = configure_settings_action_popup(SettingsPopupMenu(self.customize_button))
        self.rename_action = self.customize_menu.addAction("更换别名…")
        self.change_icon_action = self.customize_menu.addAction("选择内置图标…")
        self.choose_icon_file_action = self.customize_menu.addAction("选择图片文件…（最大 5 MB）")
        self.choose_icon_file_action.setToolTip(
            "支持 PNG、JPG、WebP、BMP、GIF、TIFF；作为静态方形菜单图标显示"
        )
        self.icon_display_menu = configure_settings_action_popup(SettingsPopupMenu("图片显示方式", self.customize_menu))
        self.icon_contain_action = self.icon_display_menu.addAction("完整显示")
        self.icon_cover_action = self.icon_display_menu.addAction("裁切填满")
        self.icon_contain_action.setCheckable(True)
        self.icon_cover_action.setCheckable(True)
        self.customize_menu.addMenu(self.icon_display_menu)
        self.restore_presentation_action = self.customize_menu.addAction("恢复默认名称与图标")
        self.rename_action.triggered.connect(self._rename_selected)
        self.change_icon_action.triggered.connect(self._change_selected_icon)
        self.choose_icon_file_action.triggered.connect(self._choose_selected_file_icon)
        self.icon_contain_action.triggered.connect(
            lambda: self._set_selected_file_display("contain")
        )
        self.icon_cover_action.triggered.connect(
            lambda: self._set_selected_file_display("cover")
        )
        self.restore_presentation_action.triggered.connect(self._restore_selected_presentation)
        self.customize_button.setPopupMenu(self.customize_menu)

        self.more_button = SettingsMenuButton("更多", self)
        self.more_button.setIcon(vector_widget_icon(self, "more", 14))
        self.more_menu = configure_settings_action_popup(SettingsPopupMenu(self.more_button))
        self.delete_submenu_action = self.more_menu.addAction("删除所选子菜单…")
        self.delete_separator_action = self.more_menu.addAction("删除所选分割线")
        self.more_menu.addSeparator()
        self.reset_action = self.more_menu.addAction("恢复默认布局")
        self.delete_submenu_action.triggered.connect(self._delete_selected_submenu)
        self.delete_separator_action.triggered.connect(self._delete_selected_separator)
        self.reset_action.triggered.connect(self.reset_default)
        self.delete_submenu_action.setEnabled(False)
        self.delete_separator_action.setEnabled(False)
        self.more_button.setPopupMenu(self.more_menu)

        toolbar = QWidget(self)
        toolbar.setObjectName("menuEditorToolbar")
        self.toolbar = toolbar
        self.toolbar_layout = QGridLayout(toolbar)
        self.toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar_layout.setHorizontalSpacing(7)
        self.toolbar_layout.setVerticalSpacing(7)
        self.toolbar_buttons = (
            self.order_button, self.move_button,
            self.submenu_button, self.customize_button, self.more_button,
        )
        self._toolbar_mode = None

        self.split = QSplitter(Qt.Orientation.Horizontal, self)
        self.split.setObjectName("menuEditorSplit")
        self.split.addWidget(editor_panel)
        self.split.addWidget(preview_panel)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        box.addWidget(toolbar)
        box.addWidget(self.split, 1)

        self._preview_refresh_pending = False
        self._pending_empty_submenus: list[QTreeWidgetItem] = []
        self.tree.itemChanged.connect(self._on_changed)
        tree_model = self.tree.model()
        tree_model.rowsMoved.connect(self._schedule_preview_refresh)
        tree_model.rowsInserted.connect(self._schedule_preview_refresh)
        tree_model.rowsRemoved.connect(self._on_tree_rows_removed)
        tree_model.modelReset.connect(self._schedule_preview_refresh)
        self.tree.currentItemChanged.connect(self._sync_command_state)
        self.set_layout(layout or load_default_menu_layout())
        self._update_layout_mode()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_layout_mode()

    def _update_layout_mode(self) -> None:
        if hasattr(self, "split"):
            mode = "wide" if self.width() >= 760 else ("medium" if self.width() >= 600 else "compact")
            compact = mode == "compact"
            if self.property("layoutMode") != mode:
                self.setProperty("layoutMode", mode)
                self.style().unpolish(self)
                self.style().polish(self)
            self._reflow_toolbar(mode)
            self.editor_hint.setVisible(not compact)
            self.tree.setColumnHidden(2, compact)
            self.split.setOrientation(
                Qt.Orientation.Horizontal if mode == "wide" else Qt.Orientation.Vertical
            )
            window_height = self.window().height()
            preferred = (
                min(760, max(540, window_height - 160))
                if mode != "wide"
                else min(620, max(360, window_height - 360))
            )
            self.setMinimumHeight(preferred)

    def _reflow_toolbar(self, mode: str) -> None:
        if self._toolbar_mode == mode:
            return
        self._toolbar_mode = mode
        while self.toolbar_layout.count():
            self.toolbar_layout.takeAt(0)
        columns = {"wide": 5, "medium": 3, "compact": 2}[mode]
        for index, button in enumerate(self.toolbar_buttons):
            if mode == "wide":
                button.setMaximumWidth(132)
                button.setMinimumWidth(104)
                button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            else:
                button.setMaximumWidth(16777215)
                button.setMinimumWidth(0)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.toolbar_layout.addWidget(button, index // columns, index % columns)
        for column in range(columns):
            self.toolbar_layout.setColumnStretch(column, 0 if mode == "wide" else 1)
        self.toolbar_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            if mode == "wide" else Qt.AlignmentFlag.AlignTop
        )

    def _sync_command_state(self, current=None, _previous=None) -> None:
        item = current or self.tree.currentItem()
        parent = item.parent() if item is not None else None
        sibling_parent = parent or self.tree.invisibleRootItem()
        index = sibling_parent.indexOfChild(item) if item is not None else -1
        self.move_up_action.setEnabled(index > 0)
        self.move_down_action.setEnabled(
            item is not None and index < sibling_parent.childCount() - 1
        )
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else {}
        node_type = data.get("type") if data else ""
        self.move_button.setEnabled(node_type == "action")
        self.customize_button.setEnabled(node_type in {"action", "submenu"})
        icon = data.get("icon") if data else None
        self.icon_display_menu.setEnabled(
            node_type in {"action", "submenu"}
            and isinstance(icon, dict)
            and icon.get("kind") == "file"
        )
        display = icon.get("display") if isinstance(icon, dict) else ""
        self.icon_contain_action.setChecked(display == "contain")
        self.icon_cover_action.setChecked(display == "cover")
        self.delete_submenu_action.setEnabled(
            node_type == "submenu"
        )
        self.delete_separator_action.setEnabled(node_type == "separator")

    def _rebuild_move_menu(self) -> None:
        self.move_menu.clear()
        root_action = self.move_menu.addAction("根菜单")
        root_action.setData("__root__")
        root_action.triggered.connect(lambda: self._move_selected_to("__root__"))
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            item = root.child(index)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("type") != "submenu":
                continue
            target_id = str(data.get("id") or "")
            action = self.move_menu.addAction(item.text(0))
            action.setData(target_id)
            action.triggered.connect(
                lambda _checked=False, target_id=target_id: self._move_selected_to(target_id)
            )

    def set_layout(self, layout: dict) -> None:
        layout, _diagnostics = merge_default_menu_actions(
            layout, registered_actions=MENU_ACTIONS.ids
        )
        layout = materialize_implicit_separators(layout)
        self._pending_empty_submenus.clear()
        self.tree.blockSignals(True)
        self.tree.clear()
        for node in layout.get("nodes", []):
            self._append_node(None, node)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._sync_command_state()
        self._on_changed()

    def _schedule_preview_refresh(self, *_args) -> None:
        """Coalesce the remove/insert phases of cross-parent tree moves."""
        if self._preview_refresh_pending:
            return
        self._preview_refresh_pending = True
        QTimer.singleShot(0, self._flush_preview_refresh)

    def _on_tree_rows_removed(self, parent_index, *_args) -> None:
        if parent_index.isValid():
            parent = self.tree.itemFromIndex(parent_index)
            data = parent.data(0, Qt.ItemDataRole.UserRole) if parent is not None else {}
            if data and data.get("type") == "submenu":
                self._pending_empty_submenus.append(parent)
        self._schedule_preview_refresh()

    def _flush_preview_refresh(self) -> None:
        self._preview_refresh_pending = False
        for submenu in self._pending_empty_submenus:
            self._remove_empty_submenu(submenu)
        self._pending_empty_submenus.clear()
        self._on_changed()

    def _append_node(self, parent: QTreeWidgetItem | None, node: dict) -> None:
        node_type = str(node.get("type") or "")
        node_id = str(node.get("id") or "")
        alias = str(node.get("alias") or "").strip()
        original = str(node.get("label") or MENU_ACTIONS.label(node_id))
        label = f"{alias}（{original}）" if alias else original
        if node_type == "separator":
            label = "— 分割线"
        item = QTreeWidgetItem([label, "", ""])
        item.setData(0, Qt.ItemDataRole.UserRole, {
            "type": node_type,
            "id": node_id,
            "section": node.get("section"),
            "label": node.get("label"),
            "alias": alias,
            "icon": node.get("icon") if "icon" in node else None,
        })
        available = node_type != "action" or node_id in self.available_actions
        item.setData(0, Qt.ItemDataRole.UserRole + 1, available)
        enabled = node_type != "action" or node_id in self.enabled_actions
        item.setData(0, Qt.ItemDataRole.UserRole + 2, enabled)
        if node_type != "separator":
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        if node_type == "submenu":
            # Submenus stay at the root: they can receive actions, but cannot be
            # dragged into one another. Their root order is changed with the
            # explicit move buttons.
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDropEnabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        elif node_type == "action":
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
        else:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
        if node_type != "separator":
            item.setCheckState(0, Qt.CheckState.Checked if node.get("visible", True) else Qt.CheckState.Unchecked)
        if node_type == "action" and node_id in {"modern_settings", "quit"}:
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        if not available:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self._sync_item_icon(item)
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in node.get("children", []):
            self._append_node(item, child)

    def item_for_action(self, action_id: str) -> QTreeWidgetItem | None:
        def find(parent):
            for index in range(parent.childCount()):
                item = parent.child(index)
                data = item.data(0, Qt.ItemDataRole.UserRole) or {}
                if data.get("type") == "action" and data.get("id") == action_id:
                    return item
                found = find(item)
                if found is not None:
                    return found
            return None
        return find(self.tree.invisibleRootItem())

    def value(self) -> dict:
        def encode(item: QTreeWidgetItem) -> dict:
            data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
            node_type = data.get("type")
            node = {
                "type": node_type,
                "id": data.get("id"),
                "visible": True if node_type == "separator" else item.checkState(0) == Qt.CheckState.Checked,
            }
            if data.get("section"):
                node["section"] = data["section"]
            if data.get("alias"):
                node["alias"] = str(data["alias"])[:40]
            if data.get("icon") is not None:
                icon = data["icon"]
                node["icon"] = dict(icon) if isinstance(icon, dict) else str(icon)[:40]
            if node_type == "submenu":
                node["label"] = str(data.get("label") or item.text(0)).strip()[:40]
                node["children"] = [encode(item.child(i)) for i in range(item.childCount())]
            return node
        root = self.tree.invisibleRootItem()
        return {"schema_version": 1, "layout_id": "user", "nodes": [encode(root.child(i)) for i in range(root.childCount())]}

    def set_enabled_actions(self, enabled_actions) -> None:
        """Refresh runtime state styling without mutating the layout tree."""
        self.enabled_actions = frozenset(enabled_actions)
        def refresh(parent) -> None:
            for index in range(parent.childCount()):
                item = parent.child(index)
                data = item.data(0, Qt.ItemDataRole.UserRole) or {}
                enabled = data.get("type") != "action" or data.get("id") in self.enabled_actions
                item.setData(0, Qt.ItemDataRole.UserRole + 2, enabled)
                refresh(item)
        refresh(self.tree.invisibleRootItem())
        self._on_changed()

    def reset_default(self) -> None:
        self.set_layout(load_default_menu_layout())

    def set_item_alias(self, action_id: str, alias: str) -> None:
        item = self.item_for_action(action_id)
        if item is None:
            return
        self._set_item_alias(item, alias)

    def set_item_icon(self, action_id: str, icon_name: str) -> None:
        item = self.item_for_action(action_id)
        if item is None:
            return
        self._set_item_icon(item, icon_name)

    def set_item_file_icon(self, action_id: str, path, display: str = "contain") -> bool:
        item = self.item_for_action(action_id)
        if item is None:
            return False
        return self._set_item_file_icon(item, path, display)

    def _set_item_file_icon(self, item: QTreeWidgetItem, path, display: str) -> bool:
        candidate = Path(path).expanduser().resolve()
        if custom_icon_file_error(candidate):
            return False
        data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
        data["icon"] = {
            "kind": "file",
            "path": str(candidate),
            "display": "cover" if display == "cover" else "contain",
        }
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._sync_item_icon(item)
        self._sync_command_state(item)
        self._on_changed()
        return True

    def insert_separator(self, *, after_action_id: str | None = None) -> None:
        target = self.item_for_action(after_action_id) if after_action_id else self.tree.currentItem()
        parent = target.parent() if target is not None else None
        owner = parent or self.tree.invisibleRootItem()
        index = owner.indexOfChild(target) + 1 if target is not None else owner.childCount()
        existing_ids: set[str] = set()
        def collect_ids(nodes) -> None:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                existing_ids.add(str(node.get("id") or ""))
                collect_ids(node.get("children", []))
        collect_ids(self.value().get("nodes", []))
        number = 1
        while f"user.separator-{number}" in existing_ids:
            number += 1
        item = QTreeWidgetItem()
        self._configure_detached_node(item, {
            "type": "separator", "id": f"user.separator-{number}", "visible": True,
        })
        owner.insertChild(index, item)
        self.tree.setCurrentItem(item)
        self._on_changed()

    def _configure_detached_node(self, item: QTreeWidgetItem, node: dict) -> None:
        """Configure a node before insertion without coupling to tree ownership."""
        node_type = str(node.get("type") or "")
        node_id = str(node.get("id") or "")
        original = str(node.get("label") or MENU_ACTIONS.label(node_id))
        alias = str(node.get("alias") or "").strip()
        label = (
            "— 分割线" if node_type == "separator"
            else f"{alias}（{original}）" if alias else original
        )
        item.setText(0, label)
        item.setData(0, Qt.ItemDataRole.UserRole, {
            "type": node_type, "id": node_id, "section": node.get("section"),
            "label": node.get("label"), "alias": alias,
            "icon": node.get("icon") if "icon" in node else None,
        })
        item.setData(0, Qt.ItemDataRole.UserRole + 1, True)
        item.setData(0, Qt.ItemDataRole.UserRole + 2, True)
        item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)

    def _set_item_alias(self, item: QTreeWidgetItem, alias: str) -> None:
        data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
        alias = str(alias).strip()[:40]
        data["alias"] = alias
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        default = data.get("label") or MENU_ACTIONS.label(str(data.get("id") or ""))
        item.setText(0, f"{alias}（{default}）" if alias else str(default))
        self._on_changed()

    def _set_item_icon(self, item: QTreeWidgetItem, icon_name: str) -> None:
        data = dict(item.data(0, Qt.ItemDataRole.UserRole) or {})
        data["icon"] = None if icon_name == "default" else str(icon_name or "none")
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._sync_item_icon(item)
        self._sync_command_state(item)
        self._on_changed()

    def _sync_item_icon(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("type") == "separator":
            item.setIcon(0, QIcon())
            return
        item.setIcon(0, MENU_ACTIONS.icon(
            self, str(data.get("id") or ""), data.get("icon")
        ))

    def _rename_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        text, accepted = QInputDialog.getText(
            self, "更换菜单别名", "显示名称", text=str(data.get("alias") or "")
        )
        if accepted:
            self._set_item_alias(item, text)

    def _change_selected_icon(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        labels = [label for label, _value in CUSTOM_ICON_CHOICES]
        selected, accepted = QInputDialog.getItem(self, "更换菜单图标", "图标", labels, 0, False)
        if accepted:
            value = next(value for label, value in CUSTOM_ICON_CHOICES if label == selected)
            self._set_item_icon(item, value)

    def _choose_selected_file_icon(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择菜单图标",
            str(Path.home()),
            IMAGE_NAME_FILTER,
        )
        if not selected:
            return
        error = custom_icon_file_error(selected)
        if error:
            QMessageBox.warning(self, "无法使用此图标", error)
            return
        self._set_item_file_icon(item, selected, "contain")

    def _set_selected_file_display(self, display: str) -> None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else {}
        icon = data.get("icon") if data else None
        if not isinstance(icon, dict) or icon.get("kind") != "file":
            return
        self._set_item_file_icon(item, icon.get("path") or "", display)

    def _restore_selected_presentation(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        self._set_item_alias(item, "")
        self._set_item_icon(item, "default")

    def _insert_separator_after_selected(self) -> None:
        self.insert_separator()

    def _delete_selected_separator(self) -> None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else {}
        if item is None or not data or data.get("type") != "separator":
            return
        parent = item.parent() or self.tree.invisibleRootItem()
        parent.removeChild(item)
        self._on_changed()

    def _move_selected(self, offset: int) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        parent = item.parent() or self.tree.invisibleRootItem()
        index = parent.indexOfChild(item)
        target = index + offset
        if 0 <= target < parent.childCount():
            parent.takeChild(index)
            parent.insertChild(target, item)
            self.tree.setCurrentItem(item)
            self._on_changed()

    def _promote_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.parent() is None:
            return
        old_parent = item.parent()
        old_parent.removeChild(item)
        self.tree.addTopLevelItem(item)
        self._remove_empty_submenu(old_parent)
        self.tree.setCurrentItem(item)
        self._on_changed()

    def _remove_empty_submenu(self, item: QTreeWidgetItem | None) -> bool:
        if item is None or item.childCount() != 0:
            return False
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("type") != "submenu" or item.parent() is not None:
            return False
        root = self.tree.invisibleRootItem()
        index = root.indexOfChild(item)
        if index < 0:
            return False
        root.takeChild(index)
        return True

    def _move_selected_to(self, target_id: str) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        item_data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if target_id == "__root__":
            self._promote_selected()
            return
        if item_data.get("type") == "submenu":
            return
        target = None
        root = self.tree.invisibleRootItem()
        def find(parent):
            nonlocal target
            for index in range(parent.childCount()):
                candidate = parent.child(index)
                data = candidate.data(0, Qt.ItemDataRole.UserRole) or {}
                if data.get("type") == "submenu" and data.get("id") == target_id:
                    target = candidate
                    return
                find(candidate)
        find(root)
        if target is None or target is item:
            return
        parent = item.parent() or root
        parent.removeChild(item)
        target.addChild(item)
        if parent is not root:
            self._remove_empty_submenu(parent)
        target.setExpanded(True)
        self.tree.setCurrentItem(item)
        self._on_changed()

    def _create_submenu(self) -> None:
        label, accepted = QInputDialog.getText(self, "新建子菜单", "子菜单名称")
        label = label.strip()
        if not accepted or not label:
            return
        existing = {
            str((self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) or {}).get("id") or "")
            for i in range(self.tree.topLevelItemCount())
        }
        index = 1
        while f"user.submenu-{index}" in existing:
            index += 1
        self._append_node(None, {
            "type": "submenu",
            "id": f"user.submenu-{index}",
            "label": label[:40],
            "visible": True,
            "children": [],
        })
        self._on_changed()

    def _delete_selected_submenu(self) -> None:
        item = self.tree.currentItem()
        data = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else {}
        if item is None or not data or data.get("type") != "submenu":
            return
        answer = QMessageBox.question(
            self,
            "删除子菜单",
            f"确定删除“{item.text(0)}”吗？\n其中的菜单项会保留并移到根菜单。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        root = self.tree.invisibleRootItem()
        index = root.indexOfChild(item)
        children = [item.takeChild(0) for _ in range(item.childCount())]
        root.takeChild(index)
        for offset, child in enumerate(children):
            root.insertChild(index + offset, child)
        if children:
            self.tree.setCurrentItem(children[0])
        elif root.childCount():
            self.tree.setCurrentItem(root.child(min(index, root.childCount() - 1)))
        self._on_changed()

    def _on_changed(self, *_args) -> None:
        self.tree.blockSignals(True)
        self.preview.clear()
        def refresh_positions(source):
            for index in range(source.childCount()):
                item = source.child(index)
                data = item.data(0, Qt.ItemDataRole.UserRole) or {}
                if data.get("type") == "separator":
                    item.setText(1, "布局")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                elif item.data(0, Qt.ItemDataRole.UserRole + 1) is False:
                    item.setText(1, "此平台不可用")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                elif item.checkState(0) != Qt.CheckState.Checked:
                    item.setText(1, "已隐藏")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                elif item.data(0, Qt.ItemDataRole.UserRole + 2) is False:
                    item.setText(1, "已停用")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                else:
                    item.setText(1, "已启用")
                    item.setText(2, "根菜单" if item.parent() is None else item.parent().text(0))
                item.setToolTip(2, item.text(2))
                refresh_positions(item)
        refresh_positions(self.tree.invisibleRootItem())
        resolved = resolve_menu_layout(
            self.value(),
            registered_actions=MENU_ACTIONS.ids,
            available_actions=self.available_actions,
        )
        def add_preview(nodes, target):
            for node in nodes:
                if node.get("type") == "separator":
                    clone = QTreeWidgetItem(["────────"])
                    target.addChild(clone)
                    continue
                action_id = str(node.get("id") or "")
                label = str(node.get("alias") or node.get("label") or MENU_ACTIONS.label(action_id))
                clone = QTreeWidgetItem([label])
                icon = MENU_ACTIONS.icon(self, action_id, node.get("icon"))
                if not icon.isNull():
                    clone.setIcon(0, icon)
                if node.get("type") == "action" and action_id not in self.enabled_actions:
                    clone.setFlags(clone.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    clone.setToolTip(0, MENU_ACTIONS.disabled_reason(action_id))
                target.addChild(clone)
                add_preview(node.get("children", ()), clone)
        add_preview(resolved.nodes, self.preview.invisibleRootItem())
        self.preview.expandAll()
        self.tree.blockSignals(False)
        self.changed.emit()
