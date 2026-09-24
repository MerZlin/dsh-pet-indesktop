# -*- coding: utf-8 -*-
"""A2 回归：三处「续播/重启当前 clip」必须走同一出口、行为逐点一致。

三处入口（`pet/window.py`）：
1. `_on_anim_ended` 拖拽续播（drag 动画自然播完且仍在拖拽）；
2. `_on_anim_ended` 弹射飞行续播（throw 高速段悬空动画播完，原地循环）；
3. `_end_move_or_anim` 多圈移动中间圈续圈。

共同语义（抽 `_restart_current_clip` 单一出口的目标）：
- `jumpToFrame(0)` 回首帧 + `start()` 重新起播；
- 重启成功必须把 **窗口侧** `_ended_fired` 复位——末帧收口先置位该标志，
  重启若不清零，下一圈末帧的 `not self._ended_fired` 判定被挡住，续圈后
  动画链永久停摆（P0-1「三份拷贝漏了一份」同源）；
- `start()` 被拒时保留各路径原有的降级（拖拽/弹射：回退可播放动画 +
  安排重试；移动：清移动计划走播完链）。

断言口径：三处入口都先把 `_ended_fired` 置为 True（等价末帧路径真实状态），
调用入口后检查「回首帧 + start + 复位」。本文件只做行为断言（不 mock 私有
方法），抽函数前/后可比对。
"""

from __future__ import annotations

import random

import pytest
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.config import Config
from pet.window import PetWindow

NAMES = [
    catalog.IDLE,
    catalog.TURN,
    catalog.MOVES[0],
    catalog.CLICKS[0],
    catalog.DRAG,
    "写代码",
]


class FakeClip(QObject):
    """与 WebMClip 形状一致的假 clip：可配置 start() 被拒，记录跳帧/启停。"""

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, fail: bool = False, parent=None):
        super().__init__(parent)
        self.fail = fail
        self._running = False
        self.speed = 1.0
        self.jump_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False
        self.stop_calls += 1

    def start(self):
        self.start_calls += 1
        if self.fail:
            return False
        self._running = True
        return True

    def jumpToFrame(self, frame_index):
        self.jump_calls += 1
        return frame_index <= 0

    def set_playback_speed(self, speed):
        self.speed = speed

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return 10

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class FakeLibrary:
    def __init__(self, failing: set[str] | None = None):
        self._failing = set(failing or ())
        self._clips = {name: FakeClip(fail=name in self._failing) for name in NAMES}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()
        self.move_strides = {catalog.MOVES[0]: 100.0}
        self.move_curves = {}

    def names(self):
        return list(NAMES)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 10

    def duration(self, name):
        return 1.0

    def set_failing(self, name: str, failing: bool) -> None:
        self._clips[name].fail = failing


class _FakeScreen:
    def availableGeometry(self):
        return QRect(0, 0, 4000, 2000)

    def devicePixelRatio(self):
        return 1.0


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_win(tmp_path, lib):
    win = PetWindow(lib, Config(base=tmp_path))
    win._screen_available = lambda: _FakeScreen()
    win.move(500, 300)
    return win


# ---------------------------------------------------------------------------
# 路径 1：拖拽续播（_on_anim_ended 的 drag 分支）
# ---------------------------------------------------------------------------


def test_drag_restart_jumps_first_frame_and_resets_ended_flag(app, tmp_path):
    """拖拽动画自然播完：回首帧 restart，且窗口侧 _ended_fired 复位。"""
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        win._switch(catalog.DRAG)
        clip = lib.movie(catalog.DRAG)
        win._dragging = True
        win._ended_fired = True  # 末帧路径先置位（_end_move_or_anim/_on_clip_finished）
        jumps0, starts0 = clip.jump_calls, clip.start_calls

        win._on_anim_ended(catalog.DRAG)

        assert win.anim == catalog.DRAG, "拖拽中播完必须原地续播，不推进动画链"
        assert clip.jump_calls == jumps0 + 1, "续播必须回首帧（jumpToFrame(0)）"
        assert clip.start_calls == starts0 + 1, "续播必须重新起播（start()）"
        assert win._ended_fired is False, "续播成功后窗口侧 _ended_fired 必须复位，否则下一圈末帧永远检测不到"
        assert clip._running is True
    finally:
        win.close()
        app.processEvents()


def test_drag_restart_rejected_keeps_chain_advanceable(app, tmp_path):
    """拖拽续播被拒：保留原降级（安排重试）并把 _ended_fired 留在可再检测态。"""
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        win._switch(catalog.DRAG)
        win._dragging = True
        lib.set_failing(catalog.DRAG, True)  # 拖拽 clip 退役池卡死
        win._ended_fired = True

        win._on_anim_ended(catalog.DRAG)

        assert win._pending_switch == catalog.DRAG, "被拒必须登记待重试（原降级语义）"
        assert win._switch_retry_timer.isActive(), "被拒必须安排稍后重试"
        assert win._ended_fired is False, "重启被拒也必须回到「末帧可再检测」，不得留下永久挡箭牌"
    finally:
        win.close()
        app.processEvents()


# ---------------------------------------------------------------------------
# 路径 2：弹射飞行续播（_on_anim_ended 的 throw 分支）
# ---------------------------------------------------------------------------


def test_throw_flight_restart_jumps_first_frame_and_resets_ended_flag(app, tmp_path):
    """高速飞行中悬空动画播完：原地循环（回首帧 restart + 复位 _ended_fired）。"""
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        win._enter_physics_mode("throw")
        win._phys_vel[:] = [900.0, 0.0]  # 高速段
        win._switch(catalog.DRAG)
        clip = lib.movie(catalog.DRAG)
        win._ended_fired = True
        jumps0, starts0 = clip.jump_calls, clip.start_calls

        win._on_anim_ended(catalog.DRAG)

        assert win.anim == catalog.DRAG, "飞行途中循环当前 clip，不掷骰"
        assert clip.jump_calls == jumps0 + 1
        assert clip.start_calls == starts0 + 1
        assert win._ended_fired is False, "飞行续播同样必须复位 _ended_fired"
        assert clip._running is True
    finally:
        win._stop_physics()
        win.close()
        app.processEvents()


def test_throw_flight_restart_rejected_keeps_chain_advanceable(app, tmp_path):
    """弹射续播被拒：保留原降级（安排重试）并把 _ended_fired 留在可再检测态。"""
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        win._enter_physics_mode("throw")
        win._phys_vel[:] = [900.0, 0.0]
        win._switch(catalog.DRAG)
        lib.set_failing(catalog.DRAG, True)
        win._ended_fired = True

        win._on_anim_ended(catalog.DRAG)

        assert win._pending_switch == catalog.DRAG
        assert win._switch_retry_timer.isActive()
        assert win._ended_fired is False
    finally:
        win._stop_physics()
        win.close()
        app.processEvents()


# ---------------------------------------------------------------------------
# 路径 3：多圈移动中间圈续圈（_end_move_or_anim）
# ---------------------------------------------------------------------------

STRIDE = 100.0


def _pin_rng(monkeypatch, distance: float) -> None:
    monkeypatch.setattr(random, "randint", lambda a, b: int(distance))
    monkeypatch.setattr(random, "choice", lambda seq: seq[-1])


def _start_weighted_move(win, monkeypatch, distance: float, monkeypatch_heavy: bool = True):
    """建立移动计划并返回 (plan, clip)；distance=216 时 stride=72 → 3 圈。"""
    if monkeypatch_heavy:
        monkeypatch.setattr(win, "_rebuild_frame", lambda: None)
        monkeypatch.setattr(win, "update", lambda: None)
        monkeypatch.setattr(win, "_predict_prewarm", lambda *a: None)
    _pin_rng(monkeypatch, distance)
    assert win._try_move(catalog.MOVES[0]) is True
    plan = win._move_plan
    assert plan is not None
    return plan, win.lib.movie(catalog.MOVES[0])


def test_move_middle_loop_restart_resets_ended_flag(app, tmp_path, monkeypatch):
    """多圈移动中间圈末：续圈回首帧 + `start()`，并把 _ended_fired 复位。

    真实时序：末帧回调触发中间圈续圈（此时 `_ended_fired` 尚为 False），
    续圈成功后必须回到 False 的可再检测态。第一圈末帧后的第二次末帧回调
    命中续圈分支时 `_ended_fired` 已被置位——若续圈不复位，后续圈末帧的
    收口就被永久挡住（P0-1 同源的冻结形态）。
    """
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        plan, clip = _start_weighted_move(win, monkeypatch, 216)  # 3 圈
        assert plan["loops"] == 3, plan
        ended: list = []
        monkeypatch.setattr(win, "_on_anim_ended", lambda name: ended.append(name))
        starts0, jumps0 = clip.start_calls, clip.jump_calls

        # 第一圈末帧：中间圈续圈
        win._on_frame(catalog.MOVES[0], 9)

        assert plan["loops_done"] == 1
        assert win._move_plan is plan, "中间圈不得清计划"
        assert ended == [], "中间圈不得推动画链"
        assert clip.start_calls == starts0 + 1, "中间圈必须重新 start()"
        assert clip.jump_calls == jumps0 + 1, "续圈必须回首帧（jumpToFrame(0)）"
        assert win._ended_fired is False, "续圈后必须复位 _ended_fired，否则后续圈末帧收口被挡住（冻结）"

        # 第二圈末帧：续圈分支在 _ended_fired 已被置位后仍须成立
        win._on_frame(catalog.MOVES[0], 9)
        assert plan["loops_done"] == 2
        assert ended == []
        assert win._ended_fired is False

        # 末圈末帧：收口成立（清计划 + 推进播完链）
        win._on_frame(catalog.MOVES[0], 9)
        assert win._move_plan is None
        assert win._move_timer.isActive() is False
        assert ended == [catalog.MOVES[0]]
    finally:
        win.close()
        app.processEvents()


def test_move_middle_loop_restart_rejected_cancels_move(app, tmp_path, monkeypatch):
    """多圈移动续圈被拒：清移动计划并走播完链（原降级语义），链不冻结。"""
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        plan, _clip = _start_weighted_move(win, monkeypatch, 216)  # 3 圈
        ended: list = []
        monkeypatch.setattr(win, "_on_anim_ended", lambda name: ended.append(name))
        lib.set_failing(catalog.MOVES[0], True)  # 续圈 start() 被拒

        win._on_frame(catalog.MOVES[0], 9)  # 第一圈末帧：续圈被拒

        assert win._move_plan is None, "续圈被拒必须清移动计划"
        assert win._move_timer.isActive() is False, "清计划必须停移动守卫表"
        assert ended == [catalog.MOVES[0]], "续圈被拒落播完链（原降级）"
        assert win._ended_fired is True, "播完链由 _end_move_or_anim 置位"
    finally:
        win.close()
        app.processEvents()


# ---------------------------------------------------------------------------
# 单一出口守卫：三条路径都必须经由 _restart_current_clip
# ---------------------------------------------------------------------------


def test_move_restart_branch_reachability_is_guarded_by_ended_flag(app, tmp_path, monkeypatch):
    """记录中间圈续圈分支的可达性边界（事实钉桩，不是缺陷断言）。

    `_on_frame` 的末帧分发守卫是 `is_last and not self._ended_fired`：标志一旦
    被置位，同一末帧的后续回调直接返回，**不会**再进 `_end_move_or_anim`。
    因此正常跨圈时序里，中间圈续圈每次都在标志为 False 时进入；续圈若不复位
    标志，下一圈的末帧收口就会被挡住（见上一条用例对复位的要求）。
    「标志已被外部置位」这种状态在本仓库当前控制流里不可达，本用例把这条
    边界钉住，避免后人误以为该分支自带复位能力（也避免为它写无法复现的
    缺陷测试）。
    """
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        plan, _clip = _start_weighted_move(win, monkeypatch, 216)  # 3 圈
        ended: list = []
        monkeypatch.setattr(win, "_on_anim_ended", lambda name: ended.append(name))

        win._ended_fired = True
        win._on_frame(catalog.MOVES[0], 9)  # 标志已置位 → 末帧分发守卫直接挡回

        assert plan["loops_done"] == 0, "标志置位时不得再进末帧收口（守卫语义）"
        assert win._move_plan is plan
        assert ended == []
    finally:
        win.close()
        app.processEvents()


def test_three_restart_paths_share_single_exit(app, tmp_path, monkeypatch):
    """三条续播路径都必须经 `_restart_current_clip` 单一出口。

    P0-1 的教训是「三份拷贝漏了一份」：本用例把「只有一个出口」变成机器门禁——
    任何一处再抄一份内联重启（绕开单一出口）都会在此红。
    """
    if getattr(PetWindow, "_restart_current_clip", None) is None:
        pytest.skip("单一出口尚未抽取（测试先行阶段）")

    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        calls: list = []

        def _spy(name, **_kw):
            calls.append(name)
            return True

        monkeypatch.setattr(win, "_restart_current_clip", _spy)

        # 路径 1：拖拽续播
        win._switch(catalog.DRAG)
        win._dragging = True
        win._on_anim_ended(catalog.DRAG)
        win._dragging = False
        # 路径 2：弹射飞行续播
        win._enter_physics_mode("throw")
        win._phys_vel[:] = [900.0, 0.0]
        win._on_anim_ended(catalog.DRAG)
        # 路径 3：多圈移动中间圈续圈
        win._stop_physics()
        plan, _clip = _start_weighted_move(win, monkeypatch, 216)  # 3 圈
        win._on_frame(catalog.MOVES[0], 9)  # 第一圈末帧 → 中间圈续圈

        assert plan["loops_done"] == 1
        assert calls == [catalog.DRAG, catalog.DRAG, catalog.MOVES[0]], calls
    finally:
        win.close()
        app.processEvents()


# ---------------------------------------------------------------------------
# 单一出口契约本身（抽出后的单元级；抽函数前会 skip）
# ---------------------------------------------------------------------------


def _restart_helper_or_skip():
    helper = getattr(PetWindow, "_restart_current_clip", None)
    if helper is None:
        pytest.skip("单一出口尚未抽取（测试先行阶段）")
    return helper


def test_restart_helper_contract_clears_park_and_resets_flag(app, tmp_path):
    """出口契约：清驻留态 → 回首帧 → 复位 `_ended_fired` → start()；返回值即结果。"""
    _restart_helper_or_skip()
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        clip = lib.movie(catalog.DRAG)
        win._switch(catalog.DRAG)
        clip._soft_parked = True  # 圈末软停驻留（移动续圈的真实起点）
        win._ended_fired = True
        jumps0, starts0 = clip.jump_calls, clip.start_calls

        assert win._restart_current_clip(catalog.DRAG) is True

        assert clip._soft_parked is False, "必须清软停驻留态，保证 start() 走 fresh start"
        assert clip.jump_calls == jumps0 + 1, "必须回首帧"
        assert clip.start_calls == starts0 + 1, "必须重新起播"
        assert win._ended_fired is False, "成功后必须复位末帧标志"
        assert clip._running is True

        # 被拒：返回 False，且 `_ended_fired` 不能停在 True（链须可再推进）
        lib.set_failing(catalog.DRAG, True)
        win._ended_fired = True
        assert win._restart_current_clip(catalog.DRAG) is False
        assert win._ended_fired is False, "被拒同样要回到可再检测态"
    finally:
        win.close()
        app.processEvents()


def test_restart_helper_pending_retry_identity_bound(app, tmp_path):
    """出口的待重试清理绑定动画身份：只清「正是本动画」的待重试。"""
    _restart_helper_or_skip()
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        win._switch(catalog.DRAG)

        # 待重试的是别的动画：重启本动画不得吞掉它
        win._pending_switch = catalog.TURN
        win._pending_switch_link = False
        win._switch_retry_count = 2
        win._switch_retry_timer.start()
        assert win._restart_current_clip(catalog.DRAG) is True
        assert win._pending_switch == catalog.TURN, "无关动画的待重试不得被吞掉"
        assert win._switch_retry_count == 2
        assert win._switch_retry_timer.isActive()

        # 待重试的正是本动画：重启成功即清空
        win._pending_switch = catalog.DRAG
        win._pending_switch_link = True
        win._switch_retry_count = 3
        assert win._restart_current_clip(catalog.DRAG) is True
        assert win._pending_switch is None
        assert win._pending_switch_link is False
        assert win._switch_retry_count == 0
        assert win._switch_retry_timer.isActive() is False
    finally:
        win.close()
        app.processEvents()


def test_restart_helper_pushes_recycle_threshold(app, tmp_path):
    """出口补推回收阈值（原先拖拽/弹射两处各自补推，移动续圈没有）。"""
    _restart_helper_or_skip()
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        clip = lib.movie(catalog.DRAG)
        pushes: list = []
        clip.set_recycle_minutes = lambda minutes: pushes.append(minutes)  # type: ignore[method-assign]
        win._switch(catalog.DRAG)
        pushes.clear()  # _switch 也会推一次，只关心出口自身
        assert win._restart_current_clip(catalog.DRAG) is True
        assert pushes == [win._ffmpeg_recycle_minutes], pushes
    finally:
        win.close()
        app.processEvents()


def test_move_middle_loop_restart_position_never_regresses_on_sync_emit(app, tmp_path, monkeypatch):
    """GifClip 语义：`jumpToFrame(0)` 同步发 frameChanged(0)。

    续圈时 `loops_done` 若在回首帧之后才递增，同步回调会按旧圈数定位——
    圈边界位置瞬态回退约一个步幅再前进（GLM 终审 F-2）。递增必须先于
    `jumpToFrame(0)`；WebMClip 消费定时器异步发帧不受影响，但顺序契约
    对两条媒体路径都必须成立。
    """
    import pet.window as window_mod

    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    try:
        clip = lib.movie(catalog.MOVES[0])
        orig_jump = clip.jumpToFrame

        def sync_emit_jump(i):  # GifClip(QMovie) 契约：跳帧成功同步发 frameChanged
            result = orig_jump(i)
            clip.frameChanged.emit(0)
            return result

        clip.jumpToFrame = sync_emit_jump

        indices: list = []
        orig_mpaf = window_mod.move_position_at_frame

        def spy_mpaf(plan, idx):
            indices.append(idx)
            return orig_mpaf(plan, idx)

        monkeypatch.setattr(window_mod, "move_position_at_frame", spy_mpaf)
        plan, _clip = _start_weighted_move(win, monkeypatch, 216)  # 3 圈
        assert plan["loops"] == 3

        indices.clear()
        win._on_frame(catalog.MOVES[0], 9)  # 第一圈末帧 → 中间圈续圈

        # 末帧定位 idx=9 之后，续圈同步回调必须按新圈数定位（idx=10），
        # 不得回落到旧圈起点（idx=0）。
        assert indices == [9, 10], f"圈边界定位序列回退: {indices}"
    finally:
        win.close()
        app.processEvents()
