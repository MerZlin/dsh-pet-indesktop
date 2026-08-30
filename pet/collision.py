# -*- coding: utf-8 -*-
"""多开桌宠碰撞物理核心与协议编解码（纯 Python 实现，无 Qt 依赖）。

包含：
1. 圆链/椭圆碰撞检测（Broad-phase AABB + Narrow-phase）
2. 质量计算（面积加权 clamp 0.5~3.0，无基准按 scale^2 fallback）
3. 冲量求解（恢复系数默认 0.82、切向摩擦 mu=0.08、库仑上限、每质量 9000px/s 限制）
4. 位置分离（逆质量分摊、每次最多 60% 重叠、min 1px / max 12px、0.5px slop、连续 3 tick 强制完整分离）
5. 稳定重合方向（两 ID 稳定哈希，禁用随机）
6. 协议帧解析与编码（4 字节大端长度前缀 + UTF-8 JSON，4096 字节上限超限丢弃）
7. 水位去重（按 epoch 记录每个 pair 最高已应用 tick）
8. 多体碰撞冲量合并与迭代分离
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# ---- 默认物理与协议常量 ----
DEFAULT_RESTITUTION: float = 0.82       # 默认恢复系数
DEFAULT_FRICTION: float = 0.08          # 默认切向摩擦系数
DEFAULT_MASS_SCALE: float = 1.0         # 默认质量倍率
DEFAULT_IMPULSE_CAP: float = 9000.0     # 每单位质量等效冲量上限 (px/s)
IMPULSE_MIN_APPROACH_SPEED: float = 80.0  # 低于此接近速度只做位置分离 (px/s)
DEFAULT_BASE_SCALE: float = 0.72        # 基准缩放

FRAME_MAX_LENGTH: int = 4096            # 单帧最大字节数（含/不含前缀，此处限制载荷<=4096）
HEADER_SIZE: int = 4                    # 4字节无符号大端整数长度头

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
    dx_a: float = 0.0
    dy_a: float = 0.0
    dx_b: float = 0.0
    dy_b: float = 0.0


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
    质量按体型加权但收窄到 0.7~1.6——0.5~3.0 的 6 倍极差会让重射手
    保留大部分动量"碾过去"（用户实机反馈不像台球）；收窄后射手撞完
    基本停下、目标飞出去，仍保留"大一点的更稳"的手感差异。
    collision_mass_scale 是用户设置的全局倍率，照常参与。
    """
    if base_radius_x is not None and base_radius_y is not None and base_radius_x > 1e-4 and base_radius_y > 1e-4:
        raw = (radius_x * radius_y) / (base_radius_x * base_radius_y) * collision_mass_scale
    else:
        scale_ratio = scale / DEFAULT_BASE_SCALE if DEFAULT_BASE_SCALE > 0 else 1.0
        raw = collision_mass_scale * (scale_ratio ** 2)
    return max(0.7, min(1.6, float(raw)))


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


def check_collision_circles(
    circles1: Sequence[Sequence[float]], circles2: Sequence[Sequence[float]],
    id1: str = "", id2: str = "",
) -> tuple[bool, float, float, float, float, float]:
    """Check all circle pairs and return the deepest overlap."""
    best = None
    for raw1 in circles1 or ():
        for raw2 in circles2 or ():
            if len(raw1) < 3 or len(raw2) < 3:
                continue
            x1, y1, r1 = map(float, raw1[:3])
            x2, y2, r2 = map(float, raw2[:3])
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
    best = None  # (t, nx, ny, contact_x, contact_y)
    for raw_a_prev, raw_a_curr in zip(a_prev or (), a_curr or ()):
        if len(raw_a_prev) < 3 or len(raw_a_curr) < 3:
            continue
        ax0, ay0 = float(raw_a_prev[0]), float(raw_a_prev[1])
        ax1, ay1 = float(raw_a_curr[0]), float(raw_a_curr[1])
        r1 = max(1e-4, float(raw_a_curr[2]))
        for raw_b_prev, raw_b_curr in zip(b_prev or (), b_curr or ()):
            if len(raw_b_prev) < 3 or len(raw_b_curr) < 3:
                continue
            bx0, by0 = float(raw_b_prev[0]), float(raw_b_prev[1])
            bx1, by1 = float(raw_b_curr[0]), float(raw_b_curr[1])
            r2 = max(1e-4, float(raw_b_curr[2]))
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
        return False, 0.0, 0.0, 0.0, 0.0, 0.0
    _, nx, ny, contact_x, contact_y = best
    return True, nx, ny, 1.0, contact_x, contact_y


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
    - 连续 3 tick 强制完整分离时 (force_full=True): 修正 100% 重叠且无 min/max 截断限制
    
    返回: (sep_dist, dx_a, dy_a, dx_b, dy_b)
    """
    sum_inv_m = inv_m_a + inv_m_b
    if sum_inv_m <= 0.0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    eff_overlap = max(0.0, overlap - slop)
    if eff_overlap <= 0.0 and not force_full:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    if force_full:
        sep_dist = overlap
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
) -> tuple[List[ImpulseResult], Dict[str, tuple[float, float]], Dict[str, int]]:
    """三体及以上/同快照的多体碰撞求解。
    
    步骤：
    1. 生成按 runtime_id 字典序排序的所有无序 pair；
    2. 检测碰撞并基于当前快照计算冲量；
    3. 位置分离采用最深重叠优先、最多 4 轮迭代；
    4. 对同一成员的冲量/位移做向量合并。
    
    返回: (impulse_list, combined_impulses_by_id, updated_overlap_history)
    - combined_impulses_by_id: {runtime_id: (total_dvx, total_dvy, total_dx, total_dy)}
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

            pos_map[id_a][0] += dxa
            pos_map[id_a][1] += dya
            pos_map[id_b][0] += dxb
            pos_map[id_b][1] += dyb

            total_pos_deltas[id_a][0] += dxa
            total_pos_deltas[id_a][1] += dya
            total_pos_deltas[id_b][0] += dxb
            total_pos_deltas[id_b][1] += dyb

            pair_sep_results[pair_k] = (sep_dist, dxa, dya, dxb, dyb)

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

    return impulse_list, combined_impulses_by_id, new_overlap_history


# ---- 协议帧编解码与水位去重 ----

@dataclass
class DecodeError:
    """协议解码错误对象（避免抛异常）。"""
    reason: str
    raw_data: bytes = b""


def encode_frame(obj: Any) -> bytes:
    """将 Python 对象编码为 4 字节大端长度前缀 + UTF-8 JSON 字节帧。"""
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    length = len(payload)
    header = length.to_bytes(HEADER_SIZE, byteorder="big", signed=False)
    return header + payload


class FrameStreamDecoder:
    """流式帧解析器，支持粘包与半包解析，超过 4096 字节安全丢弃。"""

    def __init__(self, max_frame_len: int = FRAME_MAX_LENGTH) -> None:
        self._buffer = bytearray()
        self.max_frame_len = max_frame_len

    def feed(self, chunk: bytes) -> List[Any | DecodeError]:
        """喂入字节流，返回解析成功的消息对象列表或 DecodeError 列表。"""
        if not chunk:
            return []
        self._buffer.extend(chunk)
        results: List[Any | DecodeError] = []

        while True:
            if len(self._buffer) < HEADER_SIZE:
                break

            # 读取 4 字节大端长度
            length = int.from_bytes(self._buffer[:HEADER_SIZE], byteorder="big", signed=False)

            # 超限检查
            if length > self.max_frame_len or length < 0:
                dropped = bytes(self._buffer[:HEADER_SIZE])
                del self._buffer[:HEADER_SIZE]
                results.append(DecodeError(reason=f"Frame length {length} exceeds limit {self.max_frame_len}", raw_data=dropped))
                # The payload length is untrusted, so discard only this header
                # and search the remaining stream for the next plausible header.
                sync_at = None
                for offset in range(len(self._buffer) - HEADER_SIZE + 1):
                    candidate = int.from_bytes(self._buffer[offset:offset + HEADER_SIZE], "big")
                    if 0 < candidate <= self.max_frame_len:
                        sync_at = offset
                        break
                if sync_at is None:
                    self._buffer[:] = self._buffer[-(HEADER_SIZE - 1):]
                    break
                del self._buffer[:sync_at]
                continue

            # 空帧处理 (length == 0)
            if length == 0:
                # 移除这 4 字节
                del self._buffer[:HEADER_SIZE]
                results.append(DecodeError(reason="Empty frame (length 0)", raw_data=b""))
                continue

            # 检查是否接收完整帧载荷
            if len(self._buffer) < HEADER_SIZE + length:
                # 半包，等待更多数据
                break

            # 提取完整载荷
            payload_bytes = bytes(self._buffer[HEADER_SIZE:HEADER_SIZE + length])
            del self._buffer[:HEADER_SIZE + length]

            try:
                text = payload_bytes.decode("utf-8")
                obj = json.loads(text)
                results.append(obj)
            except UnicodeDecodeError as e:
                results.append(DecodeError(reason=f"UTF-8 decode error: {e}", raw_data=payload_bytes))
            except json.JSONDecodeError as e:
                results.append(DecodeError(reason=f"JSON decode error: {e}", raw_data=payload_bytes))

        return results


class WatermarkDeduplicator:
    """基于 epoch / pair / tick 的水位去重器 (plan4 §2.1 & §3.2)。
    
    客户端每个 epoch 内以 pair 为键记录最高已应用 tick 的水位，不重复应用低于或等于水位的事件。
    当 epoch 变更时，整体重置水位表。
    """

    def __init__(self) -> None:
        self.current_epoch: str = ""
        self.watermarks: Dict[str, int] = {}

    def should_apply(self, epoch: str, pair: str, tick: int) -> bool:
        """检查该 impulse 是否应当被应用。
        
        如果通过，更新水位并返回 True；若已重复或已过期则返回 False。
        """
        if not epoch or not pair:
            return False

        # epoch 切换：整体替换
        if epoch != self.current_epoch:
            self.current_epoch = epoch
            self.watermarks = {pair: tick}
            return True

        last_tick = self.watermarks.get(pair, -1)
        if tick > last_tick:
            self.watermarks[pair] = tick
            return True

        return False
