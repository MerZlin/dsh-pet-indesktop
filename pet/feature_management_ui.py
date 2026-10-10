"""Minimal local feature-package manager; all operations go through backend service."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QSizePolicy, QStyle, QStyleOptionButton, QStylePainter, QVBoxLayout, QWidget

from .feature_package_transactions import LOCK_BUSY_REASONS, Inspection, OperationResult
from .official_features import AI_OWNER, SCREEN_OWNER, is_valid_feature_id
from .settings_widgets import ResponsiveActionRow, SettingRow, SettingsSection

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
    local_package_routed = Signal(object, object)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        if not is_valid_feature_id(manager.feature_id):
            raise ValueError("invalid feature id")
        self.feature_id = manager.feature_id
        if self.feature_id == AI_OWNER:
            self.feature_title = "AI 对话功能包"
            self.settings_domain = "AI 与对话"
        elif self.feature_id == SCREEN_OWNER:
            self.feature_title = "屏幕理解功能包"
            self.settings_domain = "自动化与联动"
        else:
            self.feature_title = "本地扩展：" + self.feature_id
            self.settings_domain = "本地扩展"
        self.plan = self.retry_plan = self.inspection = self.last_operation = None
        self.setObjectName("featureManagementAI" if self.feature_id == AI_OWNER else "featureManagement")
        self.setAccessibleName("扩展管理：" + self.feature_title)
        self.setAccessibleDescription("包级操作影响全部实例。卸载只删除已安装副本，保留原始 ZIP／源目录及个人数据，不强制退出进程。")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.status_label = self._label("状态正在读取…", "扩展安装状态")
        self.summary_label = self._label("包级启停影响所有实例；当前实例选项仍在“" + self.settings_domain + "”。", "操作确认摘要")
        layout.addWidget(self.status_label)
        layout.addWidget(self.summary_label)
        self.cleanup_label = self._label("", "安装自检临时材料清理警告")
        self.cleanup_label.setAccessibleDescription("安全清理警告不改变安装账本或启停；可安全重试，不强制退出进程。")
        self.cleanup_label.hide()
        layout.addWidget(self.cleanup_label)
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
        manager.probe_cleanup_changed.connect(self._probe_cleanup)
        if hasattr(manager, "package_routed"):
            manager.package_routed.connect(self._package_routed)
        self._probe_cleanup(manager.probe_cleanup)
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

    def _probe_cleanup(self, outcomes):
        warnings = [row for row in outcomes if row.status not in ("completed", "idempotent")][:4]
        self.cleanup_label.setVisible(bool(warnings))
        if warnings:
            self.cleanup_label.setText(
                "安装自检临时材料尚待安全清理；不会改变功能启停或安装结果。可安全重试，不强制退出进程。\n"
                + "\n".join(row.reason or row.status for row in warnings)
            )
        else:
            self.cleanup_label.clear()

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
        button.setAccessibleDescription(self.feature_title + "：" + text)
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
        window = self.window()
        getter = getattr(window, "_feature_component", None)
        component = getter(self.feature_id) if callable(getter) else (getattr(window, "_screen_component", None) if self.feature_id == SCREEN_OWNER else None)
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
        self.choose_local_package(archive)

    def choose_local_package(self, archive: bool):
        """Open the shared picker independently of this card's official owner."""
        if archive:
            value, _ = QFileDialog.getOpenFileName(self, "选择功能包 ZIP", "", "ZIP 功能包 (*.zip)")
        else:
            value = QFileDialog.getExistingDirectory(self, "选择功能包目录")
        if value:
            self.begin_selected_source(Path(value))

    def begin_source(self, source: Path):
        """Compatibility seam for tests and explicit review/confirm flows."""
        command = "upgrade" if self.inspection is not None and self.inspection.active else "install"
        return self.manager.submit(command, source)

    def begin_selected_source(self, source: Path):
        """Route a picker result and accept the exact plan automatically."""
        submit_local_source = getattr(self.manager, "submit_local_source", None)
        if callable(submit_local_source):
            return submit_local_source(source, auto_apply=True)
        return self.begin_source(source)

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
    def _package_routed(self, route):
        feature_id = getattr(route, "feature_id", "未知扩展")
        self.status_label.setText("已识别本地扩展，已转交对应管理器：" + str(feature_id))
        self.summary_label.setText("来源由用户选择；目标管理器将独立完成预检、文件完整性检查、提交和下次启动确认。")
        target = getattr(self.manager.host, "management_runtimes", {}).get(feature_id)
        if target is not None and target is not self.manager:
            self.local_package_routed.emit(route, target)
        self._update_actions()

    @Slot(object)
    def _draft_blocked(self, details):
        self.status_label.setText("本设置窗口有未保存草稿；请明确保存、放弃或取消，事务不会自动处理。")
        self._update_actions()

    def _resolve_draft(self):
        resolve = getattr(self.window(), "_prepare_feature_revocation", None)
        if callable(resolve):
            accepted = resolve(self.feature_id)
        else:
            resolve = getattr(self.window(), "_prepare_screen_revocation", None) if self.feature_id == SCREEN_OWNER else None
            accepted = bool(callable(resolve) and resolve())
        if accepted:
            self.summary_label.setText("本窗口草稿已明确处理。请重新确认或安全重试；其他进程的草稿仍由其自己的窗口处理。")
        self._update_actions()

    def _settings(self):
        window = self.window()
        select = getattr(window, "select_page", None)
        if callable(select) and self.manager.host.configurable(self.feature_id):
            if select(self.settings_domain):
                return
        # Explicit configuration access is meaningful; the management-only
        # bootstrap itself still never imports feature code or starts a Worker.
        from .modern_settings_dialog import ModernSettingsDialog

        dialog = getattr(self, "_instance_settings", None)
        import shiboken6

        if dialog is None or not shiboken6.isValid(dialog):
            dialog = ModernSettingsDialog(
                self.manager.config, window, include_ai=getattr(window, "include_ai", True), standalone=True, initial_page=self.settings_domain
            )
            self._instance_settings = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self.summary_label.setText("已明确打开当前实例配置；已加载的 host 保持进程版本 pin，升级或卸载可能需要此设置进程自然退出。")

    @Slot(object)
    def _result(self, result):
        if isinstance(result, (Inspection, OperationResult)) and result.feature_id != self.feature_id:
            return
        if isinstance(result, Inspection):
            self.inspection = result
            if self.last_operation is None:
                if result.pending_transaction:
                    self.status_label.setText("存在未完成事务：需要安全重试或下次启动确认。")
                elif result.status == "recovery_required":
                    self.status_label.setText("安装账本需要恢复；不从目录猜测状态。")
                else:
                    self.status_label.setText(
                        "已" + ("启用" if result.enabled else "停用") + " · " + result.active if result.active else "未安装：选择本地功能包目录或 ZIP。"
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
                else f"来源：{plan.source_type or '现有账本'}；本地包结构、兼容性与文件完整性已验证，来源由用户选择。"
            )
            rollback = "已接受卸载不能取消并重新启用；重装是独立操作。" if uninstall else f"回滚目标：{plan.active or '无'}。"
            self.summary_label.setText(
                "\n".join(
                    (
                        f"操作：{plan.kind} · 版本：{version}",
                        source,
                        f"预计新增：{plan.estimated_bytes:,} 字节；" + rollback,
                        (
                            "影响：拒绝新请求、取消流式响应并排空会话写入；驻留 AI host 等待自然退出，不热替换。"
                            if self.feature_id == AI_OWNER
                            else "影响：拒绝新任务、停止所属 Worker；驻留 host 等待自然退出，不热替换。"
                        ),
                        "卸载仅删除已安装副本；保留原始 ZIP／源目录、个人设置、profile、凭据、记忆、额度和聊天历史。",
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


def mount_local_feature_manager(dialog, _route, manager):
    """Mount a generic manager after a manifest-directed handoff."""
    owner = str(getattr(manager, "feature_id", "") or "")
    if not owner:
        return None
    existing = dialog.feature_management_widgets.get(owner)
    if existing is not None:
        return existing
    dialog.feature_managers[owner] = manager
    if manager.endpoint is not None:
        dialog._feature_draft_unsubscribers.append(manager.endpoint.register_draft(dialog._feature_scope, lambda: dialog._feature_draft_dirty(owner)))
    widget = FeatureManagementWidget(manager, dialog)
    widget.local_package_routed.connect(dialog._mount_local_feature_manager)
    dialog.feature_management_widgets[owner] = widget
    dialog._local_feature_management_widgets[owner] = widget
    dialog._local_feature_management_layout.addWidget(widget)
    dialog._local_feature_management_empty.hide()
    return widget


def create_local_package_rows(dialog):
    """Compose local import/management using the shared settings primitives."""
    dialog.local_package_zip_button = QPushButton("选择本地 ZIP", dialog)
    dialog.local_package_directory_button = QPushButton("选择本地目录", dialog)
    for button, archive in ((dialog.local_package_zip_button, True), (dialog.local_package_directory_button, False)):
        button.setAccessibleName(button.text())
        button.setAccessibleDescription("自动识别扩展身份，检查完整性后导入并启用。只选择你信任的包；包内 Python 可以执行，不是沙箱。")
        button.clicked.connect(lambda _checked=False, archive=archive: dialog.feature_management_widget.choose_local_package(archive))
    local_package_actions = ResponsiveActionRow(dialog.local_package_zip_button, [dialog.local_package_directory_button], dialog)
    dialog._local_feature_management_widgets = {}
    dialog._local_feature_management_container = QWidget(dialog)
    dialog._local_feature_management_container.setAccessibleName("本地第三方扩展管理")
    dialog._local_feature_management_container.setAccessibleDescription("用户选择的本地扩展按 manifest owner 分开管理；本地信任不代表 Core 沙箱。")
    dialog._local_feature_management_layout = QVBoxLayout(dialog._local_feature_management_container)
    dialog._local_feature_management_layout.setContentsMargins(0, 0, 0, 0)
    dialog._local_feature_management_layout.setSpacing(14)
    dialog._local_feature_management_empty = QLabel(
        "尚未发现已安装的本地第三方扩展。选择 ZIP 或目录后，会自动出现在这里。", dialog._local_feature_management_container
    )
    dialog._local_feature_management_empty.setWordWrap(True)
    dialog._local_feature_management_empty.setObjectName("settingHint")
    dialog._local_feature_management_layout.addWidget(dialog._local_feature_management_empty)
    for owner, manager in dialog.feature_managers.items():
        if owner not in (SCREEN_OWNER, AI_OWNER):
            dialog._mount_local_feature_manager(None, manager)
    for owner in (SCREEN_OWNER, AI_OWNER):
        dialog.feature_management_widgets[owner].local_package_routed.connect(dialog._mount_local_feature_manager)
    return (
        SettingRow(
            "local_package_import",
            "导入本地功能包",
            "选择 ZIP 或目录后按 manifest 自动识别、检查并启用。只导入你信任的包：包内 Python 可以执行，不是沙箱。升级已加载代码需要重启。",
            local_package_actions,
            stacked=True,
        ),
        SettingRow(
            "local_feature_packages",
            "本地第三方扩展",
            "未知 owner 的用户选择包按自身 manifest 分流到独立管理器；不会再用官方 owner/factory 列表替代本地身份。",
            dialog._local_feature_management_container,
            stacked=True,
        ),
    )


def create_feature_management_section(dialog, parent):
    """Keep extension UI assembly out of the settings window lifecycle."""

    dialog.feature_management_widgets = {owner: FeatureManagementWidget(dialog.feature_managers[owner], dialog) for owner in (SCREEN_OWNER, AI_OWNER)}
    dialog.feature_management_widget = dialog.feature_management_widgets[SCREEN_OWNER]
    local_import_row, local_management_row = create_local_package_rows(dialog)
    return SettingsSection(
        "扩展管理",
        [
            local_import_row,
            SettingRow(
                "feature_packages",
                "屏幕理解功能包",
                "扩展、插件、屏幕理解：本地安装、升级、启停、回滚与卸载。包级操作影响所有实例。",
                dialog.feature_management_widgets[SCREEN_OWNER],
                stacked=True,
            ),
            SettingRow(
                "ai_feature_package",
                "AI 对话功能包",
                "扩展、插件、AI、聊天、文件理解：本地安装、升级、启停、回滚与卸载。两个包独立管理，个人数据保留。",
                dialog.feature_management_widgets[AI_OWNER],
                stacked=True,
            ),
            local_management_row,
        ],
        parent,
    )
