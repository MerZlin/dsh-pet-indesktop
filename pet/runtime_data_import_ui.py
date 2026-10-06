"""Explicit source selection and confirmation; every import I/O runs off GUI.

This Core-only UI never creates Config, an executable host or a business thread.
Closing cancels only its unaccepted snapshot. Accepted work drains or remains a
recoverable intent; QApplication's event loop stays alive for queued completion.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QFileDialog, QLabel, QScrollArea, QVBoxLayout, QWidget

from .async_exit import application_exit_gate
from .feature_management_ui import _BreakableLabel, _WrappingActionButton
from .runtime_data_import import DataImportPlan, DataImportResult, RuntimeDataImporter
from .settings_widgets import ResponsiveActionRow


class DataImportRuntime(QObject):
    result_ready = Signal(object)
    busy_changed = Signal(bool)
    _finished = Signal(object)

    def __init__(self, importer: RuntimeDataImporter, app: QApplication):
        super().__init__(app)
        self.importer = importer
        self.busy = False
        self.closed = False
        self.last_result: DataImportResult | None = None
        self._preview: DataImportPlan | None = None
        self._gate = application_exit_gate(app)
        self._drain_token: str | None = None
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def submit(self, command: str, *, source: Path | None = None, plan: DataImportPlan | None = None, confirmation_token: str | None = None) -> bool:
        if self.closed or self.busy:
            return False
        action: Callable[[], DataImportResult]
        if command == "preflight" and source is not None and self._preview is None:
            action = partial(self.importer.preflight, source)
        elif command == "apply" and isinstance(plan, DataImportPlan) and confirmation_token is not None:
            action = partial(self.importer.apply, plan, confirmation_token=confirmation_token)
        elif command == "cancel" and isinstance(plan, DataImportPlan) and plan == self._preview:
            action = partial(self.importer.cancel_preflight, plan)
        elif command == "recover" and self._preview is None:
            action = self.importer.recover_pending
        else:
            return False
        self._spawn(action)
        return True

    def _spawn(self, action: Callable[[], DataImportResult]) -> None:
        self.busy = True
        if self._drain_token is None:
            self._drain_token = self._gate.register("ordinary-data-import", self.close, lambda: not self.busy)
        self.busy_changed.emit(True)

        def run():
            try:
                result = action()
            except Exception:
                # Backend failure text may contain credentials/document contents.
                result = DataImportResult("recovery_required", reason="data_import_unexpected_error")
            self._finished.emit(result)

        threading.Thread(target=run, name="ordinary-data-import", daemon=False).start()

    @Slot(object)
    def _deliver(self, result: DataImportResult) -> None:
        self.last_result = result
        if result.status == "awaiting_confirmation":
            self._preview = result.plan
        elif result.status in ("completed", "idempotent", "recovery_required"):
            self._preview = None
        if self.closed and self._preview is not None:
            plan, self._preview = self._preview, None
            self._spawn(lambda: self.importer.cancel_preflight(plan))
            return
        self.busy = False
        if self._drain_token is not None:
            self._gate.unregister(self._drain_token)
            self._drain_token = None
        if not self.closed:
            self.busy_changed.emit(False)
            self.result_ready.emit(result)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if not self.busy and self._preview is not None:
            plan, self._preview = self._preview, None
            self._spawn(lambda: self.importer.cancel_preflight(plan))


def confirmation_summary(plan: DataImportPlan) -> str:
    lines = [
        "唯一来源：" + plan.source,
        "目标数据根：" + plan.target,
        "目标身份：" + plan.data_root_id,
        f"预计普通数据：{plan.estimated_bytes} 字节；{len(plan.files)} 个文件。",
        "不自动合并其他来源；不复制旧安装代码；原数据保持不变。",
        "角色资源：仅复制通过 manifest、内容哈希和 active/previous 指针校验的资源；不复制可执行 DLC 或缓存。",
        "文件映射：",
        *(source + " → " + target for source, target in plan.destinations),
    ]
    if plan.credential_mappings:
        lines.append("需要明确授权的安全存储引用（不展示或备份秘密）：")
        lines.extend(f"{row.owner} · 实例 {row.instance} · profile {row.profile} · {row.endpoint}" for row in plan.credential_mappings)
    else:
        lines.append("本预检没有需要迁移的凭据引用。")
    lines.extend(["来源变化或目标新编辑必须重新预检；等待所有 Core／设置自然退出才写入。", "不可变确认摘要：" + plan.confirmation_digest])
    return "\n".join(lines)


class DataImportDialog(QDialog):
    def __init__(self, runtime: DataImportRuntime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.plan: DataImportPlan | None = None
        self._confirmed_plan: DataImportPlan | None = None
        self._cancel_requested = False
        self.setWindowTitle("显式导入旧桌宠数据")
        self.setAccessibleName("旧数据来源和映射确认")
        self.resize(720, 550)
        self.setMinimumSize(480, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 28, 20)
        layout.setSpacing(18)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        contents = QWidget(self.scroll_area)
        body = QVBoxLayout(contents)
        self.status_label = QLabel("请选择一个旧数据目录；尚未读取来源、导入或访问凭据。", contents)
        self.status_label.setAccessibleName("导入状态")
        self.detail_label = _BreakableLabel(
            "仅导入明确支持的配置、会话和已验证角色资源；不合并其他来源，不复制安装代码。恢复仅继续此前已接受的意图。", contents
        )
        self.detail_label.setAccessibleName("来源、映射和不可变确认摘要")
        for label in (self.status_label, self.detail_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            body.addWidget(label)
        self.credential_check = QCheckBox("授权上述凭据引用迁移", contents)
        self.credential_check.setAccessibleName("明确授权凭据引用迁移")
        self.credential_check.setAccessibleDescription("预检不会读取秘密；仅在本次导入接受后迁移列出的 owner 和实例。")
        self.credential_check.toggled.connect(lambda _checked: self._busy(runtime.busy))
        self.credential_check.hide()
        body.addWidget(self.credential_check)
        self.credential_hint = _BreakableLabel("仅迁移上述来源明确绑定的安全存储引用；秘密不写入目录、事务记录、快照或备份，原来源保持不变。", contents)
        self.credential_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.credential_hint.setWordWrap(True)
        self.credential_hint.setAccessibleName("凭据迁移范围和数据保留说明")
        self.credential_hint.hide()
        body.addWidget(self.credential_hint)
        body.addStretch(1)
        self.scroll_area.setWidget(contents)
        layout.addWidget(self.scroll_area)
        self.source_button = _WrappingActionButton("选择一个旧数据目录并预检", self)
        self.source_button.clicked.connect(self._choose)
        self.confirm_button = _WrappingActionButton("确认此来源和映射，开始导入", self)
        self.confirm_button.clicked.connect(self._confirm)
        self.retry_button = _WrappingActionButton("安全重试／恢复此前导入", self)
        self.retry_button.clicked.connect(self._retry)
        self.cancel_button = _WrappingActionButton("取消未接受的预检", self)
        self.cancel_button.clicked.connect(self._cancel)
        self.close_button = _WrappingActionButton("关闭；不取消已接受的导入", self)
        self.close_button.clicked.connect(self.close)
        for button in (self.source_button, self.confirm_button, self.retry_button, self.cancel_button, self.close_button):
            button.setAccessibleName(button.text())
            button.setAccessibleDescription("只调用后台服务，不写配置或提交执行加载回执。")
        layout.addWidget(ResponsiveActionRow(self.source_button, [self.confirm_button, self.retry_button, self.cancel_button, self.close_button], self))
        QWidget.setTabOrder(self.source_button, self.credential_check)
        QWidget.setTabOrder(self.credential_check, self.confirm_button)
        QWidget.setTabOrder(self.confirm_button, self.retry_button)
        QWidget.setTabOrder(self.retry_button, self.cancel_button)
        QWidget.setTabOrder(self.cancel_button, self.close_button)
        runtime.result_ready.connect(self._result)
        runtime.busy_changed.connect(self._busy)
        self._busy(runtime.busy)

    @Slot()
    def _choose(self) -> None:
        source = QFileDialog.getExistingDirectory(self, "选择唯一旧数据来源；不自动扫描其他目录")
        if source:
            self.runtime.submit("preflight", source=Path(source))

    @Slot()
    def _confirm(self) -> None:
        if self.plan is not None and not self._cancel_requested and (not self.plan.credential_mappings or self.credential_check.isChecked()):
            self._confirmed_plan = self.plan
            self.runtime.submit("apply", plan=self.plan, confirmation_token=self.plan.confirmation_token)

    @Slot()
    def _retry(self) -> None:
        if self.plan is not None and self._cancel_requested:
            self.runtime.submit("cancel", plan=self.plan)
        elif self.plan is not None and self._confirmed_plan == self.plan:
            self.runtime.submit("apply", plan=self.plan, confirmation_token=self.plan.confirmation_token)
        else:
            self.runtime.submit("recover")

    @Slot()
    def _cancel(self) -> None:
        if self.plan is not None:
            self._confirmed_plan = None
            self._cancel_requested = True
            self.runtime.submit("cancel", plan=self.plan)

    @Slot(bool)
    def _busy(self, busy: bool) -> None:
        self.source_button.setEnabled(not busy and self.plan is None)
        self.confirm_button.setEnabled(
            not busy and self.plan is not None and not self._cancel_requested and (not self.plan.credential_mappings or self.credential_check.isChecked())
        )
        self.retry_button.setEnabled(not busy and (self.plan is None or self._confirmed_plan == self.plan or self._cancel_requested))
        self.cancel_button.setEnabled(not busy and self.plan is not None)
        self.credential_check.setEnabled(not busy)

    @Slot(object)
    def _result(self, result: DataImportResult) -> None:
        if result.status == "awaiting_confirmation" and result.plan is not None:
            self.plan = result.plan
            self._confirmed_plan = None
            self._cancel_requested = False
            self.credential_check.setChecked(False)
            self.credential_check.setVisible(bool(self.plan.credential_mappings))
            self.credential_hint.setVisible(bool(self.plan.credential_mappings))
            self.status_label.setText("预检完成；请核对唯一来源与映射，尚未接受本次导入。")
            self.detail_label.setText(confirmation_summary(self.plan))
            (self.credential_check if self.plan.credential_mappings else self.confirm_button).setFocus()
        elif result.status == "awaiting_release":
            self.status_label.setText(
                "等待其他导入操作释放；不会强制关闭进程。" if result.reason == "import_management_busy" else "等待所有 Core／设置自然退出；不会强制关闭进程。"
            )
            self.detail_label.setText((confirmation_summary(self.plan) + "\n" if self.plan is not None else "") + (result.reason or result.status))
        elif result.status in ("completed", "idempotent"):
            self.plan = self._confirmed_plan = None
            self._cancel_requested = False
            self.credential_check.hide()
            self.credential_hint.hide()
            if result.reason == "preflight_cancelled":
                text = "未接受的预检已取消；没有导入数据。"
            elif result.reason == "no_pending_import":
                text = "无待恢复意图；此操作没有导入数据。"
            else:
                text = "导入已完成；原来源保留。请重新启动 Core，正式功能包仍需独立安装。"
            self.status_label.setText(text)
            self.detail_label.setText(result.reason or "普通数据已提交；这不是 DLC 加载确认或模型调用验收。")
        else:
            self._confirmed_plan = None
            if result.status == "recovery_required":
                self.plan = None
                self.credential_check.hide()
                self.credential_hint.hide()
            self.status_label.setText("未宣称导入完成；请取消未接受的预检或安全恢复已接受的意图。")
            self.detail_label.setText(result.reason or result.status)
        self._busy(self.runtime.busy)

    def closeEvent(self, event):  # noqa: N802
        self.runtime.close()
        super().closeEvent(event)
