"""多宠碰撞的纯几何/冲量求解（无 Qt）。

几何口径对齐全项目主分支 `pet/collision.py`：
- 碰撞体是**内切三圆链**（`circles_from_rect`），不是整框椭圆——椭圆在四角不判撞，
  看起来就是穿模；
- 位置分离按「0.5px slop + 每次最多修正 60% 重叠 + 单次 1..12px」，
  同一对连续 `FORCE_FULL_TICKS` 个 tick 仍重叠时强制完整分离（上限 4×12px）；
- 重合方向用对 (slot_a, slot_b) 的稳定哈希，禁止随机抖动。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

RESTITUTION = 0.82
FRICTION = 0.08
IMPULSE_MIN_APPROACH_SPEED = 80.0
HIT_FEEDBACK_MIN_SPEED = 300.0

OVERLAP_RATIO = 0.6
MIN_SEPARATION = 1.0
MAX_SEPARATION = 12.0
SLOP = 0.5
FORCE_FULL_TICKS = 3
FORCE_FULL_FACTOR = 4.0

BASE_MASS_SCALE = 0.72
MIN_DEPTH = 1e-6

Circle = tuple[float, float, float]


def circles_from_rect(rect: tuple[float, float, float, float]) -> tuple[Circle, ...]:
    """可见内容矩形的内切三圆（沿长边排），同主分支 ``circles_from_rect``。"""
    left, top, width, height = (float(value) for value in rect)
    width = max(0.0, width)
    height = max(0.0, height)
    radius = min(width, height) / 2.0
    if radius <= 0.0:
        return ()
    cx = left + width / 2.0
    cy = top + height / 2.0
    if width >= height:
        return (
            (left + radius, cy, radius),
            (cx, cy, radius),
            (left + width - radius, cy, radius),
        )
    return (
        (cx, top + radius, radius),
        (cx, cy, radius),
        (cx, top + height - radius, radius),
    )


def stable_tie_break(slot_a: int, slot_b: int) -> tuple[float, float]:
    """完全同心时的稳定分离方向（同一对宠物永远同一个方向）。"""
    low, high = sorted((int(slot_a), int(slot_b)))
    angle = math.radians((low * 37 + high * 61) % 360)
    return math.cos(angle), math.sin(angle)


#: 轴对齐粗筛的容差：内切圆链只可能在矩形内/相切处相交，留 1px 抵消浮点与 SLOP。
MAY_TOUCH_MARGIN = 1.0


def rects_may_touch(
    rect_a: tuple[float, float, float, float],
    rect_b: tuple[float, float, float, float],
    *,
    margin: float = MAY_TOUCH_MARGIN,
) -> bool:
    """两矩形是否可能接触（**必要**条件，不是判定结果）。

    碰撞几何是矩形**内切**的三圆链（:func:`circles_from_rect`），圆只会在矩形内部或
    相切处相交，所以「矩形分离」⇒「圆链不可能重叠」。多宠数量上去以后这条粗筛把
    n² 对里的绝大多数（彼此离得远的）压到几次比较，真正的圆链数学只跑相邻的那几对；
    相交的矩形仍照旧交给 :func:`deepest_overlap`，判定结果一个都没改。
    """
    ax, ay, aw, ah = rect_a
    bx, by, bw, bh = rect_b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    reach = float(margin)
    return (
        ax - reach < bx + bw
        and bx - reach < ax + aw
        and ay - reach < by + bh
        and by - reach < ay + ah
    )


@dataclass(frozen=True)
class CollisionBody:
    x: float
    y: float
    width: float
    height: float
    vx: float = 0.0
    vy: float = 0.0
    dragging: bool = False
    static: bool = False
    scale: float = BASE_MASS_SCALE
    slot: int = 0

    @property
    def cx(self) -> float:
        return self.x + self.width / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.height / 2.0

    @property
    def circles(self) -> tuple[Circle, ...]:
        return circles_from_rect((self.x, self.y, self.width, self.height))

    @property
    def mass(self) -> float:
        if self.dragging or self.static:
            return math.inf
        area = max(1.0, self.width * self.height)
        reference = max(1.0, (640.0 * self.scale) * (360.0 * self.scale))
        return max(0.5, min(2.5, area / reference))


@dataclass(frozen=True)
class CollisionResult:
    ax: float
    ay: float
    bx: float
    by: float
    avx: float
    avy: float
    bvx: float
    bvy: float
    nx: float
    ny: float
    depth: float
    impact_speed: float
    separation: float = 0.0
    force_full: bool = False


def deepest_overlap(
    circles_a: tuple[Circle, ...],
    circles_b: tuple[Circle, ...],
    *,
    tie_break: tuple[float, float] = (1.0, 0.0),
) -> tuple[float, float, float] | None:
    """两圆链的最深重叠：返回 (depth, nx, ny)，n 由 A 指向 B。"""
    best: tuple[float, float, float] | None = None
    for ax, ay, ar in circles_a:
        for bx, by, br in circles_b:
            dx = bx - ax
            dy = by - ay
            limit = ar + br
            distance = math.hypot(dx, dy)
            if distance >= limit:
                continue
            depth = limit - distance
            if best is not None and depth <= best[0]:
                continue
            if distance <= 1e-9:
                nx, ny = tie_break
            else:
                nx, ny = dx / distance, dy / distance
            best = (depth, nx, ny)
    return best


def separation_distance(depth: float, *, force_full: bool = False) -> float:
    """单 tick 分离距离：60% 重叠、1..12px；强制分离走 100% 且上限 4×12px。"""
    if force_full:
        return min(float(depth), MAX_SEPARATION * FORCE_FULL_FACTOR)
    effective = max(0.0, float(depth) - SLOP)
    if effective <= 0.0:
        return 0.0
    return max(MIN_SEPARATION, min(MAX_SEPARATION, effective * OVERLAP_RATIO))


def collide(
    a: CollisionBody,
    b: CollisionBody,
    *,
    force_full: bool = False,
    tie_break: tuple[float, float] | None = None,
) -> CollisionResult | None:
    """求解一对碰撞。``force_full`` 时一次完全分离，否则按 60% 规则逐步推。"""
    # 粗筛放在这里（而不是只放在调用方）：圆链在矩形内切，矩形分离时圆链必不重叠，
    # 直接省掉 9 对圆心距离计算；对相交矩形没有任何影响。
    if not rects_may_touch(
        (a.x, a.y, a.width, a.height), (b.x, b.y, b.width, b.height)
    ):
        return None
    direction = tie_break or stable_tie_break(a.slot, b.slot)
    overlap = deepest_overlap(a.circles, b.circles, tie_break=direction)
    if overlap is None:
        return None
    depth, nx, ny = overlap
    if depth <= MIN_DEPTH:
        return None

    sep = separation_distance(depth, force_full=force_full)
    if sep <= 0.0:
        return None

    mass_a = a.mass
    mass_b = b.mass
    if not math.isfinite(mass_a) and not math.isfinite(mass_b):
        return None
    if not math.isfinite(mass_a):
        share_a, share_b = 0.0, 1.0
    elif not math.isfinite(mass_b):
        share_a, share_b = 1.0, 0.0
    else:
        total = mass_a + mass_b
        share_a, share_b = mass_b / total, mass_a / total

    ax = a.x - nx * sep * share_a
    ay = a.y - ny * sep * share_a
    bx = b.x + nx * sep * share_b
    by = b.y + ny * sep * share_b

    rvx = b.vx - a.vx
    rvy = b.vy - a.vy
    approach = rvx * nx + rvy * ny
    avx, avy, bvx, bvy = a.vx, a.vy, b.vx, b.vy
    if approach < -IMPULSE_MIN_APPROACH_SPEED:
        inv_a = 0.0 if not math.isfinite(mass_a) else 1.0 / mass_a
        inv_b = 0.0 if not math.isfinite(mass_b) else 1.0 / mass_b
        inv_sum = inv_a + inv_b
        if inv_sum > 0.0:
            impulse = -(1.0 + RESTITUTION) * approach / inv_sum
            avx -= impulse * inv_a * nx
            avy -= impulse * inv_a * ny
            bvx += impulse * inv_b * nx
            bvy += impulse * inv_b * ny
            tx, ty = -ny, nx
            tangent = (b.vx - a.vx) * tx + (b.vy - a.vy) * ty
            friction_impulse = max(
                -FRICTION * abs(impulse), min(FRICTION * abs(impulse), -tangent / inv_sum)
            )
            avx -= friction_impulse * inv_a * tx
            avy -= friction_impulse * inv_a * ty
            bvx += friction_impulse * inv_b * tx
            bvy += friction_impulse * inv_b * ty

    return CollisionResult(
        ax=ax,
        ay=ay,
        bx=bx,
        by=by,
        avx=avx,
        avy=avy,
        bvx=bvx,
        bvy=bvy,
        nx=nx,
        ny=ny,
        depth=depth,
        impact_speed=abs(approach),
        separation=sep,
        force_full=bool(force_full),
    )
