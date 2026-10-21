# -*- coding: utf-8 -*-
"""飞行期气泡门禁（B1）+ 起飞预热去重（B2）回归。

用户需求：鱼飞行过程中不弹气泡；被撞飞瞬间若已有气泡立即关闭；飞行结束后
提醒队列正常恢复。

缺口成因：气泡入口里有一半直写控件（提醒队列 / 自言自语 / 节日播报 / 语音
报时 / 识屏答复），它们只读设置页抑制位 ``_bubble_suppressed``——而那个位同时
是 proactive/联动节流信号（multi_window_shared / agent_link / todo_reminder
读它），飞行期置真会把主动搭话与联动一起掐掉。故飞行态另开一位：行为层报
边沿（``BehaviorController.on_flight_changed``），壳按 sprite 维护飞行集合，
全部气泡入口统一读 ``OverlayShell._bubble_blocked(sprite)``。

覆盖：
1. 起飞边沿收气泡、飞行期各入口（show_bubble / 自言自语 / 提醒 / 节日播报 /
   语音报时）全部不弹，且提醒入队不丢、落地照常弹出（含粘滞审批提醒重挂）；
2. 按 sprite 判定：飞的那只禁泡，其他宠（含子宠自言自语宿主）不受影响；
3. 起飞预热同一飞行窗口只向 ``_WARM_EXECUTOR`` 提交一次（B2），落地复位、
   下一次飞行照旧预热（去重只覆盖本次飞行，不是永久跳过）。

纪律：offscreen；真壳 + 真 ``PetSpeechBubble`` + 真行为控制器，飞行状态由
``sprite.interaction_state`` + ``behavior.tick`` 驱动（与 TickDriver 同一条
真路径）；不 sleep 赌时序。唯一替身是跨线程的 ``_WARM_EXECUTOR``（记录型，
同 ``test_voice_chime_service._WorkerSpy`` 口径）——线程边界不可确定性复现。
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import pet.sprite_behavior as sprite_behavior_mod
import tests.test_sprite_behavior as beh
import tests.test_sprite_menu_facade as fac
from pet.festival.service import FestivalReminderService
from pet.pet_sprite import INTERACTION_NORMAL, INTERACTION_THROWN
from pet.sprite_behavior import STATE_IDLE, STATE_THROWN, BehaviorController
from pet.voice.chime_service import VoiceChimeService

app = QApplication.instance() or QApplication([])

DT = 0.016


# ---------------------------------------------------------------- 脚手架
def _make_shell(tmp_path):
    """真壳 + 真 PetSprite + 真气泡（``isVisible`` 是气泡入口的第一道门）。"""
    shell, lib = fac._make_shell(tmp_path)
    shell.overlay.show()
    app.processEvents()
    return shell, lib


def _tick(shell, sprite):
    """行为段推进（TickDriver.tick_sim 里的 behavior 那一段，同一入口）。"""
    shell.behavior.tick([sprite], DT)


def _take_off(shell, sprite=None):
    """被撞飞：先接管进待机（首次 tick），再由物理侧置 thrown 走真边沿。"""
    sprite = shell.sprite if sprite is None else sprite
    _tick(shell, sprite)
    sprite.interaction_state = INTERACTION_THROWN
    _tick(shell, sprite)


def _land(shell, sprite=None):
    """落地：物理收尾写回 normal，tick 里走飞行落地边沿。"""
    sprite = shell.sprite if sprite is None else sprite
    sprite.interaction_state = INTERACTION_NORMAL
    _tick(shell, sprite)


# ---------------------------------------------------------------- B1：起飞收气泡
def test_flight_bubble_closed_on_takeoff(tmp_path):
    """被撞飞瞬间：嘴边已有的气泡立即关闭（不是等它自己超时）。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.show_bubble("在吗")
        assert shell._speech_bubble.isVisible() is True
        assert shell._bubble_blocked() is False

        _take_off(shell)

        assert shell._sprite_in_flight() is True, "飞行集合必须记上这只"
        assert shell._speech_bubble.isVisible() is False, "被撞飞瞬间必须收掉当前气泡"
    finally:
        shell._delete_runtime_marker()


def test_flight_bubble_entries_blocked_for_flying_sprite(tmp_path):
    """飞行期：show_bubble / 自言自语 / 识屏答复都不得换掉当前气泡内容。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.show_bubble("起飞前")
        _take_off(shell)

        shell.show_bubble("飞行中不该出现")
        assert shell._show_self_talk_text("飞行中自言自语") is False
        assert shell._show_random_self_talk() is False
        _tick(shell, shell.sprite)  # 飞行中继续 tick：门禁不随时间松掉

        assert shell._speech_bubble._raw_text == "起飞前", "飞行期气泡文案一字不动"
        assert shell._speech_bubble.isVisible() is False
    finally:
        shell._delete_runtime_marker()


def test_flight_bubble_alert_deferred_then_pumped_after_landing(tmp_path):
    """飞行期提醒不弹但不丢：入队等着，落地走恢复分支正常 pump。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        _take_off(shell)
        shell.show_alert("落地后弹我", sticky=False, alert_type="watchdog")

        assert shell._alert_current is None, "飞行期不得弹提醒"
        assert [item["text"] for item in shell._alert_queue] == ["落地后弹我"], \
            "飞行期提醒入队，绝不丢弃（飞行只有几秒，瞬时抑制只推迟上屏）"
        assert shell._speech_bubble.isVisible() is False

        _land(shell)

        assert shell._alert_current is not None
        assert shell._alert_current["text"] == "落地后弹我"
        assert shell._speech_bubble._raw_text == "落地后弹我"
        assert shell._speech_bubble.isVisible() is True, "落地后提醒队列恢复 pump"
    finally:
        shell._delete_runtime_marker()


def test_flight_bubble_sticky_alert_restored_on_landing(tmp_path):
    """起飞时正挂着的粘滞提醒（审批）：隐藏但不被 hidden 链路推进队列，
    落地按原样重挂——否则气泡一飞就永久消失。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.show_alert("审批 A", alert_id="a", buttons=[("同意", lambda: None)],
                         alert_type="approval", priority=0)
        assert shell._speech_bubble._raw_text == "审批 A"

        _take_off(shell)
        assert shell._speech_bubble.isVisible() is False, "起飞必须收气泡"
        assert shell._alert_current is not None, "飞行期不推进队列（current 原样留着）"

        _land(shell)
        assert shell._alert_current["text"] == "审批 A"
        assert shell._speech_bubble._raw_text == "审批 A"
        assert shell._speech_bubble.isVisible() is True, "落地粘滞提醒必须回来"
    finally:
        shell._delete_runtime_marker()


def test_flight_bubble_hidden_signal_does_not_pump_during_flight(tmp_path):
    """气泡在飞行期被隐藏（dismiss/超时）时 hidden 链路必须被门禁截断：
    否则会在飞行中接着弹下一条。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        _take_off(shell)
        shell.show_alert("排队一", sticky=False, alert_type="watchdog")
        shell.show_alert("排队二", sticky=False, alert_type="watchdog")
        shell._on_speech_bubble_hidden()
        assert shell._alert_current is None
        assert len(shell._alert_queue) == 2, "飞行期 hidden 链路不得推进队列"

        _land(shell)
        assert shell._alert_current["text"] == "排队一", "落地后按序恢复"
    finally:
        shell._delete_runtime_marker()


def test_flight_bubble_settings_unsuppress_does_not_break_flight_gate(tmp_path):
    """门禁双路交错的经典坑：设置页抑制刚解除时鱼还在飞，恢复分支必须复判
    门禁，不能把粘滞提醒顶回正在飞的那只嘴边（留给落地那次恢复）。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.show_alert("审批 A", alert_id="a", buttons=[("同意", lambda: None)],
                         alert_type="approval", priority=0)
        shell.set_bubble_suppressed(True)
        assert shell._speech_bubble.isVisible() is False

        _take_off(shell)                              # 设置页开着时被撞飞
        shell.set_bubble_suppressed(False)            # 飞行中途关掉设置页

        assert shell._speech_bubble.isVisible() is False, \
            "另一路（飞行）仍挡着：抑制解除不得破门"
        _land(shell)
        assert shell._speech_bubble._raw_text == "审批 A"
        assert shell._speech_bubble.isVisible() is True, "落地后由飞行的那一路恢复"
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 按 sprite 判定
def test_flight_bubble_other_sprite_still_bubbles(tmp_path):
    """飞的那只禁泡，其他宠照常（气泡门禁是按 sprite 判的，不是整窗开关）。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        child_bubble = shell._bubble_followers[child].bubble
        _take_off(shell)

        assert shell._bubble_blocked() is True
        assert shell._bubble_blocked(child) is False
        assert shell._sprite_host(child).show_bubble("小肥鱼在") is True
        app.processEvents()
        assert child_bubble._raw_text == "小肥鱼在"
        assert child_bubble.isVisible() is True, "未飞的子宠气泡不受牵连"

        host = shell._self_talk_hosts.get(child)
        assert host is not None and host._bubble_blocked() is False, \
            "子宠自言自语宿主同样按自己那只判定"
        assert host._show_self_talk_text("子宠自语") is True
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()


def test_flight_bubble_child_flight_leaves_main_alone(tmp_path):
    """反向：子宠飞行不牵连主宠（主宠照常冒泡，也不会被推进提醒队列）。"""
    shell, _lib = _make_shell(tmp_path)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        _take_off(shell, child)

        assert shell._bubble_blocked(child) is True
        assert shell._bubble_blocked() is False
        shell.show_bubble("主宠照常冒泡")
        assert shell._speech_bubble._raw_text == "主宠照常冒泡"
        assert shell._speech_bubble.isVisible() is True
    finally:
        shell.clear_spawned_pets()
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 服务层播报
class _ServiceApp:
    """节日/报时服务眼里的应用壳：只用到 ``win`` / ``system_notify``。

    ``win`` 是真 ``OverlayShell``（走真门禁），本替身只是服务构造面的边界。
    """

    def __init__(self, win):
        self.win = win
        self.notices: list[tuple[str, str]] = []

    def system_notify(self, title: str, message: str, **_kw) -> None:
        self.notices.append((title, message))


def test_flight_bubble_drops_festival_and_voice_chime_announcements(tmp_path):
    """飞行期节日播报 / 语音报时（一次性播报）直接丢弃：不冒泡、不改道系统通知。"""
    shell, _lib = _make_shell(tmp_path)
    service_app = _ServiceApp(shell)
    festival_self = SimpleNamespace(
        _app=service_app, BUBBLE_DURATION_MS=FestivalReminderService.BUBBLE_DURATION_MS)
    chime_self = SimpleNamespace(
        _app=service_app, _cfg={"show_bubble": True},
        BUBBLE_DURATION_MS=VoiceChimeService.BUBBLE_DURATION_MS)
    try:
        _take_off(shell)

        FestivalReminderService._bubble(festival_self, "节日快乐")
        VoiceChimeService._bubble(chime_self, "现在十二点")

        assert shell._speech_bubble._raw_text == "", "飞行期一次性播报不得冒泡"
        assert shell._speech_bubble.isVisible() is False
        assert service_app.notices == [], "丢的是瞬时播报，不改道系统通知"

        _land(shell)

        FestivalReminderService._bubble(festival_self, "节日快乐")
        assert shell._speech_bubble._raw_text == "节日快乐", \
            "落地后照常播报（门禁只覆盖飞行期，不是把功能关掉）"
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- B1：飞行边沿契约
def test_flight_bubble_edge_callback_only_on_transitions():
    """边沿回调只在进出 THROWN 各一次：飞行中每 tick 不重复回调。"""
    lib = beh._make_library(drag="hang")
    sprite = beh._make_sprite(lib)
    controller = BehaviorController(beh.BOUNDS, rng=beh.ScriptedRng(rolls=(0.99,)))
    controller.predict_enabled = False
    seen: list = []
    controller.on_flight_changed = lambda sp, flying: seen.append((sp, flying))

    controller.tick([sprite], DT)
    assert seen == [], "待机不是飞行边沿"

    sprite.interaction_state = INTERACTION_THROWN
    controller.tick([sprite], DT)
    controller.tick([sprite], DT)
    assert seen == [(sprite, True)], "飞行中只报一次边沿"

    sprite.interaction_state = INTERACTION_NORMAL
    controller.tick([sprite], DT)
    assert seen == [(sprite, True), (sprite, False)]
    assert controller.state_of(sprite) == STATE_IDLE


def test_flight_bubble_edge_callback_failure_does_not_break_tick():
    """回调抛异常绝不掀掉 tick（门禁是附加能力，飞行本身不能因它中断）。"""
    lib = beh._make_library(drag="hang")
    sprite = beh._make_sprite(lib)
    controller = BehaviorController(beh.BOUNDS, rng=beh.ScriptedRng(rolls=(0.99,)))
    controller.predict_enabled = False
    calls: list = []

    def _boom(_sprite, flying):
        calls.append(flying)
        raise RuntimeError("门禁消费方炸了")

    controller.on_flight_changed = _boom
    controller.tick([sprite], DT)
    sprite.interaction_state = INTERACTION_THROWN
    controller.tick([sprite], DT)

    assert calls == [True], "边沿确实报给了消费方（只是异常被隔离，不外溢）"
    assert controller.state_of(sprite) == STATE_THROWN


def test_flight_bubble_edge_notifies_landing_when_flying_sprite_removed():
    """飞行中被移除（托盘退出一只飞着的宠）：补一次落地边沿，不然壳的飞行
    集合会永久留着这只已注销的 sprite。"""
    lib = beh._make_library(drag="hang")
    sprite = beh._make_sprite(lib)
    controller = BehaviorController(beh.BOUNDS, rng=beh.ScriptedRng(rolls=(0.99,)))
    controller.predict_enabled = False
    seen: list = []
    controller.on_flight_changed = lambda sp, flying: seen.append((sp, flying))

    controller.tick([sprite], DT)
    sprite.interaction_state = INTERACTION_THROWN
    controller.tick([sprite], DT)
    controller.forget(sprite)

    assert seen == [(sprite, True), (sprite, False)]
    assert controller.state_of(sprite) is None


# ---------------------------------------------------------------- B2：起飞预热去重
class _RecordingExecutor:
    """记录型预热执行器（替掉跨线程边界，不跑真实解码）。

    只记 ``submit`` 的批次：本批要验的是"同一飞行窗口提交几次"，执行时机与
    线程无关，故不同步跑 ``fn``（真 worker 的执行语义由 webm_clip 自己的
    测试覆盖）。
    """

    def __init__(self) -> None:
        self.batches: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.batches.append((fn, args, kwargs))
        return None


def _warm_library():
    lib = beh._make_library(idles=["idle1", "idle2"], drag="hang")
    for name in lib.idles:
        lib.movie(name).warm_first_frame = lambda: None
    return lib


def test_flight_warm_submitted_once_per_flight(tmp_path, monkeypatch):
    """同一 sprite 同一次飞行只提交一批落地预热；落地复位、下场飞行照旧预热。"""
    executor = _RecordingExecutor()
    monkeypatch.setattr(sprite_behavior_mod, "_WARM_EXECUTOR", executor)
    lib = _warm_library()
    sprite = beh._make_sprite(lib)
    controller = BehaviorController(beh.BOUNDS, rng=beh.ScriptedRng(rolls=(0.99,)))
    controller.predict_enabled = False

    controller.tick([sprite], DT)                 # 先接管进待机
    sprite.interaction_state = INTERACTION_THROWN
    controller.tick([sprite], DT)                 # 起飞边沿

    assert controller.state_of(sprite) == STATE_THROWN
    assert len(executor.batches) == 1, "起飞边沿恰好提交一批预热"
    _fn, args, _kwargs = executor.batches[0]
    assert set(args[0]) == {lib.movie(name) for name in lib.idles}, \
        "预热内容一字不变：整池 idle 首帧"

    state = controller._states[sprite]
    controller._enter_thrown(sprite, state)       # 同一次飞行的重复触发
    assert len(executor.batches) == 1, "同一次飞行未结束：绝不重复提交预热"
    assert set(state.landing_pinned) == {lib.movie(name) for name in lib.idles}, \
        "去重不得把飞行期 pin 弄丢"

    sprite.interaction_state = INTERACTION_NORMAL
    controller.tick([sprite], DT)                 # 落地：复位去重标记
    assert controller.state_of(sprite) == STATE_IDLE
    assert state.warm_landing_submitted is False

    sprite.interaction_state = INTERACTION_THROWN
    controller.tick([sprite], DT)                 # 下一次飞行照旧预热
    assert len(executor.batches) == 2, "去重只覆盖同一次飞行，不是永久跳过"


def test_flight_warm_skipped_when_no_idle_clips(tmp_path, monkeypatch):
    """无 idle 素材：不提交、**不置位**——查不到素材就置位，这只宠此后再起飞
    也永远不会预热（去重标记被一次空查吃掉）。

    不变式守卫：探针态两侧都必须绿（它拦的是"置位早于空查"这类突变）。
    """
    executor = _RecordingExecutor()
    monkeypatch.setattr(sprite_behavior_mod, "_WARM_EXECUTOR", executor)
    lib = beh._make_library(idles=[], drag="hang")
    sprite = beh._make_sprite(lib)
    controller = BehaviorController(beh.BOUNDS, rng=beh.ScriptedRng(rolls=(0.99,)))
    controller.predict_enabled = False

    controller.tick([sprite], DT)
    sprite.interaction_state = INTERACTION_THROWN
    controller.tick([sprite], DT)

    assert executor.batches == []
    assert controller._states[sprite].warm_landing_submitted is False


class _GatedLibrary:
    """带库级预热闸门的假库：``warm_allowed()`` 可切，其余面直通 inner。"""

    def __init__(self, inner):
        self._inner = inner
        self.allowed = True

    def warm_allowed(self):
        return self.allowed

    def __getattr__(self, item):
        return getattr(self._inner, item)


def test_flight_warm_respects_library_gate(tmp_path, monkeypatch):
    """起飞落地预热必须过库级闸门（设置页关预热 / 隐藏暂停）：零提交，且不占位。

    这条路径直提 ``_WARM_EXECUTOR``，不经过库的 ``warm_predicted``——不查闸门
    就等于"关闭后台动画预热"的设置对该路径无效。占位（pin + 去重标记）同样要
    留在闸门之后：被一次空跑吃掉标记，开闸后的下一次起飞照样不预热。
    """
    executor = _RecordingExecutor()
    monkeypatch.setattr(sprite_behavior_mod, "_WARM_EXECUTOR", executor)
    lib = _GatedLibrary(_warm_library())
    sprite = beh._make_sprite(lib)
    controller = BehaviorController(beh.BOUNDS, rng=beh.ScriptedRng(rolls=(0.99,)))
    controller.predict_enabled = False

    controller.tick([sprite], DT)                 # 先接管进待机
    lib.allowed = False
    sprite.interaction_state = INTERACTION_THROWN
    controller.tick([sprite], DT)                 # 起飞边沿（闸门关）

    state = controller._states[sprite]
    assert controller.state_of(sprite) == STATE_THROWN
    assert executor.batches == [], "库级闸门关闭时落地预热不得提交"
    assert state.warm_landing_submitted is False, "闸门关闭不得占位去重标记"
    assert state.landing_pinned == [], "闸门关闭不得打飞行期 pin"

    lib.allowed = True
    controller._enter_thrown(sprite, state)       # 开闸：本次飞行照旧整批提交
    assert len(executor.batches) == 1
    _fn, args, _kwargs = executor.batches[0]
    assert set(args[0]) == {lib.movie(name) for name in lib.idles}
