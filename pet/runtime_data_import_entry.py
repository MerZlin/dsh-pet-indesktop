"""Closed, Core-owned data import process; no implicit source or normal host."""

from __future__ import annotations

import sys
from pathlib import Path


def launch_data_import() -> bool:
    from .__main__ import _is_unified_frozen_core
    from .runtime_layout import current_layout

    if not _is_unified_frozen_core() or current_layout() is None:
        return False
    from PySide6.QtCore import QProcess

    try:
        started, _pid = QProcess.startDetached(sys.executable, ["--import-local-data"], str(Path(sys.executable).parent))
        return bool(started)
    except Exception:
        return False


def run_data_import() -> int:
    # Defence in depth: no source path from CLI/env and no source fallback.
    from .__main__ import _is_unified_frozen_core
    from .runtime_layout import RuntimeLayout, RuntimeLayoutError

    if not _is_unified_frozen_core() or sys.argv[1:] != ["--import-local-data"]:
        return 64
    try:
        layout = RuntimeLayout.discover(Path(sys.executable))
        barrier = layout.acquire_import_gate()
    except RuntimeLayoutError as exc:
        print("RuntimeLayout: " + str(exc), file=sys.stderr)
        return 2
    with barrier:
        from PySide6.QtWidgets import QApplication

        from .runtime_credential_import import RuntimeCredentialImporter
        from .runtime_data_import import RuntimeDataImporter
        from .runtime_data_import_ui import DataImportDialog, DataImportRuntime

        app = QApplication([sys.argv[0]])
        app.setQuitOnLastWindowClosed(True)
        importer = RuntimeDataImporter(layout, credentials=RuntimeCredentialImporter(layout))
        runtime = DataImportRuntime(importer, app)
        dialog = DataImportDialog(runtime)
        dialog.show()
        try:
            app.exec()
            result = runtime.last_result
            # A successful close/cancel/no-op is not proof of an import.
            return (
                0
                if not runtime.busy
                and result is not None
                and result.plan is not None
                and result.status in ("completed", "idempotent")
                and result.reason != "preflight_cancelled"
                else 3
            )
        finally:
            runtime.close()


def mount_data_import(settings, layout, parent) -> None:
    """A command/deep link in the existing General domain; no preference key."""
    from .runtime_layout import current_layout

    if current_layout() is None:
        return
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    from .feature_management_ui import _WrappingActionButton
    from .settings_widgets import SettingRow, SettingsSection

    control = QWidget(parent)
    box = QVBoxLayout(control)
    box.setContentsMargins(0, 0, 0, 0)
    button = _WrappingActionButton("打开独立旧数据导入工具", control)
    button.setAccessibleName("旧数据导入：选择唯一来源并确认映射")
    button.setAccessibleDescription("只打开确认工具，不读取来源或凭据；写入前需所有 Core 和设置自然退出。")
    hint = QLabel("预检不会读密钥。请先在独立工具确认来源和映射，再自然退出 Core／设置后安全重试。", control)
    hint.setTextFormat(Qt.TextFormat.PlainText)
    hint.setWordWrap(True)

    def launch():
        hint.setText("已请求打开独立工具；未自动导入数据。" if launch_data_import() else "未能启动导入工具；请使用新小 Core，不会回退到旧产品或源码。")

    button.clicked.connect(launch)
    box.addWidget(button)
    box.addWidget(hint)
    settings.data_import_button = button
    layout.addWidget(
        SettingsSection(
            "数据交付",
            [
                SettingRow(
                    "legacy_data_import", "显式导入旧数据", "旧数据、迁移、导入、便携：只选一个来源，原数据不删除，旧功能代码不导入。", control, stacked=True
                )
            ],
            parent,
        )
    )
