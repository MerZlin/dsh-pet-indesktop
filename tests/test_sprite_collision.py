# -*- coding: utf-8 -*-
"""pet/sprite_collision.py 的纯逻辑测试（零 Qt：轻量假 sprite 鸭子类型）。

覆盖：两体弹性碰撞、拖拽方无限质量、扫掠防穿透、位置分离去抖、真撞击
置 thrown + 限速、静态成员反弹（灵动岛预留 API）、on_collision 回调。
"""

import math

from pet import collision
from pet.sprite_collision import (
    INTERACTION_DRAG,
    INTERACTION_NORMAL,
    INTERACTION_THROWN,
    SpriteCollisionWorld,
)


class FakePoint:
    """QPointF 的鸭子类型替身（world 用 type(pos)(x, y) 构造，保持零 Qt）。"""

    def __init__(self, x=0.0, y=0.0):
        self._x = float(x)
        self._y = float(y)

    def x(self):
        return self._x

    def y(self):
        return self._y


class FakeRect:
    def __init__(self, x, y, w, h):
        self._x, self._y, self._w, self._h = x, y, w, h

    def x(self):
        return self._x

    def y(self):
        return self._y

    def width(self):
        return self._w

    def height(self):
        return self._h


class FakeClock:
    """可推进假钟（monotonic 口径）：去抖窗口按墙钟推进，不 sleep 赌时序。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeSprite:
    """协议鸭子类型：pos/set_pos/velocity/set_velocity/rect/center/radius/
    dragging/interaction_state/scale/facing（+ 可选 collision_id）。"""

    def __init__(self, x, y, w=100.0, h=100.0, vx=0.0, vy=0.0,
                 scale=0.72, collision_id=""):
        self.pos = FakePoint(x, y)
        self.velocity = FakePoint(vx, vy)
        self._w, self._h = float(w), float(h)
        self.scale = float(scale)
        self.facing = "left"
        self.dragging = False
        self.interaction_state = INTERACTION_NORMAL
        self.collision_id = collision_id

    def rect(self):
        return FakeRect(self.pos.x(), self.pos.y(), self._w, self._h)

    def center(self):
        return FakePoint(self.pos.x() + self._w / 2.0, self.pos.y() + self._h / 2.0)

    def radius(self):
        return 0.45 * min(self._w, self._h)

    def set_pos(self, pos):
        self.pos = FakePoint(pos.x(), pos.y())

    def set_velocity(self, velocity):
        self.velocity = FakePoint(velocity.x(), velocity.y())

    def speed(self):
        return math.hypot(self.velocity.x(), self.velocity.y())


def test_head_on_elastic_collision_exchanges_velocity():
    """两体正面对撞（等质量、e=1、无摩擦）：速度交换。"""
    world = SpriteCollisionWorld(restitution=1.0, friction=0.0)
    a = FakeSprite(0, 0, vx=500, collision_id="a")
    b = FakeSprite(90, 0, vx=-500, collision_id="b")  # 圆链重叠 10px
    world.tick([a, b], 0.016)
    assert a.velocity.x() == -500.0
    assert b.velocity.x() == 500.0
    assert abs(a.velocity.y()) < 1e-9
    assert abs(b.velocity.y()) < 1e-9


def test_momentum_conserved_with_default_restitution():
    """默认恢复系数 0.82：动量守恒、动能损失、方向对调。"""
    world = SpriteCollisionWorld()  # e=0.82, friction=0.08
    a = FakeSprite(0, 0, vx=500, collision_id="a")
    b = FakeSprite(90, 0, vx=-500, collision_id="b")
    world.tick([a, b], 0.016)
    # 等质量正碰：动量（初态为 0）守恒
    assert abs(a.velocity.x() + b.velocity.x()) < 1e-6
    assert a.velocity.x() < 0 < b.velocity.x()
    # e<1：末速小于初速
    assert abs(a.velocity.x()) < 500.0


def test_dragged_sprite_is_infinite_mass():
    """拖拽中 = 无限质量：撞来的停住（非静态无限质量 e=0 吸能），被握的岿然不动。"""
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, vx=500, collision_id="a")
    b = FakeSprite(90, 0, vx=0, collision_id="b")
    b.dragging = True
    b.interaction_state = INTERACTION_DRAG
    world.tick([a, b], 0.016)
    # 撞来的一方速度被吸停（e=0：贴停不弹飞）
    assert abs(a.velocity.x()) < 1e-9
    assert a.interaction_state == INTERACTION_THROWN  # dv=500 >= 300 → 真撞击
    # 被拖拽的一方：速度与位置都不动、状态不被改写
    assert b.velocity.x() == 0.0 and b.velocity.y() == 0.0
    assert b.pos.x() == 90.0 and b.pos.y() == 0.0
    assert b.interaction_state == INTERACTION_DRAG


def test_swept_collision_prevents_tunneling():
    """高速小球一 tick 飞越静止大球：两帧快照都不重叠，扫掠仍命中。"""
    world = SpriteCollisionWorld()
    ball = FakeSprite(0, 0, w=20, h=20, vx=20000, collision_id="ball")
    wall = FakeSprite(400, 0, w=100, h=100, vx=0, collision_id="wall")
    events = []
    world.add_collision_listener(events.append)
    # tick 1：相距远，无碰撞；登记帧末快照
    results = world.tick([ball, wall], 0.016)
    assert results == []
    # 模拟 advance：20000px/s × 0.03s = 600px——直接飞越 wall（400..500）
    ball.set_pos(FakePoint(600, 0))
    # tick 2：当前快照 ball 在 wall 右侧 100px 外，不重叠；上一帧在左侧——
    # 只有扫掠能抓到这次穿越
    world.tick([ball, wall], 0.03)
    assert wall.velocity.x() > 0.0        # 静止大球被撞动 = 扫掠命中
    assert ball.velocity.x() < 20000.0    # 小球被减速（未穿透了事）
    assert wall.interaction_state == INTERACTION_THROWN
    assert len(events) == 1
    assert events[0].pair == "ball|wall"


def test_position_only_separation_is_debounced():
    """纯位置分离（j=0）按 pair 去抖：窗口内不反复推，防抖动。

    M12a 后窗口是秒基（``SEPARATION_DEBOUNCE_SECS`` = T0 15 tick 等价），
    用假钟推进窗口，不依赖真实时间流逝。
    """
    from pet.sprite_collision import SEPARATION_DEBOUNCE_SECS

    clock = FakeClock()
    world = SpriteCollisionWorld(clock=clock)
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(80, 0, collision_id="b")  # 静止重叠 20px，vn=0 → j=0
    world.tick([a, b], 0.016)  # 首次分离生效
    ax1, bx1 = a.pos.x(), b.pos.x()
    assert ax1 < 0.0 and bx1 > 80.0  # 确实被推开了
    for _ in range(14):  # 去抖窗口内（墙钟未到）：位置不许再动
        world.tick([a, b], 0.016)
    assert a.pos.x() == ax1 and b.pos.x() == bx1
    clock.advance(SEPARATION_DEBOUNCE_SECS)  # 窗口届满，分离再次生效
    world.tick([a, b], 0.016)
    assert a.pos.x() < ax1 and b.pos.x() > bx1


def test_light_touch_separates_without_thrown():
    """轻触（dv 低于真撞击阈值）：只分离，速度/状态都不变，不触发回调。"""
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, vx=50, collision_id="a")   # 慢速接近（vn=-50 > -80 → e=0）
    b = FakeSprite(95, 0, vx=0, collision_id="b")
    events = []
    world.add_collision_listener(events.append)
    world.tick([a, b], 0.016)
    assert a.velocity.x() == 50.0  # 微冲量不吸收（非 thrown 且未达 300 阈值）
    assert b.velocity.x() == 0.0
    assert a.interaction_state == INTERACTION_NORMAL
    assert b.interaction_state == INTERACTION_NORMAL
    assert events == []


def test_real_hit_marks_thrown_and_soft_clamps_speed():
    """真撞击置 thrown；被撞方末速过 soft_clamp_speed 软上限（不超 cap）。"""
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, vx=20000, collision_id="a")
    b = FakeSprite(90, 0, vx=0, collision_id="b")
    world.tick([a, b], 0.016)
    assert a.interaction_state == INTERACTION_THROWN
    assert b.interaction_state == INTERACTION_THROWN
    cap = 6000.0  # physics.MAX_THROW_SPEED
    assert b.speed() <= cap
    assert b.speed() > 0.0
    # 软膝曲线是渐近的：未被硬钳成恰等于 cap
    assert b.speed() < cap


def test_already_thrown_absorbs_contact_impulse_above_floor():
    """已 thrown 的成员继续吸收 >= 50px/s 的接触冲量（CONTACT_DV_FLOOR）。"""
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, vx=0, collision_id="a")
    b = FakeSprite(90, 0, vx=-150, collision_id="b")  # vn=-150：e=0.82 真撞前…dv≈136<300
    b.interaction_state = INTERACTION_THROWN
    world.tick([a, b], 0.016)
    # b 的 dv ≈ -(1.82)(-150)/2 = 136.5：未达 300 但 >= 50 且已 thrown → 吸收
    assert b.velocity.x() > -150.0
    # a 仍是 normal 且 dv < 300 → 不吸收、不置 thrown
    assert a.velocity.x() == 0.0
    assert a.interaction_state == INTERACTION_NORMAL


def test_static_member_bounces_with_trampoline_restitution():
    """静态成员（灵动岛预留 API）：STATIC_RESTITUTION=1.3 果冻墙加速弹开。"""
    world = SpriteCollisionWorld()
    world.add_static_member("island", 300, 0, 40, 200)
    # A 圆心 (280,100) 正对岛中圆 (320,100)：法线纯 +x，切向速度为零不吃摩擦
    a = FakeSprite(230, 50, vx=500, collision_id="a")
    events = []
    world.add_collision_listener(events.append)
    world.tick([a], 0.016)
    # e=1.3：vn=-500 → dv_a=-1150，末速 500-1150=-650（出射/入射 = 1.3，加速弹开）
    assert a.velocity.x() == -650.0
    assert a.interaction_state == INTERACTION_THROWN
    # 撞静态成员命中阈值放宽到 60：dv=1150 >> 60，回调触发
    assert len(events) == 1
    assert events[0].pair == "a|island"
    # 注销后不再参与结算
    world.remove_static_member("island")
    a.set_velocity(FakePoint(500, 0))
    a.set_pos(FakePoint(230, 50))
    a.interaction_state = INTERACTION_NORMAL
    world.tick([a], 0.016)
    assert a.velocity.x() == 500.0
    assert a.interaction_state == INTERACTION_NORMAL
    assert len(events) == 1


def test_on_collision_listener_receives_hit_details():
    """on_collision 回调负载：撞击对、冲量大小、接触点、法线。"""
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, vx=500, collision_id="a")
    b = FakeSprite(90, 0, vx=-500, collision_id="b")
    events = []
    world.add_collision_listener(events.append)
    world.tick([a, b], 0.016)
    assert len(events) == 1
    ev = events[0]
    assert ev.a == "a" and ev.b == "b"
    assert ev.j > 0.0
    assert ev.nx > 0.0 and abs(ev.ny) < 1e-9  # 法线从 a 指向 b（+x）
    # 接触点在两圆心之间（50 与 140 之间）
    assert 50.0 < ev.contact_x < 140.0
    assert ev.tick == 1
    # 注销后不再触发
    world.remove_collision_listener(events.append)
    a.set_velocity(FakePoint(500, 0))
    b.set_velocity(FakePoint(-500, 0))
    world.tick([a, b], 0.016)
    assert len(events) == 1


def test_member_flags_match_coordinator_semantics():
    """快照语义：拖拽 → FLAG_DRAGGING + 无限质量；静态 → FLAG_STATIC + 无限质量。"""
    world = SpriteCollisionWorld()
    sprite = FakeSprite(0, 0, collision_id="s")
    member = world._member_from_sprite(sprite)
    assert member.flags & collision.FLAG_VISIBLE
    assert member.flags & collision.FLAG_COLLISION_ENABLED
    assert not member.is_infinite_mass
    sprite.dragging = True
    dragged = world._member_from_sprite(sprite)
    assert dragged.flags & collision.FLAG_DRAGGING
    assert dragged.is_infinite_mass
    world.add_static_member("isle", 0, 0, 40, 200)
    static = world._static_member_state("isle", world._static_members["isle"])
    assert static.flags & collision.FLAG_STATIC
    assert static.is_infinite_mass


# ---------------------------------------------------------------- V-2：body_box 口径
class BodyBoxSprite(FakeSprite):
    """带稳定身体框的 sprite：身体 = 画布内缩 (10,20,60,50)。"""

    def body_rect(self):
        return FakeRect(self.pos.x() + 10, self.pos.y() + 20, 60.0, 50.0)


def test_member_from_sprite_uses_body_rect():
    """V-2：碰撞体口径 = 身体框（旧口径用整 canvas，含透明边会隔空弹开）。"""
    world = SpriteCollisionWorld()
    sprite = BodyBoxSprite(100, 100)
    member = world._member_from_sprite(sprite)
    assert member.x == 100 + 10 + 30.0     # 身体中心
    assert member.y == 100 + 20 + 25.0
    assert member.w == 60.0
    assert member.h == 50.0
    # 圆链也必须落在身体框上（首圆圆心 = 身体框左内切圆）
    first = member.circles[0]
    assert first[0] == 110.0 + 25.0        # 半径 = min(60,50)/2 = 25
    assert first[1] == 120.0 + 25.0


# ---------------------------------------------------------------- P1/③-1：静止豁免
def test_static_world_skips_solve(monkeypatch):
    """静止豁免：全员静止且无未结清交互时整 tick 跳过（成本→0）。"""
    calls = []
    real_solve = collision.solve_multi_body_collision
    monkeypatch.setattr(collision, "solve_multi_body_collision",
                        lambda *a, **kw: (calls.append(1), real_solve(*a, **kw))[1])
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(500, 500, collision_id="b")

    world.tick([a, b], 0.016)   # 首 tick 必求解（建立快照）
    assert len(calls) == 1
    world.tick([a, b], 0.016)   # 静止：跳过
    world.tick([a, b], 0.016)
    assert len(calls) == 1


def test_world_wakes_on_motion_and_static_change(monkeypatch):
    """签名变化（位移）或静态成员变更 → 立即恢复求解。"""
    calls = []
    real_solve = collision.solve_multi_body_collision
    monkeypatch.setattr(collision, "solve_multi_body_collision",
                        lambda *a, **kw: (calls.append(1), real_solve(*a, **kw))[1])
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(500, 500, collision_id="b")
    world.tick([a, b], 0.016)
    world.tick([a, b], 0.016)
    assert len(calls) == 1

    b.set_pos(FakePoint(150, 0))            # 位移到"可能接触区"内 → 唤醒
    world.tick([a, b], 0.016)
    assert len(calls) == 2

    world.tick([a, b], 0.016)               # 再次静止 → 跳过
    assert len(calls) == 2
    world.add_static_member("island", 100, 100, 200, 80)  # 静态变更 → 唤醒
    world.tick([a, b], 0.016)
    assert len(calls) == 3


def test_world_broad_phase_skips_solve_when_nothing_near(monkeypatch):
    """世界级宽相：成员都在安全距离外时，位移变化也不值得求解。

    与静态豁免同族、更前置的一层：AABB 宽相能证明本 tick 任何 pair 都
    不可能接触时（身体框外扩 margin 仍不相交），扫掠与求解结果必为空集，
    直接跳过——这是撞岛场景里桌宠"离岛很远"绝大多数 tick 的常态。
    """
    calls = []
    real_solve = collision.solve_multi_body_collision
    monkeypatch.setattr(collision, "solve_multi_body_collision",
                        lambda *a, **kw: (calls.append(1), real_solve(*a, **kw))[1])
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(500, 500, collision_id="b")
    world.tick([a, b], 0.016)               # 首 tick 必求解（建立基线）
    assert len(calls) == 1

    b.set_pos(FakePoint(600, 500))          # 位移，但仍相隔几百像素
    world.tick([a, b], 0.016)
    assert len(calls) == 1                  # 宽相判定不可能接触 → 不求教

    world.add_static_member("island", 0, 0, 60, 60)   # 岛就压在 a 身上 → 唤醒
    world.tick([a, b], 0.016)
    assert len(calls) == 2


# ---------------------------------------------------------------- D9：岛 stadium（胶囊圆链）
def test_capsule_circles_geometry():
    from pet.sprite_collision import capsule_circles
    # 宽扁岛 600×90：高度截到 44（island_collision._CAPSULE_HEIGHT 口径），
    # r=22，轴线 y=top+22，x∈[left+22, left+578]，圆间距 ≤ r 连续覆盖
    circles = capsule_circles(100, 200, 600, 90)
    r = 22.0
    assert all(c[2] == r for c in circles)
    assert circles[0][0] == 100 + r and circles[-1][0] == 100 + 600 - r
    assert all(c[1] == 200 + r for c in circles)
    gaps = [circles[i + 1][0] - circles[i][0] for i in range(len(circles) - 1)]
    assert all(g <= r + 1e-9 for g in gaps)      # 连续覆盖无空档
    # 窄于高度上限的岛：高度不截断
    small = capsule_circles(0, 0, 200, 30)
    assert small[0][2] == 15.0


def test_static_member_capsule_covers_axis_midpoint():
    """胶囊中段的碰撞（内切三圆口径会漏——两端圆心距 278px，中段是空档）。"""
    from pet.sprite_collision import capsule_circles
    world = SpriteCollisionWorld()
    # 岛 400×80 @ (100,100)：rect 三圆圆心在 122/300/478（r=44→截 44,r=22 口径不同，
    # 但即便同口径三圆也只覆盖两端+中心），胶囊圆链连续
    world.add_static_member("island", 100, 100, 400, 80,
                            circles=capsule_circles(100, 100, 400, 80))
    # sprite 落在胶囊中段边缘（x=210，两端圆都够不着的位置），与岛相触
    s = FakeSprite(180, 80, w=60, h=60, collision_id="fish")
    events = []
    world.add_collision_listener(events.append)
    world.tick([s], 0.016)
    assert events or s.pos.y() != 80 or s.velocity.y() != 0.0  # 发生碰撞结算


# ---------------------------------------------------------------- G4：宠物间总碰撞开关
def _three_pets(*, restitution=1.0, friction=0.0):
    """三宠同水平线：a/b 迎面对撞（重叠 10px），b/c 相邻（重叠 10px）。

    e=1/无摩擦以便断言精确速度交换（口径同 test_head_on_elastic_collision）。
    """
    world = SpriteCollisionWorld(restitution=restitution, friction=friction)
    a = FakeSprite(0, 0, vx=500, collision_id="a")
    b = FakeSprite(90, 0, vx=-500, collision_id="b")
    c = FakeSprite(180, 0, vx=0, collision_id="c")
    return world, a, b, c


def test_pet_collision_disabled_excludes_only_that_pet():
    """G4：关掉某只的宠物间总开关 = 那只退出宠-宠结算，别的宠之间照常。"""
    world, a, b, c = _three_pets()
    c.collision_enabled = False
    before_c = (c.pos.x(), c.pos.y())
    events = []
    world.add_collision_listener(events.append)

    world.tick([a, b, c], 0.016)

    # a/b 照常真撞（速度交换、进抛掷）；c 与 b 的 pair 被过滤，所以 b 只与 a 结算
    assert a.velocity.x() == -500.0
    assert b.velocity.x() == 500.0
    assert a.interaction_state == INTERACTION_THROWN
    # 被关的那只：不吸收冲量、不被位置分离推开、不出事件
    assert c.velocity.x() == 0.0 and c.velocity.y() == 0.0
    assert (c.pos.x(), c.pos.y()) == before_c
    assert c.interaction_state == INTERACTION_NORMAL
    pairs = {event.pair for event in events}
    assert pairs == {"a|b"}
    # 过滤发生在碰撞检测之前：该 pair 连重叠簿记都不该留下
    assert not any("c" in key for key in world._overlap_history)


def test_pet_collision_default_enabled_for_all_pets():
    """不设该属性（默认）= 全部参与结算，老行为不变。"""
    world, a, b, c = _three_pets()
    events = []
    world.add_collision_listener(events.append)

    world.tick([a, b, c], 0.016)

    # a/b 真撞出事件；b/c（相对速度 ~0 → 纯位置分离）照样结算，把 c 推开
    assert [event.pair for event in events] == ["a|b"]
    assert c.pos.x() > 180.0
    assert any("c" in key for key in world._overlap_history)


def test_pet_collision_toggle_off_on_leaves_no_ghost_impulse():
    """关→开热切：监听器不增长；关闭期间的长位移不留过期扫掠/幽灵冲量。"""
    world = SpriteCollisionWorld(restitution=1.0, friction=0.0)
    a = FakeSprite(0, 0, vx=0, collision_id="a")
    b = FakeSprite(0, 0, vx=0, collision_id="b")
    events = []
    world.add_collision_listener(events.append)

    # 关：完全重叠也不结算
    b.collision_enabled = False
    for _ in range(3):
        world.tick([a, b], 0.016)
    assert events == []
    assert a.velocity.x() == 0.0 and b.velocity.x() == 0.0
    assert len(world._listeners) == 1

    # 关闭期间 b 一帧内跳远（帧间扫掠若还覆盖该 pair 就是幽灵撞击）
    b.set_pos(FakePoint(2000, 0))
    world.tick([a, b], 0.016)
    assert events == []

    # 开：远处重新启用不得补发过期冲量（prev 快照每 tick 照常刷新）
    b.collision_enabled = True
    world.tick([a, b], 0.016)
    assert events == []
    assert a.velocity.x() == 0.0 and b.velocity.x() == 0.0

    # 真靠近才有真撞击；监听器仍只有 1 份
    b.set_pos(FakePoint(90, 0))
    a.set_velocity(FakePoint(500, 0))
    b.set_velocity(FakePoint(-500, 0))
    world.tick([a, b], 0.016)
    assert [event.pair for event in events] == ["a|b"]
    assert len(world._listeners) == 1


def test_pet_collision_disabled_keeps_static_member_contact():
    """岛的静态接触不受宠物间总开关影响（岛开关才是岛-宠碰撞的门）。"""
    world = SpriteCollisionWorld()
    world.add_static_member("island", 300, 0, 40, 200)
    a = FakeSprite(230, 50, vx=500, collision_id="a")
    a.collision_enabled = False
    events = []
    world.add_collision_listener(events.append)

    world.tick([a], 0.016)

    # 口径同 test_static_member_bounces_with_trampoline_restitution：1.3 加速弹开
    assert a.velocity.x() == -650.0
    assert [event.pair for event in events] == ["a|island"]


# ---------------------------------------------------------------- 累计分离写回（D9）
def _overlap_between(world, first, second) -> float:
    """两 sprite 当前成员快照的实际重叠深度（真值取自世界自己的取数口径）。"""
    hit, _nx, _ny, overlap, _cx, _cy = collision.check_collision_members(
        world._member_from_sprite(first), world._member_from_sprite(second))
    return overlap if hit else 0.0


def test_tick_writes_back_solver_cumulative_separation():
    """一轮 tick 的位移 = 求解器**累计**分离量，不是末轮迭代增量。

    旧口径逐 pair 按 ImpulseResult.dx_a/dx_b 写回，而那两个字段只记最后一轮
    的分配：20px 静止重叠四轮迭代每只该移 9.626px，实际只写回 0.5px，97% 的
    分离量被丢到下一 tick（与连续重叠记账/去抖互相打架）。
    """
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(80, 0, collision_id="b")      # 静止重叠 20px，vn=0 → j=0
    members = [world._member_from_sprite(s) for s in (a, b)]
    impulses, combined, _history = collision.solve_multi_body_collision(
        members, tick=1)

    assert impulses[0].dx_a > -1.0               # 前提：末轮只有 -0.5（旧写回量）
    assert combined["a"][2] < -5.0               # 前提：累计量是它的十几倍

    world.tick([a, b], 0.016)

    assert abs(a.pos.x() - (0.0 + combined["a"][2])) < 1e-9
    assert abs(b.pos.x() - (80.0 + combined["b"][2])) < 1e-9


def test_multi_iteration_separation_is_not_deferred_to_next_tick():
    """小重叠一轮解完：tick 后只剩求解器自己的 slop，下一 tick 无事可做。

    关掉去抖只看分离推进：3px 重叠两轮迭代累计 2.5px（每只 1.25px）一轮到位，
    残余 0.5px = 求解器 slop；旧口径只写回末轮 0.5px，残余 2.5px 留给下一 tick
    继续推（跨 tick 接力）。
    """
    world = SpriteCollisionWorld(separation_debounce_secs=0.0)
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(97, 0, collision_id="b")      # 静止重叠 3px

    world.tick([a, b], 0.016)
    ax1 = a.pos.x()
    assert _overlap_between(world, a, b) <= 0.5  # 只剩 slop 量级的残差

    world.tick([a, b], 0.016)                    # 残量在 slop 内：不再分离
    assert a.pos.x() == ax1


def test_writeback_aggregates_every_pair_once_per_sprite():
    """三体同帧：每只 sprite 只写回一次，位移 = 它在所有 pair 上的累计之和。"""
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(70, 0, collision_id="b")
    c = FakeSprite(140, 0, collision_id="c")     # a|b、b|c 各重叠 30px
    members = [world._member_from_sprite(s) for s in (a, b, c)]
    _impulses, combined, _history = collision.solve_multi_body_collision(
        members, tick=1)

    assert combined["a"][2] < -5.0               # 前提：累计量远大于末轮（3.675）

    world.tick([a, b, c], 0.016)

    assert abs(a.pos.x() - (0.0 + combined["a"][2])) < 1e-9
    assert abs(b.pos.x() - (70.0 + combined["b"][2])) < 1e-9
    assert abs(c.pos.x() - (140.0 + combined["c"][2])) < 1e-9


def test_debounced_pair_does_not_ride_another_pairs_writeback():
    """去抖抑制按 pair 生效：被抑制的 pair 不许搭别的 pair 的写回一起落地。

    a|b 静止贴贴已进窗口（去抖抑制它的位移），同帧 d 撞上 a（有冲量 + 位移）：
    a 的位移必须只等于 a|d 那一对的累计。按 combined 整块写回会把 a|b 的 0.5px
    也带出来，窗口形同虚设。
    """
    clock = FakeClock()
    world = SpriteCollisionWorld(clock=clock)
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(80, 0, collision_id="b")
    world.tick([a, b], 0.016)                    # 首次分离 + 记入去抖表
    assert "a|b" in world._position_only_at
    ax1, ay1 = a.pos.x(), a.pos.y()

    d = FakeSprite(0, -60, vy=300.0, collision_id="d")   # 压向 a（b 够不着）
    members = [world._member_from_sprite(s) for s in (a, b, d)]
    impulses, combined, _history = collision.solve_multi_body_collision(
        members, tick=2, overlap_history=world._overlap_history)
    pair_deltas = {r.pair: r for r in impulses}
    suppressed = pair_deltas["a|b"]
    assert suppressed.sep_dx_a != 0.0            # 前提：被抑制的 pair 确实带位移
    assert pair_deltas["a|d"].sep_dx_a != 0.0

    world.tick([a, b, d], 0.016)

    kept_dx = combined["a"][2] - suppressed.sep_dx_a
    kept_dy = combined["a"][3] - suppressed.sep_dy_a
    assert abs(kept_dx - combined["a"][2]) > 0.1  # 前提：抑制量非零且可分辨
    assert abs(a.pos.x() - (ax1 + kept_dx)) < 1e-9
    assert abs(a.pos.y() - (ay1 + kept_dy)) < 1e-9


# ---------------------------------------------------------------- 逐只隐藏（D10）
def test_hidden_sprite_is_not_a_collider():
    """隐藏的 sprite 退出碰撞世界：不产生结果、不推动可见成员。

    旧行为：隐藏宠仍是可见碰撞体——隐形障碍墙推开/撞飞可见宠、触发碰撞反馈。
    """
    world = SpriteCollisionWorld()
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(80, 0, vx=-500.0, collision_id="b")
    b.visible = False                     # 鸭子类型判据同 overlay_window.sprite_at

    assert world.tick([a, b], 0.016) == []
    assert a.pos.x() == 0.0 and a.pos.y() == 0.0
    assert a.velocity.x() == 0.0          # 不给速度
    assert a.interaction_state == INTERACTION_NORMAL   # 也不触发真撞击抛掷

    b.visible = True                      # 恢复可见：碰撞干净回归
    results = world.tick([a, b], 0.016)
    assert [r.pair for r in results] == ["a|b"]
    assert a.velocity.x() < 0.0           # 被撞飞


def test_debounce_entry_dropped_when_member_leaves_collision_world():
    """成员离场即清掉它的去抖条目：重新可见后第一次分离不被旧窗口抑制。"""
    clock = FakeClock()
    world = SpriteCollisionWorld(clock=clock)
    a = FakeSprite(0, 0, collision_id="a")
    b = FakeSprite(80, 0, collision_id="b")
    world.tick([a, b], 0.016)
    assert "a|b" in world._position_only_at        # 已记入去抖表（窗口内不再推）

    b.visible = False
    world.tick([a, b], 0.016)
    assert "a|b" not in world._position_only_at    # 离场不留号

    b.visible = True
    a.set_pos(FakePoint(0, 0))                     # 重叠复原 = 另起一段接触
    b.set_pos(FakePoint(80, 0))
    world.tick([a, b], 0.016)
    assert a.pos.x() < -1.0                        # 分离立即生效（没被旧条目抑制）


def test_probe_sprite_exempt_from_island_static_wall():
    """探头姿态（探出屏幕边缘）的 sprite 不与岛静态墙结算：开关「岛碰撞」把墙
    注册到探头宠身上时，不得把它从探头姿态里挤出去（位置/速度/交互态都不动，
    否则姿态与气泡锚点错位）。真实 sprite 的 ``probe_active`` 是 property，
    这里用同名实例属性鸭子对齐（读取侧走 getattr）。"""
    world = SpriteCollisionWorld()
    probe = FakeSprite(100, 0)
    probe.probe_active = True
    world.add_static_member("island", 80, -10, 200, 44)
    world.tick([probe], 0.016)
    assert probe.pos.x() == 100 and probe.pos.y() == 0
    assert probe.speed() == 0.0
    assert probe.interaction_state == INTERACTION_NORMAL


def test_normal_sprite_still_separated_from_island_wall():
    """对照组：非探头 sprite 与岛墙重叠照常分离（探头豁免不把岛墙变幽灵）。"""
    world = SpriteCollisionWorld()
    sprite = FakeSprite(100, 0)
    world.add_static_member("island", 80, -10, 200, 44)
    world.tick([sprite], 0.016)
    assert (sprite.pos.x(), sprite.pos.y()) != (100, 0)
