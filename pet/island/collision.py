# -*- coding: utf-8 -*-
"""灵动岛碰撞（本进程直连版，同步硬墙 + 事件驱动撞岛反应）。

检测机制：屏幕边界式同步位置硬墙（无 30Hz 定时器）——桌宠身体框任何移动
只要会进岛区，就在统一位置出口 move_window_towwards 里被钳出，没有采样间隙、
没有 velocity 反馈循环，从源头杜绝穿透抽搐（issue #146 实机教训：30Hz 判定
帧间钻进→被弹→速度不足离场→再判定的鬼畜循环，velocity 反弹修不干净）。

业务反应是原有业务，从 30Hz 检测整体迁移到墙事件驱动，不删：
- 真撞（相对接近速度足够）：复用 _apply_hit 业务链——冲量 → 限速 → 音效 →
  挤压 → 岛弹跳 → 进抛掷物理（撞岛像撞弹床，e=STATIC_RESTITUTION）；
- 抛掷中撞墙：墙处反射速度（口径同屏幕边缘 throw_step 的 RESTITUTION）并钉住
  物理位置，避免物理空间穿过岛、视觉被钉在墙上；真撞附加命中反馈；
- 轻贴（接近速度不足）：只推出，不响不挤；
- 岛被拖到桌宠身上（拖岛拍鱼）：on_geometry_changed → submit 事件驱动把被
  压住的桌宠经统一出口推出，岛速（submit 内事件采样估计）参与相对速度结算。

低占用：无定时器；只在落窗/岛几何变化时做几何查询与接触跟踪。
"""
from __future__ import annotations

import logging
import math
import time

from PySide6.QtCore import QObject, QTimer

from .. import collision
from .. import physics as physics_mod

log = logging.getLogger(__name__)

_CAPSULE_HEIGHT = 44            # 胶囊视觉高度（与 dynamic_island._CAPSULE_HEIGHT 同步）
_HIT_COOLDOWN_S = 0.15          # 每只桌宠的命中冷却（防一帧多弹/音效连发）
_SQUASH_INTERVAL_S = 0.25       # 每只桌宠的挤压动画错峰
_CONTACT_VELOCITY_MAX_DT = 0.5  # 接触跟踪的有效间隔（超此按首次接触，无速度）
_MAX_ISLAND_SPEED = 1500.0      # 岛速估计上限（px/s）：异常大的估计不进拍鱼结算


def _rect_radial(rx: float, ry: float, nx: float, ny: float) -> float:
    """身体矩形在半轴 (rx, ry) 下沿单位法线 (nx, ny) 的径向半径。

    矩形边界到中心的距离比内切椭圆大（椭圆只相切于四条边中点）；同步硬墙
    钳制用矩形口径，保证身体框整体不进入岛碰撞区。
    """
    if abs(nx) <= 1e-9:
        return ry / max(abs(ny), 1e-9)
    if abs(ny) <= 1e-9:
        return rx / max(abs(nx), 1e-9)
    return min(rx / abs(nx), ry / abs(ny))


def _virtual_xy(win) -> tuple[float, float]:
    """虚拟窗口坐标（物理/碰撞的坐标系，贴边时与实际窗口位置差一个绘制偏移）。

    与已退役的碰撞客户端（4.4b 删除）同口径：#137 视口模型后抛掷物理按虚拟坐标
    跑，撞岛进 throw 的起点必须用同一坐标系（issue #146 后续反馈「意料之外
    的情况」）。轻量桩无该接口时回退实际位置。
    """
    vp_fn = getattr(win, "_virtual_pos", None)
    if callable(vp_fn):
        vp = vp_fn()
        return float(vp.x()), float(vp.y())
    return float(win.x()), float(win.y())


class IslandCollisionBody(QObject):
    """灵动岛的同步硬墙 + 事件驱动撞岛反应（无 30Hz 检测/结算定时器）。

    墙：统一位置出口 move_window_towwards 里按需钳制位置（host 上挂
    _island_clamp_body hook）；岛自己移动时（on_geometry_changed → submit）
    事件驱动推出被压住的桌宠。真撞复用桌宠侧真实撞击业务链（_apply_hit）。
    """

    def __init__(self, island, config, pets_provider=None, parent=None):
        super().__init__(parent if isinstance(parent, QObject) else None)
        self._island = island  # 本进程持有岛 widget（退役层删除后不再有远端模式）
        self._config = config
        # 返回本进程全部桌宠窗口的回调（AppShell 注入）
        self._pets_provider = pets_provider or (lambda: ())
        self._running = False
        # 岛速估计（拖岛拍鱼用相对速度；submit 事件采样刷新，无定时器）
        self._last_center: tuple[float, float] | None = None
        self._last_motion_ts = 0.0
        self._last_size: tuple[int, int] | None = None
        self._vx = 0.0
        self._vy = 0.0
        # 接触跟踪（墙触发时测得桌宠接近速度）+ 命中冷却 + 挤压错峰
        self._contact: dict[int, tuple[float, float, float]] = {}
        self._hit_cooldown: dict[int, float] = {}
        self._pet_squash: dict[int, float] = {}

    @property
    def has_local_island(self) -> bool:
        """本进程是否持有岛 widget（4.4a 起恒为 True：远端模式随退役层删除）。"""
        return self._island is not None

    # ------------------------------------------------------------ 生命周期
    def _register_clamp_hooks(self) -> None:
        """给全部桌宠窗口挂同步硬墙 hook（move_window_towwards 里按需调用）。

        同时把碰撞体自身挂到窗口上（_island_collision_body），供窗口侧后续
        查询岛碰撞体的归属（如「本进程有岛」判定）。
        """
        try:
            for win in self._pets_provider():
                if win is not None:
                    win._island_clamp_body = self._clamp_body
                    win._island_collision_body = self
        except RuntimeError:
            pass  # 窗口已销毁

    def _clear_clamp_hooks(self) -> None:
        try:
            for win in self._pets_provider():
                if win is not None:
                    win._island_clamp_body = None
        except RuntimeError:
            pass

    def refresh_hooks(self) -> None:
        """运行中窗口增减后重挂硬墙钩子（幂等，不改运行状态）。

        start() 只覆盖"那一刻已存在"的窗口，本进程 spawn 出来的新窗（生小肥鱼）
        必须经这里补挂，否则它会直接走进岛里；碰撞体已停（果冻墙关掉）时是
        no-op——刷新钩子绝不偷偷把墙挂回来。
        """
        if not self._running:
            return
        self._register_clamp_hooks()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._register_clamp_hooks()
        log.info("灵动岛碰撞体已启动（同步硬墙，无 30Hz 检测）")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._clear_clamp_hooks()
        self._contact.clear()
        self._hit_cooldown.clear()
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
        """岛几何变化（拖拽/展开/停靠/归位）：刷新岛速，并把被岛压住的桌宠
        经统一出口推出（岛动桌宠没动的穿越；桌宠动时由同步墙挡）。

        事件驱动、无定时器；几何动画/细条态不推挤（与 _clamp_body 同口径）。
        拖岛拍鱼的相对速度在此参与结算——推出会触发 _clamp_body 的撞岛判定。
        """
        self._update_motion()
        if not self._wall_active():
            return
        self._push_out_covered_pets()

    def _push_out_covered_pets(self) -> None:
        """把当前被岛（本地或远端几何）压住的桌宠经统一出口推出。"""
        if not self._wall_active():
            return
        try:
            stadium = self._island_stadium()
        except Exception:
            return
        for win in self._pets_provider():
            try:
                if win is None or not win.isVisible():
                    continue
                if getattr(win, "_hidden_paused", False):
                    continue
                if getattr(win, "_physics_mode", "") == "drag" \
                        or getattr(win, "_interaction_state", "") == "DRAGGING":
                    continue  # 用户正在摆放这只，不抢位置
                sbr_fn = getattr(win, "_stable_body_local_rect", None)
                vp_fn = getattr(win, "_virtual_pos", None)
                mover = getattr(win, "_move_window_towards", None)
                if not callable(sbr_fn) or not callable(vp_fn) or not callable(mover):
                    continue
                vp = vp_fn()
                (_xi, _yi), moved, _n = self._compute_clamped(
                    stadium, float(vp.x()), float(vp.y()), sbr_fn())
                if moved:
                    # 经统一出口：_clamp_body 会钳出（真撞一并走 _apply_hit 业务链）
                    mover(vp.x(), vp.y())
            except RuntimeError:
                continue  # 窗口已销毁
            except Exception:  # noqa: BLE001 - 单只异常不拖垮推挤循环
                log.warning("灵动岛推挤跳过异常桌宠", exc_info=True)

    # ------------------------------------------------------------ 岛速估计
    def _update_motion(self) -> None:
        """从相邻两次 submit 采样的中心位移估计岛速；静止/异常间隔时清零。

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
    def _wall_active(self) -> bool:
        """墙是否生效：运行中 + 岛可见 + 非细条态 + 非几何动画。

        ``self._island`` 可能是 ``None``：岛被配置关掉时 AppShell 仍会按
        ``collision_enabled`` 建碰撞体并挂到窗口的 ``_island_clamp_body`` 钩子上。
        这里不判空就抛 ``AttributeError: 'NoneType' object has no attribute
        'isVisible'``，而异常发生在 ``move_window_towards`` 内部、``host.move()``
        之前 —— 位置永不更新，表现为「拖不动、也不漫游」，且窗口化的冻结程序
        把槽内异常吞掉，日志里一个字都没有（issue #311 附带的第二条）。
        """
        if not self._running:
            return False
        if self._island is None:
            return False
        if not self._island.isVisible():
            return False
        if getattr(self._island, "_mode", "") == "docked" \
                and not getattr(self._island, "_hover_peek", False):
            return False  # 细条态不设墙（stadium 水平轴假设不成立）
        if getattr(self._island, "_geo_to", None) is not None:
            return False  # 展开/停靠/归位动画中几何在变，不设墙
        return True

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

    def _compute_clamped(
        self, stadium, xi: float, yi: float, sbr,
    ) -> tuple[tuple[float, float], bool, tuple[float, float]]:
        """把身体框推出岛碰撞区的虚拟左上；未越界时原样返回。

        返回 (钳制后的 (xi, yi), 是否发生了钳制, 墙法线 (nx, ny))。身体框屏幕
        位置 = 虚拟左上 + 身体框局部偏移；中心沿"轴最近点→中心"方向推出到
        (身体矩形径向半径 + 岛半径 + 1)。
        """
        ax0, ax1, ay, rr, _h = stadium
        body_left = xi + sbr.x()
        body_top = yi + sbr.y()
        cx = body_left + sbr.width() / 2.0
        cy = body_top + sbr.height() / 2.0
        rx = sbr.width() / 2.0
        ry = sbr.height() / 2.0
        closest_x = min(max(cx, ax0), ax1)
        dx, dy = cx - closest_x, cy - ay
        dist = math.hypot(dx, dy)
        if dist <= 1e-9:
            # 中心恰在轴上：沿 -y 推出（向上）
            nx, ny, radial = 0.0, -1.0, ry
        else:
            nx, ny = dx / dist, dy / dist
            radial = _rect_radial(rx, ry, nx, ny)
        if dist >= radial + rr:
            return (xi, yi), False, (0.0, 0.0)
        gap = radial + rr + 1.0
        target_cx = closest_x + nx * gap
        target_cy = ay + ny * gap
        return ((target_cx - sbr.width() / 2.0 - sbr.x(),
                 target_cy - sbr.height() / 2.0 - sbr.y()), True, (nx, ny))

    # ------------------------------------------------------------ 撞岛反应
    def _clamp_body(self, host, xi: float, yi: float, sbr) -> tuple[float, float]:
        """同步硬墙（move_window_towwards 调用的 hook）：把身体框钳出岛区。

        与屏幕边界同语义：身体框永远进不了岛区，只有这一个位置出口、没有
        30Hz 采样间隙，从根上杜绝"钻进→被弹→再钻回"的抽搐。

        撞岛反应（原有业务）也在这里事件驱动结算：按相对接近速度区分真撞与
        轻贴——真撞走 _apply_hit 业务链（冲量/音效/挤压/岛弹跳/进抛掷物理）；
        抛掷中撞墙反射速度（口径同屏幕边缘 RESTITUTION）并钉住物理位置。
        """
        if not self._wall_active():
            return xi, yi
        try:
            stadium = self._island_stadium()
        except Exception:
            return xi, yi
        (nx_pos, ny_pos), moved, (nx, ny) = self._compute_clamped(
            stadium, xi, yi, sbr)
        if not moved:
            return xi, yi
        key = id(host)
        now = time.monotonic()
        in_throw = getattr(host, "_physics_mode", "") == "throw"
        if in_throw:
            # 抛掷中 _phys_vel 是权威速度
            vel = getattr(host, "_phys_vel", None)
            if isinstance(vel, list) and len(vel) >= 2:
                pet_vx, pet_vy = vel[0], vel[1]
            else:
                pet_vx = pet_vy = 0.0
        else:
            # 漫游是帧驱动位移不写 _phys_vel：用墙接触跟踪测接近速度
            #（事件驱动、无定时器；首次接触/间隔过长视为无速度=轻贴）
            pet_vx = pet_vy = 0.0
            prev = self._contact.get(key)
            if prev is not None:
                pxi, pyi, pts = prev
                dt = now - pts
                if 0.0 < dt < _CONTACT_VELOCITY_MAX_DT:
                    pet_vx = (xi - pxi) / dt
                    pet_vy = (yi - pyi) / dt
        self._contact[key] = (float(xi), float(yi), now)
        # 相对岛速的接近分量（法线指向桌宠一侧，接近为负）
        vn = (pet_vx - self._vx) * nx + (pet_vy - self._vy) * ny
        hit = vn < -collision.IMPULSE_MIN_APPROACH_SPEED
        cooldown_ok = now - self._hit_cooldown.get(key, 0.0) >= _HIT_COOLDOWN_S
        if in_throw:
            # 抛掷中撞墙：反射速度（避免物理空间穿过岛、视觉钉在墙上）并钉住
            # 物理位置；真撞附加命中反馈（音效/挤压/岛弹跳，冷却内不重复）。
            vel = getattr(host, "_phys_vel", None)
            if isinstance(vel, list) and len(vel) >= 2:
                vn_pet = vel[0] * nx + vel[1] * ny
                if vn_pet < 0.0:
                    # 反射后法向分量反号，同一位置不会二次反射
                    k = (1.0 + physics_mod.RESTITUTION) * vn_pet
                    vel[0] -= k * nx
                    vel[1] -= k * ny
            if hit and cooldown_ok:
                self._hit_cooldown[key] = now
                self._apply_feedback(host, key, now, -vn, -nx, -ny)
            phys = getattr(host, "_phys_pos", None)
            if isinstance(phys, list) and len(phys) >= 2:
                phys[:] = [float(nx_pos), float(ny_pos)]
            return nx_pos, ny_pos
        if not hit or not cooldown_ok:
            # 轻贴/冷却内：只推出不撞。先取消自主移动计划（与旧 _separate
            # 同口径）——否则漫游的帧驱动位移会原地踏步顶墙，直到计划走完。
            cancel_move = getattr(host, "_cancel_move", None)
            if callable(cancel_move):
                cancel_move()
            cancel_gap = getattr(host, "_cancel_animation_gap", None)
            if callable(cancel_gap):
                cancel_gap()
            return nx_pos, ny_pos
        # 非抛掷真撞：冲量 + 进抛掷物理 + 全量反馈（原有业务，撞岛像撞弹床）
        self._hit_cooldown[key] = now
        dv = -(1.0 + collision.STATIC_RESTITUTION) * vn
        dvx, dvy = dv * nx, dv * ny
        self._apply_hit(host, dvx, dvy, now, key)
        phys = getattr(host, "_phys_pos", None)
        if isinstance(phys, list) and len(phys) >= 2:
            # 抛掷起点钉在墙表面（_apply_hit 写的是旧虚拟位置，墙内一侧）
            phys[:] = [float(nx_pos), float(ny_pos)]
        return nx_pos, ny_pos

    def _apply_feedback(self, win, key, now, strength: float,
                        dir_x: float, dir_y: float) -> None:
        """命中反馈（音效/挤压/岛弹跳）——抛掷撞墙与 _apply_hit 共用。"""
        play_sound = getattr(win, "_play_collision_sound", None)
        if callable(play_sound):
            play_sound()
        if not getattr(win, "_squash_active", False) \
                and now - self._pet_squash.get(key, 0.0) >= _SQUASH_INTERVAL_S:
            self._pet_squash[key] = now
            squash = getattr(win, "_start_squash", None)
            if callable(squash):
                squash()
        bump = getattr(self._island, "bump", None)
        if callable(bump):
            bump(min(3.0, strength / 400.0), dir_x, dir_y)

    def _apply_hit(self, win, dvx: float, dvy: float, now: float, key: int) -> None:
        """复用桌宠侧真实撞击反应（原有业务）：加冲量 → 限速 → 音效 → 挤压 →
        进抛掷物理（口径沿用已退役碰撞客户端的手感，不走 IPC）。
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
        dv_mag = math.hypot(dvx, dvy)
        self._apply_feedback(win, key, now, dv_mag, -dvx / dv_mag, -dvy / dv_mag)
        edge_probe = getattr(win, "_edge_probe", None)
        if edge_probe is not None:
            cancel = getattr(edge_probe, "cancel", None)
            if callable(cancel):
                cancel("island_hit", restore=False)
        win._interaction_state = "THROWN"
        # 补齐旧权威冲量路径的两个副作用：幽灵点击抑制
        #（被撞飞的鱼落地不应触发点击动画）+ 落地后允许重新进入边缘探头
        clear_dragged = getattr(win, "_clear_just_dragged", None)
        if callable(clear_dragged):
            win._just_dragged = True
            if isinstance(win, QObject):
                QTimer.singleShot(120, win, clear_dragged)
            else:
                QTimer.singleShot(120, clear_dragged)
        # 探头重入 arm：落地停稳后由窗口侧 _stop_physics 读它并通知边缘探头
        # （4.4a 起不再经 CollisionClient 中转，标记直接写窗口本体）。
        win._reentry_after_throw_armed = True
        enter = getattr(win, "_enter_physics_mode", None)
        if callable(enter):
            enter("throw")
        if isinstance(getattr(win, "_phys_pos", None), list):
            # 抛掷起点用虚拟坐标：_tick_throw_physics 按虚拟坐标跑，贴边时
            # 实际窗口位置差一个绘制偏移，写实际坐标会让 throw 从错误位置起跳
            vx_p, vy_p = _virtual_xy(win)
            win._phys_pos[:] = [float(vx_p), float(vy_p)]
        win._last_physics_tick_time = None
        physics_timer = getattr(win, "_physics_timer", None)
        if physics_timer is not None:
            physics_timer.start()
