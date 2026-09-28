# -*- coding: utf-8 -*-
"""规则图形编辑器：定时提醒 / 用久了提醒 / 全局快捷键 三张表。

以前这三样只能改 ``config.json``（见 ``docs/PR-REPORT-REMAINING-FEATURES-2026-09-29.md``
的「已知限制」）；本对话框把它们摆成表格，改完点保存即刻生效：

- 定时提醒 → ``timed_rules``：类型写「每天 / 每周 / 每隔」，时间写 ``23:30``，
  星期写 ``周一,周三``，间隔写分钟数；
- 用久了提醒 → ``app_usage_rules``：进程名 + 分钟 + 重复分钟 + 台词；
- 全局快捷键 → ``hotkeys``：按键写 ``Ctrl+Alt+1``（至少带 Ctrl），动作写「说一句 / 看天气 / 看余额」。

文本 ↔ 规则的转换与校验全在 ``pet/rules_text.py``（纯逻辑、可离线单测）；
本模块只负责摆控件、收集单元格、把非法的行指出来不让保存。
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import app_usage
from . import app_usage_service
from . import hotkey_rules
from . import hotkey_service
from . import rules_text
from . import timed_reminder
from . import timed_reminder_service

logger = logging.getLogger("dsh-pet-standalone")


class _RuleTable(QWidget):
    """一张规则表：表头 + 单元格 + 「添加一行 / 删除选中 / 恢复本页默认」。"""

    def __init__(self, columns: tuple[str, ...], rows, *, hint: str = "",
                 defaults=None, blank_row=None, parent=None) -> None:
        super().__init__(parent)
        self._defaults = defaults
        # 「添加一行」预填的默认值：给一份合法的起点，用户只改关心的格子
        # （以前给全空行，得逐格填满才能保存，容易卡在"写得不完整"）
        self._blank_row = list(blank_row) if blank_row else None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if hint:
            tip = QLabel(hint, self)
            tip.setWordWrap(True)
            layout.addWidget(tip)
        self.table = QTableWidget(0, len(columns), self)
        self.table.setHorizontalHeaderLabels(list(columns))
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        add = QPushButton("添加一行", self)
        add.clicked.connect(lambda: self.add_row())
        remove = QPushButton("删除选中", self)
        remove.clicked.connect(self.remove_selected)
        reset = QPushButton("恢复本页默认", self)
        reset.clicked.connect(self.reset_to_defaults)
        for button in (add, remove, reset):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.set_rows(rows or [])
        self.table.resizeColumnsToContents()

    # ---------------------------------------------------------- 数据
    def set_rows(self, rows) -> None:
        self.table.setRowCount(0)
        for cells in rows:
            self.add_row(cells)

    def add_row(self, cells=None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        if cells is None:
            values = list(self._blank_row or ["" for _ in range(self.table.columnCount())])
        else:
            values = list(cells)
        for column in range(self.table.columnCount()):
            text = str(values[column]) if column < len(values) else ""
            self.table.setItem(row, column, QTableWidgetItem(text))

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def reset_to_defaults(self) -> None:
        if callable(self._defaults):
            self.set_rows(self._defaults())

    def rows(self) -> list[list[str]]:
        out: list[list[str]] = []
        for row in range(self.table.rowCount()):
            cells: list[str] = []
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                cells.append(item.text() if item is not None else "")
            if any(str(cell).strip() for cell in cells):
                out.append(cells)
        return out


class RulesEditorDialog(QDialog):
    """三张规则的编辑对话框；保存即写回 config 并让服务立刻生效。"""

    def __init__(self, config, window=None, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.window = window
        self.setWindowTitle("编辑规则（定时提醒 / 用久了 / 快捷键）")
        self.resize(980, 560)
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget(self)
        self.timed_table = _RuleTable(
            rules_text.TIMED_COLUMNS,
            [rules_text.timed_rule_to_row(rule)
             for rule in timed_reminder.clean_timed_rules(self._cfg("timed_rules"))],
            hint="类型：每天 / 每周 / 每隔；时间写 23:30；星期写 周一,周三（仅每周用）；"
                 "间隔写分钟数（仅每隔用）；台词一栏每行一句；「带汇总」是先把今日汇总说在前头。",
            defaults=lambda: [rules_text.timed_rule_to_row(rule)
                              for rule in timed_reminder.default_timed_rules()],
            blank_row=["是", "每天", "09:00", "", "", "否", ""],
            parent=self,
        )
        self.tabs.addTab(self.timed_table, "定时提醒")
        self.usage_table = _RuleTable(
            rules_text.USAGE_COLUMNS,
            [rules_text.usage_rule_to_row(exe, rule)
             for exe, rule in app_usage.clean_rules(self._cfg("app_usage_rules")).items()],
            hint="进程名写 exe 名（如 steam.exe）；「分钟」是连续用满多久说话，"
                 "「重复」是之后每隔多少分钟再说一次（0 = 只说一次）；台词一栏每行一句。",
            defaults=lambda: [rules_text.usage_rule_to_row(exe, rule)
                              for exe, rule in app_usage.DEFAULT_USAGE_RULES.items()],
            blank_row=["是", "", "60", "0", ""],
            parent=self,
        )
        self.tabs.addTab(self.usage_table, "用久了提醒")
        self.hotkey_table = _RuleTable(
            rules_text.HOTKEY_COLUMNS,
            [rules_text.hotkey_rule_to_row(rule)
             for rule in hotkey_rules.clean_hotkeys(self._cfg("hotkeys"))],
            hint="按键写 Ctrl+Alt+1 这种（至少带 Ctrl；Ctrl+C/V/X/Z 等会被拒）；"
                 "动作写 说一句 / 看天气 / 看余额；「说一句」必须在台词里写内容。",
            defaults=lambda: [rules_text.hotkey_rule_to_row(rule)
                              for rule in hotkey_rules.default_hotkeys()],
            # 预填成"看天气"：它不需要台词，所以新行一加进来就是合法的；
            # 想让它说一句，把动作改成"说一句"再写台词即可。
            blank_row=["是", "Ctrl+Alt+G", "看天气", ""],
            parent=self,
        )
        self.tabs.addTab(self.hotkey_table, "全局快捷键")
        layout.addWidget(self.tabs, 1)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                               | QDialogButtonBox.StandardButton.Cancel, self)
        box.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _cfg(self, key, default=None):
        try:
            return self.config.get(key, default) if self.config is not None else default
        except Exception:
            return default

    # ---------------------------------------------------------- 保存
    def _save(self) -> None:
        timed_rules, timed_bad = self._collect(self.timed_table, rules_text.timed_row_to_rule)
        usage_rules, usage_bad = self._collect_usage()
        hotkeys, hotkey_bad = self._collect(self.hotkey_table, rules_text.hotkey_row_to_rule)
        problems = []
        if timed_bad:
            problems.append(f"定时提醒第 {self._rows_text(timed_bad)} 行写得不完整")
        if usage_bad:
            problems.append(f"用久了提醒第 {self._rows_text(usage_bad)} 行写得不完整")
        if hotkey_bad:
            problems.append(f"全局快捷键第 {self._rows_text(hotkey_bad)} 行写得不完整"
                            "（按键至少要带 Ctrl，且别占用 Ctrl+C/V/X/Z 这类）")
        if problems:
            QMessageBox.warning(self, "还有几行要改", "；".join(problems) + "。")
            return
        if self.config is not None:
            self.config.set("timed_rules", timed_rules)
            self.config.set("app_usage_rules", usage_rules)
            self.config.set("hotkeys", hotkeys)
            saver = getattr(self.config, "save", None)
            if callable(saver):
                saver()
        self._reload_services()
        self.accept()

    @staticmethod
    def _rows_text(rows) -> str:
        return "、".join(str(row + 1) for row in rows)

    @staticmethod
    def _collect(table: _RuleTable, converter):
        """逐行转换；返回 ``(规则列表, 出错行号列表)``（行号从 1 起，给人看的）。"""
        rules: list[dict] = []
        bad: list[int] = []
        for index, cells in enumerate(table.rows()):
            rule = converter(cells, rule_id=f"ui-{index + 1}")
            if rule is None:
                bad.append(index)
                continue
            rules.append(rule)
        return rules, bad

    def _collect_usage(self):
        rules: dict[str, dict] = {}
        bad: list[int] = []
        for index, cells in enumerate(self.usage_table.rows()):
            converted = rules_text.usage_row_to_rule(cells)
            if converted is None:
                bad.append(index)
                continue
            exe, rule = converted
            rules[exe] = rule
        return rules, bad

    def _reload_services(self) -> None:
        """保存后立刻生效：快捷键要重注册、用久了要换规则、定时提醒保持节拍。"""
        win = self.window
        if win is None:
            return
        try:
            app_usage_service.ensure_started(win)
        except Exception:
            logger.debug("刷新用久了提醒失败", exc_info=True)
        try:
            hotkey_service.ensure_installed(win)
        except Exception:
            logger.debug("刷新快捷键失败", exc_info=True)
        try:
            timed_reminder_service.ensure_started(win)
        except Exception:
            logger.debug("刷新定时提醒失败", exc_info=True)


def open_rules_editor(config, window=None, parent=None) -> bool:
    """打开编辑器；返回是否真的保存了（给菜单/设置页用）。"""
    dialog = RulesEditorDialog(config, window=window, parent=parent)
    return dialog.exec() == QDialog.DialogCode.Accepted
