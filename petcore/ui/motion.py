"""拖拽跟随、松手抛掷与碰边反弹。"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QTimer, Qt

from ..engine import physics
from ..platform import screens as screen_mod

_CONTACT_EPS = 1.0

#: 拖动倾斜（被牵引感）：目标角按水平拖速换算，弹簧积分；松手后回零。
#: 100% 强度 = 24°；默认 30% ≈ 7.2°，与旧手感一致。再大整只会被拖到窗口外，
#: 见 AGENTS 的旋转命中口径。
LEAN_FULL_DEG = 24.0
LEAN_MAX_DEG = LEAN_FULL_DEG * 0.30
LEAN_SPEED_REF = 1000.0
LEAN_STIFFNESS = 120.0
LEAN_DAMPING = 10.0
#: 松手后的回正预算：弹簧本身 ~0.4s 收敛，这里留一点余量再停表。
LEAN_SETTLE_SECONDS = 0.6
LEAN_STOP_DEG = 0.05
LEAN_STOP_SPEED = 0.5


class MotionController(QObject):
    def __init__(self, window, *, clock=None) -> None:
        super().__init__(window if isinstance(window, QObject) else None)
        self.window = window
        self._clock = clock if callable(clock) else time.monotonic
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        self.mode = "normal"  # normal / drag / thrown
        self._x = float(window.x())
        self._y = float(window.y())
        self._vx = 0.0
        self._vy = 0.0
        self._target_x = self._x
        self._target_y = self._y
        self._last_tick = self._clock()
        self._trail: list[tuple[float, float, float]] = []
        self._throw_touching: tuple[bool, bool, bool, bool] = (False, False, False, False)
        self._island_touching = False
        #: 拖动倾斜的当前角/角速度（度），以及松手回正的剩余预算。
        self._lean = 0.0
        self._lean_vel = 0.0
        self._settling = False
        self._settle_left = 0.0

    # ---------------------------------------------------------------- 查询
    @property
    def dragging(self) -> bool:
        return self.mode == "drag"

    @property
    def thrown(self) -> bool:
        return self.mode == "thrown"

    def lean_angle(self) -> float:
        """拖动倾斜角（度）：正 = 顺时针。渲染/命中都只读这一个值。"""
        return self._lean

    def velocity(self) -> tuple[float, float]:
        return self._vx, self._vy

    def set_velocity(self, vx: float, vy: float) -> None:
        self._vx, self._vy = float(vx), float(vy)
        if self.mode == "normal" and (abs(self._vx) > 1.0 or abs(self._vy) > 1.0):
            # 从「正常」切到「抛掷」必须把内部坐标锚到窗口当前位置：
            # `_x/_y` 只在拖拽/抛掷里维护，normal 期间可能是上一次抛掷的旧值
            # （甚至构造时的 0,0），不重锚就会整只闪现到屏幕另一端。
            self._x, self._y = float(self.window.x()), float(self.window.y())
            self.mode = "thrown"
            self._throw_touching = (False, False, False, False)
            self._island_touching = False
            self._last_tick = self._clock()
            self._ensure_timer()
            callback = getattr(self.window, "_on_thrown", None)
            if callable(callback):
                callback()

    # ---------------------------------------------------------------- 拖拽
    def begin_drag(self, x: int, y: int) -> None:
        self.mode = "drag"
        self._x, self._y = float(x), float(y)
        self._target_x, self._target_y = self._x, self._y
        self._vx = self._vy = 0.0
        self._trail = [(self._clock(), self._x, self._y)]
        self._settling = False
        self._settle_left = 0.0
        self._last_tick = self._clock()
        self._ensure_timer()

    def drag_to(self, x: int, y: int) -> None:
        if self.mode != "drag":
            self.begin_drag(self.window.x(), self.window.y())
        self._target_x, self._target_y = float(x), float(y)
        if not bool(self.window.config.get("drag_physics_enabled")):
            self._x, self._y = self._target_x, self._target_y
            self.window.move(int(round(self._x)), int(round(self._y)))
        self._record_trail()
        self._ensure_timer()

    def release(self) -> tuple[float, float]:
        if self.mode != "drag":
            return 0.0, 0.0
        now = self._clock()
        cap = physics.throw_speed_cap(str(self.window.config.get("throw_strength") or "standard"))
        vx, vy = physics.estimate_release_velocity(self._trail, now, cap=cap)
        speed = math.hypot(vx, vy)
        physics_on = bool(self.window.config.get("drag_physics_enabled"))
        drop_gravity = bool(self.window.config.get("drop_gravity_enabled"))
        if (physics_on and speed >= physics.DEAD_ZONE_SPEED) or drop_gravity:
            self.begin_throw(vx, vy)
        else:
            self._begin_settle()
        return vx, vy

    def _begin_settle(self) -> None:
        """松手不甩出：收掉拖拽态但让倾角弹簧跑完（画面不瞬移回正）。"""
        was_active = self.mode != "normal"
        self.mode = "normal"
        self._vx = self._vy = 0.0
        self._island_touching = False
        self._trail.clear()
        effects = self._effects()
        if effects is not None:
            end = getattr(effects, "end_throw_egg", None)
            if callable(end):
                end()
        if was_active:
            self.window._on_motion_stopped()
        if abs(self._lean) <= LEAN_STOP_DEG and abs(self._lean_vel) <= LEAN_STOP_SPEED:
            self._clear_lean()
            self._timer.stop()
            return
        self._settling = True
        self._settle_left = LEAN_SETTLE_SECONDS
        self._ensure_timer()

    # ---------------------------------------------------------------- 抛掷
    def begin_throw(self, vx: float, vy: float) -> None:
        self.mode = "thrown"
        self._vx, self._vy = float(vx), float(vy)
        self._x, self._y = float(self.window.x()), float(self.window.y())
        self._throw_touching = (False, False, False, False)
        self._island_touching = False
        # 甩飞由击飞角度接管：倾角立刻回正，别把两套旋转叠在一起。
        self._settling = False
        self._clear_lean()
        if bool(getattr(self.window, "probe_active", False)):
            effects = self._effects()
            if effects is not None:
                # 兜底入口：主入口是碰撞时 edge_probe.cancel("collision") 前的 arm。
                effects.arm_throw_egg()
        self._last_tick = self._clock()
        self._ensure_timer()
        callback = getattr(self.window, "_on_thrown", None)
        if callable(callback):
            callback()

    def _effects(self):
        return getattr(self.window, "effects", None)

    def stop(self) -> None:
        was_active = self.mode != "normal"
        self.mode = "normal"
        self._vx = self._vy = 0.0
        self._island_touching = False
        self._trail.clear()
        self._settling = False
        self._settle_left = 0.0
        self._clear_lean()
        self._timer.stop()
        # 落地/停稳都无条件回正（幂等）；探头击飞彩蛋不设飞行时间上限，
        # 但稳定态必须是直立。
        effects = self._effects()
        if effects is not None:
            end = getattr(effects, "end_throw_egg", None)
            if callable(end):
                end()
        if was_active:
            self.window._on_motion_stopped()

    # ---------------------------------------------------------------- tick
    def _ensure_timer(self) -> None:
        if not self._timer.isActive():
            self._last_tick = self._clock()
            self._timer.start()

    def _tick(self) -> None:
        now = self._clock()
        dt = max(0.001, min(0.05, now - self._last_tick))
        self._last_tick = now
        self._tick_lean(dt)
        if self.mode == "drag":
            self._tick_drag(dt)
        elif self.mode == "thrown":
            self._tick_throw(dt, now)

    def _tick_drag(self, dt: float) -> None:
        if not bool(self.window.config.get("drag_physics_enabled")):
            return
        self._vx = physics.spring_velocity(self._vx, self._x, self._target_x, dt)
        self._vy = physics.spring_velocity(self._vy, self._y, self._target_y, dt)
        self._x += self._vx * dt
        self._y += self._vy * dt
        x, y = self.window._clamp_position(int(round(self._x)), int(round(self._y)))
        self._x, self._y = float(x), float(y)
        self.window.move(x, y)
        self._record_trail()

    # ---------------------------------------------------------------- 拖动倾斜
    def _clear_lean(self) -> None:
        changed = abs(self._lean) > 1e-9
        self._lean = 0.0
        self._lean_vel = 0.0
        if changed:
            self._notify_paint()

    def _notify_paint(self) -> None:
        update = getattr(self.window, "update", None)
        if callable(update):
            update()

    def _horizontal_speed(self) -> float:
        """最近 ~120ms 的水平漂移速度（px/s）：直接跟手拖动时没有弹簧滞后，
        只能从带时间戳的轨迹里取。"""
        samples = self._trail
        if len(samples) < 2:
            return 0.0
        now = self._clock()
        latest = samples[-1]
        cutoff = now - physics.TRAIL_KEEP_SEC
        oldest = None
        for item in reversed(samples):
            if item[0] < cutoff:
                break
            oldest = item
        if oldest is None or latest[0] - oldest[0] < 0.016:
            return 0.0
        return (latest[1] - oldest[1]) / max(1e-6, latest[0] - oldest[0])

    def _lean_limit(self) -> float:
        """当前允许的最大倾角：0% / 关闭 = 0，100% = 24°。"""
        if not bool(self.window.config.get("drag_lean_enabled", True)):
            return 0.0
        try:
            strength = float(self.window.config.get("drag_lean_strength", 30) or 0.0)
        except (TypeError, ValueError):
            strength = 30.0
        strength = max(0.0, min(100.0, strength))
        return LEAN_FULL_DEG * strength / 100.0

    def _lean_target(self) -> float:
        limit = self._lean_limit()
        if limit <= 0.0 or self.mode != "drag":
            return 0.0
        speed = self._horizontal_speed()
        # 往右拖 → 顺时针倾（正角），跟着拖动方向走；实机反馈反向会像被拖在地上。
        target = speed / LEAN_SPEED_REF * limit
        return max(-limit, min(limit, target))

    def _tick_lean(self, dt: float) -> None:
        limit = self._lean_limit()
        if self.mode == "drag":
            target = self._lean_target()
            self._lean_vel += (
                (target - self._lean) * LEAN_STIFFNESS - self._lean_vel * LEAN_DAMPING
            ) * dt
            self._lean += self._lean_vel * dt
            self._lean = max(-limit, min(limit, self._lean))
            self._notify_paint()
            return
        if not self._settling:
            return
        self._settle_left = max(0.0, self._settle_left - dt)
        self._lean_vel += (
            (0.0 - self._lean) * LEAN_STIFFNESS - self._lean_vel * LEAN_DAMPING
        ) * dt
        self._lean += self._lean_vel * dt
        self._lean = max(-limit, min(limit, self._lean))
        self._notify_paint()
        if (
            self._settle_left <= 0.0
            or (
                abs(self._lean) <= LEAN_STOP_DEG
                and abs(self._lean_vel) <= LEAN_STOP_SPEED
            )
        ):
            self._settling = False
            self._clear_lean()
            self._timer.stop()

    def _tick_throw(self, dt: float, now: float) -> None:
        left, top, right, bottom = self._throw_bounds()
        prev_speed = math.hypot(self._vx, self._vy)
        px, py, vx, vy, bounced = physics.throw_step(
            self._x,
            self._y,
            self._vx,
            self._vy,
            dt,
            left,
            top,
            right,
            bottom,
        )
        self._x, self._y, self._vx, self._vy = px, py, vx, vy
        touched_island = False
        field = getattr(getattr(self.window, "host", None), "island_field", None)
        if field is not None:
            resolution = field.reflect_throw(self.window, px, py, vx, vy)
            px, py, vx, vy = resolution.px, resolution.py, resolution.vx, resolution.vy
            self._x, self._y, self._vx, self._vy = px, py, vx, vy
            touched_island = bool(resolution.hit)
            if resolution.rested:
                # 落在岛顶且竖直速度已很小 = 落地：不收尾就会永远在岛顶小反弹，
                # `physics.is_at_rest` 只认屏幕底边，motion 会卡在 thrown。
                self.window.move(int(round(px)), int(round(py)))
                self.stop()
                return
            if (
                resolution.hit
                and not self._island_touching
                and prev_speed >= physics.BOUNCE_TRIGGER_SPEED
            ):
                # 撞岛没有「屏幕边沿」可数（墙是曲面）：上升沿 + 速度门各给一次，
                # 贴着墙滑行不会每帧响一声；节流在碰撞层（与轻撞共用 0.25s 闸）。
                field.notify_hit(
                    self.window, prev_speed, (resolution.nx, resolution.ny)
                )
        touching = (
            px <= left + _CONTACT_EPS,
            px >= right - _CONTACT_EPS,
            py >= bottom - _CONTACT_EPS,
            py <= top + _CONTACT_EPS,
        )
        # 每次「离地→接触」都要反馈，而不是一整个抛掷会话只响一次。
        if any(now_on and not before for now_on, before in zip(touching, self._throw_touching)):
            if prev_speed >= physics.BOUNCE_TRIGGER_SPEED:
                self.window._on_motion_bounce()
        self._island_touching = touched_island
        self._throw_touching = touching
        self.window.move(int(round(px)), int(round(py)))
        effects = self._effects()
        if effects is not None and getattr(effects, "throw_egg_active", False):
            effects.update_throw_egg(vx, vy, bounced)
        speed = math.hypot(vx, vy)
        if physics.is_at_rest(py, vx, vy, bottom, bounced, speed):
            self.stop()

    def _throw_bounds(self) -> tuple[float, float, float, float]:
        layout = self.window._layout or screen_mod.snapshot_from_qt()
        screen = screen_mod.screen_for_rect(self.window.body_rect(), layout)
        if screen is None:
            screen = layout.by_name(layout.primary)
        if screen is None:
            return 0.0, 0.0, float(self.window.width()), float(self.window.height())
        bx, by, bw, bh = self.window.body_local_rect()
        left = screen.x - bx
        top = screen.y - by
        right = screen.x + screen.width - bx - bw
        bottom = screen.y + screen.height - by - bh
        return float(left), float(top), float(max(left, right)), float(max(top, bottom))

    def _record_trail(self) -> None:
        now = self._clock()
        self._trail.append((now, self._x, self._y))
        cutoff = now - physics.TRAIL_KEEP_SEC
        while self._trail and self._trail[0][0] < cutoff:
            self._trail.pop(0)

    def shutdown(self) -> None:
        self._timer.stop()
