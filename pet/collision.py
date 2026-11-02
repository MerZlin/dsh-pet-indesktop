# -*- coding: utf-8 -*-
"""多开桌宠碰撞物理核心（纯 Python 实现，无 Qt 依赖）。

包含：
1. 圆链/椭圆碰撞检测（Broad-phase AABB + Narrow-phase）
2. 质量计算（面积加权 clamp 0.5~2.5，无基准按 scale^2 fallback）
3. 冲量求解（恢复系数默认 0.82、切向摩擦 mu=0.08、库仑上限、每质量 9000px/s 限制）
4. 位置分离（逆质量分摊、每次最多 60% 重叠、min 1px / max 12px、0.5px slop、连续 3 tick 强制完整分离）
5. 稳定重合方向（两 ID 稳定哈希，禁用随机）
6. 多体碰撞冲量合并与迭代分离

本模块只做纯数学/几何：协议帧编解码与水位去重曾住在 collision_codec.py，
已随多进程多宠退役层（4.4b）删除。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

# ---- 默认物理常量 ----
DEFAULT_RESTITUTION: float = 0.82       # 默认恢复系数
DEFAULT_FRICTION: float = 0.08          # 默认切向摩擦系数
DEFAULT_MASS_SCALE: float = 1.0         # 默认质量倍率
DEFAULT_IMPULSE_CAP: float = 9000.0     # 每单位质量等效冲量上限 (px/s)
IMPULSE_MIN_APPROACH_SPEED: float = 80.0  # 低于此接近速度只做位置分离 (px/s)
DEFAULT_BASE_SCALE: float = 0.72        # 基准缩放

# ---- 状态 Flags 位定义 (plan4 §2.1) ----
FLAG_VISIBLE: int = 1 << 0              # 1: 可见
FLAG_THROWN: int = 1 << 1               # 2: 正在抛掷中
FLAG_DRAGGING: int = 1 << 2             # 4: 正在拖拽中 (无限质量)
FLAG_SLINGSHOT_AIMING: int = 1 << 3     # 8: 弹弓蓄力中
FLAG_LOCK_POSITION: int = 1 << 4        # 16: 锁定位置 (无限质量)
FLAG_NO_MOVE: int = 1 << 5              # 32: 禁止自主漫游
FLAG_MOUSE_THROUGH: int = 1 << 6        # 64: 鼠标穿透
FLAG_AUTO_CURSOR_HIDDEN: int = 1 << 7   # 128: 自动光标穿透/隐藏
FLAG_PAUSED: int = 1 << 8               # 256: 暂停活动
FLAG_COLLISION_ENABLED: int = 1 << 9    # 512: 开启碰撞
FLAG_PREDICTED_BOUNCE: int = 1 << 10    # 1024: 客户端已预测的反弹事件
FLAG_STATIC: int = 1 << 11              # 2048: 静态布景（无限质量但保留弹性）。
# 灵动岛静态成员的碰撞世界成员 id（常量，全网唯一）：岛宿主进程以此 id 把岛
# 几何注册进碰撞世界（几何复制给远端进程挂本地硬墙 + FLAG_STATIC 结算反弹）。
# 穿透防御分层：远端本地硬墙（stale-keep TTL）兜底穿透，结算只负责反弹体验——
# 旧「IPC 保活/快照时序让岛掉出碰撞世界」的穿透根因被本地墙按住（失效方向
# 是保守的：墙多留几秒，绝不提前穿透）。
ISLAND_MEMBER_ID: str = "island"

# 静态布景（灵动岛果冻墙）专用恢复系数：>1 表示撞岛被"加速弹开"——
# 撞到岛像撞到弹床，比撞墙更活泼；**只作用于"动态方尚未抛掷"的入场那一次**
# （见 solve_collision_impulse 的 FLAG_STATIC 分支）：静态布景参与的碰撞里，
# 动态方一旦带 FLAG_THROWN，恢复系数必须回到 ≤1 的普通值，否则每次触岛都
# 按 >1 放大法向速度 = 能量泵（thrown 越弹越高、永不落地的根因）。
# 鱼撞鱼/撞拖拽鱼仍走 collision_restitution 配置。
STATIC_RESTITUTION: float = 1.3


@dataclass
class MemberState:
    """参与碰撞检测的成员状态快照。"""
    runtime_id: str
    x: float
    y: float
    radius_x: float
    radius_y: float
    vx: float = 0.0
    vy: float = 0.0
    mass: float = 1.0
    is_infinite_mass: bool = False
    flags: int = FLAG_VISIBLE | FLAG_COLLISION_ENABLED
    instance_id: str = ""
    character: str = ""
    scale: float = DEFAULT_BASE_SCALE
    w: float = 0.0
    h: float = 0.0
    circles: Optional[Sequence[Sequence[float]]] = None


@dataclass
class ImpulseResult:
    """协调者计算生成的单对碰撞冲量结果。"""
    tick: int
    pair: str
    a: str
    b: str
    nx: float
    ny: float
    j: float
    sep: float
    contact_x: float
    contact_y: float
    flags: int = 0
    # 协调者 tick 时刻认定的双方中心坐标（客户端用它判断"协调者眼中的我"
    # 是否过期，代替从 contact/normal 反推——后者依赖接触点语义，三种检测
    # 路径语义不一致会导致偏差豁免系统性误判）
    ax: float = 0.0
    ay: float = 0.0
    bx: float = 0.0
    by: float = 0.0
    # 针对 a 和 b 分配的冲量增量 (px/s) 及位移分离增量 (px)
    dvx_a: float = 0.0
    dvy_a: float = 0.0
    dvx_b: float = 0.0
    dvy_b: float = 0.0
    # dx_a..dy_b 是**末轮**迭代分配的位移分离增量（既有语义不变）
    dx_a: float = 0.0
    dy_a: float = 0.0
    dx_b: float = 0.0
    dy_b: float = 0.0
    # 本 pair 跨全部迭代轮的**累计**位移分离增量：等于该 pair 在
    # combined_impulses_by_id 里的贡献（求解器自己的记账口径）。世界侧一次
    # 到位的 per-sprite 写回用它——只写末轮会把前面几轮的分离量整块丢弃；
    # 按 pair 抑制位移（纯位置分离去抖）时也靠它精确扣掉该 pair 的贡献。
    sep_dx_a: float = 0.0
    sep_dy_a: float = 0.0
    sep_dx_b: float = 0.0
    sep_dy_b: float = 0.0


def calculate_mass(
    radius_x: float,
    radius_y: float,
    base_radius_x: Optional[float] = None,
    base_radius_y: Optional[float] = None,
    scale: float = DEFAULT_BASE_SCALE,
    collision_mass_scale: float = DEFAULT_MASS_SCALE,
) -> float:
    """计算桌宠质量。
    
    规则 (plan4 §4.1，实机手感修正)：
    质量按体型加权并限制在 0.5~2.5，优先保证街机碰撞手感。
    collision_mass_scale 是用户设置的全局倍率，照常参与。
    """
    if base_radius_x is not None and base_radius_y is not None and base_radius_x > 1e-4 and base_radius_y > 1e-4:
        raw = (radius_x * radius_y) / (base_radius_x * base_radius_y) * collision_mass_scale
    else:
        scale_ratio = scale / DEFAULT_BASE_SCALE if DEFAULT_BASE_SCALE > 0 else 1.0
        raw = collision_mass_scale * (scale_ratio ** 2)
    return max(0.5, min(2.5, float(raw)))


def stable_hash_direction(id_a: str, id_b: str) -> tuple[float, float]:
    """当两中心完全重合时，根据两 ID 的稳定哈希生成固定的二维单位方向向量（禁用随机）。
    
    使用排序后的组合计算哈希角度，确保无论输入参数顺序如何，分离方向都互为反向且确定。
    """
    ordered = sorted([str(id_a), str(id_b)])
    key = f"{ordered[0]}--{ordered[1]}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    # 取前 4 字节转整数
    val = int.from_bytes(digest[:4], byteorder="big")
    angle = (val % 360000) / 1000.0 * (math.pi / 180.0)  # 0 ~ 2pi
    ux = math.cos(angle)
    uy = math.sin(angle)
    # 若 id_a 是排序中的第 1 个则取正方向，否则反向
    if id_a == ordered[0]:
        return ux, uy
    return -ux, -uy


def circles_from_rect(left: float, top: float, width: float, height: float) -> list[list[float]]:
    """Generate the three equal circles inscribed in a visible content rect."""
    left, top = float(left), float(top)
    width, height = max(0.0, float(width)), max(0.0, float(height))
    radius = min(width, height) / 2.0
    cx, cy = left + width / 2.0, top + height / 2.0
    if width >= height:
        return [[left + radius, cy, radius], [cx, cy, radius],
                [left + width - radius, cy, radius]]
    return [[cx, top + radius, radius], [cx, cy, radius],
            [cx, top + height - radius, radius]]


def circle_chain_min_gap(
    circles1: Sequence[Sequence[float]],
    circles2: Sequence[Sequence[float]],
    limit: float = math.inf,
) -> float:
    """两圆链的最小间距（负值 = 重叠深度）。

    与 check_collision_circles 的"最深重叠"口径互补：本函数要的是**最近距离**
    （贴合判定），且带早退上限——间距一旦 <=0（已接触/互穿）立即返回，调用方
    在"离得远"时是 O(1)。只服务近邻判定（静态成员支撑落定），不参与冲量求解。
    """
    best = float(limit)
    for raw1 in circles1 or ():
        if len(raw1) < 3:
            continue
        x1, y1 = float(raw1[0]), float(raw1[1])
        r1 = max(1e-4, float(raw1[2]))
        for raw2 in circles2 or ():
            if len(raw2) < 3:
                continue
            x2, y2 = float(raw2[0]), float(raw2[1])
            r2 = max(1e-4, float(raw2[2]))
            gap = math.hypot(x2 - x1, y2 - y1) - (r1 + r2)
            if gap < best:
                best = gap
                if best <= 0.0:
                    return best
    return best


def check_collision_circles(
    circles1: Sequence[Sequence[float]], circles2: Sequence[Sequence[float]],
    id1: str = "", id2: str = "",
) -> tuple[bool, float, float, float, float, float]:
    """Check all circle pairs and return the deepest overlap.

    性能（撞岛路径）：内层是每 tick 每 pair 都要跑的热循环，索引直取代替
    ``map(float, raw[:3])``（避免每对圆切片 + map 两个临时对象）——语义逐位
    不变（圆链元素本就是数值；``float()`` 只影响类型提示不影响运算）。
    """
    best = None
    for raw1 in circles1 or ():
        if len(raw1) < 3:
            continue
        x1, y1, r1 = raw1[0], raw1[1], raw1[2]
        for raw2 in circles2 or ():
            if len(raw2) < 3:
                continue
            x2, y2, r2 = raw2[0], raw2[1], raw2[2]
            r1, r2 = max(1e-4, r1), max(1e-4, r2)
            dx, dy = x2 - x1, y2 - y1
            distance = math.hypot(dx, dy)
            if distance >= r1 + r2:
                continue
            if distance < 1e-6:
                nx, ny = stable_hash_direction(id1, id2)
            else:
                nx, ny = dx / distance, dy / distance
            overlap = r1 + r2 - distance
            contact_x = (x1 + nx * r1 + x2 - nx * r2) / 2.0
            contact_y = (y1 + ny * r1 + y2 - ny * r2) / 2.0
            candidate = (overlap, nx, ny, contact_x, contact_y)
            if best is None or overlap > best[0]:
                best = candidate
    if best is None:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0
    overlap, nx, ny, contact_x, contact_y = best
    return True, nx, ny, overlap, contact_x, contact_y


def swept_circle_chain_collision(
    a_prev: Sequence[Sequence[float]], a_curr: Sequence[Sequence[float]],
    b_prev: Sequence[Sequence[float]], b_curr: Sequence[Sequence[float]],
) -> tuple[bool, float, float, float, float, float]:
    """两圆链快照间的扫掠碰撞检测（首次接触时刻 TOI）。

    把 B 的位移从 A 中减去使 B 相对静止，然后对每个圆对解二次方程
    |start + t*delta| = r1+r2 在 t∈[0,1] 的最早根——不能用"线段最近接近点"：
    匀速穿过时最近点的法线必垂直于运动方向，vn≈0 会被最小接近速度阈值
    过滤掉（表现为高速直接穿透）。TOI 时刻的法线沿两圆心连线，与相对
    速度反向分量必然为负，冲量判定必成立。

    返回: (collided, nx, ny, overlap, contact_x, contact_y)
    - nx, ny: 接触瞬间从 A 圆心指向 B 圆心的单位法线（世界坐标）
    - overlap: 固定为一个小的正值（扫掠接触是边界相触，无嵌入深度）
    - contact_x, contact_y: A 圆心在接触时刻的世界坐标
    """
    result = swept_circle_chain_collision_at_t(a_prev, a_curr, b_prev, b_curr)
    if not result[0]:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0
    _, _, nx, ny, overlap, contact_x, contact_y = result
    return True, nx, ny, overlap, contact_x, contact_y


def _swept_bounds(circles_prev: Sequence[Sequence[float]] | None,
                  circles_curr: Sequence[Sequence[float]] | None):
    """圆链在 [prev, curr] 这一帧内的保守包围盒（已含各圆半径）。

    顶点会随两帧位置做线性插值，t∈[0,1] 时刻的圆盘一定落在两端圆盘的
    凸包内，因此两端包围盒的并（AABB）覆盖整段扫掠轨迹。返回
    ``(min_x, min_y, max_x, max_y)``；空链/数据不足返回 ``None``。
    """
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for chain in (circles_prev, circles_curr):
        if not chain:
            continue
        for c in chain:
            if len(c) < 3:
                continue
            r = c[2]
            if r < 1e-4:
                r = 1e-4
            x, y = c[0], c[1]
            if x - r < min_x:
                min_x = x - r
            if x + r > max_x:
                max_x = x + r
            if y - r < min_y:
                min_y = y - r
            if y + r > max_y:
                max_y = y + r
    if min_x > max_x:
        return None
    return (min_x, min_y, max_x, max_y)


def swept_circle_chain_collision_at_t(
    a_prev: Sequence[Sequence[float]], a_curr: Sequence[Sequence[float]],
    b_prev: Sequence[Sequence[float]], b_curr: Sequence[Sequence[float]],
) -> tuple[bool, float, float, float, float, float, float]:
    """扫掠碰撞检测，同时返回首次接触的归一化时间 t。"""
    # 宽相：整段扫掠的包围盒不相交 → 这一帧不可能接触，直接早退。
    # 撞岛场景里桌宠十有八九离岛很远，这层判据把每 tick 的圆对二次方程
    # （3×N 个 sqrt/disc）整段跳过；语义不变（包围盒保守）。
    bounds_a = _swept_bounds(a_prev, a_curr)
    bounds_b = _swept_bounds(b_prev, b_curr)
    if (bounds_a is None or bounds_b is None
            or bounds_a[2] < bounds_b[0] or bounds_b[2] < bounds_a[0]
            or bounds_a[3] < bounds_b[1] or bounds_b[3] < bounds_a[1]):
        return False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    best = None  # (t, nx, ny, contact_x, contact_y)
    for raw_a_prev, raw_a_curr in zip(a_prev or (), a_curr or ()):
        if len(raw_a_prev) < 3 or len(raw_a_curr) < 3:
            continue
        ax0, ay0 = raw_a_prev[0], raw_a_prev[1]
        ax1, ay1 = raw_a_curr[0], raw_a_curr[1]
        r1 = raw_a_curr[2]
        if r1 < 1e-4:
            r1 = 1e-4
        for raw_b_prev, raw_b_curr in zip(b_prev or (), b_curr or ()):
            if len(raw_b_prev) < 3 or len(raw_b_curr) < 3:
                continue
            bx0, by0 = raw_b_prev[0], raw_b_prev[1]
            bx1, by1 = raw_b_curr[0], raw_b_curr[1]
            r2 = raw_b_curr[2]
            if r2 < 1e-4:
                r2 = 1e-4
            # 相对运动：B 静止化
            start_x, start_y = ax0 - bx0, ay0 - by0
            delta_x = (ax1 - ax0) - (bx1 - bx0)
            delta_y = (ay1 - ay0) - (by1 - by0)
            radius_sum = r1 + r2
            a_coef = delta_x * delta_x + delta_y * delta_y
            if a_coef <= 1e-12:
                continue  # 相对静止：交由当前帧检测处理
            b_coef = 2.0 * (start_x * delta_x + start_y * delta_y)
            c_coef = start_x * start_x + start_y * start_y - radius_sum * radius_sum
            if c_coef <= 0.0:
                continue  # 帧首已重叠：交由当前帧检测处理
            disc = b_coef * b_coef - 4.0 * a_coef * c_coef
            if disc < 0.0:
                continue  # 轨迹未触及
            t = (-b_coef - math.sqrt(disc)) / (2.0 * a_coef)
            if t < 0.0 or t > 1.0:
                continue
            # 接触瞬间 A 相对 B 的圆心位置（B 参考系）
            rel_x = start_x + delta_x * t
            rel_y = start_y + delta_y * t
            dist = math.hypot(rel_x, rel_y)
            if dist > 1e-6:
                # rel = A − B，法线取从 A 指向 B = −rel/dist
                nx, ny = -rel_x / dist, -rel_y / dist
            else:
                # 正中靶心法线退化：沿相对运动方向取法线
                delta_len = math.sqrt(a_coef)
                nx, ny = delta_x / delta_len, delta_y / delta_len
            # A 圆心在接触时刻的世界坐标
            contact_x = ax0 + (ax1 - ax0) * t
            contact_y = ay0 + (ay1 - ay0) * t
            if best is None or t < best[0]:
                best = (t, nx, ny, contact_x, contact_y)
    if best is None:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    t, nx, ny, contact_x, contact_y = best
    return True, t, nx, ny, 1.0, contact_x, contact_y


def check_collision_members(a: MemberState, b: MemberState) -> tuple[bool, float, float, float, float, float]:
    """Use the advertised circle chain, falling back to the legacy ellipse."""
    if a.circles is not None and b.circles is not None:
        # Keep the existing AABB broad phase before the exact circle pairs.
        if abs(b.x - a.x) >= a.radius_x + b.radius_x or abs(b.y - a.y) >= a.radius_y + b.radius_y:
            return False, 0.0, 0.0, 0.0, 0.0, 0.0
        return check_collision_circles(a.circles, b.circles, a.runtime_id, b.runtime_id)
    return check_collision_ellipse(a.x, a.y, a.radius_x, a.radius_y,
                                   b.x, b.y, b.radius_x, b.radius_y,
                                   id1=a.runtime_id, id2=b.runtime_id)


def check_collision_ellipse(
    x1: float, y1: float, rx1: float, ry1: float,
    x2: float, y2: float, rx2: float, ry2: float,
    id1: str = "", id2: str = "",
) -> tuple[bool, float, float, float, float, float]:
    """两椭圆碰撞检测与法线/重叠量/接触点计算。
    
    椭圆定义：中心 (x1, y1) 半轴 rx1, ry1；中心 (x2, y2) 半轴 rx2, ry2。
    
    返回: (collided, nx, ny, overlap, contact_x, contact_y)
    - collided: 是否碰撞
    - nx, ny: 指向物体 2 的单位碰撞法线 (从 1 指向 2)
    - overlap: 归一化重叠映射回的几何重叠深度 (px)
    - contact_x, contact_y: 期望接触点坐标
    """
    rx1, ry1 = max(1e-4, float(rx1)), max(1e-4, float(ry1))
    rx2, ry2 = max(1e-4, float(rx2)), max(1e-4, float(ry2))

    dx = float(x2 - x1)
    dy = float(y2 - y1)

    # 1. Broad-phase: AABB 快速排除
    if abs(dx) >= (rx1 + rx2) or abs(dy) >= (ry1 + ry2):
        return False, 0.0, 0.0, 0.0, 0.0, 0.0

    # 2. Narrow-phase: 归一化椭圆检测（按两半轴之和归一化，
    # 使 ndist < 1 恰好等价于沿该方向两椭圆相交）
    rx_sum = rx1 + rx2
    ry_sum = ry1 + ry2

    ndx = dx / rx_sum
    ndy = dy / ry_sum
    ndist_sq = ndx * ndx + ndy * ndy

    if ndist_sq >= 1.0:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0

    ndist = math.sqrt(ndist_sq)
    if ndist < 1e-6:
        # 完全重合，使用稳定哈希方向
        nx, ny = stable_hash_direction(id1, id2)
        overlap = min(rx_sum, ry_sum)
    else:
        # 将归一化位移映射回屏幕空间得到法线方向
        # 归一化空间下的法线向量是 (ndx, ndy)
        # 映射回屏幕坐标: (ndx * rx_sum, ndy * ry_sum) 即 (dx, dy)
        phys_dist = math.hypot(dx, dy)
        if phys_dist < 1e-6:
            nx, ny = stable_hash_direction(id1, id2)
        else:
            nx = dx / phys_dist
            ny = dy / phys_dist
        # 重叠深度按归一化比例映射回平均有效半径
        # 在方向 (nx, ny) 上，椭圆的组合半径为 R_eff
        eff_r1 = math.hypot(rx1 * nx, ry1 * ny)
        eff_r2 = math.hypot(rx2 * nx, ry2 * ny)
        overlap = max(0.0, (eff_r1 + eff_r2) - phys_dist)

    # 计算接触点 (取两椭圆边界相交中间)
    contact_x = x1 + nx * rx1
    contact_y = y1 + ny * ry1

    return True, nx, ny, overlap, contact_x, contact_y


def solve_collision_impulse(
    state_a: MemberState,
    state_b: MemberState,
    nx: float,
    ny: float,
    restitution: float = DEFAULT_RESTITUTION,
    friction: float = DEFAULT_FRICTION,
    impulse_cap: float = DEFAULT_IMPULSE_CAP,
) -> tuple[float, float, float, float, float]:
    """求解两体碰撞冲量 (带恢复系数、切向摩擦、库仑上限、每质量上限)。
    
    nx, ny 为从 A 指向 B 的单位法线。
    
    返回: (j_normal, dvx_a, dvy_a, dvx_b, dvy_b)
    """
    # 逆质量计算
    inv_m_a = 0.0 if state_a.is_infinite_mass or state_a.mass <= 0 else 1.0 / state_a.mass
    inv_m_b = 0.0 if state_b.is_infinite_mass or state_b.mass <= 0 else 1.0 / state_b.mass

    if inv_m_a == 0.0 and inv_m_b == 0.0:
        # 两者都无限质量，无速度冲量
        return 0.0, 0.0, 0.0, 0.0, 0.0

    # 相对速度 (B 相对 A): v_rel = v_b - v_a
    vrx = state_b.vx - state_a.vx
    vry = state_b.vy - state_a.vy

    # 法向相对速度 v_rel_n = dot(v_rel, n)
    vn = vrx * nx + vry * ny

    # 低速接触改非弹性冲量（e=0，只挡不弹）：vn>=0 已分离不施加；
    # 0>vn>-阈值 时用 e=0 消除法向接近速度（静置接触不抖动，但贴贴
    # 有"被挡住"的实体感，不再像空气）；vn<=-阈值 才用正常恢复系数。
    if vn >= 0.0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    e = max(0.0, min(1.0, float(restitution)))
    if state_a.is_infinite_mass or state_b.is_infinite_mass:
        # 无限质量体（拖拽/锁定中的肥鱼）吸能 e=0：被握着的一方不动，
        # 撞来的也贴停不弹飞。但 FLAG_STATIC 静态布景（灵动岛果冻墙）
        # 例外：撞岛像撞弹床，吸停会显得岛"不存在"。
        static_involved = (
            (state_a.is_infinite_mass and state_a.flags & FLAG_STATIC)
            or (state_b.is_infinite_mass and state_b.flags & FLAG_STATIC)
        )
        # 弹床系数是**入场用的一次性系数**：只有动态方还没被抛掷时才是
        # "撞岛进抛掷"的那一下（对齐 legacy island_collision._clamp_body：
        # 非 throw 分支给 dv=-(1+STATIC_RESTITUTION)*vn 并把桌宠切进抛掷物理）。
        # 动态方一旦带 FLAG_THROWN，岛就必须退化成普通反弹面，用调用方传入
        # 的恢复系数（已在上面 clamp 到 ≤1）——legacy 的 throw 分支反射速度
        # 用的就是 physics.RESTITUTION(0.78<1)。若这里仍按 1.3 放大，thrown
        # 每次触岛净吸能：反弹越来越高 → 落不了地 → is_at_rest 永不成立 →
        # 行为机永不重绑（实机"碰撞后画面卡住不动"的根因）。
        # 无限质量方（拖拽/锁定/静态）不可能是"被抛掷的动态方"。
        dynamic_thrown = (
            (not state_a.is_infinite_mass and bool(state_a.flags & FLAG_THROWN))
            or (not state_b.is_infinite_mass and bool(state_b.flags & FLAG_THROWN))
        )
        if static_involved:
            if not dynamic_thrown:
                # 入场那一次：弹床系数（静态布景的"果冻墙"手感）
                e = STATIC_RESTITUTION
            # 已在抛掷中：保持上面 clamp 到 ≤1 的调用方恢复系数
        else:
            e = 0.0
    if vn >= -IMPULSE_MIN_APPROACH_SPEED:
        e = 0.0
    sum_inv_m = inv_m_a + inv_m_b

    # 法向冲量大小 jn
    # jn = -(1 + e) * vn / sum_inv_m
    jn = -(1.0 + e) * vn / sum_inv_m

    # 限制单体冲量增量不超过 impulse_cap (等效每质量 9000px/s)
    # 对于 A: |j * inv_m_a| <= cap -> j <= cap / inv_m_a = cap * m_a
    max_j = impulse_cap / max(inv_m_a, inv_m_b) if max(inv_m_a, inv_m_b) > 0 else impulse_cap
    jn = min(jn, max_j)

    # 切向速度与摩擦 (库仑摩擦)
    # 切向向量 t 垂直于 n: tx = -ny, ty = nx
    tx = -ny
    ty = nx
    vt = vrx * tx + vry * ty

    jt = 0.0
    if abs(vt) > 1e-6 and friction > 0.0:
        # 理想无滑动切向冲量: jt_ideal = -vt / sum_inv_m
        jt_ideal = -vt / sum_inv_m
        # 库仑摩擦上限: |jt| <= mu * jn
        mu_max = friction * abs(jn)
        jt = max(-mu_max, min(mu_max, jt_ideal))

    # 总冲量向量 J (施加在 B 上为 +J，施加在 A 上为 -J)
    # J = jn * n + jt * t
    jx = jn * nx + jt * tx
    jy = jn * ny + jt * ty

    dvx_a = -jx * inv_m_a
    dvy_a = -jy * inv_m_a
    dvx_b = jx * inv_m_b
    dvy_b = jy * inv_m_b

    return jn, dvx_a, dvy_a, dvx_b, dvy_b


def calculate_position_separation(
    overlap: float,
    nx: float,
    ny: float,
    inv_m_a: float,
    inv_m_b: float,
    overlap_ratio: float = 0.6,
    min_sep: float = 1.0,
    max_sep: float = 12.0,
    slop: float = 0.5,
    force_full: bool = False,
) -> tuple[float, float, float, float, float]:
    """计算位置分离位移。
    
    规则 (plan4 §4.2):
    - 逆质量分摊，固定方由动态方承担
    - 增加 0.5px slop 容差 (有效重叠 = max(0, overlap - slop))
    - 每次最多修正 60% 重叠 (overlap_ratio=0.6)
    - 最小 1px，最大 12px (min_sep=1.0, max_sep=12.0)
    - 连续 3 tick 强制完整分离时 (force_full=True): 修正 100% 重叠且单次最多 4 倍 max_sep；应用后重置该 pair 历史
    
    返回: (sep_dist, dx_a, dy_a, dx_b, dy_b)
    """
    sum_inv_m = inv_m_a + inv_m_b
    if sum_inv_m <= 0.0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    eff_overlap = max(0.0, overlap - slop)
    if eff_overlap <= 0.0 and not force_full:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    if force_full:
        sep_dist = min(overlap, max_sep * 4)
    else:
        target_sep = eff_overlap * overlap_ratio
        sep_dist = max(min_sep, min(max_sep, target_sep))

    frac_a = inv_m_a / sum_inv_m
    frac_b = inv_m_b / sum_inv_m

    # A 沿 -n 移动，B 沿 +n 移动
    dx_a = -nx * sep_dist * frac_a
    dy_a = -ny * sep_dist * frac_a
    dx_b = nx * sep_dist * frac_b
    dy_b = ny * sep_dist * frac_b

    return sep_dist, dx_a, dy_a, dx_b, dy_b


def solve_multi_body_collision(
    members: Sequence[MemberState],
    tick: int = 0,
    overlap_history: Optional[Dict[str, int]] = None,
    restitution: float = DEFAULT_RESTITUTION,
    friction: float = DEFAULT_FRICTION,
    impulse_cap: float = DEFAULT_IMPULSE_CAP,
    max_separation_iterations: int = 4,
    swept_collisions: Optional[Dict[str, tuple[bool, float, float, float, float, float]]] = None,
    ignored_pairs: Optional[Set[str]] = None,
) -> tuple[List[ImpulseResult], Dict[str, tuple[float, float]], Dict[str, int]]:
    """三体及以上/同快照的多体碰撞求解。
    
    步骤：
    1. 生成按 runtime_id 字典序排序的所有无序 pair；
    2. 检测碰撞并基于当前快照计算冲量；
    3. 位置分离采用最深重叠优先、最多 4 轮迭代；
    4. 对同一成员的冲量/位移做向量合并。
    
    返回: (impulse_list, combined_impulses_by_id, updated_overlap_history)
    - combined_impulses_by_id: {runtime_id: (total_dvx, total_dvy, total_dx, total_dy)}
      其中 total_dx/total_dy 是该成员**跨全部迭代轮**的累计位移分离量；
      每个 ImpulseResult 的 sep_dx_a..sep_dy_b 是本 pair 对它的分解（按成员
      求和 == total_dx/total_dy），dx_a..dy_b 则只是末轮增量。
    - updated_overlap_history: 更新后的连续重叠计数器
    """
    sorted_members = sorted(members, key=lambda m: m.runtime_id)
    n = len(sorted_members)
    new_overlap_history: Dict[str, int] = {}
    history = overlap_history or {}

    pairs_data = []

    # 1. 收集所有发生碰撞的 pair
    for i in range(n):
        for j in range(i + 1, n):
            m1 = sorted_members[i]
            m2 = sorted_members[j]

            # 检查是否参与碰撞
            if not (m1.flags & FLAG_VISIBLE) or not (m2.flags & FLAG_VISIBLE):
                continue
            if not (m1.flags & FLAG_COLLISION_ENABLED) or not (m2.flags & FLAG_COLLISION_ENABLED):
                continue
            if (m1.flags & FLAG_PAUSED) or (m2.flags & FLAG_PAUSED):
                continue

            collided, nx, ny, overlap, cx, cy = check_collision_members(m1, m2)
            was_swept = False

            pair_key = f"{m1.runtime_id}|{m2.runtime_id}"
            if ignored_pairs and pair_key in ignored_pairs:
                continue
            if not collided and swept_collisions:
                collided, nx, ny, overlap, cx, cy = swept_collisions.get(
                    pair_key, (False, 0.0, 0.0, 0.0, 0.0, 0.0))
                was_swept = collided
            if collided and overlap >= 0.0:
                consecutive = history.get(pair_key, 0) + 1
                new_overlap_history[pair_key] = consecutive

                # 速度冲量计算
                jn, dvx_a, dvy_a, dvx_b, dvy_b = solve_collision_impulse(
                    m1, m2, nx, ny,
                    restitution=restitution,
                    friction=friction,
                    impulse_cap=impulse_cap,
                )

                # A persistent overlap must not keep carrying normal approach
                # velocity into the next tick after the forced separation.
                if consecutive >= 3:
                    inv_a = 0.0 if m1.is_infinite_mass or m1.mass <= 0 else 1.0 / m1.mass
                    inv_b = 0.0 if m2.is_infinite_mass or m2.mass <= 0 else 1.0 / m2.mass
                    inv_sum = inv_a + inv_b
                    vn = (m2.vx - m1.vx) * nx + (m2.vy - m1.vy) * ny
                    if inv_sum > 0.0 and vn < -IMPULSE_MIN_APPROACH_SPEED:
                        jn = -vn / inv_sum
                        dvx_a, dvy_a = -jn * nx * inv_a, -jn * ny * inv_a
                        dvx_b, dvy_b = jn * nx * inv_b, jn * ny * inv_b

                pairs_data.append({
                    "pair": pair_key,
                    "m1": m1,
                    "m2": m2,
                    "nx": nx,
                    "ny": ny,
                    "overlap": overlap,
                    "cx": cx,
                    "cy": cy,
                    "jn": jn,
                    "dvx_a": dvx_a,
                    "dvy_a": dvy_a,
                    "dvx_b": dvx_b,
                    "dvy_b": dvy_b,
                    "consecutive": consecutive,
                    "swept": was_swept,
                })

    # 2. 迭代分离位置 (最深重叠优先，最多 max_separation_iterations 轮)
    # 维护临时的位置拷贝以进行迭代调整
    pos_map = {m.runtime_id: [float(m.x), float(m.y)] for m in sorted_members}
    member_map = {m.runtime_id: m for m in sorted_members}
    total_pos_deltas: Dict[str, list[float]] = {m.runtime_id: [0.0, 0.0] for m in sorted_members}
    pair_sep_results: Dict[str, tuple[float, float, float, float, float]] = {}
    # pair -> 跨迭代轮累计位移 [dxa, dya, dxb, dyb]（pair_sep_results 只留末轮，
    # 这里留累计；两者按成员求和都等于 total_pos_deltas）
    pair_pos_totals: Dict[str, list[float]] = {}
    force_full_pairs: set[str] = set()

    # Swept contacts are no longer overlapping at the current snapshot, so
    # apply their separation directly from the swept closest approach.
    for p in pairs_data:
        if not p["swept"]:
            continue
        m_a, m_b = p["m1"], p["m2"]
        inv_m_a = 0.0 if m_a.is_infinite_mass or m_a.mass <= 0 else 1.0 / m_a.mass
        inv_m_b = 0.0 if m_b.is_infinite_mass or m_b.mass <= 0 else 1.0 / m_b.mass
        separation = calculate_position_separation(
            p["overlap"], p["nx"], p["ny"], inv_m_a, inv_m_b)
        sep_dist, dxa, dya, dxb, dyb = separation
        pos_map[m_a.runtime_id][0] += dxa
        pos_map[m_a.runtime_id][1] += dya
        pos_map[m_b.runtime_id][0] += dxb
        pos_map[m_b.runtime_id][1] += dyb
        total_pos_deltas[m_a.runtime_id][0] += dxa
        total_pos_deltas[m_a.runtime_id][1] += dya
        total_pos_deltas[m_b.runtime_id][0] += dxb
        total_pos_deltas[m_b.runtime_id][1] += dyb
        pair_sep_results[p["pair"]] = separation
        pair_pos_totals[p["pair"]] = [dxa, dya, dxb, dyb]

    for _ in range(max_separation_iterations):
        # 重新对各 pair 计算当前重叠深度
        current_overlaps = []
        for p in pairs_data:
            if p["swept"]:
                continue
            id_a = p["m1"].runtime_id
            id_b = p["m2"].runtime_id
            p_x1, p_y1 = pos_map[id_a]
            p_x2, p_y2 = pos_map[id_b]
            m_a, m_b = p["m1"], p["m2"]
            def moved_member(member, x, y):
                circles = member.circles
                if circles is not None:
                    ox, oy = x - member.x, y - member.y
                    circles = [[float(cx) + ox, float(cy) + oy, float(r)]
                               for cx, cy, r in circles]
                return MemberState(**{**member.__dict__, "x": x, "y": y, "circles": circles})
            a_at_pos = moved_member(m_a, p_x1, p_y1)
            b_at_pos = moved_member(m_b, p_x2, p_y2)
            c, cur_nx, cur_ny, cur_ov, _, _ = check_collision_members(a_at_pos, b_at_pos)
            if c and cur_ov > 0.5:
                current_overlaps.append((cur_ov, p["pair"], id_a, id_b, cur_nx, cur_ny, p["consecutive"]))

        if not current_overlaps:
            break

        # 按重叠最深降序排序，平局按 pair 字典序升序
        current_overlaps.sort(key=lambda x: (-x[0], x[1]))

        for cur_ov, pair_k, id_a, id_b, cur_nx, cur_ny, consecutive in current_overlaps:
            m_a = member_map[id_a]
            m_b = member_map[id_b]
            inv_m_a = 0.0 if m_a.is_infinite_mass or m_a.mass <= 0 else 1.0 / m_a.mass
            inv_m_b = 0.0 if m_b.is_infinite_mass or m_b.mass <= 0 else 1.0 / m_b.mass

            force_full = (consecutive >= 3)
            sep_dist, dxa, dya, dxb, dyb = calculate_position_separation(
                cur_ov, cur_nx, cur_ny, inv_m_a, inv_m_b,
                force_full=force_full,
            )
            if force_full:
                force_full_pairs.add(pair_k)

            pos_map[id_a][0] += dxa
            pos_map[id_a][1] += dya
            pos_map[id_b][0] += dxb
            pos_map[id_b][1] += dyb

            total_pos_deltas[id_a][0] += dxa
            total_pos_deltas[id_a][1] += dya
            total_pos_deltas[id_b][0] += dxb
            total_pos_deltas[id_b][1] += dyb

            pair_sep_results[pair_k] = (sep_dist, dxa, dya, dxb, dyb)
            sep_total = pair_pos_totals.setdefault(pair_k, [0.0, 0.0, 0.0, 0.0])
            sep_total[0] += dxa
            sep_total[1] += dya
            sep_total[2] += dxb
            sep_total[3] += dyb

    # 3. 构造输出 ImpulseResult 与成员累积冲量/位移
    impulse_list: List[ImpulseResult] = []
    combined_impulses_by_id: Dict[str, tuple[float, float, float, float]] = {
        m.runtime_id: (0.0, 0.0, total_pos_deltas[m.runtime_id][0], total_pos_deltas[m.runtime_id][1])
        for m in sorted_members
    }

    # 累加速度增量
    acc_dv = {m.runtime_id: [0.0, 0.0] for m in sorted_members}

    for p in pairs_data:
        pair_k = p["pair"]
        sep_dist, dxa, dya, dxb, dyb = pair_sep_results.get(pair_k, (0.0, 0.0, 0.0, 0.0, 0.0))
        sep_dxa, sep_dya, sep_dxb, sep_dyb = pair_pos_totals.get(
            pair_k, (0.0, 0.0, 0.0, 0.0))

        res = ImpulseResult(
            tick=tick,
            pair=pair_k,
            a=p["m1"].runtime_id,
            b=p["m2"].runtime_id,
            nx=p["nx"],
            ny=p["ny"],
            j=p["jn"],
            sep=sep_dist,
            contact_x=p["cx"],
            contact_y=p["cy"],
            ax=p["m1"].x,
            ay=p["m1"].y,
            bx=p["m2"].x,
            by=p["m2"].y,
            dvx_a=p["dvx_a"],
            dvy_a=p["dvy_a"],
            dvx_b=p["dvx_b"],
            dvy_b=p["dvy_b"],
            dx_a=dxa,
            dy_a=dya,
            dx_b=dxb,
            dy_b=dyb,
            sep_dx_a=sep_dxa,
            sep_dy_a=sep_dya,
            sep_dx_b=sep_dxb,
            sep_dy_b=sep_dyb,
        )
        impulse_list.append(res)

        acc_dv[p["m1"].runtime_id][0] += p["dvx_a"]
        acc_dv[p["m1"].runtime_id][1] += p["dvy_a"]
        acc_dv[p["m2"].runtime_id][0] += p["dvx_b"]
        acc_dv[p["m2"].runtime_id][1] += p["dvy_b"]

    for m_id in combined_impulses_by_id:
        dvx, dvy = acc_dv[m_id]
        dx = total_pos_deltas[m_id][0]
        dy = total_pos_deltas[m_id][1]
        combined_impulses_by_id[m_id] = (dvx, dvy, dx, dy)

    for pair_k in force_full_pairs:
        new_overlap_history[pair_k] = 0
    return impulse_list, combined_impulses_by_id, new_overlap_history
