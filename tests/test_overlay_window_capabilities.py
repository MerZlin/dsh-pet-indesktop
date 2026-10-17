# -*- coding: utf-8 -*-
"""Phase 4.1b 窗口能力 parity offscreen 单测（QT_QPA_PLATFORM=offscreen 可跑）。

每条对应一条「旧语义 = 新行为」（pet/window.py / pet/window_screen.py）：
- FullscreenCursorWatcher：全屏探测翻转、配置门关停即恢复、光标 HIDDEN
  0.2s 去抖、探针异常不翻转；
- OverlayShell：on_top 应用与持久化、有效穿透 = 用户 OR 光标自动、
  全屏隐藏/恢复、拖拽滞留冲刷、runtime 避让标记写/更新/删除、托盘显隐。

纪律：探测函数注入假探针、假钟注入；同步直调 handler，不 sleep 赌时序。
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from pet.overlay_peripherals import FullscreenCursorWatcher
from pet.overlay_shell import OverlayShell

app = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 假件
class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


class FakeScreen(QObject):
    geometryChanged = Signal(QRect)
    availableGeometryChanged = Signal(QRect)
    refreshRateChanged = Signal(float)

    def __init__(self, geo, avail, *, dpr=1.0, refresh=60.0):
        super().__init__()
        self._geo = QRect(*geo)
        self._avail = QRect(*avail)
        self._dpr = float(dpr)
        self._refresh = float(refresh)

    def geometry(self):
        return QRect(self._geo)

    def availableGeometry(self):
        return QRect(self._avail)

    def devicePixelRatio(self):
        return self._dpr

    def refreshRate(self):
        return self._refresh

    def set_refresh(self, rate):
        """同分辨率下切刷新率（60↔165/180）：触发 refreshRateChanged。"""
        self._refresh = float(rate)
        self.refreshRateChanged.emit(self._refresh)


class CapSprite:
    SIZE = (120, 80)

    def __init__(self, lib, pos, scale):
        self.lib = lib
        self.pos = QPointF(pos)
        self.scale = scale
        self.home_screen = None
        self.interaction_state = "normal"
        self.dragging = False

    def rect(self):
        return QRect(int(self.pos.x()), int(self.pos.y()), *self.SIZE)

    def body_rect(self):
        r = self.rect()
        return QRect(r.x() + 10, r.y() + 10, r.width() - 20, r.height() - 20)

    def set_dpr(self, dpr):
        pass

    def set_bounds(self, bounds):
        pass

    def set_clamp_rects(self, rects):
        self.clamp_rects = None if rects is None else tuple(rects)

    def set_pos(self, pos):
        self.pos = QPointF(pos)

    def advance(self, dt):
        return None

    def paint(self, painter):
        pass

    def alpha_at(self, local):
        return 0

    def on_release(self, pos):
        pass


class CapLibrary:
    def __init__(self):
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = {}

    def names(self):
        return []

    def stop_all_clips(self):
        pass

    def pause_warm(self):
        pass


class CapConfig:
    """假配置面 = 生产里的 ``instance.config``（overlay 拓扑下即 SlotRoutedConfig）。

    每宠键的**真值在槽档案里**（规范 §五「唯一归属表」）：构造与 ``set`` 都把每宠键
    写进 ``config-slot-0.json``，全局键留在内存里。旧版把每宠键只塞进这个内存字典、
    由壳体回读的做法随契约作废——那样档案永远为空，读端只好回落默认值。
    """

    def __init__(self, tmp_path: Path, values=None):
        self._values = dict(values or {})
        self.dir = tmp_path
        self.instance_id = ""
        self.saved = 0
        self._write_slot_values(self._values)

    def _write_slot_values(self, values) -> None:
        from pet import pet_profiles

        per_pet = {key: value for key, value in values.items()
                   if key in pet_profiles.PER_PET_KEYS}
        if not per_pet:
            return
        result = pet_profiles.write_profile_settings(self.dir, 0, per_pet)
        if not result.ok:  # 假配置自己写不进去 = 测试前提没成立，别让用例默默跑在默认值上
            raise AssertionError(f"CapConfig 每宠键落档失败：{result}")

    def get(self, key, default=None):
        return self._values.get(key, default)

    def set(self, key, value):
        self._values[key] = value
        self._write_slot_values({key: value})

    def save(self):
        self.saved += 1


class CapInstance:
    def __init__(self, config):
        self.config = config

    def _create_library(self, character_id):
        return CapLibrary()


def _make_shell(tmp_path, config_values=None, screen=None):
    config = CapConfig(tmp_path, config_values)
    instance = CapInstance(config)
    screen = screen or FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040))
    shell = OverlayShell(
        app, instance, screen=screen,
        sprite_factory=lambda lib, pos, scale: CapSprite(lib, pos, scale))
    return shell


def _markers(config_dir: Path):
    return list(config_dir.glob("pet-runtime-v2-*.json"))


# ---------------------------------------------------------------- watcher 单元
def test_watcher_fullscreen_flip_and_gate():
    state = {"hit": False}
    w = FullscreenCursorWatcher(fullscreen_probe=lambda: state["hit"],
                                cursor_probe=lambda: "UNKNOWN")
    hits = []
    w.fullscreen_changed.connect(hits.append)
    w.set_fullscreen_enabled(True)
    state["hit"] = True
    w._poll_fullscreen()
    assert hits == [True]
    state["hit"] = False
    w._poll_fullscreen()
    assert hits == [True, False]
    # 配置门：开着时被隐藏（hit=True），关门 → 立即恢复 False 并停表
    state["hit"] = True
    w._poll_fullscreen()
    assert hits[-1] is True
    w.set_fullscreen_enabled(False)
    assert hits[-1] is False
    assert not w._fs_timer.isActive()
    w.stop()


def test_watcher_cursor_debounce_and_gate():
    clock = FakeClock()
    state = {"vis": "HIDDEN"}
    w = FullscreenCursorWatcher(fullscreen_probe=lambda: False,
                                cursor_probe=lambda: state["vis"],
                                clock=clock)
    events = []
    w.cursor_visibility_changed.connect(events.append)
    w.set_cursor_enabled(True)
    w._poll_cursor()                 # t=100：首次 HIDDEN，开始计时
    assert events == []
    clock.advance(0.1)
    w._poll_cursor()                 # 0.1s < 0.2s：仍不报
    assert events == []
    clock.advance(0.15)
    w._poll_cursor()                 # 0.25s ≥ 0.2s：报 HIDDEN
    assert events == ["HIDDEN"]
    state["vis"] = "SHOWING"
    w._poll_cursor()
    assert events == ["HIDDEN", "SHOWING"]
    # 配置门：关门 → 恢复 SHOWING 并停表
    w.set_cursor_enabled(False)
    assert events[-1] == "SHOWING"
    assert not w._cursor_timer.isActive()
    w.stop()


def test_watcher_probe_exception_keeps_state():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        raise RuntimeError("probe boom")

    w = FullscreenCursorWatcher(fullscreen_probe=flaky,
                                cursor_probe=lambda: "UNKNOWN")
    hits = []
    w.fullscreen_changed.connect(hits.append)
    w.set_fullscreen_enabled(True)
    w._poll_fullscreen()
    w._poll_fullscreen()             # 异常：不翻转
    assert hits == [True]
    assert w._fs_last is True
    w.stop()


# ---------------------------------------------------------------- shell 能力
def test_on_top_applied_from_config_and_persisted(tmp_path):
    shell = _make_shell(tmp_path, {"on_top": False})
    flags = shell.overlay.windowFlags()
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)
    shell.set_on_top(True)
    assert shell.overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert shell._config.get("on_top") is True
    assert shell._config.saved >= 1
    shell._delete_runtime_marker()


def test_effective_through_is_user_or_auto(tmp_path):
    shell = _make_shell(tmp_path, {"mouse_through": False})
    assert shell.overlay.mouse_through is False
    # 用户开（菜单直写收编）
    shell.overlay.set_mouse_through(True)
    assert shell._user_mouse_through is True
    assert shell._config.get("mouse_through") is True
    assert shell.overlay.mouse_through is True
    # 用户关、光标自动开（HIDDEN 事件）→ 仍然有效
    shell.overlay.set_mouse_through(False)
    shell._on_cursor_visibility_changed("HIDDEN")
    assert shell._auto_cursor_hidden is True
    assert shell.overlay.mouse_through is True
    # 光标恢复 → 全关
    shell._on_cursor_visibility_changed("SHOWING")
    assert shell.overlay.mouse_through is False
    shell._delete_runtime_marker()


def test_cursor_restore_pending_flushed_on_grab_finished(tmp_path):
    shell = _make_shell(tmp_path, {"mouse_through": False})
    shell._on_cursor_visibility_changed("HIDDEN")
    assert shell.overlay.mouse_through is True
    # 拖拽中收到 SHOWING → 滞留
    shell.overlay._mouse_grab = object()
    shell._on_cursor_visibility_changed("SHOWING")
    assert shell._cursor_restore_pending is True
    assert shell.overlay.mouse_through is True   # 滞留未解除
    shell.overlay._mouse_grab = None
    shell._on_grab_finished()
    assert shell._cursor_restore_pending is False
    assert shell.overlay.mouse_through is False
    shell._delete_runtime_marker()


def _mouse_press(pos):
    return QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(*pos), QPointF(*pos),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def test_click_only_grab_with_lost_release_does_not_lock_passthrough(tmp_path):
    """缺陷回归：丢 release 的 click-only grab 不得把自动穿透态永久锁死。

    报告里的卡死帧（逐字段复现）：``_auto_cursor_hidden`` 已置位 + 一次丢
    release 的 click-only grab（``_press_global`` 为空，看门狗旧实现首行就
    返回）。此后光标恢复的 SHOWING 只被滞留，整窗穿透再也退不出去——用户
    看到的是"窗口看得见但点不到，点击零反应且无法自愈"。
    """
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        # 1) 光标隐藏 → 自动穿透（此刻无 grab，壳层不拦）
        shell._on_cursor_visibility_changed("HIDDEN")
        assert shell._auto_cursor_hidden is True
        assert overlay.mouse_through is True
        # 2) click-only grab 的 release 丢失（alt-tab / 模态框 / 屏迁移）
        overlay._mouse_grab = shell.sprite
        overlay._press_click_only = True
        assert overlay._press_global is None
        # 3) 光标恢复：被滞留
        shell._on_cursor_visibility_changed("SHOWING")
        assert shell._cursor_restore_pending is True
        assert overlay.mouse_through is True
        # 4) tick 路径的看门狗收尾（tick_driver.on_tick 每拍调用）
        overlay._check_stale_press()
        assert overlay._mouse_grab is None
        assert shell._cursor_transition_blocked() is False
        assert shell._cursor_restore_pending is False
        assert shell._auto_cursor_hidden is False
        assert overlay.mouse_through is False
    finally:
        shell._delete_runtime_marker()


def test_auto_cursor_hidden_converges_on_input_reaching_the_window(tmp_path):
    """自动穿透态在**真到达窗口**的输入上收敛（输入侧绕过 HIDDEN 闸）。

    输入能进来本身就是证据：原生穿透样式与窗口上的标记已经脱同步（正常开着
    穿透时点击根本到不了窗口），此刻恢复拦截是把状态收敛回一致。光标若真的
    还藏着，20Hz watcher 一拍内会重报 HIDDEN 重新置回——代价一拍，可接受。

    定位（别当通用自愈读）：这条时机只是上面那个"脱同步"场景的兜底，真正
    解开卡死的是看门狗（``_check_stale_press``）与显示/恢复（``_shown_cb``）
    两道；正常穿透期间点击到不了窗口。
    """
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        shell._on_cursor_visibility_changed("HIDDEN")   # 已确认隐藏：show 侧会被挡
        assert shell._auto_cursor_hidden is True
        assert overlay.mouse_through is True

        QApplication.sendEvent(overlay, _mouse_press((100, 100)))

        assert shell._auto_cursor_hidden is False
        assert overlay.mouse_through is False
    finally:
        shell._delete_runtime_marker()


def test_auto_cursor_hidden_holds_while_grab_in_flight(tmp_path):
    """grab 在途时输入侧收敛不得动手，只能记待办；该待办在 grab 收尾后必须
    被补做（只断言中间态是终审指出的盲区）——这是"拖拽中不误开/误关穿透 +
    收尾不漏账"的两半判据。"""
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        shell._on_cursor_visibility_changed("HIDDEN")
        overlay._mouse_grab = object()          # 拖拽/点击在途
        overlay._note_user_input()              # 输入侧收敛入口（press 事件首行）

        # 前半：此刻不许改穿透态，但请求不许被丢弃
        assert shell._auto_cursor_hidden is True
        assert overlay.mouse_through is True
        assert shell._converge_pending is True

        # 后半：grab 经"未命中"分支清掉 → 待办被补做（无 grab 即清）
        QApplication.sendEvent(overlay, _mouse_press((100, 100)))
        assert overlay._mouse_grab is None
        assert shell._converge_pending is False
        assert shell._auto_cursor_hidden is False
        assert overlay.mouse_through is False
    finally:
        shell._delete_runtime_marker()


def test_missed_left_press_notifies_grab_finished_and_flushes_pending(tmp_path):
    """P2 回归：未命中分支清 grab 也要走收尾通知，否则待办无人补做。

    可达链：``_on_cursor_visibility_changed("HIDDEN")`` 置自动穿透 → 遗留
    click-only grab（丢过 release）→ 样式脱同步时左键到达窗口但**未命中**
    sprite → 输入侧收敛先记下待办、同一事件里 grab 被清 → 旧实现直接赋值
    ``None``、壳层收尾钩子收不到事件，待办与穿透一起永久滞留。
    """
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        shell._on_cursor_visibility_changed("HIDDEN")   # 生产路径置位自动穿透
        assert shell._auto_cursor_hidden is True
        assert overlay.mouse_through is True
        overlay._mouse_grab = shell.sprite              # 遗留 click-only grab
        overlay._press_click_only = True

        # 未命中 sprite 的左键 press（CapSprite 全透明 → sprite_at 恒 None）
        QApplication.sendEvent(overlay, _mouse_press((100, 100)))

        assert overlay._mouse_grab is None
        assert overlay._press_click_only is False
        assert shell._converge_pending is False
        assert shell._auto_cursor_hidden is False
        assert overlay.mouse_through is False
    finally:
        shell._delete_runtime_marker()


def test_auto_cursor_hidden_converges_on_show(tmp_path):
    """显示/恢复侧收敛：不能带着上一个会话的自动穿透态回来（真恢复路径）。

    隐藏期探测表是停的（O2），恢复后若 watcher 一时报不出 SHOWING（探针失败
    报 UNKNOWN），滞留下来的穿透态会让整窗点不到。收敛排在可见性上报**之前**：
    探测表随即补拍，光标真隐藏时同一拍就会重新报 HIDDEN 并把状态置回——所以
    本用例把探针按失败态静音（UNKNOWN 不翻转任何状态），只验显示侧这一道。

    走完整 show → hide → show：首次显示没有待解除的状态；隐藏期"滞留没人
    冲刷"（grab 被 ``_end_drag`` 这类路径直接清掉，没走 grab 收尾钩子）的收口
    落在第二次显示上。
    """
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        shell._watcher._cursor_probe = lambda: "UNKNOWN"
        overlay.show()                                     # 首次显示
        shell._on_cursor_visibility_changed("HIDDEN")      # 光标隐藏 → 自动穿透
        assert overlay.mouse_through is True
        overlay._mouse_grab = object()                     # 拖拽在途
        shell._on_cursor_visibility_changed("SHOWING")     # 恢复被滞留
        assert shell._cursor_restore_pending is True
        overlay._mouse_grab = None                         # grab 被直接清掉
        overlay.hide()
        assert shell._auto_cursor_hidden is True           # 隐藏期保持（探测表已停）
        assert overlay.mouse_through is True

        overlay.show()                                     # 真恢复：收敛

        assert shell._auto_cursor_hidden is False
        assert shell._cursor_restore_pending is False
        assert overlay.mouse_through is False
    finally:
        shell._delete_runtime_marker()


def test_blocked_convergence_request_is_replayed_after_grab_finishes(tmp_path):
    """P2-1 回归：被 grab 闸住的收敛请求不许被无条件丢弃（真路径复现）。

    序列：``_on_cursor_visibility_changed("HIDDEN")``（生产上唯一能把
    ``_auto_cursor_hidden`` 置真的路径）→ 卡死的 click-only grab（丢 release
    形态）→ 隐藏后重新显示、探针静音 UNKNOWN（不产生回调）→ show 的收敛被
    grab 闸挡住（此刻不改穿透态，但**必须记下待办**）→ 看门狗清掉 grab →
    收尾时补做收敛，穿透解除。
    修前（带 HIDDEN 闸那版）：待办补做被闸门永远拦住（最后见过 HIDDEN，探针
    转 UNKNOWN 后再无回调），穿透永久卡在"看得见点不到"。
    """
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        shell._watcher._cursor_probe = lambda: "UNKNOWN"
        shell._on_cursor_visibility_changed("HIDDEN")   # 生产路径置位自动穿透
        assert shell._auto_cursor_hidden is True
        assert overlay.mouse_through is True
        overlay.hide()
        overlay._mouse_grab = shell.sprite        # 丢 release 的 click-only grab
        overlay._press_click_only = True

        overlay.show()                            # 收敛撞上 grab

        assert shell._auto_cursor_hidden is True  # 此刻不得改穿透态
        assert overlay.mouse_through is True
        # 待办快照：断言放在行为断言之后（字段本身也是被测行为——修前没有它）
        pending_at_show = getattr(shell, "_converge_pending", False)

        overlay._check_stale_press()              # 看门狗收尾（tick_driver 每拍调用）

        assert overlay._mouse_grab is None
        assert shell._auto_cursor_hidden is False
        assert overlay.mouse_through is False
        assert pending_at_show is True            # 请求记过待办，收尾时补做
    finally:
        shell._delete_runtime_marker()


class _RecordingThroughController:
    """记录 set_drag_active 调用的假穿透控制器（真控制器是 Win32/平台边界）。"""

    def __init__(self):
        self.drag_states: list[bool] = []

    def set_drag_active(self, active):
        self.drag_states.append(bool(active))

    def resume(self):
        pass

    def stop(self):
        pass


def test_end_drag_finishes_the_grab_through_the_existing_protocol(tmp_path):
    """屏事件收尾 grab（``_end_drag``）必须走既有 grab 收尾协议。

    旧实现自己清 ``_mouse_grab``/``_press_global`` 就返回：不清 click-only /
    拖拽提交旗标、不 ``set_drag_active(False)``、也不发壳层收尾通知。漏掉通知
    的那一笔最致命——壳层被 grab 闸住的穿透收敛（``_converge_pending``）与
    ``_auto_cursor_hidden`` 一起永久滞留 = 窗口看得见点不到。
    """
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        sprite = shell.sprite
        released: list = []
        sprite.on_release = released.append
        controller = _RecordingThroughController()
        overlay._input_controller = controller
        shell._on_cursor_visibility_changed("HIDDEN")   # 自动穿透（生产唯一置位路径）
        assert overlay.mouse_through is True
        # 在途会话（混装形态：两个旗标都占着——旧实现两个都不清）
        overlay._mouse_grab = sprite
        overlay._press_click_only = True
        overlay._drag_committed = True
        overlay._press_global = QPointF(10, 10).toPoint()
        shell._converge_auto_cursor_hidden()            # 收敛请求撞上 grab → 记待办
        assert shell._converge_pending is True
        assert shell._auto_cursor_hidden is True

        shell._end_drag()

        assert released == [QPointF(sprite.pos)]        # 按当前位置松手，且只松一次
        assert overlay._mouse_grab is None
        assert overlay._press_click_only is False       # 旧实现不清
        assert overlay._drag_committed is False         # 旧实现不清
        assert overlay._press_global is None
        assert controller.drag_states == [False]        # 旧实现不调
        assert shell._converge_pending is False         # 待办被补做
        assert shell._auto_cursor_hidden is False
        assert overlay.mouse_through is False
    finally:
        shell._delete_runtime_marker()


def test_end_drag_without_a_grab_touches_nothing(tmp_path):
    """没有在途会话：``_end_drag`` 不产生任何收尾副作用（幂等入口）。"""
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        released: list = []
        shell.sprite.on_release = released.append
        controller = _RecordingThroughController()
        overlay._input_controller = controller

        shell._end_drag()
        shell._end_drag()

        assert released == []
        assert controller.drag_states == []
        assert shell._converge_pending is False
    finally:
        shell._delete_runtime_marker()


def test_convergence_on_show_clears_and_watcher_rebuilds_passthrough(tmp_path):
    """P2-2 改判（撤销 HIDDEN 闸）：show/迁移侧收敛**先清**，真实 HIDDEN 由
    watcher 一拍内重建。

    屏迁移先显示新 overlay 再关旧的（可见性计数 1→2→1，闸门不翻转 → 不补探测），
    这一刻会清掉穿透、窗口在下一拍 50ms 轮询前错误拦截点击——这是**刻意接受的
    代价**：探针一旦恢复报 HIDDEN，穿透随即重建（≤1 拍自愈）。留着 HIDDEN 闸来
    挡这一拍的代价是永久卡死（``_auto_cursor_hidden`` 必由 HIDDEN 置位，探针转
    UNKNOWN 后再无回调，待办补做永远被拦）。
    """
    shell = _make_shell(tmp_path, {"mouse_through": False})
    try:
        overlay = shell.overlay
        shell._watcher._cursor_probe = lambda: "UNKNOWN"
        shell._on_cursor_visibility_changed("HIDDEN")   # 自动穿透已置位
        assert shell._auto_cursor_hidden is True
        overlay.hide()
        overlay.show()                                  # 迁移/恢复侧收敛：先清
        assert shell._auto_cursor_hidden is False
        assert overlay.mouse_through is False

        shell._on_cursor_visibility_changed("HIDDEN")   # watcher 重报 HIDDEN → 重建
        assert shell._auto_cursor_hidden is True
        assert overlay.mouse_through is True
    finally:
        shell._delete_runtime_marker()


def test_fullscreen_hides_and_restores(tmp_path):
    shell = _make_shell(tmp_path, {"auto_hide_fullscreen": True})
    shell.overlay.show()
    shell._on_fullscreen_changed(True)
    assert shell._auto_hidden is True
    assert not shell.overlay.isVisible()
    shell._on_fullscreen_changed(False)
    assert shell._auto_hidden is False
    assert shell.overlay.isVisible()
    shell.overlay.close()
    shell._delete_runtime_marker()


def test_fullscreen_hidden_stays_when_config_off(tmp_path):
    shell = _make_shell(tmp_path, {"auto_hide_fullscreen": False})
    # 配置关 → watcher 全屏门未启用（计时器未开）
    assert not shell._watcher._fs_timer.isActive()
    shell._delete_runtime_marker()


def test_runtime_marker_lifecycle(tmp_path):
    shell = _make_shell(tmp_path)
    shell.start()
    markers = _markers(tmp_path)
    assert len(markers) == 1
    data = json.loads(markers[0].read_text(encoding="utf-8"))
    body = shell.sprite.body_rect()
    origin = shell.overlay.geometry().topLeft()
    assert (data["x"], data["y"]) == (origin.x() + body.x(), origin.y() + body.y())
    assert (data["w"], data["h"]) == (body.width(), body.height())
    # 移动 → 节流后更新
    shell.sprite.set_pos(QPointF(500, 400))
    shell._last_marker_write = 0.0
    shell._on_main_sprite_moved(shell.sprite)
    data2 = json.loads(markers[0].read_text(encoding="utf-8"))
    assert (data2["x"], data2["y"]) == (origin.x() + 500 + 10, origin.y() + 400 + 10)
    shell.stop()
    assert _markers(tmp_path) == []


def test_tray_toggle_visibility(tmp_path):
    shell = _make_shell(tmp_path)
    shell.overlay.show()
    shell._toggle_pet_visible()
    assert not shell.overlay.isVisible()
    shell._toggle_pet_visible()
    assert shell.overlay.isVisible()
    shell.overlay.close()
    shell._delete_runtime_marker()


def test_refresh_settings_reapplies(tmp_path):
    shell = _make_shell(tmp_path, {"mouse_through": False, "on_top": True})
    shell._config.set("mouse_through", True)
    shell._config.set("on_top", False)
    shell.refresh_settings()
    assert shell.overlay.mouse_through is True
    assert not (shell.overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    shell._delete_runtime_marker()


def test_refresh_rate_change_rewires_tick_interval(tmp_path, monkeypatch):
    """同分辨率切刷新率（60→180Hz）时 refreshRateChanged 应让 tick 间隔立即重读。

    回归：此前只接 geometryChanged，同分辨率模式切换不重读刷新率，
    运行中的桌宠卡在 16ms(60Hz) 档直到重启（任务A 实锤缺口）。刷新率取**屏枚举**
    里最高一档（单窗覆盖整个虚拟桌面），故这台"机器"上只有这块假屏。
    """
    screen = FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040), refresh=60.0)
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [screen]))
    shell = _make_shell(tmp_path, {"on_top": False}, screen=screen)
    shell.start()
    driver = shell.overlay.tick_driver
    assert driver._timer.interval() == 16
    screen.set_refresh(180.0)
    assert driver._timer.interval() == 6
    shell._delete_runtime_marker()
