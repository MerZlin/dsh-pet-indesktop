"""Real event-loop/Thread queued lifecycle boundaries, no fixed sleeps."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from pet.feature_lifecycle import FeatureLifecycleEndpoint, LifecyclePrepareRequest, QueuedFeatureLifecycle
from pet.plugins.feature_host import FeatureDefinition, FeatureHost
from tests.test_feature_package_transactions import _installed


def _pump(app, done):
    deadline = time.monotonic() + 15
    while not done.is_set() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    assert done.is_set()


def _background(adapter, request):
    result = []
    done = threading.Event()

    def run():
        try:
            result.append(adapter.prepare(request))
        finally:
            done.set()

    thread = threading.Thread(target=run)
    thread.start()
    return result, done, thread


def test_queued_prepare_stops_and_revokes_only_on_gui_owner_thread(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = _installed(tmp_path)
    host = FeatureHost()
    host.provide(FeatureDefinition("official.screen-understanding", (), lambda: None))
    calls = []
    owner_thread = threading.get_ident()
    host.bind_execution("official.screen-understanding", lambda: calls.append(threading.get_ident()), lambda: None)
    endpoint = FeatureLifecycleEndpoint(service.store, host)
    request = LifecyclePrepareRequest("fixture-operation", service.store.read().state.revision, "uninstall", ("1.2.3",))
    result, done, thread = _background(QueuedFeatureLifecycle(endpoint), request)
    _pump(app, done)
    thread.join(2)
    assert result[0].status == "ready" and calls == [owner_thread]
    assert host.state("official.screen-understanding") == "absent"
    assert service.store.read().state.enabled  # Reply is NOT state commit/release proof.
    endpoint.close()


def test_unsaved_draft_blocks_without_disabling_saving_or_discarding(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = _installed(tmp_path)
    host = FeatureHost()
    host.provide(FeatureDefinition("official.screen-understanding", (), lambda: None))
    endpoint = FeatureLifecycleEndpoint(service.store, host)
    queries = []
    endpoint.register_draft("settings-draft", lambda: queries.append(threading.get_ident()) or True)
    request = LifecyclePrepareRequest("fixture-operation", service.store.read().state.revision, "upgrade", ("1.2.3",))
    result, done, thread = _background(QueuedFeatureLifecycle(endpoint), request)
    _pump(app, done)
    thread.join(2)
    assert result[0].status == "draft_blocked" and host.enabled("official.screen-understanding")
    assert queries == [threading.get_ident()]
    endpoint.close()


def test_timed_out_queued_request_does_not_later_revoke_or_touch_closed_endpoint(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = _installed(tmp_path)
    host = FeatureHost()
    host.provide(FeatureDefinition("official.screen-understanding", (), lambda: None))
    endpoint = FeatureLifecycleEndpoint(service.store, host)
    request = LifecyclePrepareRequest("fixture-operation", service.store.read().state.revision, "uninstall", ("1.2.3",))
    result, done, thread = _background(QueuedFeatureLifecycle(endpoint, timeout=0.02), request)
    assert done.wait(5)
    thread.join(2)
    assert result[0].reason == "lifecycle_prepare_timeout"
    endpoint.close()
    app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    assert host.enabled("official.screen-understanding")
    assert QueuedFeatureLifecycle(endpoint).prepare(request).reason == "queued_lifecycle_requires_background"


def test_gui_prepare_cyclic_draft_collection_preserves_qt_main_thread():
    """A borrowed main-thread wrapper must not become a cyclic endpoint child."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = """
import gc
import json
import weakref
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from pet.feature_lifecycle import FeatureLifecycleEndpoint, LifecyclePrepareRequest, QueuedFeatureLifecycle
from pet.plugins.feature_host import FeatureHost

app = QApplication([])
for iteration in range(3):
    main = app.thread()
    endpoint = FeatureLifecycleEndpoint(None, FeatureHost())
    endpoint.register_draft("cyclic-draft", lambda endpoint=endpoint: endpoint._closed)
    request = LifecyclePrepareRequest("lc-" + "0" * 32, 0, "disable", (), feature_id="acme.thread-regression")
    result = QueuedFeatureLifecycle(endpoint).prepare(request)
    assert result.reason == "queued_lifecycle_requires_background"
    reference = weakref.ref(endpoint)
    del endpoint, main
    gc.collect()
    assert reference() is None, "the cyclic endpoint must actually be collected"
    loop = QEventLoop()
    QTimer.singleShot(0, loop.quit)
    assert loop.exec() == 0
    print(json.dumps({"iteration": iteration, "endpoint_collected": True, "loop_exit": 0}), flush=True)
"""
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    environment["PYTHONPATH"] = str(root) + os.pathsep + environment.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", script], cwd=root, env=environment, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stdout.count('"endpoint_collected": true') == 3
