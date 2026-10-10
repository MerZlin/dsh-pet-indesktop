"""Small local resource-control channel; Qt objects stay on their owning thread."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .feature_state_io import StateError, open_kernel_lock, safe_path
from .mod_management import ModOutcome, ResourceMods

_LIMIT = 16384
_LOG = logging.getLogger(__name__)


def _name(root, identity):
    digest = hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:16]
    return "dsh-mod-" + digest + "-" + identity[:16]


class ResourceServer(QObject):
    def __init__(self, data_root, handler, parent=None):
        super().__init__(parent)
        self.root, self.handler = Path(data_root), handler
        self.identity = uuid4().hex
        self.lock_path = self.root / ".mod-runtime" / (self.identity + ".lock")
        self.lock = open_kernel_lock(self.lock_path)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(16)
        self.sockets = {}
        self.server.newConnection.connect(self._accept)
        if not self.server.listen(_name(self.root, self.identity)):
            self.lock.close()
            raise RuntimeError("角色控制通道无法启动")

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.sockets) >= 16:
                socket.abort()
                socket.deleteLater()
                continue
            socket.setReadBufferSize(_LIMIT + 1)
            self.sockets[socket] = bytearray()
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.setInterval(15000)
            timer.timeout.connect(socket.abort)
            timer.start()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._drop(s))
            self._read(socket)

    def _drop(self, socket):
        self.sockets.pop(socket, None)
        import shiboken6
        if shiboken6.isValid(socket):
            socket.deleteLater()

    def _read(self, socket):
        data = self.sockets.get(socket)
        if data is None:
            return
        data.extend(bytes(socket.read(_LIMIT + 1 - len(data))))
        if len(data) > _LIMIT:
            socket.abort()
            return
        if b"\n" not in data:
            return
        try:
            request = json.loads(bytes(data).decode("utf-8"))
            if not isinstance(request, dict) or request.get("command") not in {"use", "release"}:
                raise ValueError("invalid command")
            reply = self.handler(request)
        except Exception:
            _LOG.exception("Resource request rejected")
            reply = {"ok": False, "message": "角色切换或释放失败，请自然退出桌宠后重试"}
        socket.write(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
        socket.disconnectFromServer()
        self.sockets.pop(socket, None)

    def close(self):
        for socket in tuple(self.sockets):
            socket.abort()
        self.sockets.clear()
        self.server.close()
        self.lock.close()
        # Only this server's own verified notification file is removed.
        try:
            safe_path(self.lock_path)
            self.lock_path.unlink(missing_ok=True)
        except (OSError, StateError):
            pass


class ResourceClient:
    def __init__(self, data_root, instance_id=""):
        self.root, self.instance_id = Path(data_root), instance_id

    def request_all(self, request):
        directory = self.root / ".mod-runtime"
        safe_path(directory)
        if not directory.exists():
            return []
        replies = []
        locks = list(directory.glob("*.lock"))
        if len(locks) > 128:
            return [{"ok": False, "message": "角色控制记录过多，请先自然退出桌宠"}]
        for path in locks:
            if not re.fullmatch(r"[a-f0-9]{32}", path.stem):
                continue
            try:
                # Stale notification files are ignored only with kernel proof.
                with open_kernel_lock(path, create=False):
                    continue
            except StateError as exc:
                if exc.code == "missing":
                    continue
                if exc.code != "lock_busy":
                    return [{"ok": False, "message": "无法检查桌宠占用，请自然退出后重试"}]
            replies.append(self._request(path.stem, request))
        return replies

    def _request(self, identity, request):
        # Run an event loop in the calling background thread. Windows named
        # pipes need queued completion notifications; blocking write waits alone
        # can starve them (and an in-process Core's Python GUI callbacks).
        from PySide6.QtCore import QEventLoop
        socket = QLocalSocket()
        socket.setReadBufferSize(_LIMIT + 1)
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setInterval(15000)
        data, replies = bytearray(), []
        def read():
            data.extend(bytes(socket.read(_LIMIT + 1 - len(data))))
            if len(data) > _LIMIT:
                loop.quit()
            elif b"\n" in data:
                try:
                    reply = json.loads(data.decode())
                    if isinstance(reply, dict) and type(reply.get("ok")) is bool:
                        replies.append(reply)
                except (ValueError, UnicodeError):
                    pass
                loop.quit()
        socket.connected.connect(lambda: socket.write(json.dumps(request).encode() + b"\n"))
        socket.readyRead.connect(read)
        socket.disconnected.connect(loop.quit)
        socket.errorOccurred.connect(loop.quit)
        timer.timeout.connect(loop.quit)
        try:
            timer.start()
            socket.connectToServer(_name(self.root, identity))
            loop.exec()
            if not replies and socket.bytesAvailable():
                read()
            return replies[0] if replies else {"ok": False, "message": "桌宠未响应，请自然退出相关桌宠后重试"}
        finally:
            timer.stop()
            socket.abort()

    def prepare_removal(self, entry):
        return all(reply["ok"] for reply in self.request_all({"command": "release", "id": entry.id}))

    def use(self, entry):
        replies = self.request_all({"command": "use", "id": entry.id, "instance": self.instance_id})
        matched = [reply for reply in replies if reply.get("matched")]
        if len(matched) == 1 and matched[0]["ok"]:
            return ModOutcome(True, "当前桌宠已换装，其他实例保持不变")
        reason = next((reply.get("message") for reply in replies if not reply["ok"]), None)
        return ModOutcome(False, reason or "未找到当前桌宠实例，请从需要换装的桌宠打开设置")


def _fallback(data_root):
    from .content.registry import CharacterRegistry
    registry = CharacterRegistry(installed_root=Path(data_root) / ".no-installed-characters", legacy_roots=[])
    builtin = next((item for item in registry.list_available() if item.source == "bundled"), None)
    if builtin is None:
        return None
    return builtin.character_id, builtin.video_dir


def release_resource_instances(shell, entry, *, fallback=None):
    fallback = fallback or _fallback(shell.config.dir)
    # All managed versions, not only the selected version; compare actual source
    # so an installed override with the built-in ID cannot evade release.
    versions = entry.path.parent.resolve()
    affected = []
    for instance in shell.instances:
        lib = getattr(getattr(instance, "win", None), "lib", None)
        source = getattr(lib, "_asset_dir", None)
        if source is not None and Path(source).resolve().is_relative_to(versions):
            affected.append(instance)
    if not affected:
        return True
    if fallback is None:
        return False
    return all(instance.switch_character(fallback[0], asset_dir=fallback[1], force=True) for instance in affected)


def handle_resource_request(shell, request):
    rows = ResourceMods(shell.config.dir).entries()
    entry = next((row for row in rows if row.id == request.get("id")), None)
    if entry is None:
        return {"ok": False, "message": "角色资源已变化，请刷新列表后重试"}
    if request["command"] == "release":
        if not entry.managed:
            return {"ok": False, "message": "内置或手动目录不由管理器删除"}
        return {"ok": release_resource_instances(shell, entry), "message": "已释放旧角色"}
    target = next((instance for instance in shell.instances if str(instance.config.instance_id or "") == request.get("instance")), None)
    if target is None:
        return {"ok": True, "matched": False}
    if not entry.enabled:
        return {"ok": False, "matched": True, "message": "请先启用角色资源"}
    from .content.registry import CharacterRegistry
    if entry.managed:
        registry = CharacterRegistry(installed_root=shell.config.dir / "content/characters")
        character = registry.get(entry.character_id)
        source = character.video_dir if character is not None else None
    else:
        # Unmanaged row's root comes from the trusted registry, never the request.
        source = entry.path / "videos"
    if source is None or not source.is_dir():
        return {"ok": False, "matched": True, "message": "角色素材不可用"}
    ok = target.switch_character(entry.character_id, asset_dir=source, force=True)
    return {"ok": bool(ok), "matched": True, "message": "当前桌宠已换装" if ok else "换装失败，原角色保留"}
