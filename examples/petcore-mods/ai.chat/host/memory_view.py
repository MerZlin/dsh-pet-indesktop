"""AI 对话的「记忆」页：查看、搜索、编辑、删除、置顶、恢复与外部同步状态。

这是受信 UI（chat_view），只通过 ``HostPorts.memory`` 读写；不直接碰记忆文件。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

SCOPES = (("pet", "AI 对话"), ("dsh", "DSH 对话"), ("qq", "QQ"), ("shared", "共享"))
FILTERS = (
    ("", "全部"),
    ("pet", "AI 对话"),
    ("dsh", "DSH 对话"),
    ("qq", "QQ"),
    ("shared", "共享"),
    ("__superseded__", "已更新"),
    ("__deleted__", "已删除"),
    ("__reply__", "桌宠自述"),
    ("__external__", "外部记忆"),
)
MUTED_FG = QColor("#9aa3b4")


def _bigrams(text: str) -> set[str]:
    value = "".join(str(text or "").lower().split())
    if not value:
        return set()
    if len(value) == 1:
        return {value}
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _match_score(query: str, text: str) -> int:
    wanted = str(query or "").strip().lower()
    if not wanted:
        return 0
    if wanted in str(text or "").lower():
        return 100
    terms = _bigrams(wanted)
    return len(terms & _bigrams(text))


def _scope_label(entry: dict) -> str:
    if entry.get("origin"):
        source = str(entry.get("source") or "")
        if source.startswith("meow:"):
            return "meow-memory（只读）"
        return "DSH 记忆（只读）"
    if entry.get("kind") == "reply":
        return "桌宠自述"
    return {
        "pet": "AI 对话",
        "dsh": "DSH 对话",
        "qq": "QQ",
        "shared": "共享",
    }.get(str(entry.get("scope") or ""), "其他")


def _stamp(value: object) -> str:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(number))
    except (OSError, ValueError):
        return ""


class EntryDialog(QDialog):
    def __init__(self, parent=None, *, entry: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑记忆" if entry else "新增记忆")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        layout.addWidget(QLabel("内容", self))
        self.text = QPlainTextEdit(self)
        self.text.setPlaceholderText("一句话记清楚：谁、什么时候、喜欢/不喜欢什么")
        self.text.setMaximumHeight(130)
        layout.addWidget(self.text)

        row = QHBoxLayout()
        row.addWidget(QLabel("重要度", self))
        self.importance = QSpinBox(self)
        self.importance.setRange(1, 100)
        self.importance.setValue(60)
        row.addWidget(self.importance)
        row.addSpacing(12)
        row.addWidget(QLabel("范围", self))
        self.scope = QComboBox(self)
        for key, label in SCOPES:
            self.scope.addItem(label, key)
        row.addWidget(self.scope)
        row.addStretch(1)
        layout.addLayout(row)

        self.pinned = QCheckBox("置顶（优先注入）", self)
        layout.addWidget(self.pinned)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if entry:
            self.text.setPlainText(str(entry.get("text") or ""))
            self.importance.setValue(int(entry.get("importance") or 60))
            index = self.scope.findData(str(entry.get("scope") or "pet"))
            if index >= 0:
                self.scope.setCurrentIndex(index)
            self.pinned.setChecked(bool(entry.get("pinned")))

    def values(self) -> dict:
        return {
            "text": self.text.toPlainText().strip(),
            "importance": int(self.importance.value()),
            "scope": str(self.scope.currentData() or "pet"),
            "pinned": bool(self.pinned.isChecked()),
        }


class MemoryView(QWidget):
    def __init__(self, context, parent=None) -> None:
        super().__init__(parent)
        self.context = context
        self._pet_slot = 0
        self._dark = False
        self._entries: list[dict] = []
        self._sync_tries = 0
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(400)
        self._sync_timer.timeout.connect(self._poll_sync)
        self.setObjectName("memory-view")
        self._build()
        self._reload()

    # ---------------------------------------------------------------- 端口

    def _memory(self):
        ports = getattr(self.context, "ports", None)
        return getattr(ports, "memory", None) if ports is not None else None

    def _config(self):
        ports = getattr(self.context, "ports", None)
        return getattr(ports, "config", None) if ports is not None else None

    def _config_int(self, key: str, default: int) -> int:
        config = self._config()
        getter = getattr(config, "get", None)
        if not callable(getter):
            return default
        try:
            return int(getter(key, default) or default)
        except Exception:
            return default

    def _supported(self) -> bool:
        memory = self._memory()
        return memory is not None and callable(getattr(memory, "entries", None))

    # ---------------------------------------------------------------- 构建

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(9)

        header = QHBoxLayout()
        self.title = QLabel("长期记忆", self)
        self.title.setObjectName("memory-title")
        header.addWidget(self.title)
        header.addStretch(1)
        self.summary = QLabel("", self)
        self.summary.setObjectName("memory-summary")
        header.addWidget(self.summary)
        outer.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(7)
        self.search = QLineEdit(self)
        self.search.setObjectName("memory-search")
        self.search.setPlaceholderText("搜记忆，或输入问题预览本轮注入")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._reload)
        toolbar.addWidget(self.search, 1)
        self.scope_combo = QComboBox(self)
        self.scope_combo.setObjectName("memory-scope")
        for key, label in FILTERS:
            self.scope_combo.addItem(label, key)
        self.scope_combo.currentIndexChanged.connect(self._reload)
        toolbar.addWidget(self.scope_combo)
        self.pinned_only = QCheckBox("只看置顶", self)
        self.pinned_only.setObjectName("memory-pinned")
        self.pinned_only.toggled.connect(self._reload)
        toolbar.addWidget(self.pinned_only)
        self.refresh_button = QPushButton("刷新", self)
        self.refresh_button.setObjectName("memory-action")
        self.refresh_button.clicked.connect(self._reload)
        toolbar.addWidget(self.refresh_button)
        outer.addLayout(toolbar)

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("memory-list")
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["记忆内容", "来源", "更新时间"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setColumnWidth(0, 520)
        self.tree.setColumnWidth(1, 130)
        self.tree.setColumnWidth(2, 110)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.tree.itemSelectionChanged.connect(self._sync_actions)
        self.tree.itemDoubleClicked.connect(lambda *_args: self._edit())
        outer.addWidget(self.tree, 1)

        self.empty = QLabel("", self)
        self.empty.setObjectName("memory-empty")
        self.empty.setWordWrap(True)
        self.empty.hide()
        outer.addWidget(self.empty)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.add_button = QPushButton("新增", self)
        self.edit_button = QPushButton("编辑", self)
        self.pin_button = QPushButton("置顶", self)
        self.delete_button = QPushButton("删除", self)
        self.restore_button = QPushButton("恢复", self)
        self.tidy_button = QPushButton("整理", self)
        self.sync_button = QPushButton("同步 DSH", self)
        self.dir_button = QPushButton("目录", self)
        for button in (
            self.add_button,
            self.edit_button,
            self.pin_button,
            self.delete_button,
            self.restore_button,
            self.tidy_button,
            self.sync_button,
            self.dir_button,
        ):
            button.setObjectName("memory-action")
            actions.addWidget(button)
        actions.addStretch(1)
        outer.addLayout(actions)
        self.add_button.clicked.connect(self._add)
        self.edit_button.clicked.connect(self._edit)
        self.pin_button.clicked.connect(self._toggle_pin)
        self.delete_button.clicked.connect(self._delete)
        self.restore_button.clicked.connect(self._restore)
        self.tidy_button.clicked.connect(self._tidy)
        self.sync_button.clicked.connect(self._sync)
        self.dir_button.clicked.connect(self._open_dir)

        self.preview_toggle = QPushButton("本轮注入预览", self)
        self.preview_toggle.setObjectName("memory-action")
        self.preview_toggle.setCheckable(True)
        self.preview_toggle.toggled.connect(self._toggle_preview)
        outer.addWidget(self.preview_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.preview = QPlainTextEdit(self)
        self.preview.setObjectName("memory-preview")
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(180)
        self.preview.hide()
        outer.addWidget(self.preview)

        self.source_status = QLabel("", self)
        self.source_status.setObjectName("memory-source")
        self.source_status.setWordWrap(True)
        outer.addWidget(self.source_status)

    # ---------------------------------------------------------------- 数据

    def _reload(self, *_args) -> None:
        if not hasattr(self, "tree"):
            return
        memory = self._memory()
        if not self._supported():
            self._entries = []
            self._render_rows([])
            self.summary.setText("")
            self.empty.setText("当前 Core 版本还不支持记忆管理（缺少 MemoryPort.entries）。")
            self.empty.show()
            self._sync_actions()
            return
        try:
            self._entries = list(
                memory.entries(
                    include_superseded=True,
                    include_replies=True,
                    pet_slot=self._pet_slot,
                )
            )
        except Exception:
            self._entries = []
        self._refresh_summary()
        self._render_rows(self._filtered())
        self._refresh_sources()
        if self.preview_toggle.isChecked():
            self._refresh_preview()

    def _refresh_summary(self) -> None:
        memory = self._memory()
        try:
            stats = memory.stats(pet_slot=self._pet_slot) if memory else {}
        except Exception:
            stats = {}
        if not isinstance(stats, dict):
            stats = {}
        character = str(stats.get("character") or "")
        parts = [
            f"共 {int(stats.get('entries') or 0)} 条",
            f"生效 {int(stats.get('active') or 0)}",
            f"置顶 {int(stats.get('pinned') or 0)}",
            f"已更新 {int(stats.get('superseded') or 0)}",
            f"自述 {int(stats.get('replies') or 0)}",
            f"外部 {int(stats.get('external') or 0)}",
        ]
        self.title.setText(f"长期记忆 · {character}" if character else "长期记忆")
        self.summary.setText(" · ".join(parts))

    def _filtered(self) -> list[dict]:
        scope = str(self.scope_combo.currentData() or "")
        query = self.search.text().strip()
        pinned_only = self.pinned_only.isChecked()
        items: list[dict] = []
        for entry in self._entries:
            superseded = str(entry.get("superseded_by") or "")
            if scope == "__superseded__" and not superseded:
                continue
            if scope == "__deleted__" and superseded != "deleted":
                continue
            if scope == "__reply__" and str(entry.get("kind") or "") != "reply":
                continue
            if scope == "__external__" and not entry.get("origin"):
                continue
            if scope and not scope.startswith("__") and entry.get("scope") != scope:
                continue
            if pinned_only and not entry.get("pinned"):
                continue
            if query and _match_score(query, str(entry.get("text") or "")) <= 0:
                continue
            items.append(entry)
        if query:
            items.sort(
                key=lambda entry: _match_score(query, str(entry.get("text") or "")),
                reverse=True,
            )
        else:
            items.sort(
                key=lambda entry: (
                    bool(entry.get("pinned")),
                    int(entry.get("importance") or 0),
                    float(entry.get("updated_at") or entry.get("created_at") or 0.0),
                ),
                reverse=True,
            )
        return items

    def _render_rows(self, items: list[dict]) -> None:
        self.tree.clear()
        for entry in items:
            text = str(entry.get("text") or "").replace("\n", " ")
            label = _scope_label(entry)
            if entry.get("pinned"):
                label = "置顶 · " + label
            if entry.get("superseded_by") == "deleted":
                label = "已删除 · " + label
            elif entry.get("superseded_by"):
                label = "已被更新 · " + label
            stamp = _stamp(entry.get("updated_at") or entry.get("created_at"))
            row = QTreeWidgetItem([text, label, stamp])
            row.setData(0, Qt.ItemDataRole.UserRole, str(entry.get("id") or ""))
            row.setToolTip(0, str(entry.get("text") or ""))
            if entry.get("superseded_by") or entry.get("kind") == "reply":
                for column in (0, 1, 2):
                    row.setForeground(column, MUTED_FG)
            self.tree.addTopLevelItem(row)
        has_rows = bool(items)
        self.empty.setText(
            "这个范围还没有记忆。可以在聊天里说「记住…」，或点「新增」。"
            if not has_rows
            else ""
        )
        self.empty.setVisible(not has_rows)
        self.tree.setVisible(has_rows)
        self._sync_actions()

    def _refresh_sources(self) -> None:
        memory = self._memory()
        status = {}
        if memory is not None:
            getter = getattr(memory, "external_status", None)
            if callable(getter):
                try:
                    status = getter(pet_slot=self._pet_slot) or {}
                except Exception:
                    status = {}
        sources = status.get("sources") if isinstance(status, dict) else None
        enabled = bool(status.get("enabled", True)) if isinstance(status, dict) else True
        if not isinstance(sources, list) or not sources:
            self.source_status.setText(
                "DSH 记忆：未检测到（安装 dsh.memory 或 meow-memory 后自动同步）"
                if enabled
                else "DSH 记忆：同步已关闭"
            )
            return
        parts = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            label = str(source.get("label") or source.get("key") or "DSH")
            state = str(source.get("status") or "unknown")
            count = int(source.get("count") or 0)
            if state == "ok":
                when = _stamp(source.get("last_sync"))
                parts.append(f"{label}：已同步 {count} 条" + (f" · {when}" if when else ""))
            elif state == "not_found":
                parts.append(f"{label}：未找到")
            else:
                reason = str(source.get("reason") or "读取失败")
                parts.append(f"{label}：{reason}")
        self.source_status.setText("DSH 记忆　" + "　|　".join(parts))

    def _refresh_preview(self) -> None:
        memory = self._memory()
        if memory is None:
            self.preview.setPlainText("记忆端口不可用。")
            return
        render = getattr(memory, "render_prompt", None)
        if not callable(render):
            self.preview.setPlainText("当前 Core 版本不支持注入预览。")
            return
        try:
            text = render(
                "pet",
                self.search.text().strip(),
                max_chars=2200,
                limit=self._config_int("ai_memory_inject", 3),
                pet_slot=self._pet_slot,
            )
        except Exception:
            text = ""
        self.preview.setPlainText(
            str(text).strip() or "（这次没有可注入的记忆）"
        )

    # ---------------------------------------------------------------- 选择/操作

    def _selected(self) -> dict | None:
        row = self.tree.currentItem()
        if row is None:
            return None
        entry_id = str(row.data(0, Qt.ItemDataRole.UserRole) or "")
        return next(
            (entry for entry in self._entries if str(entry.get("id") or "") == entry_id),
            None,
        )

    def _sync_actions(self) -> None:
        if not hasattr(self, "edit_button"):
            return
        entry = self._selected() if hasattr(self, "tree") else None
        external = bool(entry and entry.get("origin"))
        restorable = bool(
            entry and (entry.get("superseded_by") or entry.get("kind") == "reply")
        )
        self.edit_button.setEnabled(bool(entry) and not external)
        self.delete_button.setEnabled(bool(entry))
        self.restore_button.setEnabled(restorable)
        self.pin_button.setEnabled(bool(entry) and not external)
        self.pin_button.setText(
            "取消置顶" if entry and entry.get("pinned") else "置顶"
        )

    def _add(self) -> None:
        memory = self._memory()
        if memory is None:
            return
        dialog = EntryDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["text"]:
            QMessageBox.information(self, "新增记忆", "内容不能为空。")
            return
        remember = getattr(memory, "remember", None)
        if not callable(remember):
            return
        try:
            remember(
                values["text"],
                scope=values["scope"],
                source="user",
                kind="explicit",
                importance=values["importance"],
                pet_slot=self._pet_slot,
                pinned=values["pinned"],
            )
        except Exception:
            QMessageBox.warning(self, "新增记忆", "写入失败，详见日志。")
            return
        self._reload()

    def _edit(self) -> None:
        entry = self._selected()
        memory = self._memory()
        if entry is None or memory is None or entry.get("origin"):
            return
        dialog = EntryDialog(self, entry=entry)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not values["text"]:
            QMessageBox.information(self, "编辑记忆", "内容不能为空。")
            return
        update = getattr(memory, "update_entry", None)
        if not callable(update):
            return
        try:
            update(
                str(entry.get("id") or ""),
                text=values["text"],
                importance=values["importance"],
                pinned=values["pinned"],
                scope=values["scope"],
                pet_slot=self._pet_slot,
            )
        except Exception:
            QMessageBox.warning(self, "编辑记忆", "保存失败，详见日志。")
            return
        self._reload()

    def _toggle_pin(self) -> None:
        entry = self._selected()
        memory = self._memory()
        if entry is None or memory is None or entry.get("origin"):
            return
        update = getattr(memory, "update_entry", None)
        if not callable(update):
            return
        try:
            update(
                str(entry.get("id") or ""),
                pinned=not bool(entry.get("pinned")),
                pet_slot=self._pet_slot,
            )
        except Exception:
            return
        self._reload()

    def _delete(self) -> None:
        entry = self._selected()
        memory = self._memory()
        if entry is None or memory is None:
            return
        external = bool(entry.get("origin"))
        question = (
            "只从本地隐藏这条 DSH 记忆？不会删除 DSH 里的原件。"
            if external
            else "删除这条记忆？会保留在归档里，可随时恢复。"
        )
        if (
            QMessageBox.question(
                self,
                "删除记忆",
                question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        delete = getattr(memory, "delete_entry", None)
        if not callable(delete):
            return
        try:
            delete(str(entry.get("id") or ""), pet_slot=self._pet_slot)
        except Exception:
            QMessageBox.warning(self, "删除记忆", "删除失败，详见日志。")
            return
        self._reload()

    def _restore(self) -> None:
        entry = self._selected()
        memory = self._memory()
        if entry is None or memory is None:
            return
        if not entry.get("superseded_by") and entry.get("kind") != "reply":
            return
        restore = getattr(memory, "restore_entry", None)
        if not callable(restore):
            return
        try:
            restore(str(entry.get("id") or ""), pet_slot=self._pet_slot)
        except Exception:
            return
        self._reload()

    def _tidy(self) -> None:
        memory = self._memory()
        tidy = getattr(memory, "tidy", None) if memory is not None else None
        if not callable(tidy):
            return
        try:
            result = tidy(pet_slot=self._pet_slot) or {}
        except Exception:
            result = {}
        QMessageBox.information(
            self,
            "整理记忆",
            "合并 {merged} 条、覆盖 {superseded} 条。被覆盖的旧记忆可在「已更新」里恢复。".format(
                merged=int(result.get("merged") or 0),
                superseded=int(result.get("superseded") or 0),
            ),
        )
        self._reload()

    def _sync(self) -> None:
        memory = self._memory()
        sync = getattr(memory, "sync_external", None) if memory is not None else None
        if not callable(sync):
            return
        try:
            started = bool(sync(pet_slot=self._pet_slot, force=True))
        except Exception:
            started = False
        if not started:
            self._refresh_sources()
            return
        self._sync_tries = 0
        self.sync_button.setEnabled(False)
        self._sync_timer.start()

    def _poll_sync(self) -> None:
        self._sync_tries += 1
        self._refresh_sources()
        if self._sync_tries >= 10:
            self._sync_timer.stop()
            self.sync_button.setEnabled(True)
            self._reload()
            return
        # 同步在主线程应用后状态才会变化；轮询到状态更新就刷新列表。
        self._reload()

    def _open_dir(self) -> None:
        memory = self._memory()
        if memory is None:
            return
        stats = {}
        getter = getattr(memory, "stats", None)
        if callable(getter):
            try:
                stats = getter(pet_slot=self._pet_slot) or {}
            except Exception:
                stats = {}
        path = str(stats.get("path") or "") if isinstance(stats, dict) else ""
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _toggle_preview(self, checked: bool) -> None:
        self.preview.setVisible(bool(checked))
        if checked:
            self._refresh_preview()

    # ---------------------------------------------------------------- 生命周期

    def set_pet_slot(self, slot: int) -> None:
        self._pet_slot = max(0, int(slot or 0))
        self._reload()

    def activate(self) -> None:
        self._reload()

    def deactivate(self) -> None:
        self._sync_timer.stop()

    def set_dark(self, dark: bool) -> None:
        self._dark = bool(dark)

    def shutdown(self) -> None:
        self._sync_timer.stop()
