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
from typing import Any, cast

from PySide6.QtCore import QMetaObject, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import collision
from . import collision_codec
from . import collision_debug
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
        # socket -> 流式帧解码器；只被 worker 线程访问（_read_socket 及其
        # 清理路径都运行在 worker 线程），键为 socket 对象本身，条目在
        # socket 断开/被销毁时同步移除，防止悬挂解码器随 socket 一起泄漏。
        self._socket_decoders: dict[QLocalSocket, collision_codec.FrameStreamDecoder] = {}
        self.members: dict[str, dict[str, Any]] = {}
        self._pending_predicted: dict[str, dict] = {}
        self.previous_members: dict[str, dict[str, Any]] = {}
        self._swept_pair_versions: dict[str, tuple[int, int]] = {}
        self._predicted_pair_ticks: dict[str, int] = {}
        self._position_only_pairs: dict[str, tuple[tuple[float, ...], int]] = {}
        self.latest_state: dict[str, Any] = {}
        self.epoch = ""
        self.tick = 0
        self.overlap_history: dict[str, int] = {}
        self.watermarks = collision_codec.WatermarkDeduplicator()
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
        if not server.listen(self.name):
            # POSIX 下被杀死/崩溃的旧协调者会残留 socket 文件，listen 报
            # AddressInUseError（Windows 命名管道随进程死亡回收，无此问题）。
            # 持有排他文件锁 ⇒ 旧协调者必死（flock 随进程死亡释放），先同步
            # 探测同名服务确实无人应答，再清残留文件重试 listen（issue #42）。
            if (sys.platform != "win32" and self._coordinator_lock is not None
                    and not self._probe_live_server()):
                QLocalServer.removeServer(self.name)
                if server.listen(self.name):
                    self._become_listener(server)
                    return
            server.deleteLater()
            slot_manager.release_file_lock(self._coordinator_lock)
            self._coordinator_lock = None
            self._connect_client()
            return
        self._become_listener(server)

    def _become_listener(self, server) -> None:
        """listen 成功后的收敛：进程内同名候选去重，登记并启动决胜探测。"""
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

    def _probe_live_server(self) -> bool:
        """同步探测同名服务是否有活监听者（仅 POSIX 残留恢复路径使用）。"""
        probe = QLocalSocket()
        try:
            probe.connectToServer(self.name)
            return probe.waitForConnected(300)
        finally:
            probe.abort()

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
        self._socket_decoders.pop(socket, None)
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
            # POSIX 时序窗口：数据可能在 readyRead 槽连接之前就已到达，
            # 不兜底读取的话这批数据不会再触发信号（表现为成员表为空）。
            if socket.bytesAvailable():
                self._read_socket(socket)

    def _register_self(self) -> None:
        self.members[self.runtime_id] = dict(self.latest_state, runtime_id=self.runtime_id,
                                             instance_id=self.instance_id, last_seen=self._now())

    @staticmethod
    def _now() -> float:
        from time import monotonic
        return monotonic()

    def _welcome(self) -> collision_codec.WelcomeMessage:
        return {"type": "welcome", "epoch": self.epoch, "coordinator_id": self.runtime_id,
                "tick": self.tick, "policy": self.policy,
                "members": [self._public_member(v) for v in self.members.values()]}

    @staticmethod
    def _public_member(member: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in member.items() if k != "last_seen"}

    def _send(self, socket, message: collision_codec.WireMessage) -> None:
        try:
            socket.write(collision_codec.encode_frame(message))
            socket.flush()
        except Exception:
            logging.debug("碰撞 IPC 写入失败", exc_info=True)

    def _read_socket(self, socket) -> None:
        decoder = self._socket_decoders.get(socket)
        if decoder is None:
            decoder = collision_codec.FrameStreamDecoder()
            self._socket_decoders[socket] = decoder
        for message in decoder.feed(bytes(socket.readAll())):
            if isinstance(message, collision_codec.DecodeError) or not isinstance(message, dict):
                continue
            # 解码边界收敛：JSON dict -> 协议 TypedDict（cast 仅类型层，
            # 字段缺省/多余由各分支的 .get 容错处理，见 _handle_message）
            self._handle_message(socket, cast(collision_codec.WireMessage, message))

    def _handle_message(self, socket, message: collision_codec.WireMessage) -> None:
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
                if collision_debug.ENABLED:
                    collision_debug.log(self.runtime_id, 'hello_peer', runtime_id=runtime_id,
                                        mapped_runtime_id=self.peers[socket])
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
                if (int(state.get("flags", 0)) & collision.FLAG_PREDICTED_BOUNCE
                        and state.get("bounce_vx") is not None
                        and state.get("bounce_vy") is not None):
                    self._pending_predicted[runtime_id] = {**state, "_captured_at": self._now()}
                if runtime_id in self.members:
                    self.previous_members[runtime_id] = dict(self.members[runtime_id])
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
                    self.latest_state["flags"] = int(self.latest_state.get("flags", 0)) & ~collision.FLAG_PREDICTED_BOUNCE
                    self.latest_state.pop("bounce_vx", None)
                    self.latest_state.pop("bounce_vy", None)
                    self.role_changed.emit(False, epoch)
                    self.policy_changed.emit(message.get("policy") or {})
                    self.snapshot_ready.emit(message)
        elif kind == "snapshot":
            if message.get("epoch", self.epoch) == self.epoch:
                self._last_control_message = self._now()
                self.snapshot_ready.emit(message)
        elif kind == "impulse":
            pair = str(message.get("pair") or "")
            tick = int(message.get("tick", -1))
            if message.get("epoch") == self.epoch:
                self._last_control_message = self._now()
                if self.watermarks.should_apply(self.epoch, pair, tick):
                    self.impulse_ready.emit(message)
                    if collision_debug.ENABLED:
                        collision_debug.log(self.runtime_id, 'impulse_queued', pair=pair, tick=tick)
                elif collision_debug.ENABLED:
                    collision_debug.log(self.runtime_id, 'impulse_discard', pair=pair, tick=tick,
                                        reason='watermark')
            elif collision_debug.ENABLED:
                collision_debug.log(self.runtime_id, 'impulse_discard', pair=pair, tick=tick,
                                    reason='epoch_mismatch')

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
            self._socket_decoders.pop(socket, None)
            socket.deleteLater()
        self.peers.clear()
        self.members.clear()
        self._pending_predicted.clear()
        self.previous_members.clear()
        self._swept_pair_versions.clear()
        self._predicted_pair_ticks.clear()
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
            # 协调者自身的预测反弹也要进捕获队列：本进程状态走进程内直送，
            # 不经过 _handle_message 的 socket 路径，漏掉会导致"主桌宠撞
            # 别人时目标收不到权威冲量"（只有协调者自己的 pet 会踩中）。
            if (int(state.get("flags", 0)) & collision.FLAG_PREDICTED_BOUNCE
                    and state.get("bounce_vx") is not None
                    and state.get("bounce_vy") is not None):
                self._pending_predicted[self.runtime_id] = {
                    **state, "runtime_id": self.runtime_id, "_captured_at": self._now()}
            if self.runtime_id in self.members:
                self.previous_members[self.runtime_id] = dict(self.members[self.runtime_id])
            self.members[self.runtime_id] = dict(state, runtime_id=self.runtime_id,
                                                 instance_id=self.instance_id, last_seen=self._now())
            if collision_debug.ENABLED:
                collision_debug.log(self.runtime_id, 'state_arrive', runtime_id=self.runtime_id,
                                    x=state.get('x'), y=state.get('y'), vx=state.get('vx'),
                                    vy=state.get('vy'), seq=state.get('seq'))
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
        active_previous: dict[str, collision.MemberState] = {}
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
                             "w": 0.0, "h": 0.0, "circles": None}
                keys = ("runtime_id", "x", "y", "radius_x", "radius_y", "vx", "vy", "mass",
                         "is_infinite_mass", "flags", "instance_id", "character", "scale", "w", "h", "circles")
                values = {key: state.get(key, defaults.get(key, 0.0)) for key in keys}
                # 无限质量只认"被拖拽中"（用户手里握着）；lock_position 只是防拖拽，仍可被撞飞（碰碰车/台球需要）
                values["is_infinite_mass"] = bool(int(values["flags"]) & collision.FLAG_DRAGGING)
                values["mass"] = collision.calculate_mass(
                    values["radius_x"], values["radius_y"],
                    scale=float(values.get("scale", 0.72) or 0.72),
                    collision_mass_scale=self.policy.get("collision_mass_scale", 1.0),
                )
                active.append(collision.MemberState(**values))
                previous = self.previous_members.get(str(values["runtime_id"]))
                if previous and previous.get("circles") is not None and values.get("circles") is not None:
                    previous_values = {key: previous.get(key, defaults.get(key, 0.0)) for key in keys}
                    previous_values["runtime_id"] = values["runtime_id"]
                    active_previous[str(values["runtime_id"])] = collision.MemberState(**previous_values)
        if len(active) < 2 or not self.policy.get("collision_enabled", True):
            return
        self.tick += 1
        swept = {}
        sorted_active = sorted(active, key=lambda item: item.runtime_id)
        state_by_id = {str(state.get("runtime_id")): state for state in self.members.values()}
        predicted_results = []
        predicted_pairs = set()
        expired_pending = [rid for rid, snap in self._pending_predicted.items() if now - float(snap.get("_captured_at", now)) > 0.3]
        for rid in expired_pending:
            self._pending_predicted.pop(rid, None)

        for pred_rid, snap in list(self._pending_predicted.items()):
            self._pending_predicted.pop(pred_rid, None)
            bounce_vx = snap.get("bounce_vx")
            bounce_vy = snap.get("bounce_vy")
            if bounce_vx is None or bounce_vy is None:
                continue
            pred_flags = int(snap.get("flags", 0))
            pred_is_inf = bool(pred_flags & collision.FLAG_DRAGGING)
            pred_rx = float(snap.get("radius_x", 0.0))
            pred_ry = float(snap.get("radius_y", 0.0))
            pred_x = float(snap.get("bounce_x", snap.get("x", 0.0)))
            pred_y = float(snap.get("bounce_y", snap.get("y", 0.0)))
            pred_circles = snap.get("bounce_circles") or snap.get("circles")
            pred_scale = float(snap.get("scale", 0.72) or 0.72)
            pred_mass = collision.calculate_mass(
                pred_rx, pred_ry,
                scale=pred_scale,
                collision_mass_scale=self.policy.get("collision_mass_scale", 1.0),
            )
            event_member = collision.MemberState(
                runtime_id=pred_rid,
                x=pred_x,
                y=pred_y,
                radius_x=pred_rx,
                radius_y=pred_ry,
                vx=float(bounce_vx),
                vy=float(bounce_vy),
                mass=pred_mass,
                is_infinite_mass=pred_is_inf,
                flags=pred_flags,
                instance_id=str(snap.get("instance_id", "")),
                character=str(snap.get("character", "")),
                scale=pred_scale,
                w=float(snap.get("w", 0.0)),
                h=float(snap.get("h", 0.0)),
                circles=pred_circles,
            )
            for other in sorted_active:
                if other.runtime_id == pred_rid:
                    continue
                pair = "|".join(sorted((pred_rid, other.runtime_id)))
                if pair in predicted_pairs:
                    continue
                hit = collision.check_collision_members(event_member, other)
                if not hit[0]:
                    previous = self.previous_members.get(pred_rid)
                    if (previous and previous.get("circles") is not None
                            and event_member.circles is not None and other.circles is not None):
                        hit = collision.swept_circle_chain_collision(
                            previous["circles"], event_member.circles,
                            other.circles, other.circles)
                if not hit[0]:
                    continue
                _, nx, ny, overlap, cx, cy = hit
                j, _, _, dvx_other, dvy_other = collision.solve_collision_impulse(
                    event_member, other, nx, ny,
                    restitution=self.policy["collision_restitution"],
                    friction=self.policy["collision_friction"],
                    impulse_cap=self.policy["collision_impulse_cap"],
                )
                sep, _, _, dx_other, dy_other = collision.calculate_position_separation(
                    overlap, nx, ny,
                    0.0 if event_member.is_infinite_mass else 1.0 / event_member.mass,
                    0.0 if other.is_infinite_mass else 1.0 / other.mass,
                )
                a, b = sorted((pred_rid, other.runtime_id))
                other_is_a = other.runtime_id == a
                predicted_results.append(collision.ImpulseResult(
                    tick=self.tick, pair=pair, a=a, b=b, nx=nx, ny=ny, j=j, sep=sep,
                    contact_x=cx, contact_y=cy,
                    dvx_a=dvx_other if other_is_a else 0.0,
                    dvy_a=dvy_other if other_is_a else 0.0,
                    dvx_b=0.0 if other_is_a else dvx_other,
                    dvy_b=0.0 if other_is_a else dvy_other,
                    dx_a=dx_other if other_is_a else 0.0,
                    dy_a=dy_other if other_is_a else 0.0,
                    dx_b=0.0 if other_is_a else dx_other,
                    dy_b=0.0 if other_is_a else dy_other,
                    ax=event_member.x if a == pred_rid else other.x,
                    ay=event_member.y if a == pred_rid else other.y,
                    bx=other.x if b == other.runtime_id else event_member.x,
                    by=other.y if b == other.runtime_id else event_member.y,
                ))
                predicted_pairs.add(pair)
                break
            raw = state_by_id.get(pred_rid)
            if raw is not None:
                raw["flags"] = int(raw.get("flags", 0)) & ~collision.FLAG_PREDICTED_BOUNCE
                raw.pop("bounce_vx", None)
                raw.pop("bounce_vy", None)
                raw.pop("bounce_x", None)
                raw.pop("bounce_y", None)
                raw.pop("bounce_circles", None)
        for i, member_a in enumerate(sorted_active):
            for member_b in sorted_active[i + 1:]:
                prev_a, prev_b = active_previous.get(member_a.runtime_id), active_previous.get(member_b.runtime_id)
                pair = f"{member_a.runtime_id}|{member_b.runtime_id}"
                if pair in predicted_pairs or self._predicted_pair_ticks.get(pair, -2) >= self.tick - 1:
                    continue
                version = (int(state_by_id[member_a.runtime_id].get("seq", -1)),
                           int(state_by_id[member_b.runtime_id].get("seq", -1)))
                if prev_a and prev_b and self._swept_pair_versions.get(pair) != version:
                    swept[pair] = collision.swept_circle_chain_collision(
                        prev_a.circles, member_a.circles, prev_b.circles, member_b.circles)
                    self._swept_pair_versions[pair] = version
        results, _, self.overlap_history = collision.solve_multi_body_collision(
            active, self.tick, self.overlap_history,
            restitution=self.policy["collision_restitution"], friction=self.policy["collision_friction"],
            impulse_cap=self.policy["collision_impulse_cap"], swept_collisions=swept,
            ignored_pairs=predicted_pairs | {
                pair for pair, tick in self._predicted_pair_ticks.items()
                if tick >= self.tick - 1
            })
        results = predicted_results + results
        self._predicted_pair_ticks = {
            pair: tick for pair, tick in self._predicted_pair_ticks.items()
            if self.tick - tick <= 1
        }
        for pair in predicted_pairs:
            self._predicted_pair_ticks[pair] = self.tick
        moving = any(math.hypot(float(v.get("vx", 0)), float(v.get("vy", 0))) > 20 or
                     int(v.get("flags", 0)) & (collision.FLAG_THROWN | collision.FLAG_DRAGGING)
                     for v in self.members.values())
        if now - self._last_snapshot_at >= (0.05 if moving else 0.5):
            self._last_snapshot_at = now
            payload: collision_codec.SnapshotMessage = {"type": "snapshot", "epoch": self.epoch, "tick": self.tick,
                                                        "members": [self._public_member(v) for v in self.members.values()]}
            for peer in self.peers:
                self._send(peer, payload)
            self.snapshot_ready.emit(payload)
        for result in results:
            if result.j == 0 and result.sep == 0:
                continue
            if result.j == 0 and result.sep > 0:
                signature = (round(result.dx_a, 3), round(result.dy_a, 3),
                             round(result.dx_b, 3), round(result.dy_b, 3))
                previous = self._position_only_pairs.get(result.pair)
                if previous is not None and self.tick - previous[1] < 15:
                    continue
                self._position_only_pairs[result.pair] = (signature, self.tick)
            payload: collision_codec.ImpulseMessage = {"type": "impulse", "epoch": self.epoch, **asdict(result)}
            for peer in self.peers:
                self._send(peer, payload)
            self.impulse_ready.emit(payload)

    def _remove_member(self, runtime_id: str) -> None:
        if runtime_id:
            self.members.pop(runtime_id, None)
            self._pending_predicted.pop(runtime_id, None)
            self.previous_members.pop(runtime_id, None)
            self._swept_pair_versions = {
                pair: version for pair, version in self._swept_pair_versions.items()
                if runtime_id not in pair.split("|")
            }
            self._predicted_pair_ticks = {
                pair: tick for pair, tick in self._predicted_pair_ticks.items()
                if runtime_id not in pair.split("|")
            }
            self._position_only_pairs = {
                pair: value for pair, value in self._position_only_pairs.items()
                if runtime_id not in pair.split("|")
            }

    def _peer_lost(self, socket) -> None:
        self._welcomed_peers.discard(socket)
        self._remove_member(self.peers.pop(socket, ""))
        self._socket_decoders.pop(socket, None)
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
            self._socket_decoders.pop(self.socket, None)
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
            self._socket_decoders.pop(self._probe, None)
            self._probe.deleteLater()
            self._probe = None
        if self._client_watchdog:
            self._client_watchdog.stop()
            self._client_watchdog.deleteLater()
            self._client_watchdog = None
        if self.socket:
            socket = self.socket
            self.socket = None
            self._socket_decoders.pop(socket, None)
            self._send(socket, {"type": "leave", "seq": int(self.latest_state.get("seq", 0)) + 1})
            socket.disconnectFromServer()
            try:
                socket.deleteLater()
            except RuntimeError:
                pass
        for socket in list(self.peers):
            self._send(socket, {"type": "snapshot", "epoch": self.epoch, "tick": self.tick, "members": []})
            socket.disconnectFromServer()
            self._socket_decoders.pop(socket, None)
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
