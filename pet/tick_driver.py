# -*- coding: utf-8 -*-
"""统一 tick 驱动器（M-2）：仿真推进与 advance/paint 解耦。

背景（REVIEW_VERDICT.md 大修项 M-2 / 设计稿 PHASE4_DESIGN.md T2）：此前
"仿真推进"绑死在主 overlay 的 ``OverlayWindow._on_tick``（经
``ShellOverlayWindow.before_sprites_advance`` → ``OverlayShell._advance_controllers``），
与 T2「统一 tick 驱动器调 ``world.tick(全部 overlays 的 sprites, dt)``，不是挂在
主 overlay 的钩子」冲突：多 overlay 时成员快照只含主 overlay 的 sprites，
且每个 overlay 各持一个 QTimer 时会双频 tick。

本模块把一次 tick 拆成两段（分工边界即 M-2 的拆法）：

1. **仿真段** ``tick_sim(dt)``：行为 → 碰撞 → 抛掷物理（tick 顺序协议不变），
   sprites 聚合自**全部**已挂载 overlay（T2 的成员快照完整性）；带宿主
   （``overlay.host``，多屏装配）时按宿主分组跑各自那份控制器——见下；
2. **推进+重绘段** ``advance_overlays(dt)``：逐个 overlay 调
   ``overlay.tick_advance(dt)``（advance → 脏矩形 → 位置监听 fanout → update，
   V-3/V-4 帧直驱脏矩形语义不变）。

**按宿主仿真**：合成窗的仿真宿主是 ``screen_host.ScreenHost``（overlay + 窗口
边界 + 活动区矩形集合 + 行为/碰撞/物理控制器）。仿真段按宿主分组：**同一 tick、
同一 QTimer**，每个宿主只跑自己的世界。没有宿主的 overlay（裸
``OverlayWindow`` / demo 装配）逐位沿用单世界语义。

驱动器独立持有三控制器、QTimer/QElapsedTimer、dt 钳制与 M-1 TickGovernor
（四档降档/同步升档语义原样随迁，见 tick_governor.py）；overlay 退化为
"sprite 容器 + advance/paint"。``attach``/``detach`` 增删成员；tick 刷新率取
**全部已连接显示器**里最高者（单合成窗覆盖整个虚拟桌面，窗口自己那块屏不等于
宠所在屏）。

兼容期（deprecation）：未挂任何控制器的驱动器（裸 ``OverlayWindow`` / demo
装配）仍在仿真段调用 ``overlay.before_sprites_advance(dt)`` 钩子，旧装配不迁移
也能跑；产品壳（overlay_shell）改按宿主分组后该钩子不再被调用，避免
"驱动器 + 钩子"双份仿真。该兼容分支随 4.4 退役刀删除。

零 Qt 时钟可注入：offscreen 测试同步直调 ``on_tick(dt=...)``，不启动真实
QTimer、不 sleep 赌时序（AGENTS.md 时序测试纪律）。

tick 仪表（归因"偶发卡顿"）与电源感知（AC 下 T1 满速）见 ``TickMetrics`` /
``TickGovernor.on_ac_power``：仪表默认开、``PET_TICK_METRICS=0`` 全关；
电源感知只在 T1 的有效间隔上体现，T0/T2/T3 与降档滞回语义不变。

隐藏降载（O3/O4）：档位判定的可见性从"窗口可见"扩到"有可见 sprite"——
逐只藏光与整窗隐藏同义（``note_sprite_visibility_changed`` 立即重评，目标
T3 走既有 occluded 分支）；锁屏/挂起由 ``set_suspended`` 强制 T3 并逐 sprite
停播放节拍（只降档，不隐藏窗口），唤醒时同帧回全速并续播。
"""

from __future__ import annotations

import logging
import os
import time
from array import array

from PySide6.QtCore import QElapsedTimer, QObject, Qt, QTimer

from .pet_sprite import INTERACTION_NORMAL
from .tick_governor import (
    ANIMATING_WINDOW_S,
    TIER_ACTIVE,
    TIER_IDLE_ANIM,
    TIER_INTERVAL_MS,
    TIER_OCCLUDED,
    TickGovernor,
)

logger = logging.getLogger(__name__)

#: tick 仪表总开关：``PET_TICK_METRICS=0`` 关（默认开）。导入期读一次，
#: 热路径只剩一次模块级 bool 判断（关闭 = 零分配零调用）。
TICK_METRICS_ENABLED = (
    (os.environ.get("PET_TICK_METRICS") or "1").strip().lower()
    not in ("0", "false", "no", "off")
)
#: 间隔分位统计输出周期（s）：每周期打一行 p50/p99/max + 当前档
TICK_METRICS_REPORT_S = 60.0
#: 滚动窗口样本数（170Hz ≈ 24s；24fps ≈ 2.8min；64KB 定长缓冲）
TICK_METRICS_WINDOW = 4096
#: 单次 tick_sim + advance 超时阈值（ms）：超它记 WARNING（含耗时与档）
TICK_SLOW_MS = 50.0


class TickMetrics:
    """tick 仪表：慢帧告警 / 档位迁移 / 间隔分位（归因"偶发卡顿"）。

    热路径零分配：间隔样本写进**预分配**的 ``array('d')`` 环形缓冲（定长，
    写入即下标赋值，不新建对象）；分位排序只在 60s 一行时做一次（``n`` 个
    浮点的 sorted() —— 4096 样本实测量级 ~0.3ms/分钟，见交付报告）。

    全程由 ``TICK_METRICS_ENABLED`` 门控（调用方判断，关闭时本类零调用）；
    时钟可注入，测试用假钟推进到 60s 边界，不 sleep。
    """

    def __init__(self, *, clock=time.monotonic,
                 window: int = TICK_METRICS_WINDOW) -> None:
        self._clock = clock
        self._window = max(1, int(window))
        self._buf = array("d", bytes(8 * self._window))  # 定长环形缓冲（预分配）
        self._pos = 0
        self._filled = 0
        self._last_tick_at: float | None = None
        self._since_report = clock()

    def note_tick_start(self) -> float | None:
        """记一次 tick 起点，返回距上次的间隔（ms）；首次返回 ``None``。

        这是"tick 是否按档位间隔准时醒来"的原始读数（实际唤醒间隔，不是
        标称间隔），偶发卡顿会先在 p99/max 上露头。
        """
        now = self._clock()
        last, self._last_tick_at = self._last_tick_at, now
        if last is None:
            return None
        return (now - last) * 1000.0

    def note_interval(self, ms: float) -> None:
        """写入一个间隔样本（环形覆盖，无分配）。"""
        self._buf[self._pos] = ms
        self._pos = (self._pos + 1) % self._window
        if self._filled < self._window:
            self._filled += 1

    def skip_tick_baseline(self) -> None:
        """丢弃间隔基线（T3 心跳期不采样：跨档的长间隔不是卡顿信号）。"""
        self._last_tick_at = None

    def note_slow_tick(self, ms: float, tier: int) -> None:
        """单次 tick 的仿真+推进耗时超 ``TICK_SLOW_MS`` → WARNING（含耗时与档）。"""
        if ms > TICK_SLOW_MS:
            logger.warning("tick 慢帧 %.1fms（阈值 %.0fms）tier=T%d",
                           ms, TICK_SLOW_MS, tier)

    def maybe_report(self, tier: int, nominal_ms: int) -> None:
        """每 ``TICK_METRICS_REPORT_S`` 打一行窗口分位（p50/p99/max + 当前档）。

        标称间隔一起打：p99 与标称的比值就是"tick 被拖慢"的直接证据。
        """
        if not self._filled:
            return
        now = self._clock()
        if now - self._since_report < TICK_METRICS_REPORT_S:
            return
        self._since_report = now
        samples = sorted(self._buf[: self._filled])
        n = len(samples)
        p50 = samples[min(n - 1, int(n * 0.50))]
        p99 = samples[min(n - 1, int(n * 0.99))]
        logger.info("tick 间隔 p50=%.1fms p99=%.1fms max=%.1fms tier=T%d 标称=%dms n=%d",
                    p50, p99, samples[-1], tier, nominal_ms, n)


class TickDriver(QObject):
    """统一 tick 驱动器：独立持有控制器与 tick 时钟，驱动全部已挂载 overlay。"""

    def __init__(self, parent: QObject | None = None, *,
                 clock=time.monotonic) -> None:
        super().__init__(parent)
        self._overlays: list = []
        # 单世界装配（T1/T2：裸 overlay / demo）的一组控制器；None = 未挂
        #（兼容分支走 overlay 钩子）。多屏产品装配不挂这里——每块屏的控制器
        # 归它的宿主（``hosts`` / ``ScreenHost``），见 ``tick_sim``。
        self.behavior = None
        self.collision = None
        self.physics = None
        # 附加仿真控制器（如边缘探头）：tick_sim 尾段、三主控制器之后运行
        # ——姿态类控制器是最终写入口，不能被后续控制器覆盖
        self._extras: list = []
        # 全部 overlay advance 之后的一次性钩子（跨屏路由；见 advance_overlays）
        self._post_advance = None
        # M-1 闲置降档：governor 决策 + 最近帧到达时间（"动画在播"判定）
        self._governor = TickGovernor(clock)
        self._applied_tier = TIER_ACTIVE
        # 当前 QTimer 是否按"满速"应用（PreciseTimer + T0 间隔公式）：
        # T0 恒 True；AC 下的 T1 也 True（电源感知）；电池 T1 / T2 / T3 False
        self._applied_full_speed = True
        self._last_frame_notify: float | None = None
        self._elapsed = QElapsedTimer()
        self._tick_count = 0
        # 空窗停表标记（M12c）：overlay 还在但聚合 sprite 数归零时停表，
        # 有 sprite 加入才恢复（区分「本来就没 start 过」与「被空窗停掉」）
        self._suspended_for_empty = False
        # 锁屏/挂起（O4）：置位期间强制 T3（模拟挂起），恢复时同帧回全速
        self._suspended = False
        # tick 仪表（PET_TICK_METRICS=0 时不建实例；门控在调用点）
        self._metrics = TickMetrics(clock=clock) if TICK_METRICS_ENABLED else None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self.tick_interval_ms(None))
        self._timer.timeout.connect(self.on_tick)

    # ---------------------------------------------------------------- 装配
    def set_controllers(self, behavior=None, collision=None,
                        physics=None) -> None:
        """挂上**单世界**装配的三控制器（T1；多屏产品装配用宿主，见 ``tick_sim``）。

        只覆盖显式传入的项（``None`` = 保持原值），便于测试只替换其中几个。
        """
        if behavior is not None:
            self.behavior = behavior
        if collision is not None:
            self.collision = collision
        if physics is not None:
            self.physics = physics

    @property
    def overlays(self) -> list:
        """已挂载 overlay 的快照（只读；成员生灭经 attach/detach）。"""
        return list(self._overlays)

    def add_extra_controller(self, controller) -> None:
        """挂载附加仿真控制器（如边缘探头世界）：``tick_sim`` 尾段、
        三主控制器之后运行（姿态最终写入口）。幂等。"""
        if controller not in self._extras:
            self._extras.append(controller)

    @property
    def sprites(self) -> list:
        """全部已挂载 overlay 的 sprites 聚合快照（仿真段输入，T2）。"""
        return self._all_sprites()

    @property
    def hosts(self) -> list:
        """带宿主的成员 overlay 的宿主列表（多屏装配；``tick_sim`` 按它分组）。

        没有宿主的 overlay（裸 ``OverlayWindow`` / demo）不在其中，此时仿真段
        走单世界语义（驱动器自持的三控制器）。
        """
        hosts = []
        for overlay in self._overlays:
            host = getattr(overlay, "host", None)
            if host is not None:
                hosts.append(host)
        return hosts

    @property
    def timer(self) -> QTimer:
        """tick 时钟（只读暴露：旧 ``overlay._timer`` 属性面/测试同步驱动）。"""
        return self._timer

    @property
    def applied_tier(self) -> int:
        """当前已应用到 QTimer 的 M-1 档位。"""
        return self._applied_tier

    @property
    def metrics(self) -> TickMetrics | None:
        """tick 仪表实例（``PET_TICK_METRICS=0`` 时为 None）。"""
        return self._metrics

    def attach(self, overlay) -> None:
        """挂载 overlay：其 sprites 进入仿真快照，并收到推进+重绘段。幂等。"""
        if overlay in self._overlays:
            return
        self._overlays.append(overlay)
        self.refresh_tick_interval()

    def detach(self, overlay) -> None:
        """卸载 overlay；再无被驱动对象时停表（V-12 关窗即停）。

        多 overlay 时只摘除成员，时钟继续服务其余成员。
        """
        if overlay not in self._overlays:
            return
        self._overlays.remove(overlay)
        if not self._overlays:
            self._suspended_for_empty = False  # 空窗标记随成员清空失效
            self.stop()
        else:
            self.refresh_tick_interval()

    # ---------------------------------------------------------------- 空窗停表（M12c）
    def note_sprite_count_changed(self) -> None:
        """sprite 生灭通知（``overlay.add_sprite`` / ``remove_sprite`` 调）。

        聚合 sprite 数归零 → 停表（此前只有「再无 overlay」才停，主宠退出/
        子宠全退/角色重建的空窗期 tick 仍在空转）；有 sprite 加入且此前正是
        被空窗停掉的 → ``start()`` 恢复（同步回 T0，不吃停表期的历史流逝）。
        档位机语义不变：恢复走既有 start 路径。
        """
        if self._all_sprites():
            if self._suspended_for_empty and self._overlays:
                self._suspended_for_empty = False
                self.start()
            return
        if self._timer.isActive():
            self._suspended_for_empty = True
            self.stop()

    def note_sprite_visibility_changed(self) -> None:
        """逐只显隐通知（``set_sprite_visible`` / 整窗显隐调；O3）。

        与 ``note_sprite_count_changed`` 同款通知面：可见性变化必须让档位机
        立刻重评，不必等下一个 tick 或一次心跳——

        - 藏光最后一只可见 sprite（或整窗隐藏）⇒ 没有像素要上屏，目标档位
          T3（沿用既有 occluded 分支，降档仍走 ``DOWNGRADE_HOLD_MS`` 滞回，
          可见性瞬态抖动不降档）；
        - 恢复可见 ⇒ 升档同步立即（``note_kinetic`` 同款），恢复显示的首个
          tick 就是全速，不等 T3 心跳（最长 1s 的档位滞后）。
        """
        if self._visible_for_tier():
            self._governor.notify_kinetic()
        self._sync_tier()

    # ---------------------------------------------------------------- 时间基（V-6）
    @staticmethod
    def tick_interval_ms(refresh_rate: float | None) -> int:
        """tick 间隔：rr<75 或无读数 → 16ms；其余按刷新率取整（封顶 16ms，
        170Hz→6ms，90Hz→11ms——旧边界把 90Hz 错打成 16ms，V-6）。"""
        if not refresh_rate or refresh_rate < 75.0:
            return 16
        return max(1, min(16, round(1000.0 / refresh_rate)))

    def _refresh_rate(self) -> float | None:
        """刷新率取数：**全部已连接显示器**里最高的一档。

        单合成窗覆盖整个虚拟桌面，窗口自己那块屏（Qt 按窗口左上角归属）与 pet
        实际所在屏可能不同：按成员屏取数会让一块 60Hz 副屏把整组 tick 拖到低档
        （本机实测主屏 170Hz）。故直接枚举 ``QGuiApplication.screens()``。
        """
        from PySide6.QtGui import QGuiApplication

        best: float | None = None
        for screen in QGuiApplication.screens():
            try:
                rate = float(screen.refreshRate() or 0.0)
            except Exception:
                continue
            if best is None or rate > best:
                best = rate
        return best

    def refresh_tick_interval(self) -> None:
        """重读刷新率并刷新**满速档**（T0；AC 下的 T1）的间隔。

        M-1 语义：只改满速档——电池下 T1 的 42ms / T2 的 250ms / T3 的 1000ms
        由 ``_apply_tier`` 独占，屏事件/几何变化重读时不得把它们打回全速。
        """
        if self._applied_full_speed:
            self._timer.setInterval(self.tick_interval_ms(self._refresh_rate()))

    def _full_speed(self, tier: int) -> bool:
        """该档是否按满速（刷新率）跑：T0 恒是；AC 供电时 T1 也是（M-1 补刀）。

        T2/T3 短路返回 False，不触发电源查询；只有 T1 每 tick 问一次 5s 缓存
        的电源状态（一次时钟比较，无系统调用）。
        """
        if tier == TIER_ACTIVE:
            return True
        return tier == TIER_IDLE_ANIM and self._governor.on_ac_power()

    def _tier_reason(self, *, any_motion: bool, animating: bool,
                     visible: bool) -> str:
        """档位触发因（归因日志用）：与 governor 判定分支一一对应。"""
        if any_motion:
            return "motion"
        if self._governor.kinetic_tail:
            return "kinetic_tail"
        if not visible:
            return "hidden"
        if animating:
            return "animating"
        return "quiet"

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        self._elapsed.start()
        self._governor.notify_kinetic()  # 启动即 T0（首段仿真全速）
        self._sync_tier()
        if not self._all_sprites():
            # 空窗（M12c）：一只 sprite 都没有就不起表，等 note_sprite_count_changed
            self._suspended_for_empty = True
            self._timer.stop()
            return
        self._suspended_for_empty = False
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # ---------------------------------------------------------------- M-1 闲置降档
    @staticmethod
    def _sprite_in_motion(sprite) -> bool:
        """sprite 是否在运动（velocity≠0 或 interaction_state≠normal）。"""
        v = getattr(sprite, "velocity", None)
        is_null = getattr(v, "isNull", None)
        if callable(is_null):
            if not is_null():
                return True
        elif v:
            return True
        return getattr(sprite, "interaction_state", INTERACTION_NORMAL) != INTERACTION_NORMAL

    def _all_sprites(self) -> list:
        """全部已挂载 overlay 的 sprites 聚合（快照：tick 内成员生灭不撕裂）。"""
        sprites: list = []
        for overlay in self._overlays:
            sprites.extend(overlay.sprites)
        return sprites

    @staticmethod
    def _sprite_visible(sprite) -> bool:
        """sprite 逐只可见性；无该字段的鸭式 sprite 按旧语义视为可见。"""
        return bool(getattr(sprite, "visible", True))

    def _visible_for_tier(self) -> bool:
        """"有东西需要上屏吗"（档位判定的可见性输入，O3）。

        口径 = 存在一个可见 overlay 且它上面有可见 sprite。逐只藏光与整窗
        隐藏因此同义：没有任何像素需要上屏（隐藏 sprite 既不被绘制、也不进
        命中与位置 fanout，推进段对它没有可交付的东西）。

        两个兼容口：一只 sprite 都没有的 overlay（空窗，M12c 已停表）无从谈
        "sprite 可见"，退回窗口可见性；无 ``visible`` 字段的鸭式 sprite
        （灰假件/旧装配）沿用窗口可见性。
        """
        if not self._all_sprites():
            return any(o.isVisible() for o in self._overlays)
        return any(
            o.isVisible() and any(self._sprite_visible(s) for s in o.sprites)
            for o in self._overlays
        )

    def _sync_tier(self) -> None:
        """评估目标档位并在变化时应用（升档在 evaluate 内即生效）。"""
        if self._suspended:
            # 锁屏/挂起（O4）：只降档——挂起期间任何活动信号（鼠标、位移、
            # 帧到达）都不许把档位升回去（挂起态下这些都是旧状态的回声）
            if self._applied_tier != TIER_OCCLUDED:
                self._apply_tier(TIER_OCCLUDED, reason="suspended")
            return
        animating = (
            self._last_frame_notify is not None
            and time.monotonic() - self._last_frame_notify < ANIMATING_WINDOW_S
        )
        any_motion = any(self._sprite_in_motion(s) for s in self._all_sprites())
        visible = self._visible_for_tier()
        tier = self._governor.evaluate(
            any_motion=any_motion, animating=animating, visible=visible)
        if tier != self._applied_tier:
            self._apply_tier(tier, reason=self._tier_reason(
                any_motion=any_motion, animating=animating, visible=visible))
        elif self._full_speed(tier) != self._applied_full_speed:
            # 电源切换（AC↔电池）不改档位，但 T1 的有效间隔/定时器类型要跟着切
            # （on_ac_power 自带 5s 缓存，切换感知延迟 ≤5s）
            self._apply_tier(tier, reason="power")

    def _apply_tier(self, tier: int, reason: str = "") -> None:
        """把档位应用到 QTimer：满速档（T0；AC 下 T1）按刷新率 + Precise，
        其余固定间隔 + Coarse。"""
        old_tier = self._applied_tier
        old_full_speed = self._applied_full_speed
        full_speed = self._full_speed(tier)
        self._applied_tier = tier
        self._applied_full_speed = full_speed
        if full_speed:
            self._timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._timer.setInterval(self.tick_interval_ms(self._refresh_rate()))
        else:
            self._timer.setInterval(TIER_INTERVAL_MS[tier])
            self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        if TICK_METRICS_ENABLED and (tier != old_tier or full_speed != old_full_speed):
            if tier != old_tier:
                logger.info("tick 档位 T%d→T%d 触发因=%s interval=%dms（%s）",
                            old_tier, tier, reason or "-", self._timer.interval(),
                            "满速" if full_speed else "降载")
            else:
                logger.info("tick 电源切换：T%d interval=%dms 电源=%s（触发因=power）",
                            tier, self._timer.interval(),
                            "AC" if full_speed else "电池")
        # 切档后首 tick 不吃历史流逝（防 dt 突变一步跨出大位移）
        self._elapsed.restart()

    def note_kinetic(self) -> None:
        """运动/输入信号：升档同步立即，不等下一个 tick（M-1 硬指标——
        高刷体验只在真正闲置时让位，任何活动瞬间回全速）。"""
        self._governor.notify_kinetic()
        self._sync_tier()

    def note_user_input(self) -> None:
        """真实用户输入（按下/右键）的挂起自愈：输入到达即证明用户在屏幕前。

        解锁/恢复消息丢失（或注册因原生窗口重建失效）时，挂起态会把档位钉在
        T3 且逐 sprite 停播放节拍，直到下次显隐。用户点到桌宠就解除挂起——
        优先走宿主注入的 ``on_user_resume``（壳侧同拍续预热），缺省直接
        ``set_suspended(False)``。非挂起态无操作。"""
        if not self._suspended:
            return
        resume = getattr(self, "on_user_resume", None)
        if callable(resume):
            resume()
        else:
            self.set_suspended(False)

    def note_frame(self) -> None:
        """"动画在播"活性输入（纯帧到达、无位移）：tick 末档位评估用。"""
        self._last_frame_notify = time.monotonic()

    # ---------------------------------------------------------------- 锁屏/挂起（O4）
    @property
    def suspended(self) -> bool:
        """是否处于锁屏/挂起（强制 T3）状态。"""
        return self._suspended

    def set_suspended(self, suspended: bool) -> None:
        """锁屏/挂起（O4）：True 强制 T3 并逐 sprite 停播放节拍；False 恢复全速。

        锁屏/挂起时 overlay 仍 ``isVisible()``（窗口没被藏，只是屏幕被锁屏
        遮住），既有档位判定拿不到任何"不可见"信号——AC 供电下会一直停在
        T1 满速、clip 按帧率解码跑整夜。故由会话/电源事件显式置位：

        - True：直接 ``_apply_tier(T3)``（不走 ``DOWNGRADE_HOLD_MS`` 滞回——
          这是用户主动离开的确定性信号，不是抖动）+ 逐 sprite ``pause_clip``
          （只停播放节拍，显示位置原地保留）。
        - False：逐 sprite ``resume_clip`` 续播（**不**回第 0 帧），再
          ``note_kinetic`` 同帧回全速（升档同步立即，首 tick 即满速且不吃
          挂起期流逝）。

        **只降档**：不隐藏窗口、不动全屏/光标 watcher（唤醒后可见性/交互面
        必须原样，否则桌宠会"醒来不回来"）。幂等。
        """
        suspended = bool(suspended)
        if suspended == self._suspended:
            return
        self._suspended = suspended
        if suspended:
            for sprite in self._all_sprites():
                self._pause_sprite_clip(sprite, pause=True)
            self._apply_tier(TIER_OCCLUDED, reason="suspended")
            return
        # 恢复只对"真的能看见"的 sprite：窗口仍被藏（托盘隐藏中解锁）或那一只
        # 被逐只藏起时，播放节拍维持暂停，等显示路径（set_pet_visible /
        # set_sprite_visible）恢复——锁屏不该把隐藏期的暂停悄悄解除。
        if self._visible_for_tier():
            for sprite in self._all_sprites():
                if self._sprite_visible(sprite):
                    self._pause_sprite_clip(sprite, pause=False)
        self.note_kinetic()  # 同帧回全速（并重算档位）

    @staticmethod
    def _pause_sprite_clip(sprite, *, pause: bool) -> None:
        """逐只停/续播放节拍；单只抛错只记 debug，绝不中断整轮。

        幂等守卫（``_suspended`` 相等即早退）意味着这一轮是**唯一**机会：任何
        一只抛错而整轮中断，剩下的 sprite 就带着在跑的定时器过完整个锁屏
        （挂起期整夜按帧率解码），而再次 ``set_suspended(True)`` 直接早退、永不
        补做。抛错不是假想场景——``FrameSeqClip.pause()`` 是裸
        ``timer.stop()``，半销毁（C++ 侧已删）时会抛 RuntimeError。
        """
        name = "pause_clip" if pause else "resume_clip"
        call = getattr(sprite, name, None)
        if not callable(call):
            return
        try:
            call()
        except Exception:
            logger.debug("tick: sprite.%s 失败（继续处理其余 sprite）", name, exc_info=True)

    # ---------------------------------------------------------------- 统一 tick
    def on_tick(self, dt: float | None = None) -> None:
        """一次 tick：仿真段 → 各 overlay 的推进+重绘段（M-2 两段拆分）。

        ``dt=None`` 时自算（首 tick 取档位间隔；其后取实测流逝并按档钳上限，
        切档瞬间不吃历史流逝）。
        """
        metrics = self._metrics if TICK_METRICS_ENABLED else None
        if self._applied_tier == TIER_OCCLUDED:
            # T3 心跳：不跑仿真，只复查档位（可见性/刷新率变化经 showEvent
            # 与本路径恢复）；1000ms 的刻意见隔不进分位窗口（不是卡顿信号）
            if metrics is not None:
                metrics.skip_tick_baseline()
            self._elapsed.restart()
            self._sync_tier()
            return
        if metrics is not None:
            gap_ms = metrics.note_tick_start()
            if gap_ms is not None:
                metrics.note_interval(gap_ms)
        if dt is None:
            if self._tick_count:
                # dt 上限按档钳：T0 50ms，低档放宽到 2× 档间隔
                cap = max(2.0 * self._timer.interval() / 1000.0, 0.05)
                dt = min(cap, self._elapsed.nsecsElapsed() / 1e9)
            else:
                dt = self._timer.interval() / 1000.0
            self._elapsed.restart()
        self._tick_count += 1
        for overlay in list(self._overlays):
            overlay._check_stale_press()  # V-7：tick 路径同样兜底卡死的 grab
        work_start = time.perf_counter() if metrics is not None else 0.0
        self.tick_sim(dt)
        self.advance_overlays(dt)
        if metrics is not None:
            metrics.note_slow_tick((time.perf_counter() - work_start) * 1000.0,
                                   self._applied_tier)
            # 分位行在档位复评之前打：窗口样本是"本 tick 所在档"的读数，
            # 不能拿 tick 末尾刚降下去的档去标注（归因会指错方向）
            metrics.maybe_report(self._applied_tier, self._timer.interval())
        self._sync_tier()  # M-1：tick 末评估降档（升档在事件/回调侧同步完成）

    def tick_sim(self, dt: float) -> None:
        """仿真段：行为 → 碰撞 → 抛掷物理（tick 顺序协议固定，T2）。

        带宿主（多屏装配）时**按宿主分组**：同一 tick 内每块屏只跑自己那份
        控制器与自己的成员，不同屏的局部坐标绝不进同一个世界。宿主之外的
        附加控制器（``_extras``，如唱歌续播链/联动链）是壳级、与几何无关的，
        仍拿全部 sprite 跑一次。

        没有宿主时成员 = 全部已挂载 overlay 的 sprites 聚合；三控制器由驱动器
        持有，不再经主 overlay 的 ``before_sprites_advance`` 钩子。

        兼容期（deprecation）：三控制器都没挂且没有带宿主的成员时（裸 overlay /
        demo 装配），仿真段退回 ``overlay.before_sprites_advance(dt)`` 钩子，保证
        旧装配可用；产品壳走宿主分组，不会走这条分支。
        """
        hosts = self.hosts
        if hosts:
            for host in hosts:
                host.tick(dt)
            for ctrl in self._extras:
                ctrl.tick(self._all_sprites(), dt)
            return
        if self.behavior is None and self.collision is None and self.physics is None:
            for overlay in list(self._overlays):
                overlay.before_sprites_advance(dt)
            return
        sprites = self._all_sprites()
        if self.behavior is not None:
            self.behavior.tick(sprites, dt)
        if self.collision is not None:
            self.collision.tick(sprites, dt)
        if self.physics is not None:
            self.physics.tick(sprites, dt)
        for ctrl in self._extras:
            ctrl.tick(sprites, dt)

    def set_post_advance(self, cb) -> None:
        """挂上"全部 overlay advance 之后"的一次性钩子（跨屏路由用；None 摘除）。

        跨屏路由必须放在这里：拖动物理（弹簧档）的**实际位移发生在 sprite.advance**
        里，事件时刻读到的位置还没动——按事件时刻路由会在身体真正过缝之前提前移交
        （旧实现的大跳来源）。放在 advance 之后，每 tick 读到的就是这一拍各 sprite
        的最终位置，且**每 tick 每 sprite 只判一次**。
        """
        self._post_advance = cb

    def advance_overlays(self, dt: float) -> None:
        """推进+重绘段：逐 overlay 走 advance/脏矩形/位置 fanout/update。

        收尾调用 ``_post_advance``（若挂了）：此刻全部 overlay 的 advance 都已跑完，
        sprite 位置是本 tick 的最终值。
        """
        for overlay in list(self._overlays):
            overlay.tick_advance(dt)
        if self._post_advance is not None:
            self._post_advance(dt)
