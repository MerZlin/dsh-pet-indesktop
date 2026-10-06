"""Minimal trusted Core removal entry; no desktop, AI, screenshots or Worker.

The installer owns the exclusive Core code lock through this process and the
subsequent file deletion. This entry only prepares/confirms/recovers the two
real per-owner uninstall transactions. It does not delete Core files, change
user data or interpret a successful dialog close as removal evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path


def finish_core_removal(evidence, *, executable: Path, registry=None) -> int:
    from .core_registration_cleanup import remove_owned_autostart
    from .core_uninstall import CoreRemovalEvidence
    from .official_features import OFFICIAL_FEATURES

    if not isinstance(evidence, CoreRemovalEvidence) or {owner for owner, _ in evidence.revisions} != set(OFFICIAL_FEATURES):
        return 3
    if len(evidence.revisions) != len(OFFICIAL_FEATURES) or any(type(revision) is not int or revision < 0 for _, revision in evidence.revisions):
        return 3
    from .runtime_layout import current_layout

    layout = current_layout()
    if layout is not None:
        from .agent_link import DshMonitor

        if Path(layout.executable).absolute() != Path(executable).absolute():
            return 2
        if not DshMonitor.uninstall_bridge(scope_root=Path(executable).absolute().parent / "_internal" / "integrations" / "dsh-pet-bridge"):
            return 2
    result = remove_owned_autostart(executable, registry=registry)
    return 0 if result.status in {"completed", "idempotent"} else 2


def run_uninstall() -> int:
    from .runtime_layout import RuntimeLayoutError, initialize_for_current_build

    try:
        if initialize_for_current_build(for_core_removal=True) is None:
            return 64
    except RuntimeLayoutError:
        return 2
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .config import Config
    from .core_uninstall import CoreUninstallCoordinator
    from .core_uninstall_ui import CoreUninstallDialog, CoreUninstallRuntime
    from .feature_management import attach_official_management, close_official_management
    from .plugins.feature_host import FeatureHost

    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(True)
    host = FeatureHost()
    managers = attach_official_management(Config(), host, role="maintenance", management_only=True)
    if any(manager.service is None for manager in managers.values()):
        close_official_management(host)
        return 64
    job = CoreUninstallRuntime(CoreUninstallCoordinator({owner: manager.service for owner, manager in managers.items()}), app)
    dialog = CoreUninstallDialog(job)
    dialog.show()
    QTimer.singleShot(0, lambda: job.submit("prepare"))
    try:
        app.exec()
        # No successful exit while an accepted write/delete is still running;
        # the regular quit path is delayed by the real application exit gate.
        if job.busy or not dialog.removal_confirmed:
            return 3
        return finish_core_removal(dialog.removal_evidence, executable=Path(sys.executable))
    finally:
        job.close()
        close_official_management(host)
