# -*- coding: utf-8 -*-
"""闲置降帧（性能调研 §4.3）回归测试。

需求锁定：
- 用单调时钟记录最近一次交互；闲置超过阈值（默认 30s，可配置）且窗口可见
  时，动画按时间线隔帧呈现（24fps 素材 → 12fps 效果），动画时长不变；
- 任何交互（鼠标命中/点击/拖拽/菜单/键盘消费/联动事件）立刻回满帧率；
- Agent 联动忙碌（dsh 在干活）视为活跃，不降帧；
- 隐藏/不可见维持现有全停语义，不降帧也不额外行为；
- 降帧必须按源时间线跳帧（elapsed time 算目标帧），不允许改播放速率让
  动画时间变慢/变快；帧号契约：frameChanged/currentFrameNumber 携带
  0-based 素材源时间线帧号（显示帧索引），与 1-based 播放计数分离，
  reader 队列满丢帧后显示帧索引仍锚定素材时间线（P1 复审）；
- 设置页开关默认关（灰度）。
"""
from __future__ import annotations

import queue

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QFocusEvent, QImage, QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.agent_link import AgentLinkManager
from pet.config import Config
from pet.modern_settings_dialog import ModernSettingsDialog
from pet.webm_clip import WebMClip
from pet.window import PetWindow

NAMES = [
    catalog.IDLE,
    catalog.TURN,
    catalog.MOVES[0],
    catalog.CLICKS[0],
    catalog.DRAG,
    "写代码",
]


class FakeClock:
    """可注入的单调时钟：测试完全掌控时间流逝，零 sleep、零抖动。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeClip(QObject):
    """与 WebMClip 接口兼容的极简假播放器，记录启停与播放速率。"""

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.stop_count = 0
        self.start_count = 0
        self.speed = 1.0
        self._frame_index = 0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False
        self.stop_count += 1

    def start(self):
        self._running = True
        self.start_count += 1
        return True

    def jumpToFrame(self, frame_index):
        if frame_index <= 0:
            self._frame_index = 0
            return True
        return False

    def set_playback_speed(self, speed):
        self.speed = speed

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return self._frame_index

    def frameCount(self):
        return 1

    def duration(self):
        return 1.0

    def currentTimeSeconds(self):
        return 0.0


class FakeLibrary:
    """只包含核心动画名的假素材库，避免测试拉真 ffmpeg。"""

    def __init__(self, frame_count: int = 10):
        self._clips = {name: FakeClip() for name in NAMES}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = None
        self.no_mirror = set()
        self._frame_count = frame_count

    def names(self):
        return list(NAMES)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return self._frame_count

    def duration(self, name):
        return 1.0


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_config(tmp_path, *, enabled: bool = False, threshold: float = 30.0):
    cfg = Config(base=tmp_path)
    cfg.set("idle_low_fps_enabled", enabled)
    cfg.set("idle_low_fps_threshold", threshold)
    return cfg


def _press(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        pos,
        global_pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _release(pos: QPointF, global_pos: QPointF) -> QMouseEvent:
    return QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        pos,
        global_pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


class _FlakyQueue:
    """模拟 reader 有界队列：每隔 drop_every 次 put 抛 queue.Full（丢帧）。

    drop_every=0 表示永不丢帧。记录已交付项（reader 入队成功者）。
    """

    def __init__(self, drop_every: int):
        self._drop_every = drop_every
        self._puts = 0
        self.items = []

    def put(self, item, timeout=0):
        self._puts += 1
        if self._drop_every > 0 and self._puts % self._drop_every == 0:
            raise queue.Full
        self.items.append(item)


# ============================================================================
# 1. 配置：默认关、阈值 30s、可持久化与归一化
# ============================================================================
class TestConfig:
    def test_defaults_off_with_30s_threshold(self, tmp_path):
        cfg = Config(base=tmp_path)
        assert cfg.get("idle_low_fps_enabled") is False
        assert cfg.get("idle_low_fps_threshold") == 30.0

    def test_round_trip_and_clamp(self, tmp_path):
        cfg = Config(base=tmp_path)
        cfg.set("idle_low_fps_enabled", True)
        cfg.set("idle_low_fps_threshold", 12.5)
        cfg.save()
        reloaded = Config(base=tmp_path)
        assert reloaded.get("idle_low_fps_enabled") is True
        assert reloaded.get("idle_low_fps_threshold") == 12.5

        # 越界值归一化到 [1, 3600]
        cfg.set("idle_low_fps_threshold", 99999)
        cfg.save()
        reloaded2 = Config(base=tmp_path)
        assert reloaded2.get("idle_low_fps_threshold") == 3600.0


# ============================================================================
# 2. 活跃度门控：单调时钟 + 阈值 + 可见性 + 交互复位
# ============================================================================
class TestActivityGate:
    def test_disabled_by_default_never_reduces(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=False, threshold=0.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(3600)
            assert win._idle_reduction_active() is False
        finally:
            win.close()
            app.processEvents()

    def test_requires_threshold_elapsed_while_visible(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=5.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            assert win._idle_reduction_active() is False  # 0s < 5s
            clock.advance(4.9)
            assert win._idle_reduction_active() is False
            clock.advance(0.2)
            assert win._idle_reduction_active() is True  # 超过阈值
        finally:
            win.close()
            app.processEvents()

    def test_mark_activity_resets_immediately(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=5.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(10)
            assert win._idle_reduction_active() is True
            win.mark_activity()
            assert win._idle_reduction_active() is False  # 任何交互立刻回满帧率
            assert win._last_activity_ts == clock.now
        finally:
            win.close()
            app.processEvents()

    def test_hidden_keeps_full_stop_and_never_reduces(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=0.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(60)
            assert win._idle_reduction_active() is True
            win.hide()
            app.processEvents()
            assert win._hidden_paused is True
            # 隐藏 = 全停语义不动：不降帧（本来就停着），门控返回 False
            assert win._idle_reduction_active() is False
            assert win.movie._running is False
        finally:
            win.close()
            app.processEvents()

    def test_press_hold_blocks_reduction(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=0.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(60)
            assert win._idle_reduction_active() is True
            # 按住左键 = 用户正握着桌宠：视为活跃
            win._press_global = QPointF(0, 0).toPoint()
            try:
                assert win._idle_reduction_active() is False
            finally:
                win._press_global = None
        finally:
            win.close()
            app.processEvents()

    def test_mouse_press_marks_activity(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=5.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(10)
            assert win._idle_reduction_active() is True
            win.mousePressEvent(_press(QPointF(200, 200), QPointF(300, 300)))
            assert win._last_activity_ts == clock.now
            win.mouseReleaseEvent(_release(QPointF(200, 200), QPointF(300, 300)))
            app.processEvents()
            # 松手后按压态清除，仅靠时间戳复位维持全帧率
            assert win._press_global is None
            assert win._idle_reduction_active() is False
        finally:
            win.close()
            app.processEvents()

    def test_link_request_marks_activity(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=5.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(10)
            assert win._idle_reduction_active() is True
            win.request_link_anim("写代码")
            assert win._last_activity_ts == clock.now
            assert win._idle_reduction_active() is False
        finally:
            win.close()
            app.processEvents()


# ============================================================================
# 3. 时间线跳帧：24fps 素材隔帧呈现 12fps 效果，动画时长不变
# ============================================================================
class TestTimelineFrameSkip:
    def test_publish_decision_is_parity_by_timeline(self):
        # 帧号 = elapsed time * fps；目标呈现帧 = floor(elapsed*fps/2)*2
        # → 能被 2 整除的帧才发布（24fps 素材 → 12fps 效果）
        assert PetWindow._is_reduced_publish_frame(0) is True
        assert PetWindow._is_reduced_publish_frame(1) is False
        assert PetWindow._is_reduced_publish_frame(2) is True
        assert PetWindow._is_reduced_publish_frame(3) is False
        assert PetWindow._is_reduced_publish_frame(23) is False
        assert PetWindow._is_reduced_publish_frame(24) is True

    def test_skips_odd_frames_only_when_reduced(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=0.0)
        clock = FakeClock()
        lib = FakeLibrary(frame_count=10)
        win = PetWindow(lib, cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(60)
            assert win._idle_reduction_active() is True

            calls = []
            orig = win._rebuild_frame
            win._rebuild_frame = lambda: (calls.append(1), orig())[1]

            anim = win.anim
            win._on_frame(anim, 0)   # 源帧 0：偶数帧 → 发布
            assert len(calls) == 1
            win._on_frame(anim, 1)   # 源帧 1（奇数）：跳帧不发布
            assert len(calls) == 1
            win._on_frame(anim, 2)
            assert len(calls) == 2
            win._on_frame(anim, 3)
            assert len(calls) == 2
            win._on_frame(anim, 4)
            assert len(calls) == 3

            # 任何交互立刻回满帧率：下一帧（哪怕奇数）也发布
            win.mark_activity()
            assert win._idle_reduction_active() is False
            win._on_frame(anim, 5)
            assert len(calls) == 4
        finally:
            win.close()
            app.processEvents()

    def test_animation_time_unchanged_while_reduced(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=0.0)
        clock = FakeClock()
        lib = FakeLibrary(frame_count=10)
        win = PetWindow(lib, cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(60)
            movie = win.movie
            before_speed = movie.speed
            before_start = movie.start_count
            before_stop = movie.stop_count
            anim = win.anim
            for n in range(0, 8):
                win._on_frame(anim, n)
            # 降帧只跳过渲染，绝不改播放速率/启停——动画时间线不变
            assert movie.speed == before_speed
            assert movie.start_count == before_start
            assert movie.stop_count == before_stop
            assert win.playback_speed == 1.0
        finally:
            win.close()
            app.processEvents()

    def test_last_frame_still_advances_chain_when_skipped(self, app, tmp_path, monkeypatch):
        cfg = _make_config(tmp_path, enabled=True, threshold=0.0)
        clock = FakeClock()
        lib = FakeLibrary(frame_count=10)  # 源帧号 0..9，末帧索引 9（奇数）
        win = PetWindow(lib, cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(60)
            assert win._idle_reduction_active() is True
            # 固定动画链随机分支走到动作池（roll=0.5 → <0.80），使断言确定性
            # （否则 30% idle 分支会重播同名待机，anim 不变、旧 clip 被 stop 两次）
            import random as _random
            monkeypatch.setattr(_random, "random", lambda: 0.5)
            movie = win.movie
            before_stop = movie.stop_count
            anim = win.anim
            win._on_frame(anim, 9)  # 末帧奇数：跳帧但动画链必须推进
            # _on_anim_ended 已执行：旧 movie 被 stop（+1）且动画链切到动作
            assert movie.stop_count == before_stop + 1
            assert win.anim == "写代码"
            assert win.anim != anim
        finally:
            win.close()
            app.processEvents()

    def test_last_frame_chain_not_advanced_early(self, app, tmp_path, monkeypatch):
        cfg = _make_config(tmp_path, enabled=True, threshold=0.0)
        clock = FakeClock()
        lib = FakeLibrary(frame_count=10)  # 源帧号 0..9，末帧索引 9
        win = PetWindow(lib, cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(60)
            import random as _random
            monkeypatch.setattr(_random, "random", lambda: 0.5)
            movie = win.movie
            before_stop = movie.stop_count
            anim = win.anim
            # 倒数第二帧（8）无论降帧与否都绝不允许提前推进动画链
            win._on_frame(anim, 8)
            assert movie.stop_count == before_stop
            assert win.anim == anim
            # 只有真正的末帧（9）才推进
            win._on_frame(anim, 9)
            assert movie.stop_count == before_stop + 1
            assert win.anim != anim
        finally:
            win.close()
            app.processEvents()


# ============================================================================
# 3b. WebMClip 帧号契约：显示帧索引（0-based 源时间线）与播放计数（1-based）分离
# ============================================================================
class TestWebMClipFrameContract:
    """P1 复审：frameChanged/currentFrameNumber 携带素材源时间线 0-based 帧号，
    不是主线程消费序号；reader 队列满丢帧后源帧号照常推进。"""

    def test_stamp_source_indices_keeps_timeline_through_drops(self):
        # 队列满丢帧后，交付帧仍带素材源时间线 0-based 帧号——降帧相位
        # 锚定时间线而不是主线程消费序号（P1 复审）。
        q = _FlakyQueue(drop_every=2)
        WebMClip._stamp_source_indices(
            iter([b"f0", b"f1", b"f2", b"f3", b"f4", b"f5"]),
            q,
            lambda: False,
        )
        assert [item[0] for item in q.items] == [b"f0", b"f2", b"f4"]
        assert [item[1] for item in q.items] == [0, 2, 4]

    def test_stamp_source_indices_stops_when_stopped(self):
        q = _FlakyQueue(drop_every=0)  # 永不丢帧
        produced = {"n": 0}

        def frames():
            for i in range(20):
                produced["n"] += 1
                yield b"f%d" % i

        def is_stopped():
            return produced["n"] >= 3

        WebMClip._stamp_source_indices(frames(), q, is_stopped)
        # 停止检查在每轮循环顶部：第 3 帧产出时已停止，不再入队
        assert [item[1] for item in q.items] == [0, 1]

    def test_process_frame_emits_source_index_and_counts_playback(self, app, tmp_path):
        clip = WebMClip(str(tmp_path / "x.webm"))
        clip._w = 2
        clip._h = 2
        emitted = []
        clip.frameChanged.connect(emitted.append)
        try:
            clip._process_frame((bytes(16), 5))
            clip._process_frame((bytes(16), 6))
            clip._process_frame((bytes(16), 8))  # 源帧 7 被 reader 丢弃
            # frameChanged 携带素材源时间线 0-based 帧号（显示帧索引），
            # 不是主线程消费序号 1/2/3——丢帧后仍锚定素材时间线
            assert emitted == [5, 6, 8]
            assert clip.currentFrameNumber() == 8
            assert clip._frame_index == 3  # 播放计数 = 已消费帧数（1-based）
            assert clip.currentTimeSeconds() > 0
        finally:
            clip.cleanup()
            app.processEvents()

    def test_current_frame_number_is_source_index(self, app, tmp_path):
        clip = WebMClip(str(tmp_path / "x.webm"))
        clip._w = 2
        clip._h = 2
        try:
            assert clip.currentFrameNumber() == 0
            clip._process_frame((bytes(16), 42))
            # 显示帧索引与播放计数解耦：索引是源帧号 42，计数是 1
            assert clip.currentFrameNumber() == 42
            assert clip._frame_index == 1
        finally:
            clip.cleanup()
            app.processEvents()

    def test_jump_to_frame_resets_display_index(self, app, tmp_path):
        clip = WebMClip(str(tmp_path / "x.webm"))
        clip._w = 2
        clip._h = 2
        try:
            clip._first_image = QImage(2, 2, QImage.Format.Format_RGBA8888)
            clip._first_frame_done.set()
            clip._process_frame((bytes(16), 7))
            assert clip.currentFrameNumber() == 7
            clip.jumpToFrame(0)
            assert clip.currentFrameNumber() == 0
            assert clip._frame_index == 0
        finally:
            clip.cleanup()
            app.processEvents()


# ============================================================================
# 3c. 键盘/失焦交互计入活跃度（P2 顺修）
# ============================================================================
class TestKeyboardActivity:
    """被窗口消费的键盘交互与失焦取消弹弓 = 交互：刷新闲置降帧活跃锚点。"""

    def test_escape_cancel_slingshot_marks_activity(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=5.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(10)
            assert win._idle_reduction_active() is True
            win._interaction_state = "SLINGSHOT_AIMING"
            ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape,
                           Qt.KeyboardModifier.NoModifier)
            win.keyPressEvent(ev)
            assert win._interaction_state == "IDLE"  # 弹弓已取消
            assert win._last_activity_ts == clock.now  # 键盘取消刷新活跃锚点
            assert win._idle_reduction_active() is False
        finally:
            win.close()
            app.processEvents()

    def test_focus_out_cancel_slingshot_marks_activity(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=5.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(10)
            assert win._idle_reduction_active() is True
            win._interaction_state = "SLINGSHOT_AIMING"
            win.focusOutEvent(
                QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason)
            )
            assert win._interaction_state == "IDLE"  # 失焦取消弹弓
            assert win._last_activity_ts == clock.now
            assert win._idle_reduction_active() is False
        finally:
            win.close()
            app.processEvents()


# ============================================================================
# 4. Agent 联动忙碌 = 活跃：不降帧
# ============================================================================
class TestAgentBusy:
    def test_any_busy_considers_running_monitors(self, tmp_path):
        cfg = Config(base=tmp_path)
        mgr = AgentLinkManager(None, cfg)
        assert mgr.any_busy() is False

        mon = mgr.monitors["dsh"]
        mon._running = True
        mgr._last_raw = {"dsh": "working"}
        assert mgr.any_busy() is True
        mgr._last_raw = {"dsh": "thinking"}
        assert mgr.any_busy() is True
        mgr._last_raw = {"dsh": "idle"}
        assert mgr.any_busy() is False

        # 已停用监视器的残留 busy 不计入（关掉联动 = 不再视为活跃）
        mon._running = False
        mgr._last_raw = {"dsh": "working"}
        assert mgr.any_busy() is False

    def test_window_gate_respects_agent_busy(self, app, tmp_path):
        cfg = _make_config(tmp_path, enabled=True, threshold=0.0)
        clock = FakeClock()
        win = PetWindow(FakeLibrary(), cfg, clock=clock)
        win.show()
        app.processEvents()
        try:
            clock.advance(60)
            assert win._idle_reduction_active() is True
            mgr = win.agent_link_manager
            mon = mgr.monitors["dsh"]
            mon._running = True
            mgr._last_raw = {"dsh": "working"}
            assert win._idle_reduction_active() is False  # dsh 在干活 = 活跃
            mgr._last_raw = {"dsh": "idle"}
            assert win._idle_reduction_active() is True
        finally:
            win.close()
            app.processEvents()


# ============================================================================
# 5. 设置页开关（默认关）+ 保存即时生效
# ============================================================================
class TestSettings:
    def test_settings_toggle_round_trip(self, app, tmp_path):
        cfg_root = tmp_path / "appdata"
        cfg = Config(cfg_root)
        dialog = ModernSettingsDialog(cfg, include_ai=False)
        try:
            assert dialog.idle_low_fps_check.isChecked() is False  # 灰度默认关
            dialog.idle_low_fps_check.setChecked(True)
            ok = dialog._write_config()
            assert ok is True
        finally:
            dialog.deleteLater()
        reloaded = Config(cfg_root)
        assert reloaded.get("idle_low_fps_enabled") is True

    def test_refresh_pet_settings_syncs_toggle(self, app, tmp_path):
        cfg = Config(base=tmp_path)
        win = PetWindow(FakeLibrary(), cfg)
        win.show()
        app.processEvents()
        try:
            assert win.idle_low_fps_enabled is False
            cfg.set("idle_low_fps_enabled", True)
            cfg.set("idle_low_fps_threshold", 7.5)
            win.refresh_pet_settings()
            assert win.idle_low_fps_enabled is True
            assert win.idle_low_fps_threshold == 7.5
            # 关闭同样即时生效
            cfg.set("idle_low_fps_enabled", False)
            win.refresh_pet_settings()
            assert win.idle_low_fps_enabled is False
        finally:
            win.close()
            app.processEvents()
