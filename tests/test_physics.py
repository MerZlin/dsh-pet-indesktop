# -*- coding: utf-8 -*-
"""拖拽物理纯函数测试：初速估算（含加速度增益）、弹簧、抛掷。"""

import math

from pet.physics import (
    ACCEL_GAIN_MAX,
    DEAD_ZONE_SPEED,
    MAX_THROW_SPEED,
    estimate_release_velocity,
    spring_velocity,
    throw_step,
)


def _trail(points, dt=0.016):
    """由 (x, y) 序列生成等间隔轨迹，t 从 0 开始。"""
    return [(i * dt, x, y) for i, (x, y) in enumerate(points)]


def test_slow_drag_stays_under_dead_zone():
    tr = _trail([(100 + i * 3, 100) for i in range(8)])  # ~190px/s
    vx, vy = estimate_release_velocity(tr, tr[-1][0])
    assert math.hypot(vx, vy) < DEAD_ZONE_SPEED


def test_fast_flick_is_faster_than_slow_flick():
    slow = _trail([(100 + i * 5, 100) for i in range(8)])
    fast = _trail([(100 + i * 40, 100) for i in range(8)])
    vs = math.hypot(*estimate_release_velocity(slow, slow[-1][0]))
    vf = math.hypot(*estimate_release_velocity(fast, fast[-1][0]))
    assert vf > vs * 2


def test_direction_preserved():
    tr = _trail([(500 - i * 30, 100) for i in range(8)])  # 向左甩
    vx, vy = estimate_release_velocity(tr, tr[-1][0])
    assert vx < 0 and abs(vy) < abs(vx) * 0.2


def test_accelerating_flick_beats_decelerating_flick():
    """端点平均速度相同：末段加速的甩动要比减速的更快。"""
    # 加速：位移指数增长；减速：位移对数增长。构造总位移相同的两条轨迹。
    n = 9
    accel_pts = [(100 + int(300 * (i / (n - 1)) ** 2), 100) for i in range(n)]
    decel_pts = [(100 + int(300 * (1 - (1 - i / (n - 1)) ** 2)), 100) for i in range(n)]
    tr_a, tr_d = _trail(accel_pts), _trail(decel_pts)
    va = math.hypot(*estimate_release_velocity(tr_a, tr_a[-1][0]))
    vd = math.hypot(*estimate_release_velocity(tr_d, tr_d[-1][0]))
    assert va > vd


def test_stop_before_release_means_place_down():
    tr = _trail([(100 + i * 40, 100) for i in range(8)])
    vx, vy = estimate_release_velocity(tr, tr[-1][0] + 0.5)  # 松手前停了 0.5s
    assert (vx, vy) == (0.0, 0.0)


def test_high_polling_noise_does_not_spike():
    """1ms 间隔的高回报率抖动轨迹，不应产生离谱峰值。"""
    pts = [(100 + i * 2 + (3 if i % 2 else -3), 100 + (2 if i % 3 else -2)) for i in range(60)]
    tr = _trail(pts, dt=0.001)
    vx, vy = estimate_release_velocity(tr, tr[-1][0])
    assert math.hypot(vx, vy) <= MAX_THROW_SPEED


def test_speed_clamped_to_max():
    tr = _trail([(100 + i * 500, 100) for i in range(10)])  # 疯甩
    vx, vy = estimate_release_velocity(tr, tr[-1][0])
    assert math.hypot(vx, vy) <= MAX_THROW_SPEED + 1e-6


def test_accel_gain_capped():
    # 加速度再大，增益也不超过 1 + ACCEL_GAIN_MAX 的物理上限（由 MAX 硬钳保证）
    tr = _trail([(100 + i * i * 30, 100) for i in range(10)])
    vx, _ = estimate_release_velocity(tr, tr[-1][0])
    assert vx <= MAX_THROW_SPEED
    assert ACCEL_GAIN_MAX <= 1.0


def test_spring_is_overdamped_no_overshoot():
    x, v, target = 0.0, 0.0, 100.0
    for _ in range(600):
        v = spring_velocity(v, x, target, 0.016)
        x += v * 0.016
        assert x <= target + 1.0  # 不允许明显过冲
    assert abs(x - target) < 1.0 and abs(v) < 1.0


def test_throw_bounces_and_settles():
    px, py, vx, vy = 500.0, 100.0, 800.0, -300.0
    bounds = (0.0, 0.0, 1000.0, 600.0)
    bounced_once = False
    for _ in range(2000):
        px, py, vx, vy, bounced = throw_step(px, py, vx, vy, 0.016, *bounds)
        bounced_once = bounced_once or bounced
        assert -1 <= px <= 1001 and -1 <= py <= 601  # 不穿墙
    assert bounced_once
    assert py == 600.0 and abs(vy) < 1 and abs(vx) < 30  # 最终落地停稳
