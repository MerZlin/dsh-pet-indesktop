# -*- coding: utf-8 -*-
"""M-2 统一 tick 驱动器回归（QT_QPA_PLATFORM=offscreen 可跑）。

覆盖（REVIEW_VERDICT.md M-2 / 设计稿 PHASE4_DESIGN.md T2）：
- tick_sim 顺序协议（行为→碰撞→物理）与**成员快照完整性**：仿真成员聚合自
  全部已挂载 overlay（多 overlay 预留；单 overlay 时等价于该 overlay.sprites）；
- advance+paint 段：驱动器逐个 overlay 调 ``tick_advance``，advance/脏矩形/
  位置监听 fanout/update 仍在 overlay 侧（V-3/V-4 语义不变）；
- 驱动器独立持有控制器：已挂控制器时**不再**调 ``overlay.before_sprites_advance``
  （无双份仿真）；未挂控制器时走兼容分支（demo/裸 overlay 旧装配）；
- 生命周期：attach 幂等；detach 最后一个成员停表（V-12），多成员时其余成员
  继续被驱动；overlay.start() 在 stop() 后能重新挂回；
- 时间基：首 tick 取档位间隔、显式 dt 直通、T3 心跳不跑仿真；
- M-1：升档同步立即（note_kinetic）、note_frame 喂"动画在播"、
  刷新率取**屏枚举**里最高的一档（单合成窗铺满整个虚拟桌面，成员增减不再是
  取数口径）、refresh 不覆盖非 T0 档位间隔；
- V-7：tick 路径逐 overlay 兜底卡死拖拽。

纪律（AGENTS.md 时序测试）：同步直调 ``on_tick(dt=...)``，不启动真实 QTimer、
不 sleep 赌时序；假 overlay/假 sprite 纯鸭式，不碰素材与 ffmpeg。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication

from pet.overlay_window import OverlayWindow
from pet.tick_driver import TickDriver
from pet.tick_governor import (
    TIER_ACTIVE,
    TIER_IDLE_STILL,
    TIER_INTERVAL_MS,
    TIER_OCCLUDED,
)

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 假件
class FakeScreen:
    """鸭式 QScreen：只需 refreshRate（驱动器取数口）。"""

    def __init__(self, refresh=60.0):
        self._refresh = float(refresh)

    def refreshRate(self):
        return self._refresh


class FakeSprite:
    """鸭式 sprite：只给 advance/rect，够跑推进+脏矩形段。"""

    SIZE = (40, 30)

    def __init__(self, pos=(10, 10), *, movable=False, velocity=(0.0, 0.0)):
        self.pos = QPointF(*pos)
        self.velocity = QPointF(*velocity)
        self.interaction_state = "normal"
        self.movable = movable

    def rect(self):
        return QRect(int(self.pos.x()), int(self.pos.y()), *self.SIZE)

    def advance(self, dt):
        old = self.rect()
        if self.movable:
            self.pos += self.velocity * dt
        new = self.rect()
        return (old, new) if new != old else None


class FakeOverlay:
    """鸭式 overlay：记录驱动器要求的两段调用（仿真钩子 + 推进段）。"""

    def __init__(self, sprites=None, *, screen=None, visible=True):
        self.sprites = list(sprites or [])
        self.screen = screen
        self._visible = visible
        self.hook_calls: list[float] = []
        self.advance_calls: list[float] = []
        self.stale_checks = 0

    def isVisible(self):
        return self._visible

    def before_sprites_advance(self, dt):
        self.hook_calls.append(dt)

    def tick_advance(self, dt):
        self.advance_calls.append(dt)

    def _check_stale_press(self):
        self.stale_checks += 1


class Recorder:
    """记录 tick(sprites, dt) 的控制器替身。"""

    def __init__(self, name, calls):
        self.name = name
        self._calls = calls

    def tick(self, sprites, dt):
        self._calls.append((self.name, list(sprites), dt))


class PausableSprite(FakeSprite):
    """带 ``visible`` / ``pause_clip`` / ``resume_clip`` 的 sprite（O3 档位与播放节拍）。

    ``FakeSprite`` 本身没有 ``visible``（鸭子 sprite 恒可见的旧语义由它覆盖），
    这里按 PetSprite 的真实面扩展一条。
    """

    def __init__(self, pos=(10, 10), **kwargs):
        super().__init__(pos, **kwargs)
        self.visible = True
        self.pause_calls = 0
        self.resume_calls = 0
        #: 假装"已在播第 7 帧"：恢复时不许回落 0（详见 PetSprite/FrameSeqClip 用例）
        self.clip_frame = 7

    def set_visible(self, visible):
        self.visible = bool(visible)

    def pause_clip(self):
        self.pause_calls += 1

    def resume_clip(self):
        self.resume_calls += 1


class FakeClock:
    """可推进的假钟（档位滞回/电源感知的时钟注入面）。"""

    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


# ---------------------------------------------------------------- 两段拆分与顺序协议
def test_tick_sim_order_and_members_aggregate_across_overlays():
    """T2：成员快照聚合自全部 overlay；顺序协议行为→碰撞→物理不变。"""
    driver = TickDriver()
    s1, s2, s3 = FakeSprite(), FakeSprite(), FakeSprite()
    first = FakeOverlay([s1, s2], screen=FakeScreen(60.0))
    second = FakeOverlay([s3], screen=FakeScreen(170.0))
    driver.attach(first)
    driver.attach(second)

    calls: list = []
    driver.set_controllers(Recorder("behavior", calls), Recorder("collision", calls),
                           Recorder("physics", calls))
    driver.on_tick(dt=0.02)

    assert [c[0] for c in calls] == ["behavior", "collision", "physics"]
    for _name, sprites, dt in calls:
        assert sprites == [s1, s2, s3]      # 两个 overlay 的成员都在（聚合完整）
        assert dt == 0.02
    assert driver.sprites == [s1, s2, s3]
    assert driver.overlays == [first, second]
    # 推进段逐 overlay 各一次（同一 dt）
    assert first.advance_calls == [0.02]
    assert second.advance_calls == [0.02]
    # V-7：tick 路径兜底拖拽看门狗逐 overlay 走到
    assert (first.stale_checks, second.stale_checks) == (1, 1)


def test_controllers_attached_means_no_compat_hook_call():
    """M-2：已挂控制器 = 仿真段唯一入口在驱动器，钩子不得再被调用（无双份）。"""
    driver = TickDriver()
    overlay = FakeOverlay(screen=FakeScreen(60.0))
    driver.attach(overlay)
    calls: list = []
    driver.set_controllers(Recorder("behavior", calls), Recorder("collision", calls),
                           Recorder("physics", calls))

    driver.on_tick(dt=0.016)

    assert overlay.hook_calls == []
    assert [c[0] for c in calls] == ["behavior", "collision", "physics"]
    assert overlay.advance_calls == [0.016]


def test_partial_controllers_also_skip_compat_hook():
    """只挂一个控制器（测试/子集装配）也不算兼容分支：不回调钩子。"""
    driver = TickDriver()
    overlay = FakeOverlay(screen=FakeScreen(60.0))
    driver.attach(overlay)
    calls: list = []
    driver.set_controllers(behavior=Recorder("behavior", calls))

    driver.on_tick(dt=0.016)

    assert overlay.hook_calls == []
    assert [c[0] for c in calls] == ["behavior"]


def test_no_controllers_uses_deprecated_hook_compat_path():
    """兼容期（deprecation）：裸 overlay / demo 旧装配仍走钩子作仿真段。"""
    driver = TickDriver()
    overlay = FakeOverlay(screen=FakeScreen(60.0))
    driver.attach(overlay)

    driver.on_tick(dt=0.016)

    assert overlay.hook_calls == [0.016]
    assert overlay.advance_calls == [0.016]


# ---------------------------------------------------------------- 生命周期与成员语义
def test_attach_is_idempotent_and_detach_order_independent():
    driver = TickDriver()
    first = FakeOverlay(screen=FakeScreen(60.0))
    second = FakeOverlay(screen=FakeScreen(60.0))
    driver.attach(first)
    driver.attach(first)                     # 重复挂载是 no-op
    driver.attach(second)
    assert driver.overlays == [first, second]

    driver.detach(second)
    assert driver.overlays == [first]
    driver.detach(second)                    # 重复摘除是 no-op
    assert driver.overlays == [first]


def test_last_detach_stops_timer_others_keep_ticking():
    """V-12 + 多成员：摘最后一个才停表，其余成员继续被驱动。"""
    driver = TickDriver()
    # M12c：空 overlay 不起表——每个成员都带一只 sprite（聚合数非 0）
    first = FakeOverlay([FakeSprite()], screen=FakeScreen(60.0))
    second = FakeOverlay([FakeSprite()], screen=FakeScreen(60.0))
    driver.attach(first)
    driver.attach(second)
    driver.start()
    assert driver.timer.isActive()

    driver.detach(first)
    assert driver.timer.isActive()           # 还有成员：不停表
    driver.on_tick(dt=0.016)
    assert second.advance_calls == [0.016]
    assert first.advance_calls == []         # 摘除的成员不再被推进

    driver.detach(second)
    assert not driver.timer.isActive()       # 最后一个摘除 → 停表


def test_overlay_start_reattaches_after_stop():
    """closeEvent（detach 语义）+ stop() 后，start() 必须重新挂回驱动器。"""
    from PySide6.QtGui import QCloseEvent

    driver = TickDriver()
    overlay = OverlayWindow(driver=driver)
    overlay.add_sprite(FakeSprite())          # M12c：空 overlay 不起表
    overlay.start()
    assert driver.timer.isActive() and driver.overlays == [overlay]

    overlay.closeEvent(QCloseEvent())         # 关窗 → 从驱动器摘除
    assert driver.overlays == []
    assert not driver.timer.isActive()        # 无成员 → 停表（V-12）

    overlay.start()                           # 重启：幂等挂回并复走时钟
    assert driver.overlays == [overlay]
    assert driver.timer.isActive()
    driver.stop()


# ---------------------------------------------------------------- 时间基
def test_first_tick_uses_tier_interval_and_explicit_dt_passes_through():
    driver = TickDriver()
    overlay = FakeOverlay(screen=FakeScreen(60.0))
    driver.attach(overlay)

    driver.on_tick()                          # 首 tick：dt = 档位间隔（16ms）
    assert overlay.hook_calls == [pytest.approx(driver.timer.interval() / 1000.0)]

    driver.on_tick(dt=0.5)                    # 显式 dt 直通（测试同步驱动口）
    assert overlay.hook_calls[-1] == 0.5


def test_occluded_heartbeat_runs_no_simulation():
    driver = TickDriver()
    overlay = FakeOverlay(screen=FakeScreen(60.0), visible=False)
    driver.attach(overlay)
    driver._apply_tier(TIER_OCCLUDED)         # T3 心跳档

    driver.on_tick(dt=0.016)

    assert overlay.hook_calls == []
    assert overlay.advance_calls == []


# ---------------------------------------------------------------- M-1 挂钩（驱动器侧）
def test_note_kinetic_upgrades_synchronously():
    driver = TickDriver()
    overlay = FakeOverlay(screen=FakeScreen(60.0))
    driver.attach(overlay)
    driver._apply_tier(TIER_IDLE_STILL)

    driver.note_kinetic()

    assert driver.applied_tier == TIER_ACTIVE
    assert driver.timer.timerType() == Qt.TimerType.PreciseTimer


def test_note_frame_feeds_animating_input_and_any_motion_covers_all_overlays(monkeypatch):
    driver = TickDriver()
    still = FakeOverlay([FakeSprite()], screen=FakeScreen(60.0))
    moving_sprite = FakeSprite(velocity=(120.0, 0.0))
    moving = FakeOverlay([moving_sprite], screen=FakeScreen(60.0))
    driver.attach(still)
    seen: dict = {}

    def fake_evaluate(*, any_motion, animating, visible):
        seen.update(any_motion=any_motion, animating=animating, visible=visible)
        return TIER_ACTIVE

    monkeypatch.setattr(driver._governor, "evaluate", fake_evaluate)
    driver.note_frame()
    driver._sync_tier()
    assert seen["animating"] is True          # 帧活性喂进"动画在播"
    assert seen["visible"] is True

    seen.clear()
    driver.attach(moving)                     # 第二个 overlay 的成员也要计入
    driver._sync_tier()
    assert seen["any_motion"] is True         # M-1 不变量：任一成员在动 ⇒ 必 T0


def test_refresh_uses_the_fastest_enumerated_screen(monkeypatch):
    """刷新率 = 屏枚举里最高一档（与已挂载成员无关）。

    单合成窗覆盖整个虚拟桌面：窗口自己那块屏（Qt 按窗口左上角归属）不等于宠所在
    屏，所以取数改读 ``QGuiApplication.screens()``——一块 60Hz 副屏不该把整组
    tick 拖到低档。
    """
    from PySide6.QtGui import QGuiApplication

    slow = FakeScreen(60.0)
    fast = FakeScreen(170.0)
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [slow]))
    driver = TickDriver()
    driver.attach(FakeOverlay(screen=slow))
    assert driver.timer.interval() == TickDriver.tick_interval_ms(60.0)

    monkeypatch.setattr(QGuiApplication, "screens",
                        staticmethod(lambda: [slow, fast]))
    driver.refresh_tick_interval()

    assert driver.timer.interval() == TickDriver.tick_interval_ms(170.0)  # 6ms


def test_refresh_does_not_clobber_downgraded_interval():
    """M-1：屏事件重读刷新率不得把降档间隔（250ms）打回全速。"""
    driver = TickDriver()
    driver.attach(FakeOverlay(screen=FakeScreen(170.0)))
    driver._apply_tier(TIER_IDLE_STILL)

    driver.refresh_tick_interval()

    assert driver.timer.interval() == TIER_INTERVAL_MS[TIER_IDLE_STILL]


def test_tick_interval_boundaries_shared_with_overlay():
    assert TickDriver.tick_interval_ms(170.0) == 6
    assert TickDriver.tick_interval_ms(90.0) == 11
    assert TickDriver.tick_interval_ms(60.0) == 16
    assert TickDriver.tick_interval_ms(None) == 16
    assert OverlayWindow._tick_interval_ms(144.0) == TickDriver.tick_interval_ms(144.0)


# ---------------------------------------------------------------- 真实 overlay 端到端
class RecordingOverlay(OverlayWindow):
    """把 update() 的累加区域记下来，不做真实绘制调度。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.updated = QRegion()

    def update(self, *args):  # noqa: D102 - 测试替身
        for arg in args:
            if isinstance(arg, QRegion):
                self.updated |= arg
            elif isinstance(arg, QRect):
                self.updated |= QRegion(arg)


def test_driver_tick_advances_real_overlay_with_dirty_rect_and_fanout():
    """端到端：驱动器 tick → overlay.tick_advance → 脏矩形盖旧|新 rect + 位置 fanout。"""
    driver = TickDriver()
    overlay = RecordingOverlay(driver=driver)
    sprite = FakeSprite(pos=(10, 10), movable=True, velocity=(100.0, 0.0))
    overlay.add_sprite(sprite)
    seen: list = []
    overlay.add_position_listener(sprite, seen.append)

    old = sprite.rect()
    driver.on_tick(dt=0.1)

    new = sprite.rect()
    assert new != old
    assert QRegion(old).subtracted(overlay.updated).isEmpty()
    assert QRegion(new).subtracted(overlay.updated).isEmpty()
    assert seen == [sprite]                   # 位置 fanout 在推进段之后
    # 未挂控制器的兼容分支：推进段照跑（无仿真也推进画面）
    assert overlay._timer is driver.timer


# ---------------------------------------------------------------- O3 逐只显隐 → 档位
def _pause_driver_with(clock, sprites, *, visible=True):
    """假钟驱动器 + 一个装着给定 sprite 的可见 overlay（档位用例的统一装配）。

    ``start()`` 自带一次 kinetic 尾巴（M-1：启动即 T0），故先把假钟推过
    ``ACTIVE_HOLD_MS``，让后续的可见性判定从"真正的静默态"出发。
    """
    driver = TickDriver(clock=clock)
    overlay = FakeOverlay(sprites, screen=FakeScreen(60.0), visible=visible)
    driver.attach(overlay)
    driver.start()
    clock.advance(1.0)
    return driver, overlay


def _hide_and_settle(driver, clock, sprite):
    """藏一只并推过 kinetic 尾巴 + 降档滞回（确定性，不 sleep 赌时序）。"""
    clock.advance(1.0)                    # 先清掉上一次可见性通知留下的 kinetic 尾巴
    sprite.set_visible(False)
    driver.note_sprite_visibility_changed()   # 起降档静默计时
    clock.advance(1.0)                        # 过 DOWNGRADE_HOLD_MS=800
    driver.note_sprite_visibility_changed()   # 静默够 → 落到目标档


def test_zero_visible_sprites_targets_occluded_tier():
    """逐只藏光 = 没有像素要上屏：目标档位 T3（沿用既有 occluded 分支+滞回）。"""
    clock = FakeClock()
    sprite = PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [sprite])
    calls: list = []
    driver.set_controllers(Recorder("behavior", calls))
    assert driver.applied_tier == TIER_ACTIVE

    _hide_and_settle(driver, clock, sprite)

    assert driver.applied_tier == TIER_OCCLUDED
    assert driver.timer.interval() == TIER_INTERVAL_MS[TIER_OCCLUDED]
    assert driver.timer.timerType() == Qt.TimerType.CoarseTimer

    calls.clear()
    driver.on_tick(dt=0.016)
    assert calls == [], "T3 心跳不跑仿真（与窗口不可见同一条分支）"


def test_one_visible_sprite_keeps_simulation_running():
    """聚合口径：只要还剩一只可见 sprite，就不许降到 T3。"""
    clock = FakeClock()
    first, second = PausableSprite(), PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [first, second])
    calls: list = []
    driver.set_controllers(Recorder("behavior", calls))

    _hide_and_settle(driver, clock, first)
    assert driver.applied_tier != TIER_OCCLUDED, "还有一只可见：不许 T3"

    _hide_and_settle(driver, clock, second)
    assert driver.applied_tier == TIER_OCCLUDED


def test_window_invisible_is_still_occluded_with_visible_sprites():
    """窗口不可见（整窗隐藏）语义不变：sprite 标志为真也照样 T3。"""
    clock = FakeClock()
    driver, _overlay = _pause_driver_with(clock, [PausableSprite()], visible=False)

    driver.note_sprite_visibility_changed()
    clock.advance(1.0)
    driver.note_sprite_visibility_changed()
    assert driver.applied_tier == TIER_OCCLUDED


def test_duck_sprite_without_visible_field_keeps_window_only_rule():
    """无 ``visible`` 字段的鸭子 sprite（含灰假件）仍是"窗口可见即可见"。"""
    clock = FakeClock()
    driver, _overlay = _pause_driver_with(clock, [FakeSprite()])

    driver.note_sprite_visibility_changed()
    clock.advance(1.0)
    driver.note_sprite_visibility_changed()
    assert driver.applied_tier != TIER_OCCLUDED


class FakeElapsed:
    """假 QElapsedTimer：注入"距上次 tick 的流逝"，restart 记次并清零。"""

    def __init__(self, seconds=0.0):
        self.seconds = float(seconds)
        self.restarts = 0

    def nsecsElapsed(self):
        return int(self.seconds * 1e9)

    def restart(self):
        self.seconds = 0.0
        self.restarts += 1

    def start(self):
        self.restart()


def test_visibility_restore_is_immediate_full_speed_without_dt_jump():
    """恢复可见：升档同步立即（首个 tick 即全速），且不吃降档期历史流逝。"""
    clock = FakeClock()
    sprite = PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [sprite])
    calls: list = []
    driver.set_controllers(Recorder("behavior", calls))
    _hide_and_settle(driver, clock, sprite)
    assert driver.applied_tier == TIER_OCCLUDED

    # 假装隐藏期已过去 5s（真实流逝用 _elapsed 注入，不 sleep 赌时序）
    driver._tick_count = 1
    driver._elapsed = FakeElapsed(5.0)

    sprite.set_visible(True)
    driver.note_sprite_visibility_changed()

    assert driver.applied_tier == TIER_ACTIVE, "恢复可见必须同步回 T0，不等心跳"
    assert driver.timer.interval() == TickDriver.tick_interval_ms(60.0)
    assert driver._elapsed.restarts == 1, "切档必须丢弃历史流逝（首 tick 不吃大 dt）"
    assert calls == [], "恢复通知本身不跑仿真（下一次 tick 才跑）"

    driver.on_tick()
    assert calls, "恢复后第一次 tick 必须真的跑仿真"
    assert calls[0][2] == 0.0, "首 tick 的 dt 只能是 restart 之后的流逝，不含隐藏期"


# ---------------------------------------------------------------- O4 锁屏/挂起降档
def test_set_suspended_forces_occluded_and_pauses_every_clip():
    clock = FakeClock()
    sprite = PausableSprite(movable=True, velocity=(100.0, 0.0))
    driver, _overlay = _pause_driver_with(clock, [sprite])
    calls: list = []
    driver.set_controllers(Recorder("behavior", calls))

    driver.set_suspended(True)

    assert driver.applied_tier == TIER_OCCLUDED, "挂起强制 T3（不等滞回）"
    assert driver.timer.interval() == TIER_INTERVAL_MS[TIER_OCCLUDED]
    assert sprite.pause_calls == 1

    # 只降档：挂起期间的任何活动信号都不许把档位升回去
    driver.note_kinetic()
    assert driver.applied_tier == TIER_OCCLUDED
    driver.on_tick(dt=0.016)
    assert calls == [], "挂起期 tick 只复查档位，不跑仿真"


def test_set_suspended_false_restores_full_speed_and_resumes_clips():
    clock = FakeClock()
    sprite = PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [sprite])
    driver.set_suspended(True)
    clock.advance(3600.0)                     # 锁屏一整夜
    driver.set_suspended(False)

    assert driver.applied_tier == TIER_ACTIVE, "解锁同帧回全速"
    assert driver.timer.interval() == TickDriver.tick_interval_ms(60.0)
    assert sprite.resume_calls == 1
    assert sprite.pause_calls == 1, "重复 set_suspended(False) 不重复恢复"

    driver.set_suspended(False)
    assert sprite.resume_calls == 1


def test_suspend_resume_keeps_individually_hidden_sprite_paused():
    """逐只藏起的那只不因"解锁"复活：它的暂停理由是可见性，与挂起无关。"""
    clock = FakeClock()
    visible, hidden = PausableSprite(), PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [visible, hidden])
    hidden.set_visible(False)

    driver.set_suspended(True)
    driver.set_suspended(False)

    assert visible.resume_calls == 1
    assert hidden.resume_calls == 0, "隐藏那只的播放节拍必须保持暂停"
    assert hidden.visible is False


def test_suspend_resume_keeps_clips_paused_while_window_still_hidden():
    """窗口仍被藏（托盘隐藏中解锁）：解锁不得把隐藏期的暂停解除。"""
    clock = FakeClock()
    sprite = PausableSprite()
    driver = TickDriver(clock=clock)
    overlay = FakeOverlay([sprite], screen=FakeScreen(60.0), visible=False)
    driver.attach(overlay)
    driver.start()
    clock.advance(1.0)

    driver.set_suspended(True)
    driver.set_suspended(False)

    assert sprite.pause_calls == 1
    assert sprite.resume_calls == 0, "窗口不可见：播放节拍维持暂停"

    overlay._visible = True
    driver.note_sprite_visibility_changed()
    driver.set_suspended(False)          # 幂等（已不在挂起态）
    assert sprite.resume_calls == 0, "恢复可见走显示路径，不靠解锁重复恢复"


def test_set_suspended_is_idempotent():
    clock = FakeClock()
    sprite = PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [sprite])

    driver.set_suspended(True)
    driver.set_suspended(True)
    assert sprite.pause_calls == 1

    driver.set_suspended(False)
    driver.set_suspended(False)
    assert sprite.resume_calls == 1


class ExplodingClipSprite(PausableSprite):
    """播放节拍接口抛错的 sprite（半销毁 clip：``timer.stop()`` 打到已删对象）。

    ``explode_on`` = "pause"/"resume"：只有那一侧抛错，另一侧照常计数。
    """

    def __init__(self, explode_on="pause", **kwargs):
        super().__init__(**kwargs)
        self._explode_on = explode_on

    def pause_clip(self):
        if self._explode_on == "pause":
            raise RuntimeError("clip 已半销毁")
        super().pause_clip()

    def resume_clip(self):
        if self._explode_on == "resume":
            raise RuntimeError("clip 已半销毁")
        super().resume_clip()


def test_set_suspended_pauses_every_sprite_despite_one_error():
    """逐只容错：一只 ``pause_clip`` 抛错不得中断整轮。

    幂等守卫（``_suspended`` 已置真）意味着这一轮是**唯一**机会：中断后剩下的
    sprite 会带着在跑的定时器过完整个锁屏（挂起期整夜按帧率解码），而再次
    ``set_suspended(True)`` 直接早退，永远不会补做。
    """
    clock = FakeClock()
    first, broken, last = PausableSprite(), ExplodingClipSprite("pause"), PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [first, broken, last])

    driver.set_suspended(True)

    assert first.pause_calls == 1
    assert last.pause_calls == 1, "后面那只必须照常停表"
    assert driver.suspended is True
    assert driver.applied_tier == TIER_OCCLUDED


def test_set_suspended_resumes_every_sprite_despite_one_error():
    """恢复侧同款容错：一只 ``resume_clip`` 抛错不得让其余 sprite 永久停摆。"""
    clock = FakeClock()
    first, broken, last = PausableSprite(), ExplodingClipSprite("resume"), PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [first, broken, last])

    driver.set_suspended(True)
    driver.set_suspended(False)

    assert first.resume_calls == 1
    assert last.resume_calls == 1, "后面那只必须照常续播"
    assert driver.suspended is False


def test_user_input_self_heals_stuck_suspend():
    """解锁消息丢失：用户点到桌宠即解除挂起（否则会钉在 T3 + 停播到下次显隐）。"""
    clock = FakeClock()
    sprite = PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [sprite])
    driver.set_suspended(True)
    assert driver.applied_tier == TIER_OCCLUDED

    driver.note_user_input()

    assert driver.suspended is False
    assert driver.applied_tier == TIER_ACTIVE
    assert sprite.resume_calls >= 1


def test_user_input_prefers_host_resume_hook_and_is_noop_when_awake():
    clock = FakeClock()
    sprite = PausableSprite()
    driver, _overlay = _pause_driver_with(clock, [sprite])
    hooked: list = []
    driver.on_user_resume = lambda: (hooked.append(1), driver.set_suspended(False))

    driver.note_user_input()
    assert hooked == [], "非挂起态不应触发恢复"

    driver.set_suspended(True)
    driver.note_user_input()
    assert hooked == [1]
    assert driver.suspended is False
