"""Offline Setup intent adapter; all I/O/verification belongs to real managers.

Fixed official ZIP filenames are resolved by the backend. A Setup selection
only starts an unsigned-in-the-sense-of-unaccepted preview: no factory, Worker,
model thread or state activation occurs until the user confirms the exact plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from PySide6.QtCore import QObject, Slot

from .feature_management import FeatureManagementRuntime
from .feature_package_transactions import Inspection
from .official_features import OFFICIAL_FEATURES

if TYPE_CHECKING:
    from PySide6.QtWidgets import QDialog


def parse_intents(args: Sequence[str]) -> tuple[Path, tuple[str, ...]]:
    if not 2 <= len(args) <= 3 or any(not isinstance(value, str) or "\0" in value for value in args):
        raise ValueError("local_package_intent_invalid")
    root, owners = Path(args[0]), tuple(args[1:])
    if not root.is_absolute() or len(set(owners)) != len(owners) or not set(owners) <= set(OFFICIAL_FEATURES):
        raise ValueError("local_package_intent_invalid")
    return root, owners


class LocalPackageIntent(QObject):
    def __init__(self, manager: FeatureManagementRuntime, packages_root: Path):
        super().__init__(manager)
        if manager.feature_id not in OFFICIAL_FEATURES or not Path(packages_root).is_absolute():
            raise ValueError("local_package_intent_invalid")
        self.manager = manager
        self.packages_root = Path(packages_root)
        self.waiting = False
        self.closed = False
        manager.result_ready.connect(self._result)

    def begin(self) -> bool:
        if self.closed or self.waiting or self.manager.builtin:
            return False
        self.waiting = True
        if not self.manager.submit("inspect"):
            self.waiting = False
            return False
        return True

    @Slot(object)
    def _result(self, result) -> None:
        if self.closed or not self.waiting or not isinstance(result, Inspection) or result.feature_id != self.manager.feature_id:
            return
        self.waiting = False
        if result.pending_transaction is not None or result.status == "recovery_required":
            return
        command = "upgrade" if result.active is not None else "install"
        self.manager.submit(command, self.packages_root / (self.manager.feature_id + ".zip"))

    def close(self) -> None:
        # No authority to cancel accepted journals or re-enable removed code.
        self.closed = True
        self.waiting = False


def create_local_package_dialog(managers: dict[str, FeatureManagementRuntime], owners: tuple[str, ...]) -> QDialog:
    """Build the actual local-install UI without starting any package operation."""
    from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget

    from .feature_management_ui import FeatureManagementWidget

    dialog = QDialog()
    dialog.setWindowTitle("安装本地官方扩展：分别预检和确认")
    dialog.setAccessibleName("本地官方功能包确认")
    dialog.resize(720, 650)
    layout = QVBoxLayout(dialog)
    hint = QLabel("Core 已安装。各包独立确认；缺包或失败不会阻止 Core，也不会联网下载。等待启动确认不代表功能已可用。", dialog)
    hint.setWordWrap(True)
    layout.addWidget(hint)
    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    contents = QWidget(scroll)
    body = QVBoxLayout(contents)
    for owner in owners:
        manager = managers[owner]
        widget = FeatureManagementWidget(manager, contents)
        heading = QLabel(widget.feature_title, contents)
        heading.setObjectName("localPackageOwnerHeading")
        heading.setAccessibleName(widget.feature_title)
        heading.setWordWrap(True)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        body.addWidget(heading)
        body.addWidget(widget)
    scroll.setWidget(contents)
    layout.addWidget(scroll)
    return dialog


def run_local_packages(packages_root: Path, owners: tuple[str, ...]) -> int:
    """Normal code-lock/data-root bootstrap has already run in _main."""
    import sys

    from PySide6.QtWidgets import QApplication

    from .async_exit import application_exit_gate
    from .config import Config
    from .feature_management import attach_official_management, close_official_management
    from .plugins.feature_host import FeatureHost

    # Defence in depth for callers other than the closed production entry.
    parse_intents([str(packages_root), *owners])
    app = QApplication([sys.argv[0]])
    host = FeatureHost()
    managers = attach_official_management(Config(), host, role="local-install", management_only=True)
    dialog = create_local_package_dialog(managers, owners)
    intents = [LocalPackageIntent(managers[owner], packages_root) for owner in owners]
    # Closing the management window leaves lifecycle endpoints/event loop alive
    # through accepted writes, deletes and queued GUI preparation.
    gate = application_exit_gate(app)
    token = gate.register("local-package-intents", lambda: None, lambda: all(not manager.busy for manager in managers.values()))
    dialog.show()
    for intent in intents:
        intent.begin()
    try:
        app.exec()
        return (
            0
            if all(managers[owner].last_operation is not None and managers[owner].last_operation.status in ("completed", "idempotent") for owner in owners)
            else 3
        )
    finally:
        for intent in intents:
            intent.close()
        gate.unregister(token)
        close_official_management(host)
