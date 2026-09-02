# -*- coding: utf-8 -*-
"""多开桌宠碰撞客户端（PetWindow 碰撞职责抽取，批 6-4）。

``CollisionClient`` 承载 PetWindow 的碰撞客户端侧职责：
1. 提交自身 snapshot（``_collision_state`` / ``_submit_collision_state``，
   去重 + 20Hz 非 force 限流，运动期由 50ms/500ms 定时器兜底强制上报）
2. 接收 peer snapshot / 权威 impulse（``_on_collision_snapshot`` /
   ``_on_collision_impulse``，含 epoch/watermark/predicted 对账/contact deviation）
3. predicted bounce 本地预测（``_predict_collision_bounce``，throw 物理每 tick
   结束后对 peer 快照做本地弹跳预判并上报 FLAG_PREDICTED_BOUNCE 状态）
4. 碰撞相关状态字段（session / seq / epoch / peer snapshots / predicted
   bounces / pending predicted / watermark / 上报节流 / 碰撞 squash 冷却）

PetWindow 保留组合：持有 ``CollisionClient`` 实例，对外行为（碰撞反应、音效、
弹开）一丝不变。本模块只依赖纯物理/协议/调试层（collision / collision_codec /
collision_debug / physics），不反向依赖 window.py；交互状态常量（THROWN 等）与
碰撞阈值常量由构造时从 PetWindow 显式传入，避免模块循环导入。

数值等价死线：本文件内任何冲量/分离/predicted 的数学参数、计算顺序、默认值
都与抽取前的 window.py 逐行一致，禁止顺手调参。
"""
from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt, Slot

from . import collision
from . import collision_codec
from . import collision_debug
from . import physics as physics_mod


class CollisionClient(QObject):
    """PetWindow 的碰撞客户端子系统（GUI 线程，由窗口组合持有）。"""

    def __init__(self, win, *, thrown, dragging, slingshot_aiming,
                 hit_min_dv, contact_dv_floor):
        super().__init__(win)
        self._win = win
        # 交互状态常量与碰撞阈值由宿主传入（window.py 模块级常量，避免循环导入）
        self._thrown = thrown
        self._dragging = dragging
        self._slingshot_aiming = slingshot_aiming
        self._hit_min_dv = hit_min_dv
        self._contact_dv_floor = contact_dv_floor

        # ---- 碰撞客户端状态字段（自 window.py 抽取，语义不变）----
        self.session = None
        self.seq = 0
        self.last_state = None
        self.last_submit_at = 0.0  # 非 force 提交 20Hz 限流时间戳
        self.applied_policy = None  # 已同步到会话的碰撞策略
        self.epoch = ''
        self.peer_snapshots: dict[str, dict[str, Any]] = {}
        self.predicted_bounces: dict[str, float] = {}
        self.pending_predicted_bounce: tuple[float, float] | None = None
        self.pending_predicted_contact: tuple[float, float, list[list[float]]] | None = None
        self.impulse_watermarks = collision_codec.WatermarkDeduplicator()
        self.last_collision_squash_at = float('-inf')

        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(lambda: self._submit_collision_state(force=True))

    # ------------------------------------------------------------------
    # 会话 attach/detach 与策略同步
    # ------------------------------------------------------------------
    def attach(self, session) -> None:
        """绑定 PetApp 持有的 IPC facade，GUI 不接触 socket。"""
        self.detach()
        if session is None or not bool(self._win.cfg.get('collision_enabled', True)):
            return
        self.session = session
        session.impulse_ready.connect(self._on_collision_impulse, Qt.ConnectionType.QueuedConnection)
        session.snapshot_ready.connect(self._on_collision_snapshot, Qt.ConnectionType.QueuedConnection)
        self.timer.start()
        self._submit_collision_state(force=True)
        self._sync_collision_policy()

    def detach(self) -> None:
        session = self.session
        if session is None:
            return
        # 先关闭所有状态生产路径，再把 leave 排入同一 worker 队列；否则一个
        # 已排队的 timer tick 可能在 leave 之后重新提交状态并复活成员。
        # （顺序语义来自上游 issue #42 加固，合并时保持）
        self.timer.stop()
        self.session = None
        try:
            session.impulse_ready.disconnect(self._on_collision_impulse)
        except (RuntimeError, TypeError):
            pass
        try:
            session.snapshot_ready.disconnect(self._on_collision_snapshot)
        except (RuntimeError, TypeError):
            pass
        submit_leave = getattr(session, 'submit_leave', None)
        if callable(submit_leave):
            submit_leave()
        self.epoch = ''
        self.peer_snapshots.clear()
        self.predicted_bounces.clear()
        self.pending_predicted_bounce = None
        self.pending_predicted_contact = None
        self._sync_collision_policy()

    def _sync_collision_policy(self) -> None:
        """把当前配置的碰撞参数同步到会话 policy，运行中改动即时生效。

        协调者配置优先：本进程是协调者时碰撞求解直接用本配置；
        非协调者时本地 policy 仅在本进程未来接管协调者时才生效。
        """
        win = self._win
        session = self.session
        if session is None:
            # 本地成员 detach 后，app-owned worker 仍可能是其他实例的协调者；
            # 策略必须继续同步，尤其是 collision_enabled=False（上游 #42）。
            session = getattr(win, 'collision_app_session', None)
        policy = {
            'collision_enabled': bool(win.cfg.get('collision_enabled', True)),
            'collision_restitution': float(win.cfg.get('collision_restitution', .82)),
            'collision_friction': float(win.cfg.get('collision_friction', .08)),
            'collision_mass_scale': float(win.cfg.get('collision_mass_scale', 1.0)),
            'collision_impulse_cap': float(win.cfg.get('collision_impulse_cap', 9000.0)),
        }
        if session is None:
            self.applied_policy = None
            return
        if policy == self.applied_policy:
            return
        self.applied_policy = policy
        update_policy = getattr(session, 'update_policy', None)
        if callable(update_policy):
            update_policy(policy)

    # ------------------------------------------------------------------
    # 自身 snapshot 提交
    # ------------------------------------------------------------------
    def _collision_flags(self) -> int:
        win = self._win
        flags = collision.FLAG_VISIBLE if win.isVisible() else 0
        if not win.isVisible() or win._hidden_paused:
            flags |= collision.FLAG_PAUSED
        if win._interaction_state == self._thrown or win._physics_mode == 'throw':
            flags |= collision.FLAG_THROWN
        if win._interaction_state == self._dragging:
            flags |= collision.FLAG_DRAGGING
        if win._interaction_state == self._slingshot_aiming:
            flags |= collision.FLAG_SLINGSHOT_AIMING
        if win.lock_position:
            flags |= collision.FLAG_LOCK_POSITION
        if win.no_move:
            flags |= collision.FLAG_NO_MOVE
        if win.mouse_through:
            flags |= collision.FLAG_MOUSE_THROUGH
        if win._auto_cursor_hidden:
            flags |= collision.FLAG_AUTO_CURSOR_HIDDEN
        if bool(win.cfg.get('collision_enabled', True)):
            flags |= collision.FLAG_COLLISION_ENABLED
        if self.pending_predicted_bounce is not None:
            flags |= collision.FLAG_PREDICTED_BOUNCE
        return flags

    def _collision_velocity(self) -> tuple[float, float]:
        win = self._win
        if win._interaction_state == self._dragging and len(win._trail) >= 2:
            latest_t = win._trail[-1][0]
            samples = [sample for sample in win._trail if latest_t - sample[0] <= 0.1]
            if len(samples) >= 2:
                t0, x0, y0 = samples[0]
                t1, x1, y1 = samples[-1]
                dt = max(0.001, t1 - t0)
                return (x1 - x0) / dt, (y1 - y0) / dt
        return float(win._phys_vel[0]), float(win._phys_vel[1])

    def _collision_state(self) -> dict[str, Any]:
        win = self._win
        rect = win.collision_content_rect()
        vx, vy = self._collision_velocity()
        circles = collision.circles_from_rect(rect.x(), rect.y(), rect.width(), rect.height())
        state = {
            'seq': self.seq,
            'ts': time.monotonic(),
            'x': float(rect.center().x()), 'y': float(rect.center().y()),
            'w': float(win._w), 'h': float(win._h),
            'radius_x': max(1.0, rect.width() / 2.0),
            'radius_y': max(1.0, rect.height() / 2.0),
            'circles': circles,
            'vx': 0.0 if not win.isVisible() else vx,
            'vy': 0.0 if not win.isVisible() else vy,
            'flags': self._collision_flags(),
            'character': str(win.cfg.get('character', '')),
            'scale': float(win.scale),
        }
        if self.pending_predicted_bounce is not None:
            state['bounce_vx'], state['bounce_vy'] = self.pending_predicted_bounce
            if self.pending_predicted_contact is not None:
                state['bounce_x'], state['bounce_y'], state['bounce_circles'] = self.pending_predicted_contact
        return state

    def _submit_collision_state(self, force: bool = False) -> None:
        win = self._win
        session = self.session
        if session is None:
            return
        state = self._collision_state()
        comparable = dict(state)
        comparable.pop('seq', None)
        # 时间戳不参与"状态是否变化"比较：ts 每次不同会让去重恒失效（死代码）
        comparable.pop('ts', None)
        if not force and comparable == self.last_state:
            return
        now = time.monotonic()
        if not force and now - self.last_submit_at < 0.05:
            # 非 force 提交 20Hz 限流：moveEvent 等 60Hz 高频路径不超标，
            # 运动期间由 self.timer（50ms/500ms）兜底强制上报
            return
        self.seq += 1
        state['seq'] = self.seq
        self.last_state = comparable
        self.last_submit_at = now
        session.submit_state(state)
        if self.pending_predicted_bounce is not None:
            self.pending_predicted_bounce = None
            self.pending_predicted_contact = None
        if collision_debug.ENABLED:
            collision_debug.log(
                getattr(session, 'runtime_id', ''), 'state_submit',
                x=state['x'], y=state['y'], vx=state['vx'], vy=state['vy'],
                seq=state['seq'], force=force,
            )
        moving = (win._interaction_state in (self._dragging, self._thrown)
                   or math.hypot(*win._phys_vel) > 20.0)
        self.timer.setInterval(50 if moving else 500)

    # ------------------------------------------------------------------
    # 接收 peer snapshot / 权威 impulse
    # ------------------------------------------------------------------
    @Slot(object)
    def _on_collision_snapshot(self, message: dict[str, Any]) -> None:
        epoch = str(message.get('epoch') or '')
        if not epoch:
            return
        if epoch != self.epoch:
            # 新 epoch = 新协调者上任：权威成员表整个换人，旧 epoch 的预测
            # 反弹状态全部作废（上游 #42：不再丢弃新 epoch 快照，而是清场接纳）
            self.predicted_bounces.clear()
            self.pending_predicted_bounce = None
            self.pending_predicted_contact = None
        self.epoch = epoch
        runtime_id = str(getattr(self.session, 'runtime_id', ''))
        now = time.monotonic()
        peers = {}
        for raw_member in message.get('members') or ():
            member = dict(raw_member)
            peer_id = str(member.get('runtime_id') or '')
            if peer_id and peer_id != runtime_id:
                member['_received_at'] = now
                peers[peer_id] = member
        self.peer_snapshots = peers

    def _prune_collision_prediction_state(self, now: float) -> None:
        self.peer_snapshots = {
            runtime_id: member for runtime_id, member in self.peer_snapshots.items()
            if now - float(member.get('_received_at', 0.0)) <= 1.5
        }
        self.predicted_bounces = {
            pair: predicted_at for pair, predicted_at in self.predicted_bounces.items()
            if now - predicted_at <= 0.5
        }

    @Slot(object)
    def _on_collision_impulse(self, message: dict[str, Any]) -> None:
        win = self._win
        runtime_id = str(getattr(self.session, 'runtime_id', ''))
        def discard(reason: str) -> None:
            if collision_debug.ENABLED:
                collision_debug.log(runtime_id, 'impulse_discard', reason=reason,
                                    pair=message.get('pair', ''))
        if self.session is None or not win.isVisible() or win._hidden_paused:
            discard('session_missing_or_hidden')
            return
        epoch = str(message.get('epoch') or '')
        pair_for_watermark = str(message.get('pair') or '')
        tick = message.get('tick')
        if epoch and pair_for_watermark and tick is not None:
            if not self.impulse_watermarks.should_apply(epoch, pair_for_watermark, int(tick)):
                discard('watermark')
                return
        if win._interaction_state == self._dragging or win._physics_mode == 'drag':
            discard('dragging')
            return
        if message.get('a') == runtime_id:
            dvx, dvy = float(message.get('dvx_a', 0)), float(message.get('dvy_a', 0))
            dx, dy = float(message.get('dx_a', 0)), float(message.get('dy_a', 0))
        elif message.get('b') == runtime_id:
            dvx, dvy = float(message.get('dvx_b', 0)), float(message.get('dvy_b', 0))
            dx, dy = float(message.get('dx_b', 0)), float(message.get('dy_b', 0))
        else:
            discard('runtime_id_mismatch')
            return
        pair = str(message.get('pair') or '|'.join(sorted((str(message.get('a') or ''),
                                                           str(message.get('b') or '')))))
        now = time.monotonic()
        predicted_at = self.predicted_bounces.pop(pair, None)
        if predicted_at is not None and now - predicted_at <= 0.5:
            discard('predicted_bounce_confirmed')
            return
        rect = win.collision_content_rect()
        radius_x = max(1.0, rect.width() / 2.0)
        radius_y = max(1.0, rect.height() / 2.0)
        hit_dv = math.hypot(dvx, dvy)
        is_real_hit = hit_dv >= self._hit_min_dv
        has_velocity_impulse = abs(dvx) > 1e-9 or abs(dvy) > 1e-9
        # 偏差豁免的本意是"协调者眼中的我已经过期就别瞬移我"——直接比较
        # 协调者 tick 时认定的我方中心（ax/ay 或 bx/by）与当前实际中心，
        # 不从 contact/normal 反推（三种检测路径的 contact 语义不同，反推
        # 会系统性误判，导致所有位置分离被丢弃）
        if message.get('a') == runtime_id:
            expected_x = float(message.get('ax', rect.center().x()))
            expected_y = float(message.get('ay', rect.center().y()))
        else:
            expected_x = float(message.get('bx', rect.center().x()))
            expected_y = float(message.get('by', rect.center().y()))
        threshold = min(radius_x, radius_y) * 0.1 + math.hypot(*win._phys_vel) * 0.2
        contact_deviation = math.hypot(rect.center().x() - expected_x, rect.center().y() - expected_y) > threshold
        if contact_deviation:
            dx = dy = 0.0
            dvx = dvy = 0.0
            if collision_debug.ENABLED:
                collision_debug.log(runtime_id, 'impulse_position_discard',
                                    reason='contact_deviation', pair=message.get('pair', ''))
        if is_real_hit or (win._interaction_state == self._thrown
                        and hit_dv >= self._contact_dv_floor):
            win._phys_vel[0] += dvx
            win._phys_vel[1] += dvy
        speed = math.hypot(*win._phys_vel)
        if speed > win._throw_speed_cap:
            clamped = physics_mod.soft_clamp_speed(speed, win._throw_speed_cap)
            win._phys_vel[:] = [win._phys_vel[0] * clamped / speed, win._phys_vel[1] * clamped / speed]
        if abs(dx) > 1e-9 or abs(dy) > 1e-9:
            win._cancel_move()
            win._cancel_animation_gap()
            clamped_x, clamped_y = win._collision_clamp_pos(win.x() + dx, win.y() + dy)
            left, top = win._collision_clamp_pos(float('-inf'), float('-inf'))
            right, bottom = win._collision_clamp_pos(float('inf'), float('inf'))
            win.move(
                min(max(int(round(clamped_x)), math.ceil(left)), math.floor(right)),
                min(max(int(round(clamped_y)), math.ceil(top)), math.floor(bottom)),
            )
            win._phys_pos[:] = [float(win.x()), float(win.y())]
        if has_velocity_impulse:
            win._just_dragged = True
            QTimer.singleShot(120, win, win._clear_just_dragged)
            # 只有"有分量的撞击"才响：dv 太小（静置非弹性接触的微小抵消）
            # 不播，否则贴贴时每秒 4 声机枪响
            if is_real_hit:
                win._play_collision_sound()
        if is_real_hit and not contact_deviation:
            win._interaction_state = self._thrown
            win._enter_physics_mode('throw')
            win._phys_pos[:] = [float(win.x()), float(win.y())]
            win._last_physics_tick_time = None
            win._physics_timer.start()
        now = time.monotonic()
        if (is_real_hit and not win._squash_active
                and now - self.last_collision_squash_at >= 0.25):
            self.last_collision_squash_at = now
            win._start_squash()
        self._submit_collision_state(force=True)
        if collision_debug.ENABLED:
            collision_debug.log(runtime_id, 'impulse_apply', pair=message.get('pair', ''),
                                dv=(dvx, dvy), displacement=(dx, dy), speed=speed)

    # ------------------------------------------------------------------
    # predicted bounce 本地预测（throw 物理每 tick 结束后调用）
    # ------------------------------------------------------------------
    def _predict_collision_bounce(self, start_x: float, start_y: float,
                                  incoming_vx: float | None = None,
                                  incoming_vy: float | None = None) -> None:
        win = self._win
        if (win._physics_mode != 'throw'
                or not bool(win.cfg.get('collision_enabled', True))
                or self.session is None):
            return
        now = time.monotonic()
        self._prune_collision_prediction_state(now)
        runtime_id = str(getattr(self.session, 'runtime_id', ''))
        if not runtime_id:
            return

        rect = win.collision_content_rect()
        dx, dy = win._phys_pos[0] - win.x(), win._phys_pos[1] - win.y()
        current_circles = collision.circles_from_rect(
            rect.x() + dx, rect.y() + dy, rect.width(), rect.height())
        previous_circles = [[x - (win._phys_pos[0] - start_x),
                             y - (win._phys_pos[1] - start_y), radius]
                            for x, y, radius in current_circles]
        own = collision.MemberState(
            runtime_id=runtime_id,
            x=rect.center().x() + dx,
            y=rect.center().y() + dy,
            radius_x=max(1.0, rect.width() / 2.0),
            radius_y=max(1.0, rect.height() / 2.0),
            vx=win._phys_vel[0],
            vy=win._phys_vel[1],
            mass=collision.calculate_mass(
                max(1.0, rect.width() / 2.0), max(1.0, rect.height() / 2.0),
                scale=float(win.scale),
                collision_mass_scale=float(win.cfg.get('collision_mass_scale', 1.0))),
            flags=self._collision_flags(),
            circles=current_circles,
        )
        bounce_vx = own.vx if incoming_vx is None else incoming_vx
        bounce_vy = own.vy if incoming_vy is None else incoming_vy

        for peer_id, raw_peer in self.peer_snapshots.items():
            flags = int(raw_peer.get('flags', 0))
            if (not flags & collision.FLAG_VISIBLE or flags & collision.FLAG_PAUSED
                    or not flags & collision.FLAG_COLLISION_ENABLED):
                continue
            age = max(0.0, now - float(raw_peer['_received_at']))
            extrapolation = min(0.05, age)
            peer_vx, peer_vy = float(raw_peer.get('vx', 0.0)), float(raw_peer.get('vy', 0.0))
            peer_dx, peer_dy = peer_vx * extrapolation, peer_vy * extrapolation
            peer_circles = [[float(c[0]) + peer_dx, float(c[1]) + peer_dy, float(c[2])]
                            for c in raw_peer.get('circles') or () if len(c) >= 3]
            if not peer_circles:
                continue
            pair = '|'.join(sorted((runtime_id, peer_id)))
            if pair in self.predicted_bounces:
                continue
            hit = collision.check_collision_circles(current_circles, peer_circles, runtime_id, peer_id)
            if not hit[0]:
                hit = collision.swept_circle_chain_collision(
                    previous_circles, current_circles, peer_circles, peer_circles)
            collided, nx, ny, _, _, _ = hit
            vn = (peer_vx - own.vx) * nx + (peer_vy - own.vy) * ny
            if not collided or vn >= -collision.IMPULSE_MIN_APPROACH_SPEED:
                continue
            radius_x = max(1.0, float(raw_peer.get('radius_x', 1.0)))
            radius_y = max(1.0, float(raw_peer.get('radius_y', 1.0)))
            peer = collision.MemberState(
                runtime_id=peer_id,
                x=float(raw_peer.get('x', 0.0)) + peer_dx,
                y=float(raw_peer.get('y', 0.0)) + peer_dy,
                radius_x=radius_x,
                radius_y=radius_y,
                vx=peer_vx,
                vy=peer_vy,
                mass=collision.calculate_mass(
                    radius_x, radius_y,
                    scale=float(raw_peer.get('scale', collision.DEFAULT_BASE_SCALE) or collision.DEFAULT_BASE_SCALE),
                    collision_mass_scale=float(win.cfg.get('collision_mass_scale', 1.0))),
                is_infinite_mass=bool(flags & (collision.FLAG_DRAGGING | collision.FLAG_LOCK_POSITION)),
                flags=flags,
                circles=peer_circles,
            )
            _, dvx, dvy, _, _ = collision.solve_collision_impulse(
                own, peer, nx, ny,
                restitution=float(win.cfg.get('collision_restitution', .82)),
                friction=float(win.cfg.get('collision_friction', .08)),
                impulse_cap=float(win.cfg.get('collision_impulse_cap', 9000.0)))
            win._phys_vel[0] += dvx
            win._phys_vel[1] += dvy
            speed = math.hypot(*win._phys_vel)
            if speed > win._throw_speed_cap:
                clamped = physics_mod.soft_clamp_speed(speed, win._throw_speed_cap)
                win._phys_vel[:] = [win._phys_vel[0] * clamped / speed,
                                     win._phys_vel[1] * clamped / speed]
            self.predicted_bounces[pair] = now
            self.pending_predicted_bounce = (float(bounce_vx), float(bounce_vy))
            self.pending_predicted_contact = (
                float(own.x), float(own.y),
                [[float(c[0]), float(c[1]), float(c[2])] for c in current_circles],
            )
            win._play_collision_sound()
            self._submit_collision_state(force=True)
            if not win._squash_active and now - self.last_collision_squash_at >= 0.25:
                self.last_collision_squash_at = now
                win._start_squash()
            break
