# -*- coding: utf-8 -*-
"""P3 broker 控制面单测：两套 CollisionIpcSession（同进程，照抄
test_collision_ipc.py 手法：同 server 名 + 共享锁文件选出一个 coordinator）。

覆盖 _plan/current/P3_BROKER_DESIGN.md §3.5/§5/§6 的订阅-授权链路：
- decode_subscribe → grant（真跨 QLocal 通道 + 共享内存 attach）；
- decode_deny（not_publishing / too_late 剩余帧不足）；
- req_id 配对（双素材并发订阅各配各的）+ 过期/重复 grant 丢弃 +
  grant attach 失败 → 本地回退；
- 600ms 订阅超时（coordinator 忽略 → feed 超时返回 None）；
- 杀 coordinator → 消费端收到 abort（断流回退事件）→ 重选举后成为新
  coordinator：不回订自己、旧 session 以新 epoch 重建、当前 movie 不追溯
  改模式、下一次 start 按新角色发布。
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from pet import collision
from pet import decode_broker as broker_mod
from pet.collision_ipc import CollisionIpcSession
from pet.config import Config
from pet.decode_broker import (
    BrokerFacade,
    BrokerFeedSession,
    asset_key,
    frame_bytes,
)

FRAME_BYTES = frame_bytes(640, 360)  # 921600
_PAYLOAD = b"\x00\x00\x00\xff" * (FRAME_BYTES // 4)
_STATE_FLAGS = collision.FLAG_VISIBLE | collision.FLAG_COLLISION_ENABLED

# P3A R2 P0-1 平台门禁：broker 只在 Windows（x86/x64 TSO）启用——本文件的
# 控制面全链路测试都依赖 enabled=True 的真实 facade（真 QLocal + 共享内存
# seqlock），弱序平台（ARM macOS/Linux）上 broker 运行时强制不启用，故整
# 文件跳过（与 broker_platform_supported() 的运行时拒绝一致）。
pytestmark = pytest.mark.skipif(
    not broker_mod.broker_platform_supported(),
    reason="P3A R2 P0-1: broker Windows-only（x86 TSO，弱序平台无跨进程 barrier）",
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _Movie:
    """WebMClip 的最小替身：facade 只读写 path 与两个钩子属性。"""

    def __init__(self, path):
        self.path = str(path)
        self._publish_sink = None
        self._feed_source = None


class _Pair:
    """两个同 server 名会话（共享锁文件）→ settle 出唯一 coordinator/client。"""

    def __init__(self, tmp_path: Path, label: str) -> None:
        self.app = _app()
        self.name = f"d42-{label}-{uuid.uuid4().hex[:8]}"
        self.a = CollisionIpcSession(
            Config(tmp_path, instance_id=f"{label}-a"), server_name=self.name)
        self.b = CollisionIpcSession(
            Config(tmp_path, instance_id=f"{label}-b"), server_name=self.name)
        self.coord_session = None
        self.client_session = None
        self._seq = 0
        self._last_keepalive = 0.0
        self.a.start()
        self.b.start()

    # ---- 事件泵 + 成员保活（coordinator 快照让 client 看门狗不误判断线）----
    def _tick(self) -> None:
        self.app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        now = time.monotonic()
        if now - self._last_keepalive >= 0.3:
            self._last_keepalive = now
            self._seq += 1
            seq = self._seq
            for index, session in enumerate((self.a, self.b)):
                if session._thread.isRunning():
                    session.submit_state({
                        "seq": seq, "ts": now, "x": float(index * 5000.0), "y": 0.0,
                        "w": 100, "h": 100, "radius_x": 40.0, "radius_y": 40.0,
                        "vx": 0.0, "vy": 0.0, "flags": _STATE_FLAGS,
                    })

    def pump_until(self, predicate, timeout: float = 6.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._tick()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def settle(self, timeout: float = 8.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._tick()
            coords = [s for s in (self.a, self.b) if s.is_coordinator]
            if len(coords) == 1 and self.a.role_known and self.b.role_known:
                break
            time.sleep(0.01)
        coords = [s for s in (self.a, self.b) if s.is_coordinator]
        assert len(coords) == 1, "两套会话未能选出一个 coordinator"
        assert self.a.role_known and self.b.role_known, "GUI 侧角色镜像未就绪"
        self.coord_session = coords[0]
        self.client_session = self.b if coords[0] is self.a else self.a
        # 防御：coordinator 的服务端与 client 的连接必须真实就绪
        assert self.coord_session._worker.server is not None
        assert self.client_session._worker.socket is not None

    def stop(self) -> None:
        for session in (self.a, self.b):
            try:
                session.stop()
            except Exception:
                pass


def _after(seconds: float):
    deadline = time.monotonic() + seconds
    return lambda: time.monotonic() >= deadline


def _bind_facades(pair: _Pair, *, coord_enabled: bool = True,
                  client_enabled: bool = True,
                  coord_kwargs: dict | None = None,
                  client_kwargs: dict | None = None):
    coord_facade = BrokerFacade(ipc_session=pair.coord_session,
                                enabled=coord_enabled, **(coord_kwargs or {}))
    client_facade = BrokerFacade(ipc_session=pair.client_session,
                                 enabled=client_enabled, **(client_kwargs or {}))
    coord_facade.bind(pair.coord_session)
    client_facade.bind(pair.client_session)
    return coord_facade, client_facade


def _shutdown_facades(*facades) -> None:
    for facade in facades:
        try:
            facade.shutdown()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# subscribe → grant 全链路（真 QLocal + 共享内存 attach + 逐帧读）
# ---------------------------------------------------------------------------
def test_decode_subscribe_grant_flow_reads_frames(tmp_path):
    pair = _Pair(tmp_path, "grant")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "idle-grant.webm")
        coord_movie = _Movie(path)
        assert coord_facade.shareable_start("idle", coord_movie, path=path) == "publish"
        record = coord_facade._publishers[asset_key(path)]
        assert coord_movie._publish_sink is record.session

        client_movie = _Movie(path)
        assert client_facade.shareable_start("idle", client_movie, path=path) == "feed"
        feed = client_movie._feed_source
        assert feed is not None and isinstance(feed, broker_mod.BrokerFeed)
        assert pair.pump_until(lambda: feed.ready), "grant 未在超时内送达"
        result = feed.result
        assert isinstance(result, BrokerFeedSession)
        assert result.shm_name == record.session.name
        # client 侧已 attach：头信息与 coordinator 会话一致
        header = result._session.read_header()
        assert (header.frame_w, header.frame_h, header.bpp) == (640, 360, 4)

        # 发布端出帧 → 消费端 feed 读到同字节帧
        record.session.publish_frame(_PAYLOAD, 0)
        kind, data, src = result.poll()
        assert kind == "frame"
        assert src == 0 and data == _PAYLOAD
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


def test_decode_subscribe_denied_when_not_publishing(tmp_path):
    pair = _Pair(tmp_path, "deny-nopub")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        reasons = []
        original_deny = coord_facade._deny

        def spy_deny(runtime_id, req_id, reason):
            reasons.append(reason)
            original_deny(runtime_id, req_id, reason)

        coord_facade._deny = spy_deny
        path = str(tmp_path / "not-publishing.webm")
        movie = _Movie(path)
        assert client_facade.shareable_start("idle", movie, path=path) == "feed"
        feed = movie._feed_source
        assert pair.pump_until(lambda: feed.ready), "deny 未在超时内送达"
        assert feed.result is None  # 本地解码回退
        assert reasons and reasons[0].startswith("not_publishing")
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


def test_decode_subscribe_denied_too_late_when_remaining_below_min_join(tmp_path):
    pair = _Pair(tmp_path, "deny-late")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(
            pair, coord_kwargs={"default_total_frames": 120})
        reasons = []
        original_deny = coord_facade._deny

        def spy_deny(runtime_id, req_id, reason):
            reasons.append(reason)
            original_deny(runtime_id, req_id, reason)

        coord_facade._deny = spy_deny
        path = str(tmp_path / "late-join.webm")
        coord_movie = _Movie(path)
        assert coord_facade.shareable_start("idle", coord_movie, path=path) == "publish"
        record = coord_facade._publishers[asset_key(path)]
        # 发布端已播到 src=29 → 剩余 120-29=91 < MIN_JOIN_FRAMES(96) → deny
        for src in range(30):
            record.session.publish_frame(_PAYLOAD, src)

        movie = _Movie(path)
        assert client_facade.shareable_start("idle", movie, path=path) == "feed"
        feed = movie._feed_source
        assert pair.pump_until(lambda: feed.ready), "deny 未在超时内送达"
        assert feed.result is None
        assert reasons and reasons[0].startswith("too_late:")
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


# ---------------------------------------------------------------------------
# req_id 配对 / 过期与重复 grant / attach 失败回退
# ---------------------------------------------------------------------------
def test_req_id_pairing_two_assets_and_stale_duplicate_grants_dropped(tmp_path):
    pair = _Pair(tmp_path, "pairing")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path_a = str(tmp_path / "idle-a.webm")
        path_b = str(tmp_path / "idle-b.webm")
        for path in (path_a, path_b):
            assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"

        movie_a, movie_b = _Movie(path_a), _Movie(path_b)
        assert client_facade.shareable_start("idle", movie_a, path=path_a) == "feed"
        assert client_facade.shareable_start("idle", movie_b, path=path_b) == "feed"
        feed_a, feed_b = movie_a._feed_source, movie_b._feed_source
        assert pair.pump_until(lambda: feed_a.ready and feed_b.ready), "双 grant 未达"
        # req_id 配对：各自 grant 的 shm 是各自素材的 session（不串台）
        record_a = coord_facade._publishers[asset_key(path_a)].session
        record_b = coord_facade._publishers[asset_key(path_b)].session
        assert feed_a.result.shm_name == record_a.name
        assert feed_b.result.shm_name == record_b.name
        assert feed_a.result.shm_name != feed_b.result.shm_name
        assert not client_facade._pending  # 全部配对完成

        # 过期 req_id 的 grant：pending 已清 → 静默丢弃（幂等 attach 语义 R8）
        stale = {"type": "decode_grant", "req_id": "stale-zzz", "shm_name": "x",
                 "epoch": "", "frame_w": 640, "frame_h": 360, "bpp": 4,
                 "fps_x1000": 24000, "total_frames": 241, "slot_count": 4,
                 "seq": 0, "last_src": 0}
        client_facade._on_decode_reply(stale)  # 不抛
        # 重复 grant（同一 req_id 二次到达）：已被首次消费 → 丢弃
        dup = dict(stale, req_id=feed_a.req_id)
        client_facade._on_decode_reply(dup)
        assert feed_a.result is not None and feed_a.ready
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


def test_grant_attach_failure_completes_none_and_falls_back(tmp_path):
    """grant 携带不可 attach 的 shm（已 unlink/名字错）→ 本地回退（None）。"""
    pair = _Pair(tmp_path, "attach-fail")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "attach-fail.webm")
        assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"

        movie = _Movie(path)
        assert client_facade.shareable_start("idle", movie, path=path) == "feed"
        feed = movie._feed_source
        # 在真实 grant 送达前注入坏 shm 的 grant（同 req_id）→ attach 抛错 → None
        req_id = next(iter(client_facade._pending))
        bad_grant = {"type": "decode_grant", "req_id": req_id,
                     "shm_name": f"no-such-shm-{uuid.uuid4().hex[:12]}",
                     "epoch": "", "frame_w": 640, "frame_h": 360, "bpp": 4,
                     "fps_x1000": 24000, "total_frames": 241, "slot_count": 4,
                     "seq": 0, "last_src": 0}
        client_facade._on_decode_reply(bad_grant)
        assert pair.pump_until(lambda: feed.ready)
        assert feed.result is None  # attach 失败 → 消费端本地解码
        assert not client_facade._pending
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


# ---------------------------------------------------------------------------
# 600ms 订阅超时（coordinator 忽略订阅请求 → 预算耗尽 → 本地解码）
# ---------------------------------------------------------------------------
def test_subscribe_timeout_after_600ms_budget_when_coordinator_silent(tmp_path):
    pair = _Pair(tmp_path, "timeout")
    try:
        pair.settle()
        # coordinator 侧 facade 关闭：收到 decode_subscribe 也不回 grant/deny
        coord_facade, client_facade = _bind_facades(pair, coord_enabled=False)
        # 先让成员保活跑一会儿（快照持续刷新 client 看门狗），再发订阅
        assert pair.pump_until(_after(0.6), timeout=3.0)
        path = str(tmp_path / "timeout.webm")
        movie = _Movie(path)
        assert client_facade.shareable_start("idle", movie, path=path) == "feed"
        feed = movie._feed_source
        started = time.monotonic()
        result = feed.wait_result(broker_mod.SUBSCRIBE_BUDGET_MS / 1000.0 + 0.3)
        elapsed = time.monotonic() - started
        assert result is None  # 超时 → 本地解码
        assert elapsed >= broker_mod.SUBSCRIBE_BUDGET_MS / 1000.0 - 0.1
        assert elapsed < broker_mod.SUBSCRIBE_BUDGET_MS / 1000.0 + 1.5
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


# ---------------------------------------------------------------------------
# 杀 coordinator：断流回退事件 + 重选举后不回订自己 + 新 epoch 发布
# ---------------------------------------------------------------------------
def test_kill_coordinator_client_falls_back_then_republishes_with_new_epoch(tmp_path):
    pair = _Pair(tmp_path, "kill")
    old_shm_name = None
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "idle-kill.webm")
        coord_movie = _Movie(path)
        assert coord_facade.shareable_start("idle", coord_movie, path=path) == "publish"
        record = coord_facade._publishers[asset_key(path)]
        old_shm_name = record.session.name

        client_movie = _Movie(path)
        assert client_facade.shareable_start("idle", client_movie, path=path) == "feed"
        feed = client_movie._feed_source
        assert pair.pump_until(lambda: feed.ready), "grant 未达"
        feed_session = feed.result
        assert isinstance(feed_session, BrokerFeedSession)

        # 发布端正在出帧，消费端正常读到
        record.session.publish_frame(_PAYLOAD, 0)
        kind, data, src = feed_session.poll()
        assert kind == "frame" and src == 0 and data == _PAYLOAD

        # 杀 coordinator（模拟进程退出）：先中止发布 session（aborted 广播），再关会话
        coord_facade.shutdown()
        pair.coord_session.stop()

        # 断流回退事件：消费端 feed 读到 aborted → 本地回退
        kind, _, _ = feed_session.poll()
        assert kind == "abort"

        # 重选举：client 会话当选新 coordinator（同锁文件自动接棒）
        assert pair.pump_until(lambda: pair.client_session.is_coordinator,
                               timeout=10.0), "client 未在期限内当选新 coordinator"
        assert client_facade.role_known()
        assert client_facade.is_coordinator()

        # 不回订自己 / 当前 movie 不追溯改模式：feed 钩子原样保留、无发布钩子
        assert client_movie._feed_source is feed
        assert client_movie._publish_sink is None
        assert not client_facade._pending

        # 下一次 start 按新角色发布：新 epoch 名 ≠ 旧 session 名
        movie2 = _Movie(path)
        mode = client_facade.shareable_start("idle", movie2, path=path)
        assert mode == "publish"
        new_record = client_facade._publishers[asset_key(path)]
        assert movie2._publish_sink is new_record.session
        assert movie2._feed_source is None
        assert new_record.session.name != old_shm_name  # epoch 重建
        header = new_record.session.read_header()
        assert header.epoch != 0
        assert header.pub_pid == os.getpid()
        assert not client_facade._pending  # 发布路径绝不发起订阅
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


# ---------------------------------------------------------------------------
# P3A P1-1：coordinator 退选（_resign_to）→ role_changed(False) → 旧 publisher
# 中止（真实 worker 退选路径，经 queued invokeMethod 在 worker 线程执行）
# ---------------------------------------------------------------------------
def test_coordinator_resign_emits_role_false_and_stops_publisher(tmp_path):
    pair = _Pair(tmp_path, "resign")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "resign-idle.webm")
        coord_movie = _Movie(path)
        assert coord_facade.shareable_start("idle", coord_movie, path=path) == "publish"
        session = coord_facade._publishers[asset_key(path)].session
        assert pair.coord_session.is_coordinator
        assert coord_movie._publish_sink is session

        # 触发真实退选：新候选在决胜中以更低 runtime_id 胜出 → 本 worker
        # 在自身线程执行 _resign_to（close server / 释放锁 / 转 client）。
        worker = pair.coord_session._worker
        winner = f"resign-winner-{uuid.uuid4().hex[:6]}"
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, worker, lambda: worker._resign_to(winner))

        # role_changed(False) 到达 GUI → 镜像翻转 + facade 中止发布 session
        assert pair.pump_until(lambda: not pair.coord_session.is_coordinator,
                               timeout=8.0), "退选后 GUI 角色镜像未翻转为 False"
        assert pair.pump_until(
            lambda: asset_key(path) not in coord_facade._publishers,
            timeout=8.0), "facade 未中止发布 session"
        assert session.closed, "旧发布 session 未关闭（句柄泄漏）"
        assert coord_movie._publish_sink is None, "movie 发布钩子未摘除"
        # 退选者转 client：重选举由其它会话收敛，本测试只钉「退选即停发布」
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


# ---------------------------------------------------------------------------
# P3A P1-2：订阅超时后迟到的 grant —— attach 句柄必须有主（feed expire 闭锁）
# ---------------------------------------------------------------------------
def _make_grant(coord_facade, path, req_id: str) -> dict:
    record = coord_facade._publishers[asset_key(path)]
    header = record.session.read_header()
    return {
        "type": "decode_grant", "req_id": req_id,
        "shm_name": record.session.name,
        "epoch": f"{int(header.epoch):016x}" if header.epoch else "",
        "frame_w": int(header.frame_w), "frame_h": int(header.frame_h),
        "bpp": int(header.bpp), "fps_x1000": int(header.fps_x1000),
        "total_frames": int(header.total_frames),
        "slot_count": int(header.slot_count),
        "seq": int(header.seq),
        "frame_count": int(header.frame_count),
        "last_src": int(header.last_src),
    }


def _spy_attach(monkeypatch):
    """记录 BrokerShmSession.attach 调用（是否真的 attach 过）。"""
    calls = []
    orig_attach = broker_mod.BrokerShmSession.attach

    def spy_attach(name, *args, **kwargs):
        calls.append(name)
        return orig_attach(name, *args, **kwargs)

    monkeypatch.setattr(broker_mod.BrokerShmSession, "attach", spy_attach)
    return calls


def test_late_grant_after_reader_expire_is_dropped_no_attach(tmp_path, monkeypatch):
    """reader 600ms 超时放弃（expire）后，迟到的 grant 命中已闭锁 feed →
    静默丢弃且绝不 attach（无句柄可泄漏）。"""
    pair = _Pair(tmp_path, "late1")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "late1.webm")
        assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"
        movie = _Movie(path)
        assert client_facade.shareable_start("idle", movie, path=path) == "feed"
        feed = movie._feed_source
        assert client_facade._pending.get(feed.req_id) is feed
        attach_calls = _spy_attach(monkeypatch)
        # reader 侧超时放弃：expire 闭锁 + pending 移除
        feed.expire()
        assert feed.expired and feed.ready and feed.result is None
        assert not client_facade._pending
        # 迟到的 grant（协调者在 600ms 后才答复）
        client_facade._on_decode_reply(_make_grant(coord_facade, path, feed.req_id))
        assert feed.result is None and feed.expired  # 不得 complete
        assert attach_calls == [], "迟到 grant 不得 attach（无主句柄窗口）"
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


def test_grant_then_reader_expire_closes_attached_session(tmp_path, monkeypatch):
    """grant 恰在 reader 放弃前落定（已 attach 并 complete）→ reader 随后
    expire 必须 close 该 session（句柄有主，不泄漏）。"""
    pair = _Pair(tmp_path, "late2")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "late2.webm")
        assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"
        movie = _Movie(path)
        assert client_facade.shareable_start("idle", movie, path=path) == "feed"
        feed = movie._feed_source
        closed = []
        orig_close = broker_mod.BrokerShmSession.close

        def spy_close(self):
            closed.append(self)
            return orig_close(self)

        monkeypatch.setattr(broker_mod.BrokerShmSession, "close", spy_close)
        # grant 先到达：正常 complete（feed_session 已 attach）
        client_facade._on_decode_reply(_make_grant(coord_facade, path, feed.req_id))
        assert feed.ready and isinstance(feed.result, BrokerFeedSession)
        assert closed == []  # 正常路径不误关
        # reader 随后仍放弃（movie stop / 换代）：expire 收走已落定 session
        feed.expire()
        assert feed.result is None
        assert closed, "expire 未 close 已落定的 attach session"
        assert all(s.closed for s in closed)
        assert not client_facade._pending
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


# ---------------------------------------------------------------------------
# P3A P1-5：角色翻转 / unbind 时 pending 订阅统一失效（事件驱动回退，
# 600ms 只是无事件兜底）；迟到 grant 一律不 attach
# ---------------------------------------------------------------------------
def _subscribe_pending(client_facade, pair, path, timeout=6.0):
    movie = _Movie(path)
    assert client_facade.shareable_start("idle", movie, path=path) == "feed"
    feed = movie._feed_source
    assert pair.pump_until(lambda: bool(client_facade._pending), timeout=timeout), \
        "订阅未进入 pending"
    return movie, feed


def test_role_change_and_unbind_invalidate_pending_feeds(tmp_path, monkeypatch):
    pair = _Pair(tmp_path, "inval")
    try:
        pair.settle()
        # coordinator 侧 facade 关闭：收到订阅也不回 grant → 保持 pending
        coord_facade, client_facade = _bind_facades(pair, coord_enabled=False)
        path = str(tmp_path / "inval-idle.webm")
        attach_calls = _spy_attach(monkeypatch)

        # 场景 1：当选 coordinator（重选举/旧 coordinator 死亡）→ 旧 epoch
        # 订阅作废（事件驱动回退），其后到达的迟到 grant 不 attach
        movie, feed = _subscribe_pending(client_facade, pair, path)
        pair.client_session.role_changed.emit(True, "epoch-new")
        assert feed.expired and feed.result is None, "当选后 pending feed 未失效"
        assert not client_facade._pending
        grant = {"type": "decode_grant", "req_id": feed.req_id,
                 "shm_name": "x", "epoch": "", "frame_w": 640, "frame_h": 360,
                 "bpp": 4, "fps_x1000": 24000, "total_frames": 241,
                 "slot_count": 4, "seq": 0, "frame_count": 0, "last_src": 0}
        client_facade._on_decode_reply(grant)
        assert attach_calls == [], "失效后迟到 grant 不得 attach"
        assert feed.result is None

        # 场景 2：角色镜像复位为 client 后，卸任 coordinator（role False）
        # 同样作废 pending
        pair.client_session.role_changed.emit(False, "epoch-old")
        movie2, feed2 = _subscribe_pending(client_facade, pair, path)
        pair.client_session.role_changed.emit(False, "epoch-old2")
        assert feed2.expired and feed2.result is None
        assert not client_facade._pending

        # 场景 3：unbind（窗口 detach/会话重建）同样作废 pending
        movie3, feed3 = _subscribe_pending(client_facade, pair, path)
        client_facade.unbind()
        assert feed3.expired and feed3.result is None
        assert not client_facade._pending
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


def test_role_changed_false_aborts_publisher(tmp_path):
    """卸任 coordinator：发布 session 中止（aborted 广播 + close + 预算回收）。"""
    pair = _Pair(tmp_path, "roleoff")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "roleoff-idle.webm")
        before = _budget()
        assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"
        record = coord_facade._publishers[asset_key(path)]
        session = record.session
        assert _budget() == before + record.size
        pair.coord_session.role_changed.emit(False, "epoch-old")
        assert not coord_facade._publishers, "卸任后发布记录未清理"
        assert session.closed, "卸任后发布 session 未关闭"
        assert _budget() == before, "卸任中止重复/遗漏 release 预算"
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


# ---------------------------------------------------------------------------
# P3A P1-3：自然结束记录及时移出 + close 幂等（预算不重复释放）
# ---------------------------------------------------------------------------
def _budget() -> int:
    return broker_mod.BROKER_BUDGET.total_bytes()


def test_natural_end_replay_within_grace_releases_budget_once(tmp_path, monkeypatch):
    """自然播完 → 记录立即移出（宽限期由定时器按身份关闭）；3s 内重播同
    素材：新 epoch session，旧 session 定时器关闭时只 release 一次。"""
    monkeypatch.setattr(broker_mod, "SESSION_END_GRACE_S", 0.15)
    pair = _Pair(tmp_path, "natend")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "natend-idle.webm")
        before = _budget()
        assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"
        record1 = coord_facade._publishers[asset_key(path)]
        assert _budget() == before + record1.size

        # 自然播完：记录移出（不再可授权），预算在宽限期内保持（session 还
        # 活着供已 attach 的消费端读尾帧）
        coord_facade.publish_natural_end(path)
        assert asset_key(path) not in coord_facade._publishers
        assert _budget() == before + record1.size

        # 宽限期内同一素材重播 → 新 epoch session（预算 = 两个存活 session）
        movie2 = _Movie(path)
        assert coord_facade.shareable_start("idle", movie2, path=path) == "publish"
        record2 = coord_facade._publishers[asset_key(path)]
        assert record2.session.name != record1.session.name
        assert record2 is not record1
        assert _budget() == before + record1.size + record2.size

        # 旧 session 宽限定时器关闭 → 只 release 旧 record 一次
        assert pair.pump_until(
            lambda: _budget() == before + record2.size, timeout=3.0), \
            "旧 session 宽限关闭未恰释放一次预算"
        assert record1.session.closed
        # 新 record 不受影响（同 key 新一轮未被误删）
        assert coord_facade._publishers[asset_key(path)] is record2
        assert not record2.session.closed

        # 清理：shutdown 关闭新 record → 预算回到基线（无重复释放）
        coord_facade.shutdown()
        assert _budget() == before
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


def test_natural_end_then_switch_away_no_double_release(tmp_path, monkeypatch):
    """自然结束（记录已移出）后窗口再发 abort/切走：no-op，预算不重复释放；
    随后重播 → 正常新会话；再 abort → 预算单次回收。"""
    monkeypatch.setattr(broker_mod, "SESSION_END_GRACE_S", 0.15)
    pair = _Pair(tmp_path, "natend2")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "natend2-idle.webm")
        before = _budget()
        assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"
        coord_facade.publish_natural_end(path)
        assert asset_key(path) not in coord_facade._publishers
        # 自然结束后「切走」再发 abort：记录已移出 → no-op
        coord_facade.publish_abort(path)
        assert asset_key(path) not in coord_facade._publishers
        # 等宽限定时器收掉旧 session
        assert pair.pump_until(lambda: _budget() == before, timeout=3.0)
        # 重播 → 再 abort：预算恰好回到基线（旧 bug 会重复 release 成负数/失真）
        assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"
        assert _budget() == before + broker_mod.session_size(640, 360, 4)
        coord_facade.publish_abort(path)
        assert _budget() == before, "publish_abort 重复释放预算"
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()


def test_publisher_record_close_idempotent_releases_once(tmp_path):
    """record.close() 幂等：连续调用只 release 预算一次（含 shutdown 重叠）。"""
    pair = _Pair(tmp_path, "idem")
    try:
        pair.settle()
        coord_facade, client_facade = _bind_facades(pair)
        path = str(tmp_path / "idem-idle.webm")
        before = _budget()
        assert coord_facade.shareable_start("idle", _Movie(path), path=path) == "publish"
        record = coord_facade._publishers[asset_key(path)]
        assert _budget() == before + record.size
        record.close()
        record.close()  # 幂等：第二次 no-op
        assert _budget() == before, "close() 重复 release 预算"
        assert record.session.closed
        # shutdown 再碰同 key：map 已空 → 无重复释放
        coord_facade.shutdown()
        assert _budget() == before
    finally:
        _shutdown_facades(coord_facade, client_facade)
        pair.stop()
