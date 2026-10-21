# -*- coding: utf-8 -*-
"""overlay 拓扑下的 app 级宿主回退（批 B3）聚焦测试。

背景：overlay 拓扑不建 ``PetWindow``（``PetInstance.win`` / ``AppShell.win`` 恒
None），而一批 app 级服务仍只认 ``win``：完整聊天窗打不开、报时/节日/待办落到
系统通知、定时余额直接 return、省电开关保存后不生效、隐藏提示读不到托盘、设置页
已开时静默无反应。

装配口径：**真 AppShell + 真 PetInstance + 真 OverlayShell 最小构造**（屏/sprite
工厂替身，同 ``tests/test_overlay_*.py`` 的 ``_make_shell``）；只有素材库
（ffmpeg 子进程）、网络线程与凭据库是不可在单测里跑的边界，用替身。
``PetInstance`` 走真实实现（``open_chat`` / ``_sync_animation_prewarm`` /
``_notify_pet_hidden``），不 mock 产品对象。

纪律：offscreen；同步直调，不 sleep 赌时序。
"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication

import pet.app as app_mod
import pet.window as window_mod
import tests.test_overlay_window_capabilities as cap
import tests.test_sprite_menu_facade as fac
from pet.app import AppShell, PetInstance
from pet.config import Config
from pet.festival.service import FestivalReminderService
from pet.overlay_shell import OverlayShell, _SettingsIdentity
from pet.pet_sprite import PetSprite
from pet.todo.reminder import TodoReminderService
from pet.voice.chime_service import VoiceChimeService

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 装配
class _HostLibrary(fac.RichLibrary):
    """素材库替身：媒体解码（ffmpeg 子进程）是单测跑不了的边界。

    只比 ``RichLibrary`` 多记 ``set_prewarm_enabled``（A4 的观测点）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.prewarm_calls: list[tuple[bool, object]] = []

    def set_prewarm_enabled(self, enabled, *, visible=None) -> None:
        self.prewarm_calls.append((bool(enabled), visible))


class _HostInstance(PetInstance):
    """真 PetInstance（聊天/预热/隐藏提示全走产品实现），只换掉素材库这一边界。"""

    def __init__(self, shell, config) -> None:
        super().__init__(shell, config)
        self.libraries: list[_HostLibrary] = []

    def _create_library(self, character_id):
        lib = _HostLibrary()
        self.libraries.append(lib)
        return lib


def _make_overlay_app(tmp_path, *, chat_ui_style: str | None = None):
    """真 AppShell + 真 PetInstance + 真 OverlayShell（sprite 工厂真 PetSprite）。

    共享子系统注入与自言自语朗读接线逐条对齐 ``AppShell.start()`` 的 overlay 分支
    （不注入的话壳是"空转形态"，与实机运行形态不一致）。
    """
    config = Config(base=tmp_path)
    if chat_ui_style is not None:
        config.set("chat_ui_style", chat_ui_style)
    shell = AppShell(app, config)
    inst = _HostInstance(shell, config)
    shell.instance = inst
    shell._instances = [inst]
    overlay = OverlayShell(
        app, inst, screen=cap.FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040)),
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale),
        agent_link_manager=shell._shared.agent_link,
        proactive_watcher=shell._shared.proactive)
    overlay.on_self_talk_speak = shell.speak_self_talk
    shell._overlay_shell = overlay
    return shell, inst, overlay


def _teardown(overlay) -> None:
    overlay._teardown_settings_command_watch()
    overlay._delete_runtime_marker()
    if getattr(overlay, "tray", None) is not None:
        overlay.tray.hide()


# ---------------------------------------------------------------- A1 完整聊天窗
def test_full_chat_opens_with_overlay_host(tmp_path):
    """``OverlayShell.open_full_chat``（菜单/快速气泡「全文见聊天窗」入口）在
    overlay 拓扑下必须真开出聊天窗，且以壳为 pet 形锚点。"""
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        assert inst.win is None, "overlay 拓扑不建 PetWindow（前置不成立）"
        overlay.overlay.show()

        overlay.open_full_chat()

        assert inst.modern_chat_window is not None, "完整聊天窗在 overlay 下打不开"
        assert inst.chat_window is inst.modern_chat_window
        host = inst.modern_chat_window.pet_link.pet_window
        assert host is not None, "聊天窗必须拿到 pet 形宿主（否则定位/头像全丢）"
        assert host.visible_content_rect().isValid(), "宿主身体框无效（定位会退化）"
    finally:
        _teardown(overlay)


def test_classic_chat_dispatcher_opens_with_overlay_host(tmp_path):
    """``PetInstance.open_chat`` 的 classic 分支同样要能在 overlay 下开出。"""
    shell, inst, overlay = _make_overlay_app(tmp_path, chat_ui_style="classic")
    try:
        overlay.overlay.show()

        inst.open_chat()

        assert inst.legacy_chat_window is not None
        assert inst.chat_window is inst.legacy_chat_window
        assert inst.legacy_chat_window.pet_link.pet_window is not None
    finally:
        _teardown(overlay)


# ---------------------------------------------------------------- A1b 切角色同步聊天面（B07）
def test_overlay_switch_character_syncs_open_chat_window(tmp_path):
    """B07：overlay 主宠切角色后，已开着的完整聊天窗必须跟着换角色。

    捕获模式的 ``PetInstance.switch_character`` 一直带这条同步（对齐
    app.py:664-668），overlay 走的是 ``OverlayShell.switch_character``——缺的
    正是它，切完角色再开口仍是旧角色的会话/头像/系统提示。
    """
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        overlay.overlay.show()
        overlay.open_full_chat()
        window = inst.modern_chat_window
        assert window is not None and window.character_id != "nova"

        overlay.switch_character("nova")

        assert overlay._sprite_config(overlay.sprite).get("character") == "nova"
        assert window.character_id == "nova", "完整聊天窗必须跟着切角色"
        assert window.session.character_id == "nova", "会话也按新角色取"
    finally:
        _teardown(overlay)


def test_overlay_switch_character_syncs_island_chat_bubble(tmp_path):
    """B07：岛对话气泡（桌宠隐藏时的对话面）同样是聊天面，跟着切角色。"""
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        class _Bubble:
            def __init__(self):
                self.switched: list[str] = []

            def switch_character(self, character_id):
                self.switched.append(character_id)

        bubble = _Bubble()
        shell.island_chat = bubble

        overlay.switch_character("nova")

        assert bubble.switched == ["nova"]
    finally:
        _teardown(overlay)


# ---------------------------------------------------------------- R03 捕获模式：快速/岛气泡同步
class _CaptureWin:
    """捕获模式（legacy 单窗）的窗口替身：只提供热切换要用的两面。"""

    def __init__(self, config) -> None:
        self.cfg = config
        self.agent_link_manager = None
        self.detach_calls = 0

    def detach_decode_sessions(self) -> None:
        self.detach_calls += 1


class _RecordingChatSurface:
    """记录 ``set_pet_window`` / ``switch_character`` 的聊天面替身。"""

    def __init__(self) -> None:
        self.pet_windows: list = []
        self.switched: list[str] = []

    def set_pet_window(self, window) -> None:
        self.pet_windows.append(window)

    def switch_character(self, character_id) -> None:
        self.switched.append(character_id)


def _make_capture_app(tmp_path, monkeypatch):
    """真 AppShell + 真 PetInstance + 捕获模式窗替身（窗口构造是素材库边界，用替身）。"""
    config = Config(base=tmp_path)
    config.save()
    shell = AppShell(app, config)
    inst = _HostInstance(shell, config)
    shell.instance = inst
    shell._instances = [inst]
    inst.win = _CaptureWin(config)

    def fake_build_window(self, character_id, lib=None, build_tray=True):
        win = _CaptureWin(self.config)
        self.win = win
        return win

    monkeypatch.setattr(app_mod.PetInstance, "_build_window", fake_build_window)
    return shell, inst


def test_capture_mode_switch_character_syncs_quick_and_island_bubbles(
        tmp_path, monkeypatch):
    """R03-B：捕获模式切角色，快速对话气泡与岛对话气泡必须一并同步。

    ``PetInstance.switch_character`` 此前只同步完整聊天窗（legacy/modern），
    快速气泡（``_show_quick_chat`` 复用时只刷 settings/session，身份仍是构造期
    快照，按旧 ``character_id`` 取会话）与岛气泡（桌宠隐藏时的对话面）留在旧
    角色上——切完在这两面开口还是旧角色的会话与系统提示。
    """
    shell, inst = _make_capture_app(tmp_path, monkeypatch)
    old_win = inst.win
    quick = _RecordingChatSurface()
    inst.quick_chat = quick
    island = _RecordingChatSurface()
    shell.island_chat = island
    legacy = _RecordingChatSurface()
    modern = _RecordingChatSurface()
    inst.legacy_chat_window = legacy
    inst.modern_chat_window = modern

    inst.switch_character("nova")

    assert old_win.detach_calls == 1, "前提：走的是真热切换链路（旧窗解码会话已收尾）"
    assert shell.config.get("character") == "nova"
    for window in (legacy, modern):
        assert window.pet_windows == [inst.win], "热切换后聊天窗要指回新窗"
        assert window.switched == ["nova"]
    assert quick.switched == ["nova"], "快速对话气泡必须跟着切角色"
    assert island.switched == ["nova"], "岛对话气泡必须跟着切角色"


def test_capture_mode_switch_character_without_bubbles_is_quiet(
        tmp_path, monkeypatch):
    """未创建的气泡（``quick_chat``/``island_chat`` 恒 None）不许掀掉切角色。"""
    shell, inst = _make_capture_app(tmp_path, monkeypatch)
    assert inst.quick_chat is None and shell.island_chat is None
    legacy = _RecordingChatSurface()
    inst.legacy_chat_window = legacy

    inst.switch_character("nova")

    assert shell.config.get("character") == "nova"
    assert legacy.switched == ["nova"]


def test_overlay_promotion_switches_open_chat_window_to_promoted_character(tmp_path):
    """R03-A 端到端：主宠退出提升子宠后，已开着的聊天窗与它取的会话换成提升者的。

    走真 AppShell + 真 PetInstance + 真聊天窗（``switch_character`` 真换
    ``character_id`` 并按新角色重取会话），只把素材库与窗口构造换成替身。
    """
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        overlay.overlay.show()
        overlay.spawn_pet()
        child = overlay._spawned[0]
        # 子宠换角色（产品路径：写它自己的 slot 配置，主配置一字不动）
        overlay.switch_character("nova", child)
        overlay.open_full_chat()
        window = inst.modern_chat_window
        assert window is not None and window.character_id != "nova"

        assert overlay.exit_pet(overlay.sprite) is True

        assert overlay.sprite is None
        assert child in overlay._spawned
        assert window.character_id != "nova", "关闭0不能改变共享会话角色"
        assert window.session.character_id == window.character_id
    finally:
        _teardown(overlay)


# ---------------------------------------------------------------- A2 提醒落气泡
def _bubble_text(overlay) -> str:
    bubble = overlay._speech_bubble
    assert bubble is not None, "壳上应该有真气泡控件"
    return str(bubble._raw_text)


def test_todo_reminder_bubbles_on_overlay_host(tmp_path):
    """待办提醒宿主 = 壳：气泡出现在桌宠头顶，而不是退回系统通知。"""
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        overlay.overlay.show()
        service = TodoReminderService(shell)
        service.apply_config()

        service._notify_fire({"title": "站会", "time": "10:00"})

        assert "站会" in _bubble_text(overlay)
        assert shell._toast_windows == [], "落了气泡就不该再弹系统通知"
    finally:
        _teardown(overlay)


class _LegacyBubbleHost:
    """legacy ``PetWindow.show_bubble`` 的最小宿主（真实现，只补它读的状态面）。"""

    def __init__(self) -> None:
        self._bubble_suppressed = False
        self._alert_current = None
        self._speech_bubble = _LegacyBubble()
        self.scale = 1.0

    def isVisible(self) -> bool:  # noqa: N802 - 对齐 Qt API
        return True

    def hold_bubble(self, seconds: float) -> None:
        pass

    def visible_content_rect(self):
        from PySide6.QtCore import QRect
        return QRect(0, 0, 100, 100)


class _LegacyBubble:
    def __init__(self) -> None:
        self.shown: list[str] = []

    def hide(self) -> None:
        pass

    def show_text(self, text, *_args, **_kwargs) -> None:
        self.shown.append(text)


def test_show_bubble_returns_delivery_verdict_on_both_topologies(tmp_path):
    """overlay 壳与 legacy ``PetWindow`` 的 ``show_bubble`` 返回同一套契约。

    交付确认制（免打扰汇报 / 待办 / 报时 / 节日）靠这个返回值判断"到底上没上
    屏"：两套拓扑必须同义——真上屏 True，让路/抑制 False，且绝不返回 None
    （legacy 早期成功失败都返回 None，只能当"已展示"，误判出静默丢失）。
    """
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        overlay.overlay.show()
        overlay.spawn_pet()
        legacy = _LegacyBubbleHost()

        assert overlay.show_bubble("普通气泡") is True
        assert window_mod.PetWindow.show_bubble(legacy, "普通气泡") is True

        # 审批提醒占着气泡位：普通气泡让路，两边都返回 False
        overlay.show_alert("审批请求", buttons=[("同意", lambda: None)])
        assert overlay._alert_current is not None
        assert overlay.show_bubble("普通气泡") is False
        legacy._alert_current = {"id": "approval"}
        assert window_mod.PetWindow.show_bubble(legacy, "普通气泡") is False

        # 设置页抑制期
        overlay.clear_alerts()
        overlay.set_bubble_suppressed(True)
        assert overlay.show_bubble("普通气泡") is False
        legacy._alert_current = None
        window_mod.PetWindow.set_bubble_suppressed(legacy, True)
        assert window_mod.PetWindow.show_bubble(legacy, "普通气泡") is False
    finally:
        _teardown(overlay)


def test_voice_chime_and_festival_bubble_on_overlay_host(tmp_path):
    """报时/节日提醒宿主 = 壳（此前 win 恒 None → 直接落系统通知）。"""
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        overlay.overlay.show()
        chime = VoiceChimeService(shell)
        chime.apply_config()
        festival = FestivalReminderService(shell)
        festival.apply_config()

        chime._bubble("现在是上午九点整。")
        assert "九点" in _bubble_text(overlay)

        festival._bubble("今天是春节。")
        assert "春节" in _bubble_text(overlay)
        assert shell._toast_windows == [], "落了气泡就不该再弹系统通知"
    finally:
        _teardown(overlay)


# ---------------------------------------------------------------- P3 免打扰监听表隔离


# 上一条用例是否真的制造过残留（供紧邻的边界体检用例判断"这次有没有东西可查"）。
_LEAK_PROBE: dict = {}


def _chime_quiet_listeners() -> list:
    """共享免打扰表里属于 ``VoiceChimeService`` 的模块回调（按身份认，不碰别人的订阅）。"""
    from pet import quiet_mode

    target = VoiceChimeService._on_quiet_state_changed
    return [callback for callback in list(quiet_mode.shared_quiet_state()._listeners)
            if getattr(callback, "__func__", None) is target]


def test_chime_construction_without_stop_leaves_quiet_listener(tmp_path, request):
    """P3：构造即挂的免打扰监听确实会在**进程级共享表**里留一条。

    本文件此前正是这样漏的：``test_voice_chime_and_festival_bubble_on_overlay_host``
    构造 ``VoiceChimeService`` 却从不 ``stop()``（只测一拍内的气泡落点），而清理
    fixture 只长在 ``tests/test_voice_chime_service.py`` 里——模块级 autouse 不跨文件，
    而 ``reset_shared_quiet_state()`` 只复位状态、**不动监听表**（订阅表是订阅方的
    责任）。于是别的域照样漏一条持着服务与播放器的回调进共享表。

    这里同时锁定收口手段的**位置**：必须是 conftest 提供给所有测试文件的那一条
    （本文件自己的 fixture 修不了"别的域也漏"这件事）。
    """
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        chime = VoiceChimeService(shell)     # 构造即挂；本用例刻意不 stop()
        chime.apply_config()

        assert len(_chime_quiet_listeners()) == 1, "前提：构造确实往共享表挂了一条"
        # 残留**故意留着**：下一条用例的边界体检靠它才有东西可查
        _LEAK_PROBE["constructed"] = True
        assert "_no_stale_chime_quiet_listeners" in request.fixturenames, \
            "收口 fixture 必须由 conftest 全局面提供（只长在某个测试文件里等于别的域照旧漏）"
    finally:
        _teardown(overlay)


def test_shared_quiet_table_is_clean_at_this_boundary():
    """P3 边界体检：上一条用例留下的残留必须已被用例边界摘掉。

    断言的是**边界收口**（上一条造残留 → 本条开头表干净），不是"表永远为空"：
    单独跑本用例（``-k``）时前面没有制造残留的用例，跳过而不是空过。
    """
    if not _LEAK_PROBE.get("constructed"):
        pytest.skip("单独/乱序运行时没有上一条用例制造的残留可查（边界断言针对测试序列）")
    assert _chime_quiet_listeners() == [], "上一个用例的残留必须已被边界收口摘掉"


class _NoThread:
    """网络/线程边界替身：余额查询的 HTTP 线程不在单测里跑。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass


# ---------------------------------------------------------------- A3 定时余额
def test_show_balance_uses_overlay_host(tmp_path, monkeypatch):
    """``show_balance`` 的 ``parent or instance.win`` 在 overlay 恒 None → 回退壳。"""
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        overlay.overlay.show()
        monkeypatch.setattr(app_mod.threading, "Thread", _NoThread)
        monkeypatch.setattr(shell.config, "resolve_api_key", lambda provider: "")
        assert shell._balance_busy is False

        shell.show_balance()

        assert shell._balance_busy is True, "余额链路在 overlay 下被提前 return 掉了"
        app.processEvents()          # 派发 singleShot 的"让我看看余额…"气泡
        assert "余额" in _bubble_text(overlay)
        # 异步结果回填同样走壳（缓存/网络两条路径共用 _show_balance_payload）
        shell._balance_bridge.done.emit(True, {"text": "余额 12.34", "info": {"total": 12.34}})
        assert "12.34" in _bubble_text(overlay)
    finally:
        _teardown(overlay)


# ---------------------------------------------------------------- A4 预热开关
def test_prewarm_sync_reaches_overlay_libraries(tmp_path):
    """省电模式保存后 ``_sync_animation_prewarm`` 必须落到 sprite 壳的库上
    （主宠 + 已生子宠），此前 overlay 下 win=None 直接 return。"""
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        main_lib = overlay.lib
        assert isinstance(main_lib, _HostLibrary), "主库必须来自本用例的替身（前置不成立）"
        overlay.spawn_pet()
        assert overlay._spawned, "spawn_pet 未产出子宠（用例前置不成立）"
        child_lib = overlay._spawned_libs[overlay._spawned[0]]

        inst._sync_animation_prewarm()          # 省电关（默认）→ 预热开
        assert main_lib.prewarm_calls == [(True, False)]
        assert child_lib.prewarm_calls == [(True, False)]

        inst.config.set("idle_low_fps_enabled", True)
        inst.config.save()
        inst._sync_animation_prewarm()          # 保存后立即生效，不必等下次启动

        assert main_lib.prewarm_calls[-1][0] is False
        assert child_lib.prewarm_calls[-1][0] is False
    finally:
        _teardown(overlay)


# ---------------------------------------------------------------- A5 隐藏指路
def test_hidden_hint_uses_overlay_tray(tmp_path, monkeypatch):
    """隐藏桌宠后的「点托盘恢复」提示：overlay 下托盘挂在壳上。"""
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        assert overlay.tray is not None, "测试环境应能建出托盘对象"
        assert shell.tray is overlay.tray, "AppShell.tray 在 overlay 下必须看向壳的托盘"
        messages: list[tuple] = []
        monkeypatch.setattr(overlay.tray, "showMessage",
                            lambda *args, **kwargs: messages.append(args))

        inst._notify_pet_hidden()

        assert messages, "隐藏后必须给出恢复入口提示"
        assert "托盘" in messages[0][1]
    finally:
        _teardown(overlay)


# ---------------------------------------------------------------- A6 设置页占用
def test_settings_already_open_hints_user(tmp_path, monkeypatch):
    """设置页已开着时不许静默 return True：同一只 / 另一只都要有可见提示。"""
    shell, inst, overlay = _make_overlay_app(tmp_path)
    launched: list = []
    monkeypatch.setattr(
        shell, "_launch_settings_process",
        lambda instance=None, **kwargs: launched.append(instance) or True)
    lock = QLockFile(str(shell.config.dir / "settings.lock"))
    try:
        assert shell.open_settings_process(inst) is True     # 首次：真拉起
        assert launched == [inst]
        shell._settings_launch_at = time.monotonic() - 10.0  # 走出"刚拉起"窗口
        assert lock.tryLock(0), "模拟独立设置进程已建锁（前置不成立）"

        before = len(shell._toast_windows)
        assert shell.open_settings_process(inst) is True     # 同一只：不重复拉起 + 提示
        assert launched == [inst]
        same = shell._toast_windows[before:]
        assert same and "已经打开" in same[0]._message

        before = len(shell._toast_windows)
        assert shell.open_settings_process(_SettingsIdentity("slot-2")) is True
        assert launched == [inst]
        other = shell._toast_windows[before:]
        assert other and "先关闭" in other[0]._message
        assert other[0]._message != same[0]._message, "两只宠的提示必须区分"
    finally:
        lock.unlock()
        _teardown(overlay)


# ---------------------------------------------------------------- 回归护栏
def test_win_consumers_stay_safe_with_overlay_host(tmp_path):
    """A2 的消费者审计：``self.win`` 在 overlay 下改指壳后，另两个消费点仍安全。

    ``app.win`` 只有三处消费（三个提醒服务之外）：

    - ``win.agent_link_manager``：壳持有的是同一个共享 agent_link（DSH 离线收口照常）；
    - ``open_todo_panel``：壳是 QObject，不能当 QDialog 的 parent（必须回落 None）。
    """
    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        assert shell.win is overlay
        assert shell.win.agent_link_manager is shell._shared.agent_link

        shell.open_todo_panel()

        assert shell.todo_panel is not None
    finally:
        if shell.todo_panel is not None:
            shell.todo_panel.close()
            shell.todo_panel = None
        _teardown(overlay)


def test_legacy_topology_hosts_unchanged(tmp_path):
    """无 overlay 壳时 ``win`` / ``tray`` 逐位不变（legacy 逃生门路径）。"""
    shell = AppShell(app, Config(base=tmp_path))
    sentinel = object()
    try:
        shell.instance.win = sentinel
        assert shell.win is sentinel
        marking = object()
        shell.tray = marking
        assert shell.tray is marking
    finally:
        shell.instance.win = None


# ---------------------------------------------------------------- O5 指令通道归属
def test_settings_command_belongs_to_overlay_host_not_app(tmp_path):
    """overlay 拓扑下「退出子肥鱼」指令由壳独占消费，AppShell 不得抢先摘走。

    两条 watcher 盯同一个配置目录，都调 ``consume_command``（**消费即删**）。
    AppShell 侧的 ``clear_spawned_pets`` / slot 身份在 overlay 下都没有目标
    （子宠是 sprite，不在 ``_instances`` 里），谁先跑谁把指令摘走 → 设置页
    「一键退出子肥鱼」静默失效。故 app 侧在 overlay 拓扑下必须让路。
    """
    from pet import overlay_settings_command as cmd

    shell, inst, overlay = _make_overlay_app(tmp_path)
    try:
        shell._install_config_watcher()          # app 侧 watcher（真装、真盯同一目录）
        overlay.spawn_pet()
        assert overlay._spawned, "用例前置：有子宠才谈得上退出"
        config_dir = overlay._config.dir
        # 两条 watcher 并存且职责不同：app 侧 = 主配置外部修改 → 应用链，
        # 壳侧 = 设置指令 + per-slot 签名。两者都保留（去重的是指令消费，不是 watcher）。
        assert shell._config_watcher is not None
        assert overlay._command_watcher is not None
        assert str(config_dir) in shell._config_watcher.directories()
        assert str(config_dir) in overlay._command_watcher.directories()
        assert cmd.write_command(config_dir, cmd.CMD_EXIT_SPAWNED_PETS)
        # app 侧 watcher 槽（QFileSystemWatcher.directoryChanged 的真实接线点）
        shell._on_config_dir_changed(str(config_dir))
        assert cmd.command_path(config_dir).exists(), \
            "AppShell 把指令摘走了：壳侧再也消费不到（overlay 下它没有目标可执行）"
        shell._poll_settings_process()          # 3s 轮询兜底同样不得摘走
        assert cmd.command_path(config_dir).exists(), "轮询兜底路径也摘走了指令"
        # 壳侧照常消费并真的退出那只子肥鱼
        overlay._on_settings_command_dir_changed(str(config_dir))
        assert overlay._spawned == []
    finally:
        shell._teardown_config_watcher()
        _teardown(overlay)
