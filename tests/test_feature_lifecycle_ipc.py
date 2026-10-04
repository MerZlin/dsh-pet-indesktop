"""Real QLocal cross-process preparation, bounded/authenticated operation intent."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from tests.test_feature_package_transactions import _installed


def test_cross_process_ready_is_not_lease_release_and_unknown_intent_cannot_revoke(tmp_path):
    from pet.feature_lifecycle_contract import LifecyclePrepareRequest, authorize_request
    from pet.feature_lifecycle_ipc import request_peer

    service = _installed(tmp_path)
    child = subprocess.Popen(
        [sys.executable, "-m", "tests._feature_lifecycle_child", str(service.store.root)],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        line = child.stdout.readline()
        assert line, child.stderr.read() if child.poll() is not None else "child not ready"
        peer = json.loads(line)
        request = LifecyclePrepareRequest("lc-" + "a" * 32, service.store.read().state.revision, "uninstall", ("1.2.3",))
        refused = request_peer(service.store.root, peer["identity"], request, timeout=5)
        assert refused.status != "ready"
        authorize_request(service.store, request)
        result = request_peer(service.store.root, peer["identity"], request, timeout=5)
        assert result.status == "ready" and result.details["prepared_nonce"] == request.nonce
        assert service.store.read().state.enabled
        assert "command" not in asdict(request)
    finally:
        if child.stdin:
            child.stdin.write("exit\n")
            child.stdin.flush()
        child.wait(timeout=15)
        assert child.returncode == 0, child.stderr.read()


def test_real_socket_has_bounded_buffer_and_rejects_oversize_without_revocation(tmp_path):
    import threading
    import time

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtNetwork import QLocalSocket
    from PySide6.QtWidgets import QApplication

    from pet.feature_lifecycle import FeatureLifecycleEndpoint
    from pet.feature_lifecycle_ipc import FeatureLifecycleServer
    from pet.plugins.feature_host import FeatureDefinition, FeatureHost

    app = QApplication.instance() or QApplication([])
    service = _installed(tmp_path)
    host = FeatureHost()
    host.provide(FeatureDefinition("official.screen-understanding", (), lambda: None))
    endpoint = FeatureLifecycleEndpoint(service.store, host)
    server = FeatureLifecycleServer(endpoint)
    send = threading.Event()
    done = threading.Event()
    results = []

    def client():
        socket = QLocalSocket()
        try:
            socket.connectToServer(server.name)
            if not socket.waitForConnected(15000):
                results.append("connect_failed")
                return
            if not send.wait(15):
                results.append("gate_timeout")
                return
            # Windows pipeClosed is queued to the socket's owning thread.
            # Blocking waits alone can time out with the notification pending;
            # the real client loop must deliver it, just like production Qt IPC.
            loop = QEventLoop()
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.setInterval(15000)
            timeout.timeout.connect(loop.quit)
            socket.disconnected.connect(loop.quit)
            socket.write(b"x" * (65536 * 2))
            timeout.start()
            if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                loop.exec()
            timeout.stop()
            results.append(socket.state() == QLocalSocket.LocalSocketState.UnconnectedState)
        finally:
            socket.abort()
            done.set()

    thread = threading.Thread(target=client)
    thread.start()
    try:
        deadline = time.monotonic() + 40
        while not server.sockets and time.monotonic() < deadline:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        assert server.sockets
        assert all(socket.readBufferSize() == 65537 for socket in server.sockets)
        send.set()
        while not done.is_set() and time.monotonic() < deadline:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        assert done.is_set() and results == [True], (results, [(s.state(), s.bytesAvailable(), len(b)) for s, b in server.sockets.items()])
        assert host.enabled("official.screen-understanding") and service.store.read().state.enabled
    finally:
        send.set()
        server.close()
        endpoint.close()
        thread.join(40)
