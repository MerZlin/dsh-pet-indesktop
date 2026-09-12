# -*- coding: utf-8 -*-
"""弹射飞行动画链规则（实机卡顿定案修复）回归测试。

背景：观测实机 26 次 >100ms GUI 卡顿中 19 次是动画切换命中冷首帧
（GUI 线程同步拉 ffmpeg 解码，实测 ~100ms/次）。弹射路径的切换全部
是事件驱动，预测式预热覆盖不到（拖拽打断作废预测代次）。规则：

1. 进入 throw 物理模式：后台预热 idle 首帧（落地回待机的切换目标）；
2. 松手甩出（进入 throw）时不在松手帧现场掷骰切待机；
3. 飞行途中动画播完：不推进随机链，切到"悬空"动画（drag clip）并循环；
4. 落地停稳（_stop_physics 退出 throw）：悬空动画切回待机，链自然恢复；
5. 非 throw 的物理模式（drag）与正常模式行为不变。

全部用假 clip/假库驱动，不起真实 ffmpeg。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.config import Config
from pet.window import PetWindow

@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])

class FakeClip(QObject):
    """与 WebMClip 接口兼容的假播放器：记录预热/启停/跳帧。"""

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, name: str, frame_count: int = 10, fps: float = 10.0):
        super().__init__()
        self.name = name
        self.frame_count = max(1, frame_count)
        self.fps = max(0.1, float(fps))
        self.speed = 1.0
        self.decode_throttle_divisor = 1
        self.warm_calls = 0
        self.stop_count = 0
        self.start_count = 0
        self.jump_calls = 0
        self.recycle_minutes_calls: list = []
        self.clear_display_calls = 0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def clear_display_frame(self):
        self.clear_display_calls += 1

    def stop(self):
        self.stop_count += 1

    def start(self):
        self.start_count += 1
        return True

    def jumpToFrame(self, frame_index):
        self.jump_calls += 1
        return int(frame_index) <= 0

    def set_playback_speed(self, speed):
        self.speed = max(0.1, float(speed))

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return self.frame_count

    def duration(self):
        return self.frame_count / self.fps / self.speed

    def currentTimeSeconds(self):
        return 0.0

    def warm_first_frame(self):
        self.warm_calls += 1

    def set_decode_throttle(self, divisor):
        self.decode_throttle_divisor = max(1, int(divisor))

    def set_recycle_minutes(self, minutes):
        self.recycle_minutes_calls.append(minutes)

class FakeLibrary:
    """只含核心动画名的假素材库。"""

    def __init__(self, frame_count: int = 10, fps: float = 10.0):
        self.no_mirror = set()
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self._frame_count = frame_count
        self._fps = fps
        self.warmed: list[str] = []
        self._clips = {}
        for n in self._names():
            self._clips[n] = FakeClip(n, frame_count=frame_count, fps=fps)

    @staticmethod
    def _names() -> list[str]:
        return [
            catalog.IDLE,
            catalog.TURN,
            catalog.MOVES[0],
            catalog.CLICKS[0],
            catalog.DRAG,
            "写代码",
            "吃白饭",
        ]

    def names(self):
        return list(self._clips)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return self._clips[name].frameCount()

    def duration(self, name):
        return self._clips[name].duration()

    def warm_predicted(self, name):
        self.warmed.append(name)
        self._clips[name].warm_first_frame()

def _make_window(tmp_path, **cfg_overrides):
    cfg = Config(base=tmp_path)
    for k, v in cfg_overrides.items():
        cfg.set(k, v)
    return PetWindow(FakeLibrary(), cfg)

def test_throw_entry_warms_landing_idles(tmp_path, app):
    """进入 throw 物理模式：idle 池全部后台预热（落地回待机目标必热）。"""
    win = _make_window(tmp_path)
    idle_clip = win.lib.movie(catalog.IDLE)
    idle_clip.warm_calls = 0
    win._enter_physics_mode('throw')
    assert idle_clip.warm_calls >= 1
    win._physics_mode = None
    win.close()

def test_drag_entry_does_not_warm(tmp_path, app):
    """drag 模式不触发落地预热（拖拽不甩出就没有落地切换）。"""
    win = _make_window(tmp_path)
    idle_clip = win.lib.movie(catalog.IDLE)
    idle_clip.warm_calls = 0
    win._enter_physics_mode('drag')
    assert idle_clip.warm_calls == 0
    win._physics_mode = None
    win.close()

def test_anim_end_during_throw_switches_to_flight_anim(tmp_path, app):
    """高速飞行途中动作播完：切到悬空动画（drag clip），不推进随机链。"""
    win = _make_window(tmp_path)
    win._switch("写代码")  # 击飞前正在播动作
    assert win.anim == "写代码"
    win._enter_physics_mode('throw')
    win._phys_vel[:] = [900.0, 0.0]  # 高速飞行段
    win._on_anim_ended("写代码")
    assert win.anim == catalog.DRAG  # 固定悬空动画，而非随机链目标
    win._stop_physics()
    win.close()

def test_anim_end_during_throw_loops_flight_anim(tmp_path, app):
    """悬空动画自身播完且仍在高速飞行：原地循环（同 clip 重启），不掷骰。"""
    win = _make_window(tmp_path)
    win._enter_physics_mode('throw')
    win._phys_vel[:] = [900.0, 0.0]
    win._switch(catalog.DRAG)
    drag_clip = win.lib.movie(catalog.DRAG)
    start_before = drag_clip.start_count
    jump_before = drag_clip.jump_calls
    win._on_anim_ended(catalog.DRAG)
    assert win.anim == catalog.DRAG            # 没切走
    assert drag_clip.jump_calls > jump_before  # 回首帧
    assert drag_clip.start_count > start_before  # 重新起播
    win._stop_physics()
    win.close()

def test_anim_end_during_throw_low_speed_allows_warm_pool(tmp_path, app):
    """低速段（滚动/滑动，< THROW_SLOW_ANIM_SPEED）：放行 idle/turn 池切换。

    用户实机要求"低速也可以播动作"；idle/turn 两池首帧必热（idle 起飞
    时已预热、turn pinned 常驻），不引入冷解码。动作/移动池仍不开放。
    """
    win = _make_window(tmp_path)
    win._switch("写代码")
    win._enter_physics_mode('throw')
    win._phys_vel[:] = [120.0, 0.0]  # 低速段
    win._on_anim_ended("写代码")
    assert win.anim in (*win.idles, *win.turns)  # 只许必热池
    assert win.anim != catalog.DRAG              # 低速不再强制悬空
    win._stop_physics()
    win.close()

def test_low_speed_pool_excludes_current(tmp_path, app):
    """低速段池切换排除当前动画（避免同名自切）。"""
    win = _make_window(tmp_path)
    win._enter_physics_mode('throw')
    win._phys_vel[:] = [100.0, 0.0]
    win._switch(catalog.IDLE)
    win._on_anim_ended(catalog.IDLE)
    # idles 只有 1 个且被排除 → 唯一合法去向是 turn
    assert win.anim == catalog.TURN
    win._stop_physics()
    win.close()

def test_landing_switches_flight_anim_back_to_idle(tmp_path, app):
    """落地停稳（_stop_physics 退出 throw）：悬空动画切回待机。"""
    win = _make_window(tmp_path)
    win._enter_physics_mode('throw')
    win._switch(catalog.DRAG)
    win._stop_physics()
    assert win._physics_mode is None
    assert win.anim == catalog.IDLE
    win.close()

def test_stop_physics_from_drag_mode_keeps_anim(tmp_path, app):
    """非 throw 的 _stop_physics（如拖拽死区停下）：不做落地切换。"""
    win = _make_window(tmp_path)
    win._enter_physics_mode('drag')
    win._switch(catalog.DRAG)
    win._stop_physics()
    assert win.anim == catalog.DRAG  # 不切（松手死区路径自行处理回待机）
    win.close()

def test_anim_end_normal_mode_chain_unaffected(tmp_path, app):
    """正常模式（无物理）：动画链照旧推进（回归守卫）。"""
    win = _make_window(tmp_path)
    win._switch("写代码")
    win._on_anim_ended("写代码")
    assert win.anim != "写代码"  # 链推进了（具体目标由概率决定，不锁定）
    win.close()

def test_low_speed_plays_warm_rolled_act(tmp_path, app, monkeypatch):
    """低速段掷骰掷中"首帧已热"的动作：直接播它（不退回 idle/turn 池）。"""
    import random as _random
    monkeypatch.setattr(_random, "random", lambda: 0.5)   # 命中动作分支
    monkeypatch.setattr(_random, "choice", lambda lst: lst[0])  # 池首
    win = _make_window(tmp_path)
    win._switch(catalog.IDLE)
    win._enter_physics_mode('throw')
    win._phys_vel[:] = [120.0, 0.0]
    # acts 池内顺序由 hash 随机化决定（进程级），不能假定具体名字：
    # 暖"掷骰会选中的那一个"（choice 固定池首），断言切到的就是它。
    win.lib.movie(win.acts[0])._first_image = object()  # 标记首帧已热
    win._on_anim_ended(catalog.IDLE)
    assert win.anim == win.acts[0]
    win._stop_physics()
    win.close()

def test_low_speed_cold_rolled_act_falls_back(tmp_path, app, monkeypatch):
    """低速段掷骰掷中冷目标：不追（零冷解码），退回必热的 idle/turn 池。"""
    import random as _random
    monkeypatch.setattr(_random, "random", lambda: 0.5)
    monkeypatch.setattr(_random, "choice", lambda lst: lst[0])
    win = _make_window(tmp_path)
    win._switch(catalog.IDLE)
    win._enter_physics_mode('throw')
    win._phys_vel[:] = [120.0, 0.0]
    win._on_anim_ended(catalog.IDLE)  # 写代码首帧冷 → 退回池排除 IDLE → turn
    assert win.anim == catalog.TURN
    win._stop_physics()
    win.close()

def test_tick_low_speed_transition_switches_out_of_drag(tmp_path, app):
    """降速进入低速段的同一 tick 立即切出悬空动画（不等 clip 播完）。

    实机反馈：低速滚动仍是悬空姿势——拖拽 clip 时长可能超过整个低速段，
    等动画自然结束永远轮不到切换。高速段 tick 不应过渡。
    """
    win = _make_window(tmp_path)
    win._enter_physics_mode('throw')
    win._switch(catalog.DRAG)
    win._phys_pos[:] = [200.0, 200.0]
    win._phys_vel[:] = [900.0, 0.0]  # 高速段
    win._tick_throw_physics(0.016)
    assert win.anim == catalog.DRAG
    assert getattr(win, '_throw_slow_switched', False) is False
    win._phys_vel[:] = [100.0, 0.0]  # 降速入段
    win._tick_throw_physics(0.016)
    assert win._throw_slow_switched is True
    assert win.anim != catalog.DRAG
    assert win.anim in (*win.idles, *win.turns, *win.acts, *win.moves)
    win._stop_physics()
    win.close()
