"""Core-owned removal UI; real transactions run off the GUI thread.

This is a pre-delete gate, not an installer and not a receipt writer. Closing
its window never cancels an accepted DLC removal. The application-owned exit
gate leaves the Qt loop alive until the background transaction has drained.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget

from .async_exit import application_exit_gate
from .core_uninstall import CoreRemovalEvidence, CoreUninstallCoordinator, CoreUninstallPlan, CoreUninstallResult
from .feature_management_ui import _BreakableLabel, _WrappingActionButton
from .settings_widgets import ResponsiveActionRow


class CoreUninstallRuntime(QObject):
    result_ready = Signal(object)
    busy_changed = Signal(bool)
    _finished = Signal(object)

    def __init__(self, coordinator: CoreUninstallCoordinator, app: QApplication):
        super().__init__(app)
        self.coordinator = coordinator
        self.busy = False
        self.closed = False
        self._gate = application_exit_gate(app)
        self._drain_token: str | None = None
        self._finished.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def submit(self, command: str, plan: CoreUninstallPlan | None = None) -> bool:
        if self.closed or self.busy:
            return False
        if command == "prepare":
            action = self.coordinator.prepare
        elif command == "recover":
            action = self.coordinator.recover
        elif command == "apply" and isinstance(plan, CoreUninstallPlan):

            def action():
                return self.coordinator.apply(plan, confirmation_token=plan.confirmation_token)
        else:
            return False
        self.busy = True
        self._drain_token = self._gate.register("core-removal", self.close, lambda: not self.busy)
        self.busy_changed.emit(True)

        def run():
            try:
                result = action()
            except Exception:
                # Never include user paths, document contents or credentials.
                result = CoreUninstallResult("failed", reason="core_removal_unexpected_error")
            self._finished.emit(result)

        threading.Thread(target=run, name="core-dlc-removal", daemon=False).start()
        return True

    @Slot(object)
    def _deliver(self, result: CoreUninstallResult) -> None:
        self.busy = False
        if self._drain_token is not None:
            self._gate.unregister(self._drain_token)
            self._drain_token = None
        if self.closed:
            self.coordinator.close()
            return
        self.busy_changed.emit(False)
        self.result_ready.emit(result)

    def close(self) -> None:
        self.closed = True
        if not self.busy:
            self.coordinator.close()


class CoreUninstallDialog(QDialog):
    """Confirmation is bound to backend plans; the label is display-only."""

    def __init__(self, runtime: CoreUninstallRuntime, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        self.plan: CoreUninstallPlan | None = None
        self.removal_confirmed = False
        self.removal_evidence: CoreRemovalEvidence | None = None
        self.setWindowTitle("卸载桌宠：先安全移除官方扩展")
        self.setAccessibleName("Core 卸载前置确认")
        self.resize(720, 500)
        self.setMinimumSize(480, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 28, 20)
        layout.setSpacing(18)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        contents = QWidget(self.scroll_area)
        body = QVBoxLayout(contents)
        self.status_label = QLabel("请先预检；尚未删除任何功能包。", contents)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("卸载状态")
        self.detail_label = _BreakableLabel("全部个人数据与凭据始终保留。两个包分别完成事务，不承诺跨包回滚。", contents)
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setWordWrap(True)
        self.detail_label.setAccessibleName("确认摘要和安全阻塞原因")
        body.addWidget(self.status_label)
        body.addWidget(self.detail_label)
        body.addStretch(1)
        self.scroll_area.setWidget(contents)
        layout.addWidget(self.scroll_area)
        self.prepare_button = _WrappingActionButton("预检两个官方功能包", self)
        self.prepare_button.clicked.connect(lambda: runtime.submit("prepare"))
        self.confirm_button = _WrappingActionButton("确认逐包卸载；保留个人数据", self)
        self.confirm_button.clicked.connect(self._confirm)
        self.confirm_button.setEnabled(False)
        self.retry_button = _WrappingActionButton("安全恢复已接受卸载", self)
        self.retry_button.clicked.connect(lambda: runtime.submit("recover"))
        self.close_button = _WrappingActionButton("取消／关闭（不复活已卸载包）", self)
        self.close_button.clicked.connect(self.close)
        buttons = (self.prepare_button, self.confirm_button, self.retry_button, self.close_button)
        for left, right in zip(buttons, buttons[1:]):
            self.setTabOrder(left, right)
        for button in buttons:
            button.setAccessibleName(button.text())
        layout.addWidget(ResponsiveActionRow(self.prepare_button, [self.confirm_button], self))
        layout.addWidget(ResponsiveActionRow(self.retry_button, [self.close_button], self))
        runtime.result_ready.connect(self._result)
        runtime.busy_changed.connect(self._busy)

    @Slot()
    def _confirm(self) -> None:
        if self.plan is not None:
            self.runtime.submit("apply", self.plan)

    @Slot(bool)
    def _busy(self, busy: bool) -> None:
        self.prepare_button.setEnabled(not busy and not self.removal_confirmed)
        self.retry_button.setEnabled(not busy and not self.removal_confirmed)
        self.confirm_button.setEnabled(not busy and self.plan is not None and not self.removal_confirmed)

    @Slot(object)
    def _result(self, result: CoreUninstallResult) -> None:
        self.plan = result.plan if result.status == "awaiting_confirmation" else None
        self.removal_confirmed = result.status in ("completed", "idempotent") and result.evidence is not None
        self.removal_evidence = result.evidence if self.removal_confirmed else None
        if self.removal_confirmed:
            self.status_label.setText("两个包的代码均已移除；可继续卸载 Core。个人数据未删除。")
            self.detail_label.setText("此回执仅证明当前 DLC 状态；安装器仍必须持有 Core 文件替换锁。")
            self.close_button.setText("完成前置清理，返回卸载器")
        elif self.plan is not None:
            self.status_label.setText("预检完成；请确认不可变摘要。尚未接受本次卸载。")
            lines = [self.plan.summary]
            for entry in self.plan.packages:
                if entry.plan is not None:
                    lines.append(f"{entry.feature_id}：{entry.plan.target_version}；revision {entry.revision}；摘要 {entry.plan.confirmation_token}")
            lines.append("联合确认摘要：" + self.plan.confirmation_token)
            self.detail_label.setText("\n".join(lines))
        else:
            status = "等待自然释放；不会强制关闭进程" if result.status == "awaiting_release" else "未宣称卸载完成；请安全恢复或查看原因"
            self.status_label.setText(status)
            self.detail_label.setText(result.reason or result.status)
        self._busy(self.runtime.busy)

    def closeEvent(self, event):  # noqa: N802
        self.runtime.close()
        super().closeEvent(event)
