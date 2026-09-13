# -*- coding: utf-8 -*-
"""灵动岛碰撞（本进程直连版，stadium 几何）。

为什么重写（IPC 版的实机教训）：岛作为 FLAG_STATIC 成员进碰撞世界后，
保活/快照/预测抑制/客户端阈值任何一环被 GUI 卡顿饿死，表现就是
"撞了没反应/直接穿过去"。本进程直连后：

- 30Hz 本地检测：岛建模为体育场形（stadium = 中轴矩形 + 两端半圆），
  法线取"胶囊轴线最近点"方向——宽胶囊从正上/正下方撞不再被斜着弹飞；
- 桌宠圆链带上帧扫掠（TOI），高速甩不穿；彻底穿过的放回接触点再弹回；
- 岛是无限质量墙但保留弹性（STATIC_RESTITUTION，撞岛像撞弹床）；
- 拖岛扫鱼：岛速参与相对速度（岛=移动的拍子）；深度重叠时按相对运动
  方向解围（岛从哪边来，鱼往哪边飞）；
- 命中反应复用桌宠侧既有的真实撞击路径（进抛掷物理/音效/挤压动画），
  与鱼撞鱼手感一致；
- 低占用：CoarseTimer（不用精确定时器，避免拉高系统时钟分辨率），
  每 tick 只做几次几何查询 + AABB 预筛，亚毫秒级。

取舍：独立进程的桌宠实例不在本进程视野内，会穿过岛。常规使用（含单进程
多开）全部覆盖；这个取舍换来的是零时序风险。
"""
from __future__ import annotations

import logging
import math
import time

from PySide6.QtCore import QObject, Qt, QTimer

from . import collision
from . import physics as physics_mod

log = logging.getLogger(__name__)

_TICK_MS = 33               # 30Hz 本地检测（空闲时几何未变整体跳过，近零开销）
_APPROACH_MIN_SPEED = 20.0  # 相对接近速度低于此视为轻贴：只做分离不弹飞
_HIT_COOLDOWN_S = 0.15      # 每只桌宠的命中冷却（防一帧多弹）
_CAPSULE_HEIGHT = 44        # 胶囊视觉高度（与 dynamic_island._CAPSULE_HEIGHT 同步）
_MAX_ISLAND_SPEED = 1500.0  # 岛速估计上限（px/s）：异常大的估计不进拍鱼结算


def _segment_circle_entry(p0: tuple[float, float], p1: tuple[float, float],
                          center: tuple[float, float], radius: float) -> float | None:
    """线段进入圆的最早时刻 t∈[0,1]（TOI）；起点已在圆内返回 0，未进入 None。"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    fx, fy = p0[0] - center[0], p0[1] - center[1]
    c = fx * fx + fy * fy - radius * radius
    if c <= 0.0:
        return 0.0
    a = dx * dx + dy * dy
    if a <= 1e-9:
        return None
    b = 2.0 * (fx * dx + fy * dy)
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return None
    t = (-b - math.sqrt(disc)) / (2.0 * a)
    return t if 0.0 <= t <= 1.0 else None


def _segment_rect_entry(p0: tuple[float, float], p1: tuple[float, float],
                        left: float, top: float, right: float, bottom: float) -> float | None:
    """线段进入轴对齐矩形的最早时刻 t∈[0,1]（slab 法）；起点在内返回 0。"""
    if left <= p0[0] <= right and top <= p0[1] <= bottom:
        return 0.0
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    t_enter, t_exit = 0.0, 1.0
    for p, d, lo, hi in ((p0[0], dx, left, right), (p0[1], dy, top, bottom)):
        if abs(d) <= 1e-12:
            if p < lo or p > hi:
                return None
            continue
        t0, t1 = (lo - p) / d, (hi - p) / d
        if t0 > t1:
            t0, t1 = t1, t0
        t_enter, t_exit = max(t_enter, t0), min(t_exit, t1)
        if t_enter > t_exit:
            return None
    return t_enter


class IslandCollisionBody(QObject):
    """灵动岛的本进程碰撞体：30Hz 本地检测 + stadium 几何 + 岛速估计。"""

    def __init__(self, island, config, pets_provider=None, parent=None):
        super().__init__(parent if isinstance(parent, QObject) else None)
        self._island = island
        self._config = config
        # 返回本进程全部桌宠窗口的回调（AppShell 注入）
        self._pets_provider = pets_provider or (lambda: ())
        self._running = False
        # 岛自身的运动速度（拖岛扫鱼时岛是"移动的墙"）
        self._last_center: tuple[float, float] | None = None
        self._last_motion_ts = 0.0
        self._last_size: tuple[int, int] | None = None
        self._vx = 0.0
        self._vy = 0.0
        # 桌宠跟踪：上帧中心/时间（扫掠+实测速度）与命中冷却、挤压错峰
        self._pet_prev: dict[int, tuple[float, float]] = {}
        self._pet_prev_ts: dict[int, float] = {}
        self._pet_cooldown: dict[int, float] = {}
        self._pet_squash: dict[int, float] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        # 30Hz 碰撞探测不需要精确定时器：PreciseTimer 会拉高系统时钟分辨率、
        # 抑制 CPU 深睡，常年挂机的桌宠付不起这个电池税（CoarseTimer 5% 容差足够）
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._timer.start()
        log.info("灵动岛碰撞体已启动（本进程直连）")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        self._pet_prev.clear()
        self._pet_prev_ts.clear()
        self._pet_cooldown.clear()
        self._pet_squash.clear()
        self._last_center = None
        self._last_motion_ts = 0.0
        self._last_size = None
        self._vx = self._vy = 0.0
        log.info("灵动岛碰撞体已停止")

    def set_own_pet_visible(self, visible: bool) -> None:
        """本进程桌宠可见性回调（AppShell 接线保留）：本地版无挂起语义，
        仅留日志便于排查。"""
        log.debug("灵动岛碰撞体：本进程桌宠可见性=%s", bool(visible))

    def submit(self) -> None:
        """岛几何变化钩子（island.on_geometry_changed）：刷新岛速估计。"""
        self._update_motion()

    # ------------------------------------------------------------ 岛速估计
    def _update_motion(self) -> None:
        """从相邻两次采样的中心位移估计岛速；静止/异常间隔时清零。

        采样间隔下限（0.01s）是"跳过"而不是"清零"：拖拽时几何回调可达
        100Hz+（每次 mouseMove 都 submit），dt 常落在 0.01 以下——若按
        旧逻辑清零并刷新采样点，拖拽全程岛速恒为 0，"拖岛拍鱼"就退化成
        只推挤不弹飞（实机教训）。跳过采样等间隔累积够再估计即可。
        """
        # 岛的展开/停靠/归位动画是程序驱动的几何变化（60fps），不是拖拽；
        # 期间不采样不估计（展开动画峰值速度约 2600px/s，会把旁边静止的
        # 鱼"凭空拍飞"——审查实测）。动画结束后的首次采样重建基线。
        if getattr(self._island, "_geo_to", None) is not None:
            self._vx = self._vy = 0.0
            self._last_center = None
            return
        rect = self._island.geometry()
        size = (rect.width(), rect.height())
        if self._last_size is not None and size != self._last_size:
            # 窗口尺寸变化（展开/收起卡片、停靠切换、文本变长）会平移窗口
            # 中心，但胶囊碰撞体本体未必动——size 变了只重置采样点不估计
            self._vx = self._vy = 0.0
            self._last_center = None
            self._last_size = size
            return
        self._last_size = size
        cx, cy = float(rect.center().x()), float(rect.center().y())
        now = time.monotonic()
        if self._last_center is not None:
            dt = now - self._last_motion_ts
            if dt < 0.01:
                return  # 高频回调样本太密：保留上次速度，不刷新采样点
            dx, dy = cx - self._last_center[0], cy - self._last_center[1]
            jump = math.hypot(dx, dy)
            # 瞬移跳变守卫（与桌宠侧 jump_guard 对称）：配置变更/换屏/夹回
            # 屏幕造成的跳变不参与估计——位移超过"极速拖拽×间隔 + 窗口宽度"
            # 即视为瞬移，速度清零并只重置采样点（上限钳制挡不住这种拍飞）
            if jump > 3000.0 * dt + rect.width():
                self._vx = self._vy = 0.0
                self._last_center = (cx, cy)
                self._last_motion_ts = now
                return
            if dt <= 0.5 and jump >= 1.0:
                vx, vy = dx / dt, dy / dt
                speed = math.hypot(vx, vy)
                if speed > _MAX_ISLAND_SPEED:
                    if getattr(self._island, "_dragging", False):
                        # 拖拽中的快速甩动是合法拍鱼：钳到上限
                        vx *= _MAX_ISLAND_SPEED / speed
                        vy *= _MAX_ISLAND_SPEED / speed
                    else:
                        # 非拖拽的极速位移必是瞬移/尺寸变化残留（真实拖拽
                        # 之外的岛移动没有合法的高速来源）：清零并重置采样点
                        self._vx = self._vy = 0.0
                        self._last_center = (cx, cy)
                        self._last_motion_ts = now
                        self._last_size = (rect.width(), rect.height())
                        return
                self._vx, self._vy = vx, vy
            else:
                self._vx = 0.0
                self._vy = 0.0
        self._last_center = (cx, cy)
        self._last_motion_ts = now

    # ------------------------------------------------------------ 几何
    def _island_stadium(self) -> tuple[float, float, float, float, float]:
        """岛的体育场形：(axis_x0, axis_x1, axis_y, radius, rect_height)。

        展开卡片时只覆盖胶囊本体（卡片区域不设幽灵墙）。
        """
        rect = self._island.geometry()
        height = min(rect.height(), _CAPSULE_HEIGHT)
        radius = height / 2.0
        axis_y = rect.y() + radius
        return (rect.x() + radius, rect.x() + rect.width() - radius,
                axis_y, radius, height)

    @staticmethod
    def _axis_closest(stadium, px: float, py: float) -> tuple[float, float]:
        """胶囊轴线上离 (px, py) 最近的点。"""
        ax0, ax1, ay, _radius, _h = stadium
        return (min(max(px, ax0), ax1), ay)

    def _stadium_entry(self, p0: tuple[float, float], p1: tuple[float, float],
                       stadium, pet_radius: float) -> float | None:
        """圆心从 p0 扫到 p1 进入体育场形（外扩 pet_radius）的最早 TOI。"""
        ax0, ax1, ay, rr, _h = stadium
        expanded = pet_radius + rr
        candidates = [
            _segment_circle_entry(p0, p1, (ax0, ay), expanded),
            _segment_circle_entry(p0, p1, (ax1, ay), expanded),
            _segment_rect_entry(p0, p1, ax0, ay - expanded, ax1, ay + expanded),
        ]
        hits = [t for t in candidates if t is not None]
        return min(hits) if hits else None

    def _normal(self, stadium, ref: tuple[float, float],
                vrel: tuple[float, float]) -> tuple[float, float, bool]:
        """法线选择：优先"轴线最近点 → 参考点"的表面法线；但它与相对运动
        近垂直（岛侧向铲进桌宠体内，如拖岛横扫）时，按相对运动方向解围
        （岛从哪边来，鱼往哪边飞）。返回 (nx, ny, 是否解围方向)。"""
        _ax0, _ax1, _ay, rr, _h = stadium
        closest = self._axis_closest(stadium, ref[0], ref[1])
        nx, ny = ref[0] - closest[0], ref[1] - closest[1]
        norm = math.hypot(nx, ny)
        speed = math.hypot(*vrel)
        if norm >= rr * 0.6 and norm > 1e-9:
            axis_nx, axis_ny = nx / norm, ny / norm
            if speed <= 1.0 or abs(vrel[0] * axis_nx + vrel[1] * axis_ny) >= speed * 0.3:
                return axis_nx, axis_ny, False
        if speed > 1.0:
            return -vrel[0] / speed, -vrel[1] / speed, True
        return 0.0, -1.0, False

    # ------------------------------------------------------------ 主循环
    def _tick(self) -> None:
        if not self._running or not self._island.isVisible():
            return
        # 停靠细条态不结算：细条是 16×64 的竖条，与 stadium 的水平轴假设
        # 不符（会产生向右 28px 的幻影墙）；贴屏边细条的碰撞价值低，悬停
        # 滑出（peek）恢复胶囊形态后照常结算
        if getattr(self._island, "_mode", "") == "docked" \
                and not getattr(self._island, "_hover_peek", False):
            self._last_center = None  # 恢复检测时重建采样基线
            return
        # 几何动画（展开/停靠/归位/滑出）进行中一律不结算：几何每 16ms
        # 在变，stadium 与视觉形态不一致（滑出首帧仍是细条矩形）
        if getattr(self._island, "_geo_to", None) is not None:
            self._last_center = None
            self._vx = self._vy = 0.0
            return
        self._update_motion()
        stadium = self._island_stadium()
        island_rect = self._island.geometry()
        now = time.monotonic()
        alive_keys: set[int] = set()
        for win in self._pets_provider():
            try:
                self._check_pet(win, stadium, island_rect, now, alive_keys)
            except RuntimeError:
                continue  # 窗口已销毁
            except Exception:  # noqa: BLE001 - 单只异常不拖垮检测循环
                log.warning("灵动岛碰撞检测跳过异常桌宠", exc_info=True)
        # 清理离场桌宠的跟踪状态
        self._pet_prev = {k: v for k, v in self._pet_prev.items() if k in alive_keys}
        self._pet_prev_ts = {k: v for k, v in self._pet_prev_ts.items() if k in alive_keys}
        self._pet_cooldown = {k: v for k, v in self._pet_cooldown.items() if k in alive_keys}
        self._pet_squash = {k: v for k, v in self._pet_squash.items() if k in alive_keys}

    def _check_pet(self, win, stadium, island_rect, now: float, alive_keys: set[int]) -> None:
        if win is None:
            return
        key = id(win)
        alive_keys.add(key)
        if not win.isVisible() or getattr(win, "_hidden_paused", False):
            self._pet_prev.pop(key, None)
            self._pet_cooldown.pop(key, None)
            return
        # 注意：不读桌宠的全局碰撞开关（collision_enabled 的语义是
        # 「多开桌宠之间碰撞」，设置页文案同）；果冻墙是否生效只由灵动岛
        # 自己的开关管（app.py _sync_island_collision 的启停）
        # 拖拽中的桌宠不结算（用户在摆放它，松手后才有相对运动）
        if getattr(win, "_physics_mode", "") == "drag" \
                or getattr(win, "_interaction_state", "") == "DRAGGING":
            self._pet_prev.pop(key, None)
            self._pet_cooldown.pop(key, None)
            return
        rect = win.collision_content_rect()
        center = (float(rect.center().x()), float(rect.center().y()))
        prev = self._pet_prev.get(key)
        prev_ts = self._pet_prev_ts.get(key)
        self._pet_prev[key] = center
        self._pet_prev_ts[key] = now

        pet_radius = max(rect.width(), rect.height()) / 2.0
        # 瞬移守卫：跳变超过"极速飞行 × 间隔 + 体型余量"（传送/缩放/切屏）
        # 不扫掠，避免把瞬移轨迹当成高速路径产生幽灵命中；合法的高速甩出
        # （4800px/s × 100ms 才 480px）必须放行——守卫跟着 dt 走
        dt_guard = (now - prev_ts) if prev_ts is not None else 0.0
        jump_guard = 5000.0 * max(dt_guard, 0.02) \
            + island_rect.width() + max(rect.width(), rect.height())
        if prev is None or prev_ts is None \
                or math.hypot(center[0] - prev[0], center[1] - prev[1]) > jump_guard:
            if self._overlaps_stadium(center, pet_radius, stadium):
                self._separate_from_stadium(win, center, pet_radius, stadium,
                                            island_rect, now, key)
            return

        # 实测速度（位移/真实间隔）比 _phys_vel 更可靠：漫游走路的桌宠
        # _phys_vel 常为零或残留，扫掠接近判定要用真实位移
        dt = now - prev_ts
        if dt > 1e-3:
            measured_vx = (center[0] - prev[0]) / dt
            measured_vy = (center[1] - prev[1]) / dt
            # 整型窗口坐标的量化噪声：±1px / 33ms ≈ 30px/s，低于此按静止计
            if math.hypot(measured_vx, measured_vy) < 30.0:
                measured_vx = measured_vy = 0.0
        else:
            measured_vx = measured_vy = 0.0

        # AABB 预筛：扫掠路径包围盒（外扩桌宠半径）与岛不相交 → 必不撞
        path_left = min(prev[0], center[0]) - pet_radius
        path_right = max(prev[0], center[0]) + pet_radius
        path_top = min(prev[1], center[1]) - pet_radius
        path_bottom = max(prev[1], center[1]) + pet_radius
        if path_right < island_rect.x() or path_left > island_rect.right() \
                or path_bottom < island_rect.y() \
                or path_top > island_rect.y() + island_rect.height():
            return

        toi = self._stadium_entry(prev, center, stadium, pet_radius)
        if toi is None:
            return
        if now - self._pet_cooldown.get(key, 0.0) < _HIT_COOLDOWN_S:
            return
        entry = (prev[0] + (center[0] - prev[0]) * toi,
                 prev[1] + (center[1] - prev[1]) * toi)
        currently_overlapping = self._overlaps_stadium(center, pet_radius, stadium)
        vrel = (measured_vx - self._vx, measured_vy - self._vy)
        ref = center if currently_overlapping else entry
        nx, ny, is_fallback = self._normal(stadium, ref, vrel)
        vn = vrel[0] * nx + vrel[1] * ny
        # 解围方向（与运动近垂直时取 -vrel）下 vn = -|vrel|：方向不可靠，
        # 只放行真有速度的横扫/甩（100px/s），防量化噪声把静置鱼弹飞
        approach_floor = 100.0 if is_fallback else _APPROACH_MIN_SPEED
        if vn >= -approach_floor:
            if currently_overlapping:
                # 贴着重叠但不接近：只把桌宠推出岛体（防嵌入累积）
                self._separate_from_stadium(win, center, pet_radius, stadium,
                                            island_rect, now, key)
            return
        self._pet_cooldown[key] = now
        dv = -(1.0 + collision.STATIC_RESTITUTION) * vn
        dvx, dvy = dv * nx, dv * ny
        self._apply_hit(win, dvx, dvy, now, key)
        self._separate_from_stadium(win, center, pet_radius, stadium,
                                    island_rect, now, key,
                                    ref=None if currently_overlapping else entry)
        dv_mag = math.hypot(dvx, dvy)
        if dv_mag > 1e-6:
            log.info("灵动岛被撞（本地结算）dv=%.0f dir=(%.2f, %.2f)",
                     dv_mag, -dvx / dv_mag, -dvy / dv_mag)
            self._island.bump(min(3.0, dv_mag / 400.0),
                              -dvx / dv_mag, -dvy / dv_mag)

    def _overlaps_stadium(self, center, pet_radius: float, stadium) -> bool:
        closest = self._axis_closest(stadium, center[0], center[1])
        return math.hypot(center[0] - closest[0], center[1] - closest[1]) \
            <= pet_radius + stadium[3]

    def _separate_from_stadium(self, win, center, pet_radius: float, stadium,
                               island_rect, now: float, key: int,
                               ref=None) -> None:
        """把桌宠放到岛体表面（沿法线推出，含 TOI 放回——它本不该在岛体内）。

        ref：法线参考点，默认当前中心；隧道放回时传进入点（放回来路一侧）。
        """
        # 边缘探头会话期间位置归探头控制器管（PEEKING 稳态无 timer，被顶偏
        # 不会自动归位）——轻贴分离位移直接丢弃，与权威冲量路径同口径；
        # 真撞路径（_apply_hit）会先 cancel 探头会话，走到这里 active 已为 False
        probe = getattr(win, "_edge_probe", None)
        if getattr(probe, "active", False):
            return
        ref = center if ref is None else ref
        vrel = (0.0 - self._vx, 0.0 - self._vy)
        nx, ny, _is_fallback = self._normal(stadium, ref, vrel)
        closest = self._axis_closest(stadium, ref[0], ref[1])
        gap = pet_radius + stadium[3] + 1.0
        target_x = closest[0] + nx * gap
        target_y = closest[1] + ny * gap
        dx, dy = target_x - center[0], target_y - center[1]
        if math.hypot(dx, dy) < 1.0:
            return
        self._move_win(win, dx, dy)
        # 放回/推出后刷新跟踪中心，避免下一帧把这次修正当成高速扫掠
        rect = win.collision_content_rect()
        self._pet_prev[key] = (float(rect.center().x()), float(rect.center().y()))
        self._pet_prev_ts[key] = now

    def _move_win(self, win, dx: float, dy: float) -> None:
        # 先取消桌宠的自主移动计划：否则 33ms 后移动插值会把分离位置
        # 覆盖回去（表现为贴岛抖动/推不出去）；与权威冲量路径同口径
        cancel_move = getattr(win, "_cancel_move", None)
        if callable(cancel_move):
            cancel_move()
        cancel_gap = getattr(win, "_cancel_animation_gap", None)
        if callable(cancel_gap):
            cancel_gap()
        clamp = getattr(win, "_collision_clamp_pos", None)
        if callable(clamp):
            nx_pos, ny_pos = clamp(win.x() + dx, win.y() + dy)
            left, top = clamp(float("-inf"), float("-inf"))
            right, bottom = clamp(float("inf"), float("inf"))
            win.move(
                min(max(int(round(nx_pos)), math.ceil(left)), math.floor(right)),
                min(max(int(round(ny_pos)), math.ceil(top)), math.floor(bottom)),
            )
        else:
            win.move(int(round(win.x() + dx)), int(round(win.y() + dy)))
        phys_pos = getattr(win, "_phys_pos", None)
        if isinstance(phys_pos, list) and len(phys_pos) >= 2:
            phys_pos[:] = [float(win.x()), float(win.y())]

    def _apply_hit(self, win, dvx: float, dvy: float, now: float, key: int) -> None:
        """复用桌宠侧真实撞击反应：加冲量 → 限速 → 音效 → 进抛掷物理 → 挤压。

        与 collision_client 权威冲量路径保持一致的手感，但不走 IPC。
        """
        cancel_move = getattr(win, "_cancel_move", None)
        if callable(cancel_move):
            cancel_move()
        cancel_gap = getattr(win, "_cancel_animation_gap", None)
        if callable(cancel_gap):
            cancel_gap()
        win._phys_vel[0] += dvx
        win._phys_vel[1] += dvy
        speed = math.hypot(*win._phys_vel)
        cap = float(getattr(win, "_throw_speed_cap", 4800.0) or 4800.0)
        if speed > cap:
            clamped = physics_mod.soft_clamp_speed(speed, cap)
            win._phys_vel[:] = [win._phys_vel[0] * clamped / speed,
                                win._phys_vel[1] * clamped / speed]
        egg = getattr(win, "_throw_egg", None)
        if egg is not None and getattr(egg, "active", False):
            egg.on_pet_contact(math.hypot(*win._phys_vel))
        play_sound = getattr(win, "_play_collision_sound", None)
        if callable(play_sound):
            play_sound()
        # 进入抛掷物理（与权威路径一致）：撞飞 → 抛物线 → 落地停稳
        edge_probe = getattr(win, "_edge_probe", None)
        if edge_probe is not None:
            cancel = getattr(edge_probe, "cancel", None)
            if callable(cancel):
                cancel("island_hit", restore=False)
        win._interaction_state = "THROWN"
        # 与权威冲量路径（collision_client）补齐两个副作用：幽灵点击抑制
        #（被撞飞的鱼落地不应触发点击动画）+ 落地后允许重新进入边缘探头
        clear_dragged = getattr(win, "_clear_just_dragged", None)
        if callable(clear_dragged):
            win._just_dragged = True
            if isinstance(win, QObject):
                QTimer.singleShot(120, win, clear_dragged)
            else:
                QTimer.singleShot(120, clear_dragged)
        # 探头重入 arm 的读侧是 CollisionClient（_submit_collision_state），
        # 写到 win 上是死写——写到真正的读侧
        client = getattr(win, "_collision_client", None)
        if client is not None:
            client._reentry_after_throw_armed = True
        enter = getattr(win, "_enter_physics_mode", None)
        if callable(enter):
            enter("throw")
        if isinstance(getattr(win, "_phys_pos", None), list):
            win._phys_pos[:] = [float(win.x()), float(win.y())]
        win._last_physics_tick_time = None
        physics_timer = getattr(win, "_physics_timer", None)
        if physics_timer is not None:
            physics_timer.start()
        # 挤压动画逐只错峰（岛级单闸门会吞掉同时撞上的其他鱼）
        if not getattr(win, "_squash_active", False) \
                and now - self._pet_squash.get(key, 0.0) >= 0.25:
            self._pet_squash[key] = now
            squash = getattr(win, "_start_squash", None)
            if callable(squash):
                squash()
