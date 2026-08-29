# -*- coding: utf-8 -*-
"""多开桌宠 IPC 会话。

所有 QLocal 对象、协议解析、成员表和协调 tick 都属于 ``_CollisionWorker``，
该对象只在专用 QThread 中运行。GUI 侧只通过 queued Signal 发送状态和接收结果。
关闭顺序：停止状态生产 -> leave -> 关闭 socket/server -> 停 timer -> quit/wait。
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import secrets
import sys
from dataclasses import asdict
from typing import Any

from PySide6.QtCore import QMetaObject, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import collision
from .config import APP_DIR_NAME
from . import slot_manager


def collision_server_name(base=None) -> str:
    """按应用变体和当前用户身份生成隔离的本机命名服务名。"""
    if sys.platform == "win32":
        identity = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    else:
        identity = str(getattr(os, "getuid", lambda: 0)())
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{APP_DIR_NAME}-{digest}-collision"


def make_runtime_id(instance_id: str = "", pid: int | None = None) -> str:
    prefix = str(instance_id or "").strip() or "instance"
    return f"{prefix}-pid{pid or os.getpid()}-{secrets.token_hex(4)}"


class _CollisionWorker(QObject):
    impulse_ready = Signal(object)
    snapshot_ready = Signal(object)
    policy_changed = Signal(object)
    role_changed = Signal(bool, str)
    error = Signal(str)
    _local_election_names: set[str] = set()

    def __init__(self, name: str, runtime_id: str, instance_id: str, policy: dict[str, Any], lock_path=None):
        super().__init__()
        self.name, self.runtime_id, self.instance_id = name, runtime_id, instance_id
        self.policy = dict(policy)
        self.server = None
        self.socket = None
        self._had_client_connection = False
        self._probe = None
        self._coordinator_announced = False
        self.peers: dict[QLocalSocket, str] = {}
        self.members: dict[str, dict[str, Any]] = {}
        self.latest_state: dict[str, Any] = {}
        self.epoch = ""
        self.tick = 0
        self.overlap_history: dict[str, int] = {}
        self.watermarks = collision.WatermarkDeduplicator()
        self._timers: list[QTimer] = []
        self._election_timer = None
        self._welcome_timer = None
        self._welcome_retries = 0
        self._hello_sent = False
        self._welcomed_peers: set[Any] = set()
        self._last_control_message = 0.0
        self._client_watchdog = None
        self._coordinator_lock = None
        self._lock_path = lock_path
        self._last_snapshot_at = 0.0
        self._stopping = False

    @Slot()
    def start(self) -> None:
        self._schedule_election()

    def _schedule_election(self) -> None:
        if self._stopping:
            return
        if self._election_timer:
            self._election_timer.stop()
        self._election_timer = QTimer(self)
        self._election_timer.setSingleShot(True)
        self._election_timer.timeout.connect(self._try_election)
        self._election_timer.start(secrets.randbelow(201) + 50)

    @Slot()
    def _try_election(self) -> None:
        if self._stopping or self.server is not None or self.socket is not None:
            return
        server = QLocalServer(self)
        if self._lock_path is not None:
            self._coordinator_lock = slot_manager.acquire_file_lock(self._lock_path)
            if self._coordinator_lock is None:
                server.deleteLater()
                self._connect_client()
                return
        if server.listen(self.name):
            # 同一 Qt 进程中的 QLocalServer 在 Windows 上可能允许并行
            # listen；进程内登记用于测试/嵌入式 harness 的同名候选收敛。
            if self.name in self._local_election_names:
                server.close()
                server.deleteLater()
                slot_manager.release_file_lock(self._coordinator_lock)
                self._coordinator_lock = None
                self._connect_client()
                return
            self._local_election_names.add(self.name)
            self.server = server
            self.epoch = secrets.token_hex(12)
            server.newConnection.connect(self._accept_connection)
            self._start_probe()
            return
        # 只有连接验证失败并短暂重试后才清理残留服务。
        server.deleteLater()
        slot_manager.release_file_lock(self._coordinator_lock)
        self._coordinator_lock = None
        self._connect_client()

    def _start_probe(self) -> None:
        """Windows 命名管道允许并行 listen 时，用 QLocal 连接稳定决胜。"""
        probe = QLocalSocket(self)
        self._probe = probe
        probe.connected.connect(lambda: self._send(probe, {
            "type": "probe", "runtime_id": self.runtime_id,
        }))
        probe.readyRead.connect(lambda: self._read_socket(probe))
        probe.errorOccurred.connect(lambda _error: self._announce_coordinator())
        probe.connectToServer(self.name)
        # 候选窗口即使同时 listen，也按会话 ID 给出稳定的 50~250ms 决胜延迟；
        # 较晚者此时应已连到先宣布者并放弃自己的 listener。
        delay = 50 + (int(hashlib.sha256(self.runtime_id.encode()).hexdigest()[:8], 16) % 201)
        QTimer.singleShot(delay, self._announce_coordinator)

    def _announce_coordinator(self) -> None:
        if self.server is None or self._coordinator_announced or self._stopping:
            return
        self._coordinator_announced = True
        self.role_changed.emit(True, self.epoch)
        self._start_coordinator_timers()
        self._register_self()

    def _connect_client(self) -> None:
        if self._stopping or self.socket is not None:
            return
        socket = QLocalSocket(self)
        self.socket = socket
        socket.readyRead.connect(lambda: self._read_socket(socket))
        socket.connected.connect(lambda: self._client_connected(socket))
        socket.disconnected.connect(self._client_lost)
        socket.errorOccurred.connect(lambda _error: self._client_error(socket))
        socket.connectToServer(self.name)

    def _client_error(self, socket) -> None:
        if socket is self.socket and not self._had_client_connection:
            self._client_lost()

    def _client_connected(self, socket) -> None:
        if socket is self.socket:
            self._had_client_connection = True
            self._hello_sent = False
            self._last_control_message = self._now()
            self._welcome_timer = QTimer(self)
            self._welcome_timer.setSingleShot(True)
            self._welcome_timer.timeout.connect(self._welcome_timed_out)
            self._welcome_timer.start(1000)
            self._send_hello()
            self._client_watchdog = QTimer(self)
            self._client_watchdog.timeout.connect(self._check_client_silence)
            self._client_watchdog.start(500)

    def _check_client_silence(self) -> None:
        if self.socket is not None and self._had_client_connection and self._now() - self._last_control_message > 1.5:
            self._welcome_timed_out()

    def _welcome_timed_out(self) -> None:
        """A connected but silent listener is a stale service endpoint."""
        if self._stopping or self.socket is None or not self._had_client_connection:
            return
        socket = self.socket
        self.socket = None
        self._had_client_connection = False
        socket.abort()
        socket.deleteLater()
        self._welcome_retries += 1
        if self._welcome_retries > 1:
            QLocalServer.removeServer(self.name)
        self._schedule_election()

    def _accept_connection(self) -> None:
        while self.server and self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.peers[socket] = ""
            socket.readyRead.connect(lambda s=socket: self._read_socket(s))
            socket.disconnected.connect(lambda s=socket: self._peer_lost(s))
            socket.errorOccurred.connect(lambda _e, s=socket: self._peer_lost(s))

    def _register_self(self) -> None:
        self.members[self.runtime_id] = dict(self.latest_state, runtime_id=self.runtime_id,
                                             instance_id=self.instance_id, last_seen=self._now())

    @staticmethod
    def _now() -> float:
        from time import monotonic
        return monotonic()

    def _welcome(self) -> dict[str, Any]:
        return {"type": "welcome", "epoch": self.epoch, "coordinator_id": self.runtime_id,
                "tick": self.tick, "policy": self.policy,
                "members": [self._public_member(v) for v in self.members.values()]}

    @staticmethod
    def _public_member(member: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in member.items() if k != "last_seen"}

    def _send(self, socket, message: dict[str, Any]) -> None:
        try:
            socket.write(collision.encode_frame(message))
            socket.flush()
        except Exception:
            logging.debug("碰撞 IPC 写入失败", exc_info=True)

    def _read_socket(self, socket) -> None:
        decoder = getattr(socket, "_collision_decoder", None)
        if decoder is None:
            decoder = collision.FrameStreamDecoder()
            socket._collision_decoder = decoder
        for message in decoder.feed(bytes(socket.readAll())):
            if isinstance(message, collision.DecodeError) or not isinstance(message, dict):
                continue
            self._handle_message(socket, message)

    def _handle_message(self, socket, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if socket is self._probe and kind == "coordinator":
            self._resign_to(str(message.get("runtime_id") or ""))
            return
        if self.server is not None:
            if kind == "probe":
                remote_id = str(message.get("runtime_id") or "")
                if remote_id and remote_id != self.runtime_id:
                    if remote_id < self.runtime_id:
                        self._resign_to(remote_id)
                    else:
                        self._send(socket, {"type": "coordinator", "runtime_id": self.runtime_id,
                                            "epoch": self.epoch})
                else:
                    self._send(socket, self._welcome())
                return
            if kind == "hello":
                runtime_id = str(message.get("runtime_id") or "")
                if not runtime_id:
                    return
                self.peers[socket] = runtime_id
                if socket not in self._welcomed_peers:
                    self._welcomed_peers.add(socket)
                    self._send(socket, self._welcome())
            elif kind == "state":
                runtime_id = self.peers.get(socket, self.runtime_id)
                seq = int(message.get("seq", -1))
                old = self.members.get(runtime_id, {})
                if seq <= int(old.get("seq", -1)):
                    return
                state = dict(message, runtime_id=runtime_id, last_seen=self._now())
                if not (int(state.get("flags", 0)) & collision.FLAG_COLLISION_ENABLED):
                    self._remove_member(runtime_id)
                    return
                self.members[runtime_id] = state
            elif kind == "leave":
                self._remove_member(self.peers.get(socket, ""))
        elif kind == "coordinator":
            self._resign_to(str(message.get("runtime_id") or ""))
        elif kind == "welcome":
            epoch = str(message.get("epoch") or "")
            if epoch:
                self._welcome_retries = 0
                if self._welcome_timer:
                    self._welcome_timer.stop()
                    self._welcome_timer.deleteLater()
                    self._welcome_timer = None
                changed = epoch != self.epoch
                self._last_control_message = self._now()
                if changed:
                    self.epoch = epoch
                    self.role_changed.emit(False, epoch)
                    self.policy_changed.emit(message.get("policy") or {})
                    self.snapshot_ready.emit(message)
        elif kind == "snapshot":
            if message.get("epoch", self.epoch) == self.epoch:
                self.snapshot_ready.emit(message)
        elif kind == "impulse":
            pair = str(message.get("pair") or "")
            tick = int(message.get("tick", -1))
            if message.get("epoch") == self.epoch and self.watermarks.should_apply(self.epoch, pair, tick):
                self.impulse_ready.emit(message)

    def _resign_to(self, _winner: str) -> None:
        if self.server is None:
            return
        self.server.close()
        self.server.deleteLater()
        self.server = None
        self._local_election_names.discard(self.name)
        slot_manager.release_file_lock(self._coordinator_lock)
        self._coordinator_lock = None
        for socket in list(self.peers):
            socket.disconnectFromServer()
            socket.deleteLater()
        self.peers.clear()
        self.members.clear()
        for timer in self._timers:
            timer.stop()
            timer.deleteLater()
        self._timers.clear()
        self._coordinator_announced = False
        self._connect_client()

    def _send_hello(self) -> None:
        if self.socket and not self._hello_sent:
            self._hello_sent = True
            self._send(self.socket, {"type": "hello", "runtime_id": self.runtime_id,
                                     "instance_id": self.instance_id, "pid": os.getpid(), "epoch": self.epoch})

    @Slot(object)
    def submit_state(self, state: dict[str, Any]) -> None:
        if self._stopping:
            return
        self.latest_state = dict(state)
        if self.server is not None:
            self.members[self.runtime_id] = dict(state, runtime_id=self.runtime_id,
                                                 instance_id=self.instance_id, last_seen=self._now())
        elif self.socket:
            self._send(self.socket, dict(state, type="state"))

    @Slot(object)
    def set_policy(self, policy: dict[str, Any]) -> None:
        """更新运行期碰撞策略（客户端/协调者共同路径）。

        协调者配置优先：本进程为协调者时，碰撞求解直接使用本 policy；
        非协调者时本地 policy 只在本进程未来接管协调者时才生效。
        """
        if self._stopping:
            return
        self.policy = dict(policy)

    @Slot()
    def submit_leave(self) -> None:
        """客户端主动离开：立即向协调者发送 leave 帧，成员即时移除（不等 stale 超时）。

        与 stop() 不同：只发 leave 不断开 socket，供 detach_collision_session 等
        不销毁会话的退出路径使用。
        """
        if self._stopping or self.socket is None:
            return
        self._send(self.socket, {"type": "leave", "seq": int(self.latest_state.get("seq", 0)) + 1})

    def _start_coordinator_timers(self) -> None:
        heartbeat = QTimer(self)
        heartbeat.timeout.connect(self._heartbeat)
        heartbeat.start(1000)
        tick = QTimer(self)
        tick.timeout.connect(self._coordinator_tick)
        tick.start(33)
        self._timers.extend((heartbeat, tick))

    def _heartbeat(self) -> None:
        if self.socket and self.latest_state:
            self._send(self.socket, dict(self.latest_state, type="state"))
        if self.server is not None:
            now = self._now()
            for runtime_id, state in list(self.members.items()):
                age = now - float(state.get("last_seen", now))
                if age > 3.0:
                    self._remove_member(runtime_id)

    def _coordinator_tick(self) -> None:
        if self._stopping or self.server is None:
            return
        now = self._now()
        active = []
        # 迭代期间会 _remove_member（隐藏/暂停成员即时移除），必须用快照
        for state in list(self.members.values()):
            if now - float(state.get("last_seen", now)) > 1.2:
                continue
            flags = int(state.get("flags", 0))
            if (flags & collision.FLAG_PAUSED) or not (flags & collision.FLAG_VISIBLE):
                self._remove_member(str(state.get("runtime_id", "")))
                continue
            if state.get("flags", 0) & collision.FLAG_VISIBLE:
                defaults = {"vx": 0.0, "vy": 0.0, "mass": 1.0, "is_infinite_mass": False,
                            "flags": 0, "instance_id": "", "character": "", "scale": 0.72,
                            "w": 0.0, "h": 0.0}
                keys = ("runtime_id", "x", "y", "radius_x", "radius_y", "vx", "vy", "mass",
                        "is_infinite_mass", "flags", "instance_id", "character", "scale", "w", "h")
                values = {key: state.get(key, defaults.get(key, 0.0)) for key in keys}
                values["is_infinite_mass"] = bool(int(values["flags"]) & (collision.FLAG_DRAGGING | collision.FLAG_LOCK_POSITION))
                values["mass"] = collision.calculate_mass(
                    values["radius_x"], values["radius_y"],
                    scale=float(values.get("scale", 0.72) or 0.72),
                    collision_mass_scale=self.policy.get("collision_mass_scale", 1.0),
                )
                active.append(collision.MemberState(**values))
        if len(active) < 2 or not self.policy.get("collision_enabled", True):
            return
        self.tick += 1
        results, _, self.overlap_history = collision.solve_multi_body_collision(
            active, self.tick, self.overlap_history,
            restitution=self.policy["collision_restitution"], friction=self.policy["collision_friction"],
            impulse_cap=self.policy["collision_impulse_cap"])
        moving = any(math.hypot(float(v.get("vx", 0)), float(v.get("vy", 0))) > 20 or
                     int(v.get("flags", 0)) & (collision.FLAG_THROWN | collision.FLAG_DRAGGING)
                     for v in self.members.values())
        if now - self._last_snapshot_at >= (0.05 if moving else 0.5):
            self._last_snapshot_at = now
            for peer in self.peers:
                self._send(peer, {"type": "snapshot", "epoch": self.epoch, "tick": self.tick,
                                  "members": [self._public_member(v) for v in self.members.values()]})
        for result in results:
            if result.j == 0 and result.sep == 0:
                continue
            payload = {"type": "impulse", "epoch": self.epoch, **asdict(result)}
            for peer in self.peers:
                self._send(peer, payload)
            self.impulse_ready.emit(payload)

    def _remove_member(self, runtime_id: str) -> None:
        if runtime_id:
            self.members.pop(runtime_id, None)

    def _peer_lost(self, socket) -> None:
        self._welcomed_peers.discard(socket)
        self._remove_member(self.peers.pop(socket, ""))
        try:
            socket.deleteLater()
        except RuntimeError:
            pass

    def _client_lost(self) -> None:
        if self._welcome_timer:
            self._welcome_timer.stop()
            self._welcome_timer.deleteLater()
            self._welcome_timer = None
        if self.socket:
            self.socket.deleteLater()
        self.socket = None
        self._had_client_connection = False
        self._hello_sent = False
        if self._client_watchdog:
            self._client_watchdog.stop()
            self._client_watchdog.deleteLater()
            self._client_watchdog = None
        if not self._stopping:
            # 不凭 error 信号删除命名服务，避免把存活协调者误判为残留。
            self._schedule_election()

    @Slot()
    def stop(self) -> None:
        self._stopping = True
        owns_server = self.server is not None
        if self._election_timer:
            self._election_timer.stop()
            self._election_timer.deleteLater()
            self._election_timer = None
        if self._welcome_timer:
            self._welcome_timer.stop()
            self._welcome_timer.deleteLater()
            self._welcome_timer = None
        if self._probe:
            self._probe.abort()
            self._probe.deleteLater()
            self._probe = None
        if self._client_watchdog:
            self._client_watchdog.stop()
            self._client_watchdog.deleteLater()
            self._client_watchdog = None
        if self.socket:
            socket = self.socket
            self.socket = None
            self._send(socket, {"type": "leave", "seq": int(self.latest_state.get("seq", 0)) + 1})
            socket.disconnectFromServer()
            try:
                socket.deleteLater()
            except RuntimeError:
                pass
        for socket in list(self.peers):
            self._send(socket, {"type": "snapshot", "epoch": self.epoch, "tick": self.tick, "members": []})
            socket.disconnectFromServer()
            socket.deleteLater()
        self.peers.clear()
        if self.server:
            self.server.close()
            self.server.deleteLater()
            self.server = None
            self._local_election_names.discard(self.name)
        slot_manager.release_file_lock(self._coordinator_lock)
        self._coordinator_lock = None
        for timer in self._timers:
            timer.stop()
            timer.deleteLater()
        self._timers.clear()
        if owns_server:
            QLocalServer.removeServer(self.name)
        QTimer.singleShot(0, self.thread().quit)


class CollisionIpcSession(QObject):
    """GUI 线程持有的 IPC facade；不暴露任何 socket 或成员表。"""
    state_submitted = Signal(object)
    policy_submitted = Signal(object)
    leave_submitted = Signal()
    impulse_ready = Signal(object)
    snapshot_ready = Signal(object)
    policy_changed = Signal(object)
    role_changed = Signal(bool, str)

    def __init__(self, config, parent=None, server_name: str | None = None):
        # PetApp 是普通控制器而非 QObject；生命周期由其属性持有。
        super().__init__(parent if isinstance(parent, QObject) else None)
        self.runtime_id = make_runtime_id(getattr(config, "instance_id", ""))
        self._thread = QThread(self)
        policy = {"collision_enabled": bool(config.get("collision_enabled", True)),
                  "collision_restitution": float(config.get("collision_restitution", .82)),
                  "collision_friction": float(config.get("collision_friction", .08)),
                  "collision_mass_scale": float(config.get("collision_mass_scale", 1.0)),
                  "collision_impulse_cap": float(config.get("collision_impulse_cap", 9000.0))}
        self._worker = _CollisionWorker(server_name or collision_server_name(), self.runtime_id,
                                        getattr(config, "instance_id", ""), policy,
                                        lock_path=config.dir / "collision-coordinator.lock")
        self._worker.moveToThread(self._thread)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.started.connect(self._worker.start)
        self._worker.impulse_ready.connect(self.impulse_ready, Qt.ConnectionType.QueuedConnection)
        self._worker.snapshot_ready.connect(self.snapshot_ready, Qt.ConnectionType.QueuedConnection)
        self._worker.policy_changed.connect(self.policy_changed, Qt.ConnectionType.QueuedConnection)
        self._worker.role_changed.connect(self.role_changed, Qt.ConnectionType.QueuedConnection)
        self.state_submitted.connect(self._worker.submit_state, Qt.ConnectionType.QueuedConnection)
        self.policy_submitted.connect(self._worker.set_policy, Qt.ConnectionType.QueuedConnection)
        self.leave_submitted.connect(self._worker.submit_leave, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        self._thread.start()

    def submit_state(self, state: dict[str, Any]) -> None:
        self.state_submitted.emit(dict(state))

    def update_policy(self, policy: dict[str, Any]) -> None:
        """运行中更新碰撞策略：经 queued 调用到 worker 线程，线程安全。"""
        self.policy_submitted.emit(dict(policy))

    def submit_leave(self) -> None:
        """主动向协调者发 leave：成员即时移除，不等 stale 超时。"""
        self.leave_submitted.emit()

    def stop(self) -> None:
        if self._thread.isRunning():
            self.state_submitted.disconnect(self._worker.submit_state)
            QMetaObject.invokeMethod(self._worker, "stop", Qt.ConnectionType.QueuedConnection)
            if not self._thread.wait(3000):
                self._thread.quit()
                self._thread.wait(1000)
