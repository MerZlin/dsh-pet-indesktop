# -*- coding: utf-8 -*-
"""岛被连撞时 bump 动效的有界性与收敛性（卡顿根因：半透明顶层窗 60fps 空转重绘）。

实机现象：拖岛撞鱼/被鱼连撞时卡顿。碰撞数学已实测排除（岛+鱼 tick p50 0.228ms
vs 鱼+鱼 0.163ms，证据 `.scratch/windows-parity-20260926-a/fix-20260928-I1/`），
反馈侧才是根因：岛是独立半透明顶层窗（``dynamic_island.py`` 的
``WA_TranslucentBackground``），每次 ``bump()`` 起 16ms 定时器做整窗重绘。

``bump()`` 把 ``_squish_v``/``_tilt_v`` **裸加不钳制**，而 ``_on_anim_tick`` 把
``_squish`` 钳到 [0.85, 1.12]、``paintEvent`` 把 ``_tilt`` 钳到 ±3.5°——连撞把
弹簧顶在钳位上（形状钉死、像素不再变化），``_animating()`` 却因
``|squish_v| > 0.02`` / ``|tilt_v| > 1.0`` 长期为真 → 每 16ms 一次整窗重绘空转。

本文件锁三条契约（对应 I 批三个修复点）：
1. 连打后形变/倾斜**速度有界**（上界 = 单次最强撞击的速度）；
2. 单次最强撞击的可见果冻**不变**（钳制不得连坐观感）；
3. 连打停止后动效在**动画时长上限内收敛**（``_animating()`` 变假、定时器停表）。

驱动方式（AGENTS.md 时序纪律）：同步直调 ``_on_anim_tick``，dt 由 ``_anim_last``
回拨固定步长给出——不 sleep、不赌真定时器节奏；真动画定时器只用于断言停表。
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pet.config import Config
from pet.island.dynamic_island import DynamicIsland

#: 产品常数（dynamic_island._BUMP_ANIM_MAX_S）：单次撞击动效的动画时间上限
BUMP_ANIM_MAX_S = 0.9
#: 驱动步长 = 真动画 tick 的 dt（_ANIM_TICK_MS=16）；尾段耗时按此换算成帧数
TICK_S = 0.016
#: bump() 里 strength 封顶 3.0 时注入的速度（1.15 × 3.0 / 13.0 × 3.0）——
#: 连打叠加不得越过这两个单次最强撞击的量级
SQUISH_V_CEILING = 3.5
TILT_V_CEILING = 40.0


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _island(tmp_path: Path) -> DynamicIsland:
    cfg = Config(base=tmp_path)
    cfg.set("dynamic_island", {
        "enabled": True, "show_icon": True, "show_name": True,
        "show_info": True, "info_mode": "time", "custom_text": "",
        "show_status": True, "style": "dark", "x": 400, "y": 300,
    })
    return DynamicIsland(cfg)


def _tick(island: DynamicIsland, dt: float = TICK_S) -> None:
    """按固定步长驱动一次动画 tick（真 tick 的 dt 由 _anim_last 回拨给出）。"""
    island._anim_last = time.monotonic() - dt
    island._on_anim_tick()


def _deepest_squish(island: DynamicIsland, ticks: int) -> tuple[float, float]:
    """驱动 ticks 次 tick，返回期间（最深 squish，最大 |tilt|）。"""
    deepest, widest_tilt = 1.0, 0.0
    for _ in range(ticks):
        _tick(island)
        deepest = min(deepest, island._squish)
        widest_tilt = max(widest_tilt, abs(island._tilt))
    return deepest, widest_tilt


def test_bump_burst_keeps_velocities_bounded(tmp_path):
    """连打（左右交替 + 同向共振）后速度必须有界：不得超过单次最强撞击。

    失效模式：无上限时 ``_squish_v`` 按 -3.45/击 累加（60 击 = -207）、
    ``_tilt_v`` 按 ±39/击 累加（同向连打 120 击可达数千），弹簧被顶在
    0.85 形变钳位 / ±3.5° 倾斜钳位上反复过冲——形状不再变化，但
    ``_animating()`` 永远为真，顶层半透明窗 60fps 空转重绘。
    """
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        for i in range(60):  # 左右交替（方向符号跟随撞击侧）
            island.bump(3.0, 1.0 if i % 2 else -1.0, 0.0)
        assert abs(island._squish_v) <= SQUISH_V_CEILING, \
            f"交替连打把形变速度累到 {island._squish_v:.1f}（越过单次最强 3.45）"
        assert abs(island._tilt_v) <= TILT_V_CEILING, \
            f"交替连打把倾斜速度累到 {island._tilt_v:.1f}"

        for _ in range(60):  # 同向连打：与倾斜弹簧共振，累加更快
            island.bump(3.0, 1.0, 0.0)
        assert abs(island._squish_v) <= SQUISH_V_CEILING, \
            f"同向连打把形变速度累到 {island._squish_v:.1f}"
        assert abs(island._tilt_v) <= TILT_V_CEILING, \
            f"同向连打把倾斜速度累到 {island._tilt_v:.1f}"

        # 连撞 + 真 tick 交错（弹簧被连续泵浦，这是实机形态）：仍收敛在能级上界。
        # 上界 = 钳位状态的势能 + 注入速度上限的动能（半隐式欧拉下的最大回弹）：
        #   倾斜 √(40² + 120×3.5²) ≈ 55.4；形变 √(3.5² + 150×0.15²) ≈ 4.0
        for _ in range(120):
            island.bump(3.0, 1.0, 0.0)
            _tick(island)
        assert abs(island._tilt) <= 3.5, "倾斜状态越过绘制钳位（共振泵浦）"
        assert abs(island._tilt_v) <= 55.5, f"泵浦后倾斜速度 {island._tilt_v:.1f}"
        assert abs(island._squish_v) <= 4.0, f"泵浦后形变速度 {island._squish_v:.1f}"
        assert abs(island._squish - 1.0) <= 0.1501, "形变越过钳位范围"
    finally:
        island.hide()
        island.deleteLater()


def test_single_strong_bump_keeps_visible_jelly(tmp_path):
    """单次最强撞击的可见果冻不得被钳制连坐：胶囊仍压到形变钳位（0.85）。"""
    _qapp()
    island = _island(tmp_path)
    try:
        island.show()
        island.bump(3.0, 1.0, 0.0)
        deepest, widest_tilt = _deepest_squish(island, math.ceil(1.2 / TICK_S))
        assert deepest <= 0.86, \
            f"单次最强撞击只压到 {deepest:.3f}（可见果冻被速度钳制削掉）"
        assert widest_tilt >= 1.0, \
            f"单次最强撞击的倾斜只有 {widest_tilt:.2f}°（受击倾斜被削掉）"
    finally:
        island.hide()
        island.deleteLater()


def test_bump_storm_converges_within_animation_budget(tmp_path):
    """连打停止后动效必须在动画时长上限内收尾（定时器停表、状态归零）。

    失效模式：钳位状态下的弹簧过冲尾段（旧阈值下 ~1.0s）比上限长，且
    ``_animating()`` 判据只看速度阈值——连撞后动效尾巴会持续拖着整窗重绘。
    """
    _qapp()
    island = _island(tmp_path)
    budget_ticks = math.ceil(BUMP_ANIM_MAX_S / TICK_S)
    try:
        island.show()
        for _ in range(40):  # 2.0s 连撞风暴（60fps 节奏）
            island.bump(3.0, 1.0, 0.0)
            _tick(island)
        assert island._anim_timer.isActive(), "连撞期间动效必须照常在播"

        ticks = 0
        while island._anim_timer.isActive() and ticks <= budget_ticks + 10:
            _tick(island)
            ticks += 1

        assert island._anim_timer.isActive() is False, \
            f"连撞后动效未收敛（>{budget_ticks + 10} tick 仍在整窗重绘）"
        assert ticks <= budget_ticks, \
            f"收尾用了 {ticks} tick，越过动画时长上限 {budget_ticks} tick"
        assert island._animating() is False
        assert island._squish == 1.0 and island._tilt == 0.0
        assert island._squish_v == 0.0 and island._tilt_v == 0.0
        assert island._kick_x == 0.0 and island._kick_y == 0.0
    finally:
        island.hide()
        island.deleteLater()
