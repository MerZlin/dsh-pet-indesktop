# -*- coding: utf-8 -*-
"""待办管理面板：右键菜单「待办提醒」打开的非模态对话框。

视觉上沿用 Shared UX Contract 令牌（references/visual-system.md）：卡片
12px 圆角 1px 边框、字段/按钮 7px 圆角、13px 正文 / 12px 提示、强调色
#0a84ff，明暗跟随系统调色板亮度。面板只管理待办条目（CRUD 即时落盘，
经 TodoReminderService.set_items）；提醒开关与提前量单一归属在设置页
「自动化与联动」，面板仅以提示文案深链，不复制偏好控件。
"""
from __future__ import annotations

from datetime import date, datetime
import uuid

from PySide6.QtCore import QDate, Qt, QTime
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QDateEdit,
    QVBoxLayout,
    QWidget,
)

from .todo_reminder import new_todo_item, summarize_next


def _stylesheet(widget: QWidget) -> str:
    dark = widget.palette().color(QPalette.ColorRole.Window).lightness() < 128
    if dark:
        window, card, border, divider = "#202024", "#2a2a30", "#3a3a42", "#33333a"
        text, hint, hover = "#e0e0e6", "#9a9aa3", "#303036"
    else:
        window, card, border, divider = "#fcfcfd", "#ffffff", "#e2e4e8", "#eceef1"
        text, hint, hover = "#252525", "#777777", "#f4f5f6"
    accent = "#0a84ff"
    return f"""
    QDialog, QWidget#todoRowsHost {{
        background: {window};
        color: {text};
        font-size: 13px;
    }}
    QLabel#pageTitle {{
        font-size: 22px;
        font-weight: 600;
        color: {text};
        background: transparent;
    }}
    QLabel#todoNextLabel, QLabel#todoHintLabel, QLabel#todoEmptyLabel {{
        color: {hint};
        font-size: 12px;
        background: transparent;
    }}
    QLabel[muted="true"] {{
        color: {hint};
    }}
    QFrame#todoEditorCard, QFrame#todoListCard {{
        background: {card};
        border: 1px solid {border};
        border-radius: 12px;
    }}
    QLabel#todoAgentTitle {{
        color: {text};
        font-size: 20px;
        font-weight: 600;
        background: transparent;
    }}
    QLabel#todoAgentHint {{
        color: {hint};
        font-size: 12px;
        background: transparent;
    }}
    QFrame[divider="true"] {{
        background: {divider};
        border: none;
        max-height: 1px;
    }}
    QLineEdit, QComboBox, QTimeEdit, QDateEdit, QPlainTextEdit {{
        background: {window};
        color: {text};
        border: 1px solid {border};
        border-radius: 7px;
        min-height: 30px;
        padding: 0 8px;
    }}
    QPlainTextEdit {{ padding: 7px 8px; }}
    QLineEdit:focus, QComboBox:focus, QTimeEdit:focus, QDateEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {accent};
    }}
    QPushButton {{
        background: {card};
        color: {text};
        border: 1px solid {border};
        border-radius: 7px;
        min-height: 26px;
        padding: 3px 14px;
    }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:focus {{ border: 1px solid {accent}; }}
    QPushButton:disabled {{ color: {hint}; border-color: {divider}; }}
    QPushButton[accent="true"] {{
        background: {accent};
        color: #ffffff;
        border: 1px solid {accent};
    }}
    QPushButton[accent="true"]:hover {{ background: {accent}; }}
    QPushButton[accent="true"]:disabled {{
        background: {divider};
        color: {hint};
        border-color: {divider};
    }}
    QScrollArea {{ border: none; background: transparent; }}
    QCheckBox {{ spacing: 6px; }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 1px solid {border};
        border-radius: 4px;
        background: {card};
    }}
    QCheckBox::indicator:hover {{ border-color: {accent}; }}
    QCheckBox::indicator:checked {{
        background: {accent};
        border-color: {accent};
    }}
    QCheckBox::indicator:focus {{ border: 1px solid {accent}; }}
    """


def _divider() -> QFrame:
    line = QFrame()
    line.setProperty("divider", True)
    line.setFixedHeight(1)
    line.setFrameShape(QFrame.Shape.NoFrame)
    return line


class TodoPanelDialog(QDialog):
    """待办条目列表 + 内嵌新建/编辑表单；偏好不在本面板（深链设置页）。"""

    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._editing_id: str | None = None
        self._agent_session_id: str | None = None
        self.setWindowTitle("待办提醒")
        self.setObjectName("todoPanelDialog")
        self.setModal(False)
        self.resize(540, 600)
        self.setMinimumSize(440, 460)
        self.setStyleSheet(_stylesheet(self))

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel("待办提醒")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        self._next_label = QLabel("")
        self._next_label.setObjectName("todoNextLabel")
        self._next_label.setWordWrap(True)
        header.addWidget(self._next_label)
        root.addLayout(header)

        self._agent_dialog = self._build_agent_entry()
        root.addWidget(self._build_editor())
        root.addWidget(self._build_list(), stretch=1)
        root.addLayout(self._build_footer())
        self.reload_items()

    # ------------------------------------------------------------ 构建

    def _build_agent_entry(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setObjectName("todoAgentDialog")
        dialog.setWindowTitle("LLM生成待办")
        dialog.setAccessibleName("LLM生成待办")
        dialog.setAccessibleDescription(
            "根据消息识别待办；缺少时间时参考现有待办安排空档，并沿用全局提醒设置。"
        )
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.resize(560, 480)
        dialog.setMinimumSize(440, 440)

        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.setContentsMargins(22, 20, 22, 18)
        dialog_layout.setSpacing(10)

        title = QLabel("LLM生成待办")
        title.setObjectName("todoAgentTitle")
        dialog_layout.addWidget(title)

        hint = QLabel(
            "粘贴包含事情和时间的消息，识别后直接加入待办；未标时间时会参考已有待办安排空档，"
            "提醒提前量沿用桌宠设置。"
        )
        hint.setObjectName("todoAgentHint")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        dialog_layout.addWidget(hint)

        self._agent_model_note = QLabel()
        self._agent_model_note.setObjectName("todoAgentModelNote")
        self._agent_model_note.setProperty("muted", True)
        self._agent_model_note.setWordWrap(True)
        self._agent_model_note.setAccessibleDescription(
            "说明待办 Agent 使用的内置 AI 对话服务商、模型和额度来源。"
        )
        dialog_layout.addWidget(self._agent_model_note)
        self._refresh_agent_model_note()

        input_label = QLabel("消息内容")
        input_label.setObjectName("todoAgentInputLabel")
        dialog_layout.addWidget(input_label)

        self._agent_text = QPlainTextEdit()
        self._agent_text.setObjectName("todoAgentInput")
        self._agent_text.setAccessibleName("待办文本")
        self._agent_text.setAccessibleDescription(
            "输入聊天消息或自然语言描述，提取未来事项和时间并生成待办。"
        )
        self._agent_text.setPlaceholderText(
            "例如：明天下午三点跟客户开会；周五提交周报；每天下午六点拉伸。"
        )
        self._agent_text.setTabChangesFocus(True)
        self._agent_text.setMinimumHeight(140)
        input_label.setBuddy(self._agent_text)
        self._agent_text.textChanged.connect(self._sync_agent_submit_enabled)
        dialog_layout.addWidget(self._agent_text, stretch=1)

        self._agent_status = QLabel("识别到的事项会直接加入当前列表。")
        self._agent_status.setObjectName("todoAgentStatus")
        self._agent_status.setProperty("muted", True)
        self._agent_status.setWordWrap(True)
        self._agent_status.setAccessibleName("待办识别状态")
        dialog_layout.addWidget(self._agent_status)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)

        manual = QPushButton("手动填写")
        manual.setObjectName("todoManualAddButton")
        manual.setAccessibleName("手动填写待办")
        manual.setAccessibleDescription("关闭 LLM 生成待办窗口并打开手动待办表单。")
        manual.clicked.connect(self._switch_to_manual_editor)
        actions.addWidget(manual)

        self._agent_submit_btn = QPushButton("识别并添加")
        self._agent_submit_btn.setObjectName("todoAgentSubmitButton")
        self._agent_submit_btn.setProperty("accent", True)
        self._agent_submit_btn.setAccessibleName("识别并添加待办")
        self._agent_submit_btn.setAccessibleDescription(
            "分析上方文本中的未来事项和时间，并将识别出的内容添加到待办列表。"
        )
        self._agent_submit_btn.setDefault(True)
        self._agent_submit_btn.clicked.connect(self.submit_agent_text)
        actions.addWidget(self._agent_submit_btn)
        dialog_layout.addLayout(actions)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self._agent_text)
        shortcut.activated.connect(self.submit_agent_text)
        self._sync_agent_submit_enabled()
        dialog.finished.connect(self._restore_agent_dialog_focus)
        return dialog

    def _build_editor(self) -> QFrame:
        self._editor_card = QFrame()
        self._editor_card.setObjectName("todoEditorCard")
        form = QGridLayout(self._editor_card)
        form.setContentsMargins(12, 10, 12, 12)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)

        self._title_edit = QLineEdit()
        self._title_edit.setObjectName("todoTitleEdit")
        self._title_edit.setPlaceholderText("待办内容（必填）")
        self._title_edit.textChanged.connect(self._sync_save_enabled)
        form.addWidget(QLabel("内容"), 0, 0)
        form.addWidget(self._title_edit, 0, 1, 1, 4)

        self._kind_combo = QComboBox()
        self._kind_combo.setObjectName("todoKindSelect")
        self._kind_combo.addItem("单次", "once")
        self._kind_combo.addItem("每天", "daily")
        self._kind_combo.currentIndexChanged.connect(self._sync_date_visibility)

        self._time_edit = QTimeEdit()
        self._time_edit.setObjectName("todoTimeEdit")
        self._time_edit.setDisplayFormat("HH:mm")
        self._time_edit.setTime(QTime(9, 0))

        self._date_edit = QDateEdit()
        self._date_edit.setObjectName("todoDateEdit")
        self._date_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self.close_editor)
        self._save_btn = QPushButton("保存")
        self._save_btn.setObjectName("todoSaveButton")
        self._save_btn.setProperty("accent", True)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self.save_editor)

        form.addWidget(QLabel("重复"), 1, 0)
        form.addWidget(self._kind_combo, 1, 1)
        form.addWidget(self._time_edit, 1, 2)
        form.addWidget(self._date_edit, 1, 3)
        # 操作按钮独占一行右对齐：与重复/时间/日期同行时窄宽度会互相挤压截断
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        actions.addWidget(self._cancel_btn)
        actions.addWidget(self._save_btn)
        form.addLayout(actions, 2, 0, 1, 4)

        form.setColumnStretch(1, 1)
        form.setColumnStretch(2, 1)
        form.setColumnStretch(3, 1)
        self._editor_card.setVisible(False)
        self._sync_save_enabled()
        return self._editor_card

    def _build_list(self) -> QFrame:
        self._list_card = QFrame()
        self._list_card.setObjectName("todoListCard")
        card_layout = QVBoxLayout(self._list_card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setObjectName("todoItemList")
        self._rows_host = QWidget()
        self._rows_host.setObjectName("todoRowsHost")
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(4, 2, 4, 6)
        self._rows_layout.setSpacing(0)
        self._scroll.setWidget(self._rows_host)
        card_layout.addWidget(self._scroll, stretch=1)

        self._empty_label = QLabel(
            "还没有待办，可点「新建待办」手动添加，或用「LLM生成待办」自动添加。"
        )
        self._empty_label.setObjectName("todoEmptyLabel")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setContentsMargins(16, 24, 16, 24)
        card_layout.addWidget(self._empty_label)
        return self._list_card

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._add_btn = QPushButton("新建待办")
        self._add_btn.setObjectName("todoAddButton")
        self._add_btn.setProperty("accent", True)
        self._add_btn.setAccessibleName("新建待办")
        self._add_btn.setAccessibleDescription("展开原有手动待办表单。")
        self._add_btn.clicked.connect(self.begin_add)
        footer.addWidget(self._add_btn)

        self._agent_open_btn = QPushButton("LLM生成待办")
        self._agent_open_btn.setObjectName("todoAgentOpenButton")
        self._agent_open_btn.setProperty("accent", True)
        self._agent_open_btn.setToolTip("粘贴包含事情和时间的消息，调用 LLM 识别并添加待办")
        self._agent_open_btn.setAccessibleName("LLM生成待办")
        self._agent_open_btn.setAccessibleDescription(
            "打开独立窗口，使用当前内置 AI 对话模型识别并添加待办。"
        )
        self._agent_open_btn.clicked.connect(self.open_agent_dialog)
        footer.addWidget(self._agent_open_btn)
        footer.addStretch(1)
        hint = QLabel("提醒开关与提前量在 桌宠设置 → 自动化与联动")
        hint.setObjectName("todoHintLabel")
        hint.setWordWrap(True)
        footer.addWidget(hint, stretch=1)
        return footer

    # ------------------------------------------------------------ 数据

    def _items(self) -> list[dict]:
        return self._app.todo_service.items()

    def _save_items(self, items: list[dict]) -> None:
        self._app.todo_service.set_items(items)

    def reload_items(self) -> None:
        """重建条目行并刷新空态/下一条摘要（增删改与外部变化统一入口）。"""
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        items = self._items()
        if not items:
            self._rows_host.setVisible(False)
            self._empty_label.setVisible(True)
        else:
            self._empty_label.setVisible(False)
            self._rows_host.setVisible(True)
            for index, item in enumerate(items):
                if index:
                    self._rows_layout.addWidget(_divider())
                self._rows_layout.addWidget(self._build_row(item))
        self._rows_layout.addStretch(1)
        self._refresh_next_label(items)

    def _build_row(self, item: dict) -> QFrame:
        row = QFrame()
        row.setObjectName("todoRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        toggle = QCheckBox()
        toggle.setObjectName(f"todoEnable_{item['id']}")
        toggle.setChecked(bool(item["enabled"]))
        toggle.setToolTip("启用/停用此待办")
        # 无文本控件的可达名：屏幕阅读器朗读条目标题而非匿名 checkbox
        toggle.setAccessibleName(str(item["title"]))
        toggle.setAccessibleDescription("启用或停用此待办")
        toggle.toggled.connect(
            lambda checked, item_id=item["id"]: self._set_item_enabled(item_id, checked)
        )
        layout.addWidget(toggle)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_label = QLabel(item["title"])
        title_label.setWordWrap(True)
        if not item["enabled"]:
            title_label.setProperty("muted", True)
        text_col.addWidget(title_label)
        badge = QLabel(self._badge_text(item))
        badge.setObjectName("todoBadgeLabel")
        badge.setWordWrap(True)
        if not item["enabled"]:
            badge.setProperty("muted", True)
        text_col.addWidget(badge)
        layout.addLayout(text_col, stretch=1)

        edit_btn = QPushButton("编辑")
        edit_btn.setObjectName(f"todoEdit_{item['id']}")
        edit_btn.clicked.connect(lambda _=False, item_id=item["id"]: self.begin_edit(item_id))
        layout.addWidget(edit_btn)
        delete_btn = QPushButton("删除")
        delete_btn.setObjectName(f"todoDelete_{item['id']}")
        delete_btn.clicked.connect(lambda _=False, item_id=item["id"]: self.delete_item(item_id))
        layout.addWidget(delete_btn)
        return row

    def _badge_text(self, item: dict) -> str:
        time_text = str(item.get("time") or "")
        if item.get("kind") == "daily":
            snooze_date = str(item.get("snooze_date") or "")
            snooze_time = str(item.get("snooze_time") or "")
            try:
                snooze_day = date.fromisoformat(snooze_date)
            except ValueError:
                badge = f"每天 {time_text}"
            else:
                badge = f"推迟至 {snooze_day.month}月{snooze_day.day}日 {snooze_time}"
        else:
            expired = str(item.get("date") or "") < date.today().isoformat()
            if expired:
                badge = "已过期"
            else:
                try:
                    day = date.fromisoformat(str(item.get("date")))
                except ValueError:
                    badge = time_text
                else:
                    badge = f"{day.month}月{day.day}日 {time_text}"
        return badge

    def _refresh_next_label(self, items: list[dict]) -> None:
        config = getattr(self._app, "config", None)
        enabled = bool(config.get("todo_reminder_enabled", True)) if config else True
        if not enabled:
            self._next_label.setText("提醒已关闭：可在 桌宠设置 → 自动化与联动 中开启")
            return
        summary = summarize_next(items, datetime.now())
        self._next_label.setText(f"下一条：{summary}" if summary else "")

    # ------------------------------------------------------------ 行为

    def _sync_agent_submit_enabled(self) -> None:
        self._agent_submit_btn.setEnabled(
            bool(self._agent_text.toPlainText().strip())
            and self._agent_session_id is None
        )

    def open_agent_dialog(self) -> None:
        self._refresh_agent_model_note()
        self._agent_dialog.open()
        self._agent_text.setFocus()

    def _restore_agent_dialog_focus(self, *_args) -> None:
        if self.isVisible() and not self._editor_card.isVisible():
            self._agent_open_btn.setFocus()

    def _switch_to_manual_editor(self) -> None:
        self._agent_dialog.close()
        self.begin_add()

    def _refresh_agent_model_note(self) -> None:
        """显示待办 Agent 当前复用的内置聊天模型与额度来源。"""
        config = getattr(self._app, "config", None)
        try:
            provider = config.chat_settings().active_config
            provider_name = str(getattr(provider, "name", "") or "").strip()
            model = str(getattr(provider, "model", "") or "").strip()
        except Exception:
            provider_name = ""
            model = ""

        identity = " / ".join(value for value in (provider_name, model) if value)
        if identity:
            note = (
                f"调用内置 AI 对话当前配置的 {identity}（Chat Completions）接口，"
                "共用同一 API Key 的聊天额度。"
            )
        else:
            note = (
                "调用内置 AI 对话当前选中的模型（Chat Completions）接口，"
                "共用同一 API Key 的聊天额度。"
            )
        self._agent_model_note.setText(note)

    def submit_agent_text(self) -> None:
        text = self._agent_text.toPlainText().strip()
        if not text or self._agent_session_id is not None:
            return
        self._refresh_agent_model_note()
        agent = getattr(self._app, "todo_agent", None)
        if agent is None:
            self._agent_status.setText("待办 Agent 尚未准备好，请重试。")
            return

        session_id = f"todo-panel:{uuid.uuid4().hex}"
        self._agent_session_id = session_id
        self._agent_status.setText("正在识别并添加…")
        self._agent_text.setEnabled(False)
        self._sync_agent_submit_enabled()
        if not agent.submit(session_id, text, existing_todos=self._items()):
            self._agent_session_id = None
            self._agent_text.setEnabled(True)
            self._agent_status.setText("Agent 暂时不可用，请检查 AI 模型配置后重试。")
            self._sync_agent_submit_enabled()

    def finish_agent_request(
        self, session_id: str, status: str, *, added_count: int = 0
    ) -> None:
        """显示与当前文本请求对应的完成状态；过期请求不覆盖新状态。"""
        if session_id != self._agent_session_id:
            return
        self._agent_session_id = None
        self._agent_text.setEnabled(True)
        if status == "success":
            if added_count > 0:
                self._agent_text.clear()
            self._agent_status.setText(f"已添加 {added_count} 条待办。")
        elif status == "empty":
            self._agent_status.setText("没有识别到明确的未来待办，可补充时间后重试。")
        elif status == "duplicate":
            self._agent_status.setText("没有新增：事项已存在或待办数量已达到上限。")
        else:
            self._agent_status.setText("识别失败，请检查 AI 模型连接后重试。")
        self._sync_agent_submit_enabled()

    def _set_item_enabled(self, item_id: str, checked: bool) -> None:
        items = []
        for item in self._items():
            if item["id"] == item_id:
                item = dict(item)
                item["enabled"] = checked
            items.append(item)
        self._save_items(items)
        self.reload_items()

    def delete_item(self, item_id: str) -> None:
        self._save_items([i for i in self._items() if i["id"] != item_id])
        if self._editing_id == item_id:
            self.close_editor()
        self.reload_items()

    def begin_add(self) -> None:
        self._editing_id = None
        self._title_edit.clear()
        self._kind_combo.setCurrentIndex(0)
        self._time_edit.setTime(QTime(9, 0))
        self._date_edit.setDate(QDate.currentDate())
        self._sync_date_visibility()
        self._editor_card.setVisible(True)
        self._add_btn.setEnabled(False)
        self._agent_open_btn.setEnabled(False)
        self._title_edit.setFocus()
        self._sync_save_enabled()

    def begin_edit(self, item_id: str) -> None:
        target = next((i for i in self._items() if i["id"] == item_id), None)
        if target is None:
            return
        self._editing_id = item_id
        self._title_edit.setText(str(target["title"]))
        self._kind_combo.setCurrentIndex(1 if target["kind"] == "daily" else 0)
        hour, minute = (int(part) for part in str(target["time"]).split(":"))
        self._time_edit.setTime(QTime(hour, minute))
        try:
            day = date.fromisoformat(str(target["date"]))
            self._date_edit.setDate(QDate(day.year, day.month, day.day))
        except ValueError:
            self._date_edit.setDate(QDate.currentDate())
        self._sync_date_visibility()
        self._editor_card.setVisible(True)
        self._add_btn.setEnabled(False)
        self._agent_open_btn.setEnabled(False)
        self._title_edit.setFocus()
        self._sync_save_enabled()

    def close_editor(self) -> None:
        self._editing_id = None
        self._editor_card.setVisible(False)
        self._add_btn.setEnabled(True)
        self._agent_open_btn.setEnabled(True)

    def save_editor(self) -> None:
        title = self._title_edit.text().strip()
        if not title:
            return
        kind = self._kind_combo.currentData()
        time_text = self._time_edit.time().toString("HH:mm")
        date_text = (
            self._date_edit.date().toString("yyyy-MM-dd") if kind == "once" else ""
        )
        items = self._items()
        if self._editing_id is not None:
            merged = []
            for item in items:
                if item["id"] != self._editing_id:
                    merged.append(item)
                    continue
                item = dict(item)
                item.update({
                    "title": title,
                    "kind": kind,
                    "time": time_text,
                    "date": date_text,
                    # 内容/时间变更后重新武装，避免沿用旧触发戳漏提醒
                    "fired_lead_slot": None,
                    "fired_due_slot": None,
                    "snooze_date": None,
                    "snooze_time": None,
                    "snooze_fired_lead_slot": None,
                    "snooze_fired_due_slot": None,
                })
                merged.append(item)
            self._save_items(merged)
        else:
            self._save_items(items + [new_todo_item(title, kind, time_text, date_text)])
        self.close_editor()
        self.reload_items()

    def _sync_date_visibility(self) -> None:
        self._date_edit.setVisible(self._kind_combo.currentData() == "once")

    def _sync_save_enabled(self) -> None:
        self._save_btn.setEnabled(bool(self._title_edit.text().strip()))
