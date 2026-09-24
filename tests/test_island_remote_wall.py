# -*- coding: utf-8 -*-
"""岛远端硬墙（多进程子宠撞岛生效）回归。

链路：岛宿主进程把岛几何经 submit_static_state 发布成碰撞世界静态成员
（member_id=island）→ 快照广播 → 各进程碰撞客户端回喂本地 IslandCollisionBody
（远端模式）→ 本地同步硬墙钳制。锁定：
1. 协议：静态成员经 member_id 注册/转发，自身成员状态不受污染；
2. 远端体语义：几何以快照重建、stale-keep（缺席不撤墙）、显式暂停撤墙、
   本地 TTL 超时撤墙、几何变化把被压住桌宠推出；
3. 发布者：宿主几何即时报 + 心跳 + 停止时暂停标记；
4. 客户端：快照回喂路由 + 宿主进程岛冲量丢弃（防直连/IPC 双重结算）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QRect
from PySide6.QtWidgets import QApplication

from pet import collision
from pet.collision_ipc import _CollisionWorker
from pet.island_collision import _REMOTE_WALL_TTL_S, IslandCollisionBody

ISLAND = collision.ISLAND_MEMBER_ID
FLAGS_ACTIVE = collision.FLAG_STATIC | collision.FLAG_COLLISION_ENABLED | collision.FLAG_VISIBLE
FLAGS_PAUSED = FLAGS_ACTIVE | collision.FLAG_PAUSED

# 无 FLAG_VISIBLE 的静态成员：复刻「注册即被 tick 清退」的 bug 形态
FLAGS_NO_VISIBLE = collision.FLAG_STATIC | collision.FLAG_COLLISION_ENABLED


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _island_member(x=500.0, y=200.0, w=200.0, h=44.0, flags=FLAGS_ACTIVE, seq=1):
    return {"runtime_id": ISLAND, "seq": seq, "x": x, "y": y, "w": w, "h": h, "flags": flags, "circles": []}


class _FakeIsland:
    """最小岛 widget 桩：几何 + 可见性 + 动画/模式标记。"""

    def __init__(self, rect=None, visible=True):
        self._rect = rect or QRect(400, 100, 220, 44)
        self._visible = visible
        self._mode = ""
        self._geo_to = None
        self._dragging = False

    def geometry(self):
        return self._rect

    def isVisible(self):
        return self._visible


class _FakeSession:
    """捕获 submit_static_state 的会话桩。"""

    def __init__(self):
        self.submitted = []

    def submit_static_state(self, state):
        self.submitted.append(dict(state))


def _make_remote_body(pets=()):
    body = IslandCollisionBody(None, SimpleNamespace(), pets_provider=lambda: list(pets))
    return body


# ============================================================================
# 协议层：submit_static_state 第二成员通道
# ============================================================================


def _make_worker(server=False, socket=None):
    worker = _CollisionWorker("test-name", "self-pid1-aa", "inst", {"collision_enabled": True})
    if server:
        worker.server = object()  # 非 None 即协调者语义
    if socket is not None:
        worker.socket = socket
    return worker


def test_static_state_registers_under_member_id(app):
    """协调者路径：静态成员按 member_id 注册，自身 runtime_id 成员不受污染。"""
    worker = _make_worker(server=True)
    worker.submit_static_state(
        {
            "member_id": ISLAND,
            "seq": 1,
            "x": 500.0,
            "y": 200.0,
            "w": 200.0,
            "h": 44.0,
            "flags": FLAGS_ACTIVE,
            "circles": [],
        }
    )
    assert ISLAND in worker.members
    assert worker.members[ISLAND]["flags"] & collision.FLAG_STATIC
    assert "self-pid1-aa" not in worker.members, "第二成员通道不得注册自身"
    assert worker.latest_state == {}, "第二成员通道不得改写自身 latest_state"


def test_static_state_seq_guard(app):
    """seq 不递增的静态状态被丢弃（陈旧重放）。"""
    worker = _make_worker(server=True)
    st = {"member_id": ISLAND, "seq": 5, "x": 500.0, "y": 200.0, "w": 200.0, "h": 44.0, "flags": FLAGS_ACTIVE, "circles": []}
    worker.submit_static_state(st)
    worker.submit_static_state({**st, "seq": 3, "x": 999.0})
    assert worker.members[ISLAND]["x"] == 500.0
    worker.submit_static_state({**st, "seq": 6, "x": 999.0})
    assert worker.members[ISLAND]["x"] == 999.0


def test_static_state_requires_member_id(app):
    worker = _make_worker(server=True)
    worker.submit_static_state({"seq": 1, "x": 1.0, "y": 1.0, "w": 1.0, "h": 1.0, "flags": FLAGS_ACTIVE, "circles": []})
    assert worker.members == {}


def test_static_state_forwarded_as_client(app):
    """客户端路径：静态状态带 member_id 转发给协调者。"""
    sent = []
    sock = SimpleNamespace()
    worker = _make_worker(server=False, socket=sock)
    worker._send = lambda s, msg: sent.append(msg)  # 截获 wire 帧
    worker.submit_static_state(
        {
            "member_id": ISLAND,
            "seq": 1,
            "x": 500.0,
            "y": 200.0,
            "w": 200.0,
            "h": 44.0,
            "flags": FLAGS_ACTIVE,
            "circles": [],
        }
    )
    assert len(sent) == 1
    assert sent[0]["type"] == "state"
    assert sent[0]["member_id"] == ISLAND


# ============================================================================
# 远端体语义
# ============================================================================


def test_remote_wall_activates_and_clamps(app):
    """远端模式：快照几何重建墙，穿透位置被钳出。"""
    body = _make_remote_body()
    body.start()
    body.on_remote_snapshot(_island_member())
    assert body._wall_active()
    stadium = body._island_stadium()
    # 身体框 (50,160,100,60) 中心 (100,190) 落在岛区 (400-620,100-144) 旁边：
    # 直接取岛中心正下方位置必被钳出
    sbr = QRect(0, 0, 100, 60)
    host = SimpleNamespace(_physics_mode=None, _interaction_state="IDLE")
    xi, yi = 450.0, 150.0  # 身体框中心 (500,180)：距岛轴 20 < 53（必穿透）
    nx, ny = body._clamp_body(host, xi, yi, sbr)
    assert (nx, ny) != (xi, yi), "穿透位置必须被钳出岛区"


def test_remote_absence_keeps_wall(app):
    """stale-keep：快照缺席（None）不撤墙。"""
    body = _make_remote_body()
    body.start()
    body.on_remote_snapshot(_island_member())
    body.on_remote_snapshot(None)
    assert body._wall_active()


def test_remote_paused_disables_wall(app):
    body = _make_remote_body()
    body.start()
    body.on_remote_snapshot(_island_member())
    body.on_remote_snapshot(_island_member(flags=FLAGS_PAUSED, seq=2))
    assert not body._wall_active()


def test_remote_ttl_expires_wall(app):
    body = _make_remote_body()
    body.start()
    body.on_remote_snapshot(_island_member())
    body._remote_updated_at -= _REMOTE_WALL_TTL_S + 1.0  # 模拟超时
    assert not body._wall_active()


def test_remote_stadium_matches_member_geometry(app):
    body = _make_remote_body()
    body.start()
    body.on_remote_snapshot(_island_member(x=500.0, y=200.0, w=200.0, h=44.0))
    ax0, ax1, ay, radius, height = body._island_stadium()
    assert (ax0, ax1) == (400.0 + 22.0, 600.0 - 22.0)
    assert ay == 200.0 - 22.0 + 22.0
    assert radius == 22.0 and height == 44.0


def test_remote_geometry_change_pushes_pets(app):
    """岛（远端几何）压到桌宠身上：几何变化时被压桌宠被推出。"""
    calls = []
    sbr = QRect(0, 0, 100, 60)
    win = SimpleNamespace(
        _physics_mode=None,
        _interaction_state="IDLE",
        _hidden_paused=False,
        _stable_body_local_rect=lambda: sbr,
        _virtual_pos=lambda: SimpleNamespace(x=lambda: 450.0, y=lambda: 130.0),
        _move_window_towards=lambda x, y, **kw: calls.append((x, y)),
        isVisible=lambda: True,
    )
    body = _make_remote_body(pets=[win])
    body.start()
    body.on_remote_snapshot(_island_member(x=500.0, y=152.0, w=200.0, h=44.0))
    assert calls, "被岛压住的桌宠必须被推出"


# ============================================================================
# 发布者（宿主进程）
# ============================================================================


def test_publisher_reports_geometry_and_pause(app):
    """宿主：start 即时报几何（island id + seq 递增），stop 报暂停。"""
    session = _FakeSession()
    body = IslandCollisionBody(_FakeIsland(), SimpleNamespace())
    body.attach_publisher(session)
    body.start()
    assert len(session.submitted) == 1
    st = session.submitted[0]
    assert st["member_id"] == ISLAND
    assert st["seq"] == 1
    assert not (st["flags"] & collision.FLAG_PAUSED)
    assert st["flags"] & collision.FLAG_STATIC
    assert st["x"] == 400.0 + 220.0 / 2 and st["y"] == 100.0 + 44.0 / 2
    body.stop()
    assert len(session.submitted) == 2
    assert session.submitted[1]["flags"] & collision.FLAG_PAUSED


def test_publisher_republishes_on_geometry_change(app):
    session = _FakeSession()
    island = _FakeIsland()
    body = IslandCollisionBody(island, SimpleNamespace())
    body.attach_publisher(session)
    body.start()
    body._pub_last_at -= 1.0  # 越过合流窗口（窗口语义见下一条测试）
    body.submit()  # 岛几何变化事件 → 再报
    assert len(session.submitted) == 2
    assert session.submitted[1]["seq"] == 2
    body.stop()


def test_publisher_coalesces_geometry_burst(app):
    """几何变化洪峰合流：100Hz 回调连发只发 ≤10Hz（洪峰实机教训）。"""
    session = _FakeSession()
    island = _FakeIsland()
    body = IslandCollisionBody(island, SimpleNamespace())
    body.attach_publisher(session)
    body.start()
    base = len(session.submitted)
    for _ in range(20):  # 模拟 bump 动画的 100Hz 几何回调
        body.submit()
    assert len(session.submitted) - base <= 2, "洪峰必须被合流（≤10Hz + 尾沿）"
    body.stop()


# ============================================================================
# 客户端路由：回喂 + 宿主岛冲量丢弃
# ============================================================================


def _make_client(win):
    from pet.collision_client import CollisionClient

    return CollisionClient(win, thrown="THROWN", dragging="DRAGGING", slingshot_aiming="AIM", hit_min_dv=300.0, contact_dv_floor=60.0)


class _FakeWin(QObject):
    """最小窗桩（CollisionClient 以 win 为 QObject parent，必须真 QObject）。"""

    def __init__(self, body=None):
        super().__init__()
        self.cfg = SimpleNamespace(get=lambda k, d=None: d)
        self._hidden_paused = False
        self._interaction_state = "IDLE"
        self._physics_mode = None
        if body is not None:
            self._island_collision_body = body

    def isVisible(self):
        return True


def _fake_win(body=None):
    return _FakeWin(body=body)


def test_client_feeds_island_member_to_body(app):
    body = _make_remote_body()
    body.start()
    win = _fake_win(body=body)
    client = _make_client(win)
    client.session = SimpleNamespace(runtime_id="pet-1")
    client._on_collision_snapshot(
        {
            "epoch": "e1",
            "members": [
                {"runtime_id": "pet-2", "flags": FLAGS_ACTIVE & ~collision.FLAG_STATIC, "x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0},
                _island_member(),
            ],
        }
    )
    assert body._wall_active()


def test_host_discards_island_impulse(app):
    """宿主进程：撞岛冲量归直连路径，协调者转发的岛冲量必须丢弃。"""
    body = IslandCollisionBody(_FakeIsland(), SimpleNamespace())
    win = _fake_win(body=body)
    client = _make_client(win)
    client.epoch = "e1"
    client.session = SimpleNamespace(runtime_id="pet-1")
    win._phys_vel = [0.0, 0.0]
    client._on_collision_impulse(
        {
            "epoch": "e1",
            "pair": f"{ISLAND}|pet-1",
            "tick": 1,
            "a": ISLAND,
            "b": "pet-1",
            "dvx_a": 0.0,
            "dvy_a": 0.0,
            "dx_a": 0.0,
            "dy_a": 0.0,
            "dvx_b": -800.0,
            "dvy_b": -600.0,
            "dx_b": 0.0,
            "dy_b": 0.0,
            "ax": 0.0,
            "ay": 0.0,
            "bx": 0.0,
            "by": 0.0,
        }
    )
    assert win._phys_vel == [0.0, 0.0], "宿主进程的岛冲量必须被丢弃（直连路径管）"


def test_remote_pet_applies_island_impulse(app):
    """远端进程（无本地岛）：撞岛冲量正常应用（弹床反弹沿 FLAG_STATIC 链）。"""
    body = _make_remote_body()
    body.start()
    body.on_remote_snapshot(_island_member())
    win = _fake_win(body=body)
    win._phys_vel = [0.0, 0.0]
    win._phys_pos = [0.0, 0.0]
    win._throw_speed_cap = 1e9
    win._collision_clamp_pos = lambda x, y: (x, y)
    win._cancel_move = lambda: None
    win._cancel_animation_gap = lambda: None
    win._enter_physics_mode = lambda mode: None
    win._physics_timer = SimpleNamespace(start=lambda: None)
    win._last_physics_tick_time = None
    win._play_collision_sound = lambda: None
    win._start_squash = lambda: None
    win._squash_active = False
    win._just_dragged = False
    win._clear_just_dragged = lambda: None
    win._throw_egg = None
    win._edge_probe = None
    win._virtual_pos = lambda: SimpleNamespace(x=lambda: 0.0, y=lambda: 0.0)
    win.collision_content_rect = lambda: QRect(-50, -30, 100, 60)
    client = _make_client(win)
    client.epoch = "e1"
    client.session = SimpleNamespace(runtime_id="pet-1")
    client._submit_collision_state = lambda force=False: None
    client._on_collision_impulse(
        {
            "epoch": "e1",
            "pair": f"{ISLAND}|pet-1",
            "tick": 1,
            "a": ISLAND,
            "b": "pet-1",
            "dvx_a": 0.0,
            "dvy_a": 0.0,
            "dx_a": 0.0,
            "dy_a": 0.0,
            "dvx_b": -800.0,
            "dvy_b": -600.0,
            "dx_b": 0.0,
            "dy_b": 0.0,
            "ax": 0.0,
            "ay": 0.0,
            "bx": 0.0,
            "by": 0.0,
        }
    )
    assert win._phys_vel == [-800.0, -600.0], "远端宠物的撞岛冲量必须正常应用"


def test_island_member_survives_coordinator_tick(app):
    """带 FLAG_VISIBLE 的岛成员经协调者 tick 不被清退（漏带曾被秒清，实机复现）。"""
    from pet.config import DEFAULT_COLLISION_SETTINGS

    worker = _CollisionWorker("test-tick", "self-pid1-bb", "inst", {**DEFAULT_COLLISION_SETTINGS, "collision_enabled": True})
    worker.server = object()  # 协调者语义
    worker.epoch = "e1"
    worker.submit_static_state(
        {
            "member_id": ISLAND,
            "seq": 1,
            "x": 500.0,
            "y": 200.0,
            "w": 200.0,
            "h": 44.0,
            "flags": FLAGS_ACTIVE,
            "circles": collision.circles_from_rect(400.0, 178.0, 200.0, 44.0),
        }
    )
    worker._coordinator_tick()
    assert ISLAND in worker.members, "带 FLAG_VISIBLE 的岛成员不得被 tick 清退"


def test_island_member_without_visible_flag_is_purged(app):
    """回归钉：漏带 FLAG_VISIBLE 的成员会被清退——A4 起为两阶段：
    首 tick 墓碑标记（带不可见标记进一次快照），下一 tick 正式清退。"""
    from pet.config import DEFAULT_COLLISION_SETTINGS

    worker = _CollisionWorker("test-tick", "self-pid1-cc", "inst", {**DEFAULT_COLLISION_SETTINGS, "collision_enabled": True})
    worker.server = object()
    worker.epoch = "e1"
    worker.submit_static_state(
        {
            "member_id": ISLAND,
            "seq": 1,
            "x": 500.0,
            "y": 200.0,
            "w": 200.0,
            "h": 44.0,
            "flags": FLAGS_NO_VISIBLE,
            "circles": collision.circles_from_rect(400.0, 178.0, 200.0, 44.0),
        }
    )
    worker._coordinator_tick()
    assert ISLAND in worker.members, "首 tick 墓碑期仍在（快照携带不可见标记）"
    worker._coordinator_tick()
    assert ISLAND not in worker.members, "下一 tick 正式清退"


def test_paused_member_snapshotted_once_before_purge(app):
    """A4 修复：PAUSED 成员先带标记进一次快照（远端据此立即撤墙），
    下一 tick 才正式清退——不再被静默清退后靠 8s TTL 兜底。"""
    from pet.config import DEFAULT_COLLISION_SETTINGS

    worker = _CollisionWorker("test-tick", "self-pid1-dd", "inst", {**DEFAULT_COLLISION_SETTINGS, "collision_enabled": True})
    worker.server = object()
    worker.epoch = "e1"
    worker.submit_static_state(
        {
            "member_id": ISLAND,
            "seq": 1,
            "x": 500.0,
            "y": 200.0,
            "w": 200.0,
            "h": 44.0,
            "flags": FLAGS_ACTIVE,
            "circles": collision.circles_from_rect(400.0, 178.0, 200.0, 44.0),
        }
    )
    worker.submit_static_state(
        {
            "member_id": ISLAND,
            "seq": 2,
            "x": 500.0,
            "y": 200.0,
            "w": 200.0,
            "h": 44.0,
            "flags": FLAGS_ACTIVE | collision.FLAG_PAUSED,
            "circles": collision.circles_from_rect(400.0, 178.0, 200.0, 44.0),
        }
    )
    snapshots = []
    worker.snapshot_ready.connect(lambda payload: snapshots.append(payload))
    worker._coordinator_tick()
    assert ISLAND in worker.members, "首个 tick：墓碑期成员仍在（等待快照下发 PAUSED）"
    paused_in_snapshot = [
        m for m in (snapshots[-1].get("members", []) if snapshots else []) if m.get("runtime_id") == ISLAND and (int(m.get("flags", 0)) & collision.FLAG_PAUSED)
    ]
    assert paused_in_snapshot, "PAUSED 标记必须随快照下发一次（远端立即撤墙的信道）"
    worker._coordinator_tick()
    assert ISLAND not in worker.members, "下一 tick：墓碑成员正式清退"


def test_detach_publisher_stops_heartbeat_and_publishes_paused(app):
    """A5 修复：detach 发一次 PAUSED + 停心跳 + 清会话引用（幂等）。"""
    session = _FakeSession()
    body = IslandCollisionBody(_FakeIsland(), SimpleNamespace())
    body.attach_publisher(session)
    body.start()
    assert body._pub_session is session
    assert body._pub_timer is not None and body._pub_timer.isActive()
    count_before = len(session.submitted)
    body.detach_publisher()
    assert body._pub_session is None
    assert body._pub_timer is None
    assert len(session.submitted) == count_before + 1
    assert session.submitted[-1]["flags"] & collision.FLAG_PAUSED
    body.detach_publisher()  # 幂等：二次 detach 是 no-op
    assert len(session.submitted) == count_before + 1
    body.stop()


def test_state_message_member_id_hijack_rejected(app):
    """评审加固：非静态通道的 member_id 一律回退到连接自身 runtime_id——
    冒名覆写其它成员状态的报文不生效。"""
    worker = _make_worker(server=True)
    from unittest.mock import MagicMock

    victim_socket = MagicMock()
    worker.peers[victim_socket] = "peer-victim"
    worker.submit_static_state(
        {
            "member_id": ISLAND,
            "seq": 1,
            "x": 1.0,
            "y": 1.0,
            "w": 10.0,
            "h": 10.0,
            "flags": FLAGS_ACTIVE,
            "circles": [],
        }
    )
    # 冒名报文：声称自己是 island，但 flags 不带 FLAG_STATIC → 回退到发送方 id
    worker._handle_message(
        victim_socket,
        {
            "type": "state",
            "member_id": ISLAND,
            "seq": 99,
            "x": 777.0,
            "y": 777.0,
            "w": 10.0,
            "h": 10.0,
            "flags": collision.FLAG_COLLISION_ENABLED | collision.FLAG_VISIBLE,
            "circles": [],
        },
    )
    assert worker.members[ISLAND]["x"] == 1.0, "无 FLAG_STATIC 的冒名报文不得覆写 island 成员"
    assert worker.members["peer-victim"]["x"] == 777.0, "报文按发送方自身 runtime_id 入账"
    # 合法静态通道：member_id=island + FLAG_STATIC → 正常覆盖
    worker._handle_message(
        victim_socket,
        {
            "type": "state",
            "member_id": ISLAND,
            "seq": 2,
            "x": 888.0,
            "y": 1.0,
            "w": 10.0,
            "h": 10.0,
            "flags": FLAGS_ACTIVE,
            "circles": [],
        },
    )
    assert worker.members[ISLAND]["x"] == 888.0, "合法静态布景通道必须照常工作"
