"""Same-user, bounded QLocal preparation; never arbitrary commands or paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .feature_lifecycle import _Reply
from .feature_lifecycle_contract import LifecyclePrepareRequest, verify_request
from .feature_package_transactions import RuntimePreparation
from .feature_version_lease import process_owner_identity

_MAX_BYTES = 65536


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate lifecycle field")
        result[key] = value
    return result


def _read_frame(document):
    if document.count(b"\n") != 1 or not document.endswith(b"\n"):
        raise ValueError("one complete lifecycle message only")
    result = json.loads(document, object_pairs_hook=_unique_fields)
    if not isinstance(result, dict):
        raise ValueError("lifecycle object required")
    return result


def endpoint_name(root: Path, identity: str) -> str:
    import re

    if not re.fullmatch(r"[a-f0-9]{32}", identity):
        raise ValueError("invalid owner identity")
    material = str(root.resolve()).casefold() + "|" + identity
    return "dsh-fl-" + hashlib.sha256(material.encode()).hexdigest()[:32]


class FeatureLifecycleServer(QObject):
    def __init__(self, endpoint, parent=None):
        super().__init__(parent)
        self.endpoint = endpoint
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(16)
        self.sockets = {}
        self.server.newConnection.connect(self._accept)
        self.name = endpoint_name(endpoint.store.root, process_owner_identity())
        if not self.server.listen(self.name):
            # No stale endpoint removal: an uncertain peer cannot be displaced.
            raise RuntimeError("lifecycle_endpoint_unavailable")

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.setReadBufferSize(_MAX_BYTES + 1)
            if len(self.sockets) >= 16:
                socket.abort()
                socket.deleteLater()
                continue
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.setInterval(5000)
            timer.timeout.connect(socket.abort)
            timer.start()
            self.sockets[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._drop(s))
            self._read(socket)

    def _drop(self, socket):
        self.sockets.pop(socket, None)
        socket.deleteLater()

    def _read(self, socket):
        buffer = self.sockets.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.read(_MAX_BYTES + 1 - len(buffer))))
        if len(buffer) > _MAX_BYTES:
            socket.abort()
            return
        if b"\n" not in buffer:
            return
        try:
            document = _read_frame(buffer)
            request = LifecyclePrepareRequest.parse(document)
            verify_request(self.endpoint.store, request)
            reply = _Reply(request)
            self.endpoint._prepare(reply)  # Same owning GUI thread, no wait.
            result = reply.result
        except Exception:
            result = RuntimePreparation("awaiting_release", "lifecycle_request_rejected")
        payload = {"status": result.status, "reason": result.reason, "details": dict(result.details)}
        data = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        if len(data) > _MAX_BYTES:
            data = b'{"status":"awaiting_release","reason":"lifecycle_reply_limit","details":{}}\n'
        socket.write(data)
        socket.disconnectFromServer()
        self.sockets.pop(socket, None)

    def close(self):
        for socket in tuple(self.sockets):
            socket.abort()
        self.sockets.clear()
        self.server.close()


def request_peer(root, identity, request, *, timeout=10):
    import time

    if not 0 < timeout <= 30:
        raise ValueError("invalid lifecycle timeout")
    socket = QLocalSocket()
    socket.setReadBufferSize(_MAX_BYTES + 1)
    try:
        payload = json.dumps(request.document(), separators=(",", ":")).encode() + b"\n"
        if len(payload) > _MAX_BYTES:
            return RuntimePreparation("awaiting_release", "lifecycle_request_limit")
        socket.connectToServer(endpoint_name(root, identity))
        deadline = time.monotonic() + timeout

        def budget():
            return max(1, int((deadline - time.monotonic()) * 1000))

        if not socket.waitForConnected(budget()):
            return RuntimePreparation("awaiting_release", "lifecycle_peer_unavailable")
        socket.write(payload)
        if not socket.waitForBytesWritten(budget()):
            return RuntimePreparation("awaiting_release", "lifecycle_send_failed")
        data = bytearray()
        while time.monotonic() < deadline:
            data.extend(bytes(socket.read(_MAX_BYTES + 1 - len(data))))
            if len(data) > _MAX_BYTES:
                return RuntimePreparation("awaiting_release", "lifecycle_reply_limit")
            if b"\n" in data:
                document = _read_frame(data)
                if document.get("status") not in ("ready", "awaiting_release", "draft_blocked") or not isinstance(document.get("details"), dict):
                    break
                if document["status"] == "ready" and document["details"].get("prepared_nonce") != request.nonce:
                    break
                return RuntimePreparation(document["status"], document.get("reason"), details=document["details"])
            if not socket.waitForReadyRead(budget()) and not socket.bytesAvailable():
                break
        return RuntimePreparation("awaiting_release", "lifecycle_reply_invalid_or_timeout")
    except Exception:
        return RuntimePreparation("awaiting_release", "lifecycle_peer_failed")
    finally:
        socket.abort()


class CrossProcessFeatureLifecycle:
    def __init__(self, service, local=None):
        self.service, self.local = service, local

    def prepare(self, request):
        import os

        occupancy = [self.service.leases.inspect_occupancy(version) for version in request.versions]
        if any(item.status not in ("free", "occupied") for item in occupancy):
            return RuntimePreparation("awaiting_release", "lease_evidence_uncertain")
        peers = {(lease.owner_pid, lease.owner_identity) for item in occupancy for lease in item.leases if lease.kind in ("host", "settings")}
        if self.local is not None:
            local = self.local.prepare(request)
            if local.status != "ready":
                return local
        for pid, identity in sorted(peers):
            if pid == os.getpid() and self.local is not None:
                continue
            result = request_peer(self.service.store.root, identity, request)
            if result.status != "ready":
                return RuntimePreparation(result.status, result.reason, details={**dict(result.details), "pid": pid, "owner_identity": identity})
        return RuntimePreparation()
