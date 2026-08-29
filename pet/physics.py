# -*- coding: utf-8 -*-
"""拖拽物理的纯函数核心：弹簧步进 / 松手初速估算 / 抛掷步进。

抽成纯函数是为了可单测（PR 路线 Phase 1）；window.py 只负责喂状态和搬窗口。
所有参数集中在此并注释取值依据，不再散落硬编码。
"""

from __future__ import annotations

import math

# ---- 拖拽弹簧 ----
SPRING_K = 200.0          # 弹簧刚度：越大跟手越紧
SPRING_C = 30.0           # 阻尼：ζ=c/(2√k)≈1.06 过阻尼，不 overshoot

# ---- 松手初速估算 ----
TRAIL_KEEP_SEC = 0.15     # 拖拽途中只保留这么长的轨迹
RELEASE_WINDOW_SEC = 0.12  # 初速估算取末尾这段窗口
RELEASE_STALE_SEC = 0.15  # 松手前停顿超过它 = 静止放下（不带残余速度）
MIN_SPAN_SEC = 0.02       # 窗口太短视为不可估算
SEG_MIN_DT = 0.008        # 分段速度的最小 dt：高回报率鼠标事件间隔可低至 1ms，
                          # 过小的 dt 会把抖动放大成虚假峰值，短段向前合并
DEAD_ZONE_SPEED = 500.0   # 低于此速度 = 原地放下（px/s）
MAX_THROW_SPEED = 6000.0  # 甩出速度上限默认值（px/s）：软膝渐近值
PEAK_WEIGHT = 0.5         # 初速大小 = 端点均值*(1-w) + 窗口峰值*w
ACCEL_REF = 8000.0        # 参考加速度（px/s²）：末段加速达到它即吃满增益
ACCEL_GAIN_MAX = 0.6      # 加速度增益上限：仍在加速的甩动最多放大 60%

# ---- 甩出力度档位 ----
THROW_STRENGTH_CAPS = {
    "gentle": 3600.0,
    "standard": 4800.0,
    "strong": 7200.0,
    "crazy": 9000.0,
}


def normalize_throw_strength(value: str) -> str:
    s = str(value or "").strip().lower()
    if s in THROW_STRENGTH_CAPS:
        return s
    return "standard"


def throw_speed_cap(strength: str) -> float:
    normalized = normalize_throw_strength(strength)
    return THROW_STRENGTH_CAPS[normalized]


# ---- 弹弓参数（不可配置常量） ----
SLINGSHOT_MIN_DISTANCE = 24.0
SLINGSHOT_MAX_DISTANCE = 160.0
SLINGSHOT_BASE_SPEED = 900.0
SLINGSHOT_MAX_DEFORMATION = 1.3


def slingshot_deformation(pull_x: float, pull_y: float, progress: float,
                          maximum: float = SLINGSHOT_MAX_DEFORMATION) -> tuple[float, float]:
    """Return smooth x/y scale factors for an anisotropic slingshot stretch.

    The stretch and compression axes are projected onto the widget axes, so
    changing the pull angle never selects between discontinuous branches.
    """
    progress = max(0.0, min(1.0, float(progress)))
    maximum = max(1.0, float(maximum))
    distance = math.hypot(float(pull_x), float(pull_y))
    if distance <= 1e-6 or progress <= 0.0:
        return 1.0, 1.0
    ux, uy = float(pull_x) / distance, float(pull_y) / distance
    stretch = 1.0 + (maximum - 1.0) * progress
    squeeze = 1.0 - (1.0 - 1.0 / maximum) * progress
    return (
        math.hypot(stretch * ux, squeeze * uy),
        math.hypot(stretch * uy, squeeze * ux),
    )

# ---- 抛掷 ----
GRAVITY = 1400.0          # px/s²
RESTITUTION = 0.78        # 碰边恢复系数
GROUND_FRICTION = 2.5     # 地面水平摩擦（/s）
REST_VY = 40.0            # 落地时 |vy| 小于它直接停竖直
REST_VX = 15.0            # 地面上 |vx| 小于它认为已静止


def soft_clamp_speed(speed: float, cap: float = MAX_THROW_SPEED) -> float:
    """软上限：cap*(1-e^(-s/cap))。硬钳会把所有快甩压成同一个速度
    （"甩多快都一样"），软膝曲线保证任意力度下速度仍单调可区分，
    同时渐近不超过 cap。"""
    if speed <= 0.0 or cap <= 0.0:
        return 0.0
    return cap * (1.0 - math.exp(-speed / cap))


def slingshot_speed(distance: float, minimum: float, maximum: float, cap: float) -> float:
    """Map pull distance to an ease-out launch speed bounded by ``cap``."""
    if distance < minimum or maximum <= minimum or cap <= 0.0:
        return 0.0
    u = max(0.0, min(1.0, (float(distance) - minimum) / (maximum - minimum)))
    eased = 1.0 - (1.0 - u) ** 2
    raw = SLINGSHOT_BASE_SPEED + (3.0 * cap - SLINGSHOT_BASE_SPEED) * eased
    return soft_clamp_speed(raw, cap=cap)


def slingshot_trajectory(vx: float, vy: float, duration: float = 0.8,
                         points: int = 12, gravity: float = GRAVITY
                         ) -> list[tuple[float, float]]:
    """Sample a first-flight parabolic path relative to its launch point."""
    if duration <= 0.0 or points <= 0:
        return []
    if points == 1:
        return [(0.0, 0.0)]
    step = float(duration) / (points - 1)
    return [(float(vx) * (i * step),
             float(vy) * (i * step) + 0.5 * float(gravity) * (i * step) ** 2)
            for i in range(points)]


def spring_velocity(v: float, x: float, target: float, dt: float,
                    k: float = SPRING_K, c: float = SPRING_C) -> float:
    """过阻尼弹簧单轴速度步进（调用方随后 x += v*dt）。"""
    return v + ((target - x) * k - v * c) * dt


def _window(trail: list, now: float, span: float) -> list:
    cutoff = now - span
    return [s for s in trail if s[0] >= cutoff]


def estimate_release_velocity(trail: list, now: float, cap: float = MAX_THROW_SPEED) -> tuple[float, float]:
    """由拖拽轨迹估算松手初速 (vx, vy)。

    方向：窗口首末端点位移方向（抗抖）。
    大小：端点平均速度与窗口内峰值分段速度按 PEAK_WEIGHT 加权——
    弥补纯端点平均对"快甩"的低估（快甩的位移集中在窗口内一小段）。
    增益：窗口末段仍在加速（末分段速度 > 首分段速度）时，
    按加速度占 ACCEL_REF 的比例放大，最多 ACCEL_GAIN_MAX。
    松手前停顿 > RELEASE_STALE_SEC 返回零速（原地放下）。
    """
    if not trail:
        return 0.0, 0.0
    if now - trail[-1][0] > RELEASE_STALE_SEC:
        return 0.0, 0.0
    win = _window(trail, now, RELEASE_WINDOW_SEC)
    if len(win) < 2:
        return 0.0, 0.0
    t0, x0, y0 = win[0]
    t1, x1, y1 = win[-1]
    span = t1 - t0
    if span < MIN_SPAN_SEC:
        return 0.0, 0.0

    dx, dy = x1 - x0, y1 - y0
    base_vx, base_vy = dx / span, dy / span
    base_speed = math.hypot(base_vx, base_vy)

    # 分段速度（过密采样向前合并，dt 下限 SEG_MIN_DT）
    seg_speeds: list[tuple[float, float]] = []  # (speed, t_end)
    px_, py_, pt_ = x0, y0, t0
    for t, x, y in win[1:]:
        dt = t - pt_
        if dt >= SEG_MIN_DT:
            seg_speeds.append((math.hypot(x - px_, y - py_) / dt, t))
            px_, py_, pt_ = x, y, t
    peak_speed = max((s for s, _ in seg_speeds), default=base_speed)

    # 末段加速度：最后一个有效分段 vs 第一个有效分段
    accel = 0.0
    if len(seg_speeds) >= 2:
        accel = (seg_speeds[-1][0] - seg_speeds[0][0]) / max(seg_speeds[-1][1] - seg_speeds[0][1], MIN_SPAN_SEC)

    speed = (1.0 - PEAK_WEIGHT) * base_speed + PEAK_WEIGHT * peak_speed
    gain = 1.0 + min(max(accel, 0.0) / ACCEL_REF, 1.0) * ACCEL_GAIN_MAX
    speed = soft_clamp_speed(speed * gain, cap=cap)

    if base_speed < 1e-6:
        # 窗口内几乎纯抖动：沿峰值分段方向？没有可靠方向 → 垂直下落
        return 0.0, speed
    return base_vx / base_speed * speed, base_vy / base_speed * speed


def throw_step(px: float, py: float, vx: float, vy: float, dt: float,
               left: float, top: float, right: float, bottom: float,
               gravity: float = GRAVITY) -> tuple[float, float, float, float, bool]:
    """抛掷单步积分 + 边界反弹。返回 (px, py, vx, vy, bounced)。"""
    vy += gravity * dt
    px += vx * dt
    py += vy * dt
    bounced = False
    if px < left:
        px, vx, bounced = left, abs(vx) * RESTITUTION, True
    elif px > right:
        px, vx, bounced = right, -abs(vx) * RESTITUTION, True
    if py < top:
        py, vy, bounced = top, abs(vy) * RESTITUTION, True
    elif py >= bottom:
        py = bottom
        vx *= max(0.0, 1.0 - GROUND_FRICTION * dt)
        if abs(vy) < REST_VY:
            vy = 0.0
        else:
            vy = -abs(vy) * RESTITUTION
        bounced = True
    return px, py, vx, vy, bounced


def is_at_rest(py: float, vx: float, vy: float, bottom: float, bounced: bool, speed: float) -> bool:
    """抛掷终止判定：贴地且双轴低速，或碰边后整体低速。"""
    if py >= bottom - 1 and abs(vy) < 1 and abs(vx) < REST_VX:
        return True
    return bounced and speed < REST_VY and abs(vy) < 1
