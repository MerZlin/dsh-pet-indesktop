"""Running Core adapter discovers a package committed by separate settings."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.feature_management import FeatureManagementRuntime
from pet.modern_settings_dialog import ModernSettingsDialog
from scripts.build_mod_example import build_example
from tests.test_feature_package_transactions import StubChecker
from tests.test_mod_center_integration import pump


def test_live_core_discovers_new_package_and_revokes_old_menu_handles(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    output = build_example(Path('examples/mods/hello-local'), tmp_path / 'package')
    script = r'''
import faulthandler, json, sys, time
from pathlib import Path
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication
from pet.config import Config
from pet.plugins.feature_host import FeatureHost
from pet.mod_core import CoreModSupport
app = QApplication([])
root = Path(sys.argv[1])
faulthandler.dump_traceback_later(120, repeat=True)
class Shell(QObject):
    def __init__(self):
        super().__init__()
        self.config = Config(base=root / 'data')
        self.feature_host = FeatureHost()
        self.feature_managers = {}
        self.tray = None
shell = Shell()
support = CoreModSupport(shell)
handles = []
last = None
def snapshot():
    global last
    active = shell.feature_host.enabled('demo.hello-local')
    current = shell.feature_host.registry.list(scope=support.mounts.scope, kind='menu')
    if current and not handles:
        handles.extend(current)
    manager = shell.feature_managers.get('demo.hello-local')
    result = getattr(manager, 'last_result', None)
    doc = {'manager_status': getattr(result, 'status', None), 'manager_reason': getattr(result, 'reason', None),
           'host_state': shell.feature_host.state('demo.hello-local'), 'enabled': active, 'running': 'demo.hello-local' in support.mounts.running,
           'stale': bool(handles) and not handles[0].active}
    text = json.dumps(doc)
    if text != last:
        path = root / 'state.tmp'
        path.write_text(text)
        path.replace(root / 'state.json')
        with (root / 'state-history.jsonl').open('a', encoding='utf8') as history:
            history.write(json.dumps({'time': time.monotonic(), 'state': doc}) + '\n')
        last = text
    if (root / 'stop').exists():
        (root / 'shutdown-stage').write_text('closing-support')
        support.close()
        (root / 'shutdown-stage').write_text('closing-managers')
        for manager in shell.feature_managers.values():
            manager.close()
        (root / 'shutdown-stage').write_text('quitting-event-loop')
        app.quit()
timer = QTimer()
timer.timeout.connect(snapshot)
timer.start(30)
QTimer.singleShot(600000, app.quit)
(root / 'ready').touch()
app.exec()
(root / 'shutdown-stage').write_text('event-loop-returned')
faulthandler.cancel_dump_traceback_later()
'''
    child_log = (tmp_path / 'child-stderr.log').open('w', encoding='utf8')
    child_out = (tmp_path / 'child-stdout.log').open('w', encoding='utf8')
    child = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], stdout=child_out, stderr=child_log,
                             env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, text=True)
    dialog = None
    try:
        pump(app, lambda: (tmp_path / 'ready').exists() or child.poll() is not None, timeout=120)
        assert (tmp_path / 'ready').exists(), (tmp_path / 'child-stderr.log').read_text(encoding='utf8')
        dialog = ModernSettingsDialog(Config(base=tmp_path / 'data'), include_ai=False, standalone=True)
        results = []
        dialog.mod_controller.result.connect(lambda key, outcome: results.append((key, outcome)))
        original = FeatureManagementRuntime.submit_local_source
        def submit(manager, *args, **kwargs):
            manager.service.self_checker = StubChecker()
            return original(manager, *args, **kwargs)
        monkeypatch.setattr(FeatureManagementRuntime, 'submit_local_source', submit)
        assert dialog.mod_controller.import_source(output)
        pump(app, lambda: not dialog.mod_controller.busy, timeout=120)
        assert results and results[-1][1].success, results
        key = '功能扩展:demo.hello-local'
        assert dialog.mod_controller.perform('enable', [key])
        pump(app, lambda: not dialog.mod_controller.busy, timeout=120)
        assert len(results) == 2 and results[-1][1].success, results
        def state(field):
            import json
            try:
                return json.loads((tmp_path / 'state.json').read_text()).get(field)
            except (OSError, ValueError):
                return False
        pump(app, lambda: state('running'), timeout=120)
        assert dialog.mod_controller.perform('disable', [key])
        pump(app, lambda: not dialog.mod_controller.busy, timeout=120)
        pump(app, lambda: state('stale') and not state('running'), timeout=120)
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            app.processEvents()
        (tmp_path / 'stop').touch()
        # Wait for the actual Core process, not inherited pipe handles in helpers.
        # Both output streams remain available even if process shutdown times out.
        try:
            child.wait(timeout=120)
        finally:
            child_log.close()
            child_out.close()
        assert child.returncode == 0, (tmp_path / 'child-stderr.log').read_text(encoding='utf8')
        assert (tmp_path / 'shutdown-stage').read_text() == 'event-loop-returned'


def test_core_mod_support_accepts_real_non_qobject_app_shell(tmp_path):
    from pet.app import AppShell
    from pet.mod_core import CoreModSupport
    from pet.plugins.feature_host import FeatureHost
    app = QApplication.instance() or QApplication([])
    shell = AppShell.__new__(AppShell)
    shell.config = Config(base=tmp_path)
    shell.feature_host = FeatureHost()
    shell.feature_managers = {}
    support = CoreModSupport(shell)
    try:
        assert support.monitor.timer.isActive()
        assert support.resources.server.isListening()
    finally:
        support.close()
        app.processEvents()


@pytest.mark.parametrize("lock_kind", ["management", "state"])
def test_new_core_discovery_retries_lock_before_startup_exists(tmp_path, lock_kind):
    from types import SimpleNamespace

    from pet.feature_management import attach_feature_management
    from pet.feature_state_io import open_kernel_lock, state_lock
    from pet.mod_core import CoreModSupport
    from pet.plugins.feature_host import FeatureHost
    from tests.test_feature_package_transactions import _confirm

    app = QApplication.instance() or QApplication([])
    config = Config(base=tmp_path / "data")
    output = build_example(Path("examples/mods/hello-local"), tmp_path / "package")
    shell = SimpleNamespace(config=config, feature_host=FeatureHost(), feature_managers={}, tray=None)
    manager = attach_feature_management(config, shell.feature_host, feature_id="demo.hello-local", management_only=True)
    shell.feature_managers[manager.feature_id] = manager
    service = manager.service
    service.self_checker = StubChecker()  # Only the external OS probe boundary.
    service.runtime = None  # No host has executed this newly installed package.
    assert _confirm(service, service.preflight_install(output)).status == "awaiting_startup_confirmation"
    support = CoreModSupport(shell)
    try:
        lock = open_kernel_lock(service.management_lock_path) if lock_kind == "management" else state_lock(service.store.lock_path)
        with lock:
            support.refresh()
            assert manager.startup is None
            assert manager.last_result.reason in {"management_lock_busy", "lock_busy", "state_lock_busy"}
            assert manager._bootstrap_timer.isActive(), "retry must not require an already-created startup"
        # No further import, save, catalog token change or window close is needed.
        pump(app, lambda: "demo.hello-local" in support.mounts.running)
        assert shell.feature_host.enabled("demo.hello-local")
    finally:
        support.close()
        manager.close()
        app.processEvents()
