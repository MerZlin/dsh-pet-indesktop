"""Minimal official-package manager; all operations go through backend service."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Slot
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QSizePolicy, QStyle, QStyleOptionButton, QStylePainter, QVBoxLayout, QWidget

from .feature_package_transactions import LOCK_BUSY_REASONS, Inspection, OperationResult
from .settings_widgets import ResponsiveActionRow

_STATUS = {
    "completed": "操作已完成",
    "idempotent": "状态已一致，无需重复操作",
    "awaiting_confirmation": "预检完成，请确认本次操作",
    "awaiting_release": "等待进程自然释放；不会强制关闭",
    "awaiting_startup_confirmation": "等待下次 Core／设置启动确认，尚未完成",
    "rejected": "操作被安全拒绝",
    "recovery_required": "需要安全恢复；功能暂不可执行",
    "failed": "操作失败，未宣称完成",
}


class _BreakableLabel(QLabel):
    """Plain text with display-only Unicode break opportunities in long tokens.

    QLabel's WordWrap alone clips a long unbroken identifier. Confirmation
    always consumes OperationPlan, never label text; the original is retained
    in the tooltip/accessibility description without display separators.
    """

    def __init__(self, text, parent):
        super().__init__(parent)
        self.setText(text)

    def setText(self, text):  # noqa: N802
        displayed = re.sub(r"[!-~]{24,}", lambda match: "\u200b".join(match.group()), text)
        super().setText(displayed)
        self.setToolTip(text)
        self.setAccessibleDescription(text)


class _WrappingActionButton(QPushButton):
    """Native button semantics with height-for-width localized action text."""

    def __init__(self, text, parent):
        super().__init__(text, parent)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def setText(self, text):  # noqa: N802
        super().setText(text)
        self.setMinimumHeight(self.heightForWidth(self.width()))

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.setMinimumHeight(self.heightForWidth(self.width()))

    def _padding(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        size = self.style().sizeFromContents(QStyle.ContentsType.CT_PushButton, option, QSize(0, 0), self)
        return max(20, size.width()), max(10, size.height())

    def minimumSizeHint(self):  # noqa: N802
        _, vertical = self._padding()
        return QSize(160, self.fontMetrics().height() + vertical)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        horizontal, vertical = self._padding()
        bounds = self.fontMetrics().boundingRect(QRect(0, 0, max(1, width - horizontal), 10000), int(Qt.TextFlag.TextWordWrap), self.text())
        return bounds.height() + vertical

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.setMinimumHeight(self.heightForWidth(self.width()))

    def paintEvent(self, event):  # noqa: N802
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        contents = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(option.palette.color(group, QPalette.ColorRole.ButtonText))
        painter.drawText(contents.adjusted(8, 2, -8, -2), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), self.text())


class FeatureManagementWidget(QWidget):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.plan = self.retry_plan = self.inspection = self.last_operation = None
        self.setObjectName("featureManagement")
        self.setAccessibleName("扩展管理：官方屏幕理解功能包")
        self.setAccessibleDescription("包级操作影响全部实例。卸载保留个人数据，不强制退出进程。")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.status_label = self._label("状态正在读取…", "扩展安装状态")
        self.summary_label = self._label("包级启停影响所有实例；当前实例的识屏选项仍在自动化与联动。", "操作确认摘要")
        layout.addWidget(self.status_label)
        layout.addWidget(self.summary_label)
        self.install_button = self._button("选择目录", lambda: self._choose(False))
        self.zip_button = self._button("选择 ZIP", lambda: self._choose(True))
        self.enable_button = self._button("启用／停用", self._toggle)
        self.rollback_button = self._button("回滚 previous", lambda: manager.submit("rollback"))
        self.uninstall_button = self._button("卸载", lambda: manager.submit("uninstall"))
        self.retry_button = self._button("安全重试", self._retry)
        self.confirm_button = self._button("确认本次操作", self._confirm)
        self.cancel_button = self._button("取消未接受预检", self._cancel)
        self.settings_button = self._button("当前实例设置", self._settings)
        self.draft_button = self._button("处理本窗口未保存草稿", self._resolve_draft)
        layout.addWidget(ResponsiveActionRow(self.install_button, [self.zip_button], self))
        layout.addWidget(ResponsiveActionRow(self.enable_button, [self.settings_button, self.rollback_button], self))
        layout.addWidget(ResponsiveActionRow(self.uninstall_button, [self.retry_button], self))
        layout.addWidget(ResponsiveActionRow(self.confirm_button, [self.cancel_button], self))
        layout.addWidget(ResponsiveActionRow(self.draft_button, [], self))
        self.buttons = (
            self.install_button,
            self.zip_button,
            self.enable_button,
            self.settings_button,
            self.rollback_button,
            self.uninstall_button,
            self.retry_button,
            self.confirm_button,
            self.cancel_button,
            self.draft_button,
        )
        for left, right in zip(self.buttons, self.buttons[1:]):
            self.setTabOrder(left, right)
        manager.result_ready.connect(self._result)
        manager.busy_changed.connect(self._busy)
        manager.state_changed.connect(self._changed)
        if manager.endpoint is not None:
            manager.endpoint.draft_blocked.connect(self._draft_blocked)
        if manager.builtin:
            self.status_label.setText("内置版本：文件属于 Core，不支持物理安装、升级或卸载。")
            self.summary_label.setText("此构建保持既有功能行为；不会把停用误报为物理卸载。个人数据始终保留。")
            self._update_actions()
        else:
            if manager.last_operation is not None:
                self._result(manager.last_operation)
            else:
                QTimer.singleShot(0, self._inspect)

    def _label(self, text, name):
        label = _BreakableLabel(text, self)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setAccessibleName(name)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setMinimumWidth(0)
        # Long authenticated hashes/paths must wrap to available card width,
        # never become the horizontal minimum of the enclosing scroll page.
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return label

    def _button(self, text, action):
        button = _WrappingActionButton(text, self)
        button.setAccessibleName(text)
        button.setAccessibleDescription("官方屏幕理解功能包：" + text)
        button.clicked.connect(action)
        return button

    def _inspect(self):
        if not self.manager.builtin and not self.manager.busy:
            self.manager.submit("inspect")

    @Slot(object)
    def _changed(self, _state):
        self._inspect()

    @Slot(bool)
    def _busy(self, _busy):
        self._update_actions()

    def _update_actions(self):
        for button in self.buttons:
            button.setEnabled(False)
        if self.manager.builtin or self.manager.busy:
            return
        component = getattr(self.window(), "_screen_component", None)
        self.draft_button.setEnabled(bool(component is not None and component.dirty()))
        installed = self.inspection is not None and self.inspection.active is not None
        pending = self.inspection is not None and self.inspection.pending_transaction is not None
        confirming = self.plan is not None and self.last_operation is not None and self.last_operation.phase == "awaiting_confirmation"
        self.confirm_button.setEnabled(confirming and not pending)
        self.cancel_button.setEnabled(confirming and not pending)
        self.retry_button.setEnabled(
            pending or (self.last_operation is not None and self.last_operation.status in ("awaiting_release", "recovery_required", "failed"))
        )
        if pending or confirming:
            return
        self.install_button.setEnabled(True)
        self.zip_button.setEnabled(True)
        self.enable_button.setEnabled(installed)
        self.enable_button.setText("停用（全部实例）" if self.inspection is not None and installed and self.inspection.enabled else "启用（全部实例）")
        self.uninstall_button.setEnabled(installed)
        self.rollback_button.setEnabled(self.inspection is not None and installed and self.inspection.previous is not None)
        self.settings_button.setEnabled(installed)

    def _choose(self, archive):
        if archive:
            value, _ = QFileDialog.getOpenFileName(self, "选择官方功能包 ZIP", "", "ZIP 功能包 (*.zip)")
        else:
            value = QFileDialog.getExistingDirectory(self, "选择官方功能包目录")
        if value:
            self.begin_source(Path(value))

    def begin_source(self, source: Path):
        command = "upgrade" if self.inspection is not None and self.inspection.active else "install"
        return self.manager.submit(command, source)

    def _confirm(self):
        if self.plan is not None:
            self.manager.submit("apply", self.plan, confirmation_token=self.plan.confirmation_token)

    def _retry(self):
        if self.retry_plan is not None:
            self.manager.submit("apply", self.retry_plan, confirmation_token=self.retry_plan.confirmation_token)
        else:
            self.manager.submit("recover")

    def _cancel(self):
        if self.plan is not None:
            self.manager.submit("cancel", self.plan)

    def _toggle(self):
        if self.inspection is not None and self.inspection.revision is not None:
            self.manager.submit("enable", not self.inspection.enabled, expected_revision=self.inspection.revision)

    @Slot(object)
    def _draft_blocked(self, details):
        self.status_label.setText("本设置窗口有未保存草稿；请明确保存、放弃或取消，事务不会自动处理。")
        self._update_actions()

    def _resolve_draft(self):
        resolve = getattr(self.window(), "_prepare_screen_revocation", None)
        if callable(resolve) and resolve():
            self.summary_label.setText("本窗口草稿已明确处理。请重新确认或安全重试；其他进程的草稿仍由其自己的窗口处理。")
        self._update_actions()

    def _settings(self):
        window = self.window()
        select = getattr(window, "select_page", None)
        if callable(select) and self.manager.host.configurable("official.screen-understanding"):
            select("自动化与联动")
            return
        # Explicit configuration access is meaningful; the management-only
        # bootstrap itself still never imports feature code or starts a Worker.
        from .modern_settings_dialog import ModernSettingsDialog

        dialog = getattr(self, "_instance_settings", None)
        import shiboken6

        if dialog is None or not shiboken6.isValid(dialog):
            dialog = ModernSettingsDialog(
                self.manager.config, window, include_ai=getattr(window, "include_ai", True), standalone=True, initial_page="自动化与联动"
            )
            self._instance_settings = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.summary_label.setText("已明确打开当前实例配置；已加载的 host 保持进程版本 pin，升级或卸载可能需要此设置进程自然退出。")

    @Slot(object)
    def _result(self, result):
        if isinstance(result, Inspection):
            self.inspection = result
            if self.last_operation is None:
                if result.pending_transaction:
                    self.status_label.setText("存在未完成事务：需要安全重试或下次启动确认。")
                elif result.status == "recovery_required":
                    self.status_label.setText("安装账本需要恢复；不从目录猜测状态。")
                else:
                    self.status_label.setText(
                        "已" + ("启用" if result.enabled else "停用") + " · " + result.active if result.active else "未安装：选择本地官方目录或 ZIP。"
                    )
            self._update_actions()
            return
        if not isinstance(result, OperationResult):
            return
        self.last_operation = result
        self.retry_plan = result.plan if result.status == "awaiting_release" or (result.status == "failed" and result.reason in LOCK_BUSY_REASONS) else None
        self.plan = result.plan if result.phase == "awaiting_confirmation" else None
        self.status_label.setText(_STATUS.get(result.status, "未知状态，未确认完成") + ("\n原因：" + result.reason if result.reason else ""))
        if self.plan is not None:
            plan = self.plan
            digest = " ".join(plan.confirmation_digest[i : i + 16] for i in range(0, 64, 16))
            uninstall = plan.kind == "uninstall"
            version = "全部安装版本：" + "、".join(plan.delete_versions) if uninstall else plan.target_version or plan.active or "全部安装版本"
            source = (
                "来源：现有安装账本；仅检查删除合同，不重新执行包代码或声称验签。"
                if uninstall
                else f"来源：{plan.source_type or '现有账本'}；官方签名与兼容性已验证。"
            )
            rollback = "已接受卸载不能取消并重新启用；重装是独立操作。" if uninstall else f"回滚目标：{plan.active or '无'}。"
            self.summary_label.setText(
                "\n".join(
                    (
                        f"操作：{plan.kind} · 版本：{version}",
                        source,
                        f"预计新增：{plan.estimated_bytes:,} 字节；" + rollback,
                        "影响：拒绝新任务、停止所属 Worker；驻留 host 等待自然退出，不热替换。",
                        "保留：个人设置、profile、凭据、记忆、额度和聊天历史。",
                        "确认摘要：" + digest,
                    )
                )
            )
            self.confirm_button.setFocus(Qt.FocusReason.OtherFocusReason)
        elif result.status == "failed" and result.reason in LOCK_BUSY_REASONS:
            resource = {"management_lock_busy": "管理锁", "leases_lock_busy": "租约锁", "state_lock_busy": "状态锁"}.get(result.reason, "事务锁（来源未确定）")
            self.summary_label.setText(
                f"{resource}正在使用，本次操作未宣称完成。运行时入口可能已撤销，但不能据此判断已卸载。\n"
                "请等待当前操作结束后点击“安全重试”，沿用已确认计划重新校验；不会强制关闭或删除占用版本。\n"
                "关闭进程后确认令牌不会保存；若卸载尚未接受，需重新预检并明确确认，不会自动接受。"
            )
        elif result.details:
            self.summary_label.setText("阻塞信息：" + str(dict(result.details)) + "\n只允许安全重试；不会强制关闭或越界删除。")
        elif result.status == "awaiting_startup_confirmation":
            self.summary_label.setText("文件与账本已切换，功能仍不可执行。请自然退出并重启 Core／普通设置入口完成真实加载确认；管理页不能提交成功回执。")
        elif result.status in ("failed", "rejected", "recovery_required"):
            self.summary_label.setText("操作未确认完成。请根据原因处理后安全重试或重新预检；不会沿用旧确认摘要冒充成功。")
        self._update_actions()
        QTimer.singleShot(0, self._inspect)
