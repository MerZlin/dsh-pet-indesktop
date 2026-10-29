"""单进程多宠：身份、独立配置、生成/收回、重启恢复与碰撞。"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt

from .. import paths
from ..config import MAX_PETS, Config
from ..engine.collision import (
    FORCE_FULL_TICKS,
    HIT_FEEDBACK_MIN_SPEED,
    CollisionBody,
    collide,
    rects_may_touch,
    stable_tie_break,
)
from ..engine import spawn as spawn_mod
from ..platform import screens as screen_mod
from .pet_window import PetWindow

logger = logging.getLogger("petcore.ui.pets")

#: 播种子宠档案时不复制的键：位置/成员表/菜单布局是每宠独立的，`extra` 是全局的
#: （扩展偏好由共享运行时读取，复制进子宠只会造成「文件里有值、实际不生效」）。
SEED_EXCLUDED_KEYS = frozenset(
    {"active_pets", "positions", "menu_layout", "menu_order", "menu_hidden", "extra"}
)
SHARED_KEYS = (
    "click_through",
    "active_pets",
    "allow_feature_mods",
    "hotkey_toggle_pet",
    "last_settings_page",
    "menu_hidden",
    "menu_layout",
    "menu_order",
    "quick_launch_apps",
    "spawn_inherit_size",
    "spawn_scale",
    "stream_capture_mode",
    "ui_font_size",
)


class PetManager(QObject):
    def __init__(self, host, root: Path, config: Config):
        super().__init__()
        self.host = host
        self.root = Path(root)
        self.config = config
        self.primary: PetWindow | None = None
        self.windows: dict[int, PetWindow] = {}
        self.diagnostics: list[str] = []
        self._overlap_streak: dict[tuple[int, int], int] = {}
        #: 本拍的身体框缓存（`_tick_collision` 开头清空，移动即时失效）。
        self._rect_cache: dict[int, tuple[int, int, int, int]] = {}
        self._collision_timer = QTimer(self)
        self._collision_timer.setInterval(16)
        # 碰撞没有 dt 依赖，Coarse 的 ±1ms 抖动无感；单宠时整张表会停掉，
        # 所以不要用 Precise 把系统计时器精度钉在 1ms。
        self._collision_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._collision_timer.timeout.connect(self._tick_collision)

    # ---------------------------------------------------------------- 创建/恢复
    def create_primary(self, character) -> PetWindow:
        window = PetWindow(self.config, self.root, self.host, slot=0)
        self.primary = window
        self.windows[0] = window
        window.start(character)
        self._sync_collision_timer()
        return window

    def _sync_collision_timer(self) -> None:
        """单宠不空转：碰撞结算只在至少两只宠时才有意义。

        ``collision_enabled`` 是设置页可实时切换的，不能在停表时据此判断
        （关掉后再打开就没有任何事件来重启表）；这里只看人数，配置门由
        ``_tick_collision`` 每拍早退。
        """
        should_run = len(self.windows) >= 2
        if should_run and not self._collision_timer.isActive():
            self._collision_timer.start()
        elif not should_run and self._collision_timer.isActive():
            self._collision_timer.stop()

    def seed_config(self, slot: int) -> Config:
        path = paths.pet_config_path(self.root, slot)
        if path.exists():
            return Config.load(self.root, filename=path.name)
        seed = {
            key: value
            for key, value in self.config.data.items()
            if key not in SEED_EXCLUDED_KEYS
        }
        if not bool(self.config.get("spawn_inherit_size", True)):
            seed["scale"] = float(self.config.get("spawn_scale") or 0.72)
        seed["autostart"] = False
        config = Config(self.root, seed, filename=path.name)
        try:
            config.save()
        except OSError:
            logger.warning("failed to seed pet config %s", path, exc_info=True)
        return config

    def spawn(self, *, slot: int | None = None, character_id: str | None = None) -> PetWindow | None:
        if len(self.windows) >= MAX_PETS:
            self._say(f"最多同时显示 {MAX_PETS} 只桌宠。")
            return None
        if slot is None:
            slot = next(
                (candidate for candidate in range(1, MAX_PETS) if candidate not in self.windows),
                None,
            )
        if slot is None or slot <= 0 or slot >= MAX_PETS or slot in self.windows:
            return None
        config = self.seed_config(slot)
        window = PetWindow(config, self.root, self.host, slot=slot)
        self.windows[slot] = window
        host = self.host
        character = host.character_for(character_id or str(config.get("character") or ""))
        window.start(character)
        self._attach_interaction(window)
        self._place_spawned(window, slot)
        self._sync_collision_timer()
        self.remember_active()
        return window

    def _attach_interaction(self, window: PetWindow) -> None:
        """新窗口必须补挂点击/投喂监听：内置特性只对启动时已存在的窗口挂过。"""
        features = getattr(self.host, "features", None)
        interaction = getattr(getattr(features, "ports", None), "interaction", None)
        attach = getattr(interaction, "attach_window", None)
        if not callable(attach):
            return
        try:
            attach(window)
        except Exception:
            logger.debug("failed to attach interaction listeners", exc_info=True)
        notify = getattr(features, "pet_added", None)
        if callable(notify):
            notify(window)

    def _detach_interaction(self, window: PetWindow) -> None:
        ports = getattr(self.host, "ports", None)
        for service in (getattr(ports, "speech", None), getattr(ports, "reminders", None)):
            cancel = getattr(service, "cancel_slot", None)
            if callable(cancel):
                cancel(window.slot)
        features = getattr(self.host, "features", None)
        interaction = getattr(getattr(features, "ports", None), "interaction", None)
        detach = getattr(interaction, "detach_window", None)
        if not callable(detach):
            return
        try:
            detach(window)
        except Exception:
            logger.debug("failed to detach interaction listeners", exc_info=True)
        notify = getattr(features, "pet_removed", None)
        if callable(notify):
            notify(window)

    def _place_spawned(self, window: PetWindow, slot: int) -> None:
        window._layout = screen_mod.snapshot_from_qt()
        bounds = window.probe_bounds()
        if bounds is None:
            return
        bx, by, bw, bh = window.body_local_rect()
        primary = self.primary
        anchor = primary.body_rect() if primary is not None else bounds
        occupied = [
            other.body_rect()
            for other in self.windows.values()
            if other is not window and other.isVisible()
        ]
        body_x, body_y = spawn_mod.choose_spawn_position(
            anchor, (bw, bh), bounds, occupied
        )
        window.move(int(body_x - bx), int(body_y - by))
        window._remember_geometry()

    def restore(self) -> None:
        slots = list(self.config.get("active_pets") or [0])
        restored: list[PetWindow] = []
        for slot in slots:
            if slot == 0 or slot in self.windows:
                continue
            try:
                if not paths.pet_config_path(self.root, slot).exists():
                    self.diagnostics.append(f"pet slot {slot}: missing config, skipped")
                    continue
                window = self.spawn(slot=slot)
                if window is not None:
                    restored.append(window)
            except Exception as exc:
                self.diagnostics.append(f"pet slot {slot}: {type(exc).__name__}: {exc}")
                logger.warning("failed to restore pet slot %s", slot, exc_info=True)
        for window in restored:
            self._attach_interaction(window)
        self.remember_active()

    # ---------------------------------------------------------------- 收回
    def close_slot(self, slot: int) -> bool:
        if slot <= 0:
            return False
        window = self.windows.pop(slot, None)
        if window is None:
            return False
        for key in [pair for pair in self._overlap_streak if slot in pair]:
            self._overlap_streak.pop(key, None)
        self._sync_collision_timer()
        self._detach_interaction(window)
        try:
            window.shutdown()
            window.close()
            window.deleteLater()
        except Exception:
            logger.debug("failed to close pet slot %s", slot, exc_info=True)
        self.remember_active()
        return True

    def close_secondary(self) -> None:
        for slot in sorted(self.windows, reverse=True):
            if slot:
                self.close_slot(slot)

    # ---------------------------------------------------------------- 查询/设置
    def each(self) -> list[PetWindow]:
        return [self.windows[slot] for slot in sorted(self.windows)]

    def secondary(self) -> list[PetWindow]:
        return [window for slot, window in sorted(self.windows.items()) if slot]

    def active_slots(self) -> list[int]:
        return sorted(self.windows)

    def window_for_slot(self, slot: int) -> PetWindow | None:
        return self.windows.get(slot)

    def remember_active(self) -> None:
        self.config.set("active_pets", self.active_slots())
        try:
            self.config.save()
        except OSError:
            logger.warning("failed to save active pets", exc_info=True)

    def sync_shared_settings(self) -> None:
        for slot, window in self.windows.items():
            if slot == 0:
                continue
            for key in SHARED_KEYS:
                window.config.set(key, self.config.get(key))
            try:
                window.config.save()
            except OSError:
                logger.warning("failed to save pet config for slot %s", slot, exc_info=True)
            window.set_scale(float(window.config.get("scale") or 0.72), persist=False)
            window.set_always_on_top(bool(window.config.get("always_on_top")), persist=False)
            window.set_click_through(bool(window.config.get("click_through")), persist=False)
            window.set_edge_probe_enabled(bool(window.config.get("edge_probe_enabled")), persist=False)

    def set_visible_all(self, visible: bool) -> None:
        for window in self.each():
            window.set_visible(bool(visible))

    def handle_screens_changed(self) -> None:
        for window in self.each():
            window.handle_screens_changed()

    def pause_for_session(self) -> None:
        """锁屏/睡眠：停碰撞表与全部宠的播放节拍（保留当前帧）。"""
        self._collision_timer.stop()
        for window in self.each():
            pause = getattr(window, "pause_animation", None)
            if callable(pause):
                pause()

    def resume_after_session(self) -> None:
        """解锁/恢复：续播并只在多宠时重新启动碰撞表。"""
        for window in self.each():
            resume = getattr(window, "resume_animation", None)
            if callable(resume):
                resume()
        self._sync_collision_timer()

    def shutdown(self) -> None:
        self._collision_timer.stop()
        for slot in sorted(self.windows, reverse=True):
            window = self.windows.pop(slot, None)
            if window is None:
                continue
            try:
                window.shutdown()
            except Exception:
                logger.debug("pet shutdown failed for slot %s", slot, exc_info=True)

    # ---------------------------------------------------------------- 碰撞
    def _tick_collision(self) -> None:
        if not bool(self.config.get("collision_enabled", True)):
            return
        windows = [
            window
            for window in self.each()
            if window.isVisible() and window.character is not None
        ]
        if len(windows) < 2:
            return
        # 粗筛缓存按拍重建：上一拍的位置不能拿来判这一拍（拖拽/漫游都在动）。
        self._rect_cache.clear()
        # 两轮分离：三只以上叠在一起时，单轮会被「后来者」顶回去（主分支同理）。
        for _pass in range(2):
            moved = False
            for index, first in enumerate(windows):
                for second in windows[index + 1 :]:
                    moved = self._collide_pair(first, second) or moved
            if not moved:
                break

    def _collide_pair(self, first: PetWindow, second: PetWindow) -> bool:
        # 拖拽中的桌宠不与别人结算：否则会推着另一只一路滑走，也会把对方顶出屏幕。
        if first.dragging or second.dragging:
            return False
        # 轴对齐粗筛（99 只 = 4851 对）：矩形分离时圆链必不重叠，省掉两次 body 构造
        # 与 9 对圆心距离。可能与接触的配对仍走完整几何，判定结果不变。
        if not rects_may_touch(self._body_rect(first), self._body_rect(second)):
            # 离开接触就清连续重叠计数（与旧行为一致：以前这里也会被 collide 的
            # None 分支清掉）。空表时不构造 key，99 只的稳态下这对是纯空转。
            if self._overlap_streak:
                self._overlap_streak.pop(
                    (
                        min(int(first.slot), int(second.slot)),
                        max(int(first.slot), int(second.slot)),
                    ),
                    None,
                )
            return False
        a = self._body(first)
        b = self._body(second)
        key = (min(a.slot, b.slot), max(a.slot, b.slot))
        streak = self._overlap_streak.get(key, 0)
        result = collide(
            a,
            b,
            force_full=streak >= FORCE_FULL_TICKS,
            tie_break=stable_tie_break(*key),
        )
        if result is None:
            self._overlap_streak.pop(key, None)
            return False
        hit = result.impact_speed >= HIT_FEEDBACK_MIN_SPEED
        probing = [window for window in (first, second) if window.probe_active]
        if probing and not hit:
            # 探头姿态是「身体按设计探出屏幕」的旋转态。只有真被撞飞才退出探头；
            # 低速蹭上时把探头宠物当无限质量，只推开对方（主分支同款手感）。
            static_first = first.probe_active and not second.probe_active
            static_second = second.probe_active and not first.probe_active
            if static_first or static_second:
                result = collide(
                    replace(a, static=True) if static_first else a,
                    replace(b, static=True) if static_second else b,
                    force_full=streak >= FORCE_FULL_TICKS,
                    tie_break=stable_tie_break(*key),
                )
                if result is None:
                    self._overlap_streak.pop(key, None)
                    return False
        elif probing:
            for window in probing:
                # 主分支口径：探头宠物被真正撞飞时才授权「击飞旋转」彩蛋，
                # 授权必须早于 cancel（cancel 会把探头姿态复位）。
                window.effects.arm_throw_egg()
            for window in probing:
                window._edge_probe.cancel("collision", restore=True)
            a = self._body(first)
            b = self._body(second)
            result = collide(
                a,
                b,
                force_full=True,
                tie_break=stable_tie_break(*key),
            )
            if result is None:
                self._overlap_streak.pop(key, None)
                return False
        self._overlap_streak[key] = streak + 1
        # 走路是帧驱动写位置的，但轻碰不能取消计划：只把计划整体平移，
        # 否则每撞一次就被拉回原位（看起来就是「推不动 / 卡住」）。
        before_first = (first.x(), first.y())
        before_second = (second.x(), second.y())
        moved_a = self._apply_body(
            first,
            result.ax,
            result.ay,
            result.avx,
            result.avy,
            impact_speed=result.impact_speed,
        )
        moved_b = self._apply_body(
            second,
            result.bx,
            result.by,
            result.bvx,
            result.bvy,
            impact_speed=result.impact_speed,
        )
        first.shift_move_plan(first.x() - before_first[0], first.y() - before_first[1])
        second.shift_move_plan(second.x() - before_second[0], second.y() - before_second[1])
        if probing:
            if hit:
                # 撞击授权了彩蛋却没真的进入抛掷（例如彩蛋被关掉）时必须立刻收口，
                # 否则会一直把动画锁在 idle/turn。
                for window in probing:
                    if not window.motion.thrown:
                        window.effects.end_throw_egg()
            else:
                # 被探头宠物挡下的走路宠回待机，避免每 tick 顶着磨。
                for window in (first, second):
                    if not window.probe_active:
                        window.abort_move_plan()
        if hit:
            first.effects.start_squash(int(first.config.get("click_squash_ms") or 220))
            second.effects.start_squash(int(second.config.get("click_squash_ms") or 220))
            if bool(first.config.get("collision_sound")):
                first._play_click_sound()
        return moved_a or moved_b

    def _body(self, window: PetWindow) -> CollisionBody:
        x, y, width, height = window.body_rect()
        vx, vy = window.motion.velocity()
        return CollisionBody(
            x=float(x),
            y=float(y),
            width=float(width),
            height=float(height),
            vx=vx,
            vy=vy,
            dragging=window.dragging,
            static=bool(window.config.get("lock_position")),
            scale=float(window.state.scale),
            slot=int(window.slot),
        )

    def _body_rect(self, window: PetWindow) -> tuple[int, int, int, int]:
        """本拍的身体框缓存：粗筛只要矩形，真可能接触的才去建 ``CollisionBody``。

        ``PetWindow.body_rect()`` 每次都读窗口几何，多宠时 n² 对里重复取同两只的矩形
        是纯浪费（实测 99 只时一对要 1.5µs 建 body）。移动后由 ``_apply_body`` 立刻
        失效，所以同一拍内的矩形永远是新鲜的。
        """
        slot = int(window.slot)
        rect = self._rect_cache.get(slot)
        if rect is None:
            rect = window.body_rect()
            self._rect_cache[slot] = rect
        return rect

    def _apply_body(
        self,
        window: PetWindow,
        body_x: float,
        body_y: float,
        vx: float,
        vy: float,
        *,
        impact_speed: float = 0.0,
    ) -> bool:
        bx, by, _bw, _bh = window.body_local_rect()
        new_x = int(round(body_x - bx))
        new_y = int(round(body_y - by))
        # 碰撞分离也必须按身体框收口：否则会把宠物一路顶出屏幕。
        new_x, new_y = window._clamp_position(new_x, new_y)
        moved = (new_x, new_y) != (window.x(), window.y())
        if moved:
            window.move(new_x, new_y)
            # 本拍剩下的配对要看到新位置（窗口一定带 slot；测试替身可以不带）。
            slot = getattr(window, "slot", None)
            if slot is not None:
                self._rect_cache.pop(int(slot), None)
        # 只有真撞击才写速度：``set_velocity`` 会切到 thrown（换飞行素材、
        # 停止掷骰），轻碰写速度 = 走路宠一碰就变抛掷态。已经在抛掷里的不覆盖——
        # 位置出口可能刚因撞岛写过冲量，这里再按分离速度重写就是「两处改速度打架」。
        if (
            not window.dragging
            and not window.motion.thrown
            and impact_speed >= HIT_FEEDBACK_MIN_SPEED
        ):
            window.motion.set_velocity(vx, vy)
        return moved

    def _say(self, text: str) -> None:
        if self.primary is not None:
            self.primary.say(text)
