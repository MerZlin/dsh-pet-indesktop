# -*- coding: utf-8 -*-
"""B7 审查修复回归：动画启动被拒（start() 返回 False）时窗口层的可观测降级。

锁定 P1-1 窗口层行为：
1. _switch 拿到 start() 失败必须回退到上一个可播放动画（或 idle），
   绝不留下 "anim 已切换但 movie 未在播" 的停滞态；
2. 失败后安排稍后重试被拒动画，reader 可回收后重试成功恢复；
3. 上一动画与 idle 都被拒（极端）时保留最后渲染帧、释放 click/interaction
   hold，并仍安排重试（用户可见的恢复路径，而非静默死停）。

以及 P1-2 库层：pause_warm（隐藏/切角色）必须取消在飞首帧预热。

B7 复审（R2）遗留修复：
4. _switch 返回明确 bool；移动路径感知切换失败——_try_move 不得按失败
   移动动画建立移动计划；_trigger_move 不得重复尝试（双重降级/重复计数）；
5. Agent 联动失败 retry 绑定请求来源与目标身份：Agent 回到 idle 取消联动
   重试；无关动画的成功切换不得吞掉待重试；新联动请求覆盖旧联动重试。
"""
from __future__ import annotations

from pathlib import Path

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
    """与 WebMClip 形状一致的假 clip：start() 可配置为失败（返回 False）。"""

    frameChanged = Signal(int)
    finished = Signal()
    errorOccurred = Signal(str)

    def __init__(self, fail: bool = False, parent=None):
        super().__init__(parent)
        self.fail = fail
        self._running = False
        self.speed = 1.0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False

    def start(self):
        if self.fail:
            return False
        self._running = True
        return True

    def jumpToFrame(self, frame_index):
        return frame_index <= 0

    def set_playback_speed(self, speed):
        self.speed = speed

    def currentPixmap(self):
        return self._pm

    def currentFrameNumber(self):
        return 0

    def frameCount(self):
        return 1

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

    def names(self):
        return list(NAMES)

    def movies(self):
        return dict(self._clips)

    def movie(self, name):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0

    def set_failing(self, name: str, failing: bool) -> None:
        self._clips[name].fail = failing


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_win(tmp_path, lib):
    cfg = Config(base=tmp_path)
    return PetWindow(lib, cfg)


def test_switch_rejected_start_restores_previous_animation_and_retries(app, tmp_path):
    """P1-1：目标动画 start() 被拒时回退上一动画（仍在播），并安排稍后重试。"""
    lib = FakeLibrary(failing={"写代码"})
    win = _make_win(tmp_path, lib)
    # 初始 idle 播放成功
    assert win.anim == catalog.IDLE
    assert win.movie is lib.movie(catalog.IDLE)
    assert win.movie._running is True

    win._switch("写代码")  # 目标动画 start 被拒

    # 回退：anim 仍是 idle 且其 clip 在播——绝无"anim 已切但 movie 未播"
    assert win.anim == catalog.IDLE
    assert win.movie is lib.movie(catalog.IDLE)
    assert win.movie._running is True
    assert win._click_hold is False, "回退后不得残留点击 hold"
    assert win._pending_switch == "写代码", "被拒动画必须登记待重试"
    assert win._switch_retry_timer.isActive(), "必须安排稍后重试"

    # reader 可回收后重试成功：切到目标动画并清除待重试状态
    lib.set_failing("写代码", False)
    win._on_switch_retry_timeout()
    assert win.anim == "写代码"
    assert win.movie is lib.movie("写代码")
    assert win.movie._running is True
    assert win._pending_switch is None
    assert win._switch_retry_timer.isActive() is False

    win.close()
    app.processEvents()


def test_switch_rejected_start_no_previous_falls_back_to_idle(app, tmp_path):
    """P1-1：无上一动画可回退（含上一动画同样被拒）时回退到可播放 idle。"""
    # 让 idle 与目标动画都失败：初始 _switch(idle) 即失败（prev_movie 为 None）
    lib = FakeLibrary(failing={catalog.IDLE, "写代码"})
    win = _make_win(tmp_path, lib)

    # 初始 idle 被拒：不得停滞——保留最后渲染帧、释放 hold、安排重试
    assert win.anim == catalog.IDLE
    assert win._click_hold is False
    assert win._pending_switch == catalog.IDLE
    assert win._switch_retry_timer.isActive()

    # 恢复后重试成功：idle 开始播放
    lib.set_failing(catalog.IDLE, False)
    win._on_switch_retry_timeout()
    assert win.anim == catalog.IDLE
    assert win.movie._running is True
    assert win._pending_switch is None

    # 再切目标动画：此时上一动画（idle）可回退
    lib.set_failing("写代码", True)
    win._switch("写代码")
    assert win.anim == catalog.IDLE, "目标被拒必须回退上一动画"
    assert win.movie._running is True
    assert win._pending_switch == "写代码"
    assert win._switch_retry_timer.isActive()

    win.close()
    app.processEvents()


def test_pause_activity_drops_pending_switch_retry(app, tmp_path):
    """P1-1：窗口隐藏（pause_activity）时停止待重试，避免隐藏期间反复重试。"""
    lib = FakeLibrary(failing={"写代码"})
    win = _make_win(tmp_path, lib)
    win._switch("写代码")  # 被拒 → 待重试
    assert win._pending_switch == "写代码"
    assert win._switch_retry_timer.isActive()

    win._pause_activity()
    assert win._pending_switch is None
    assert win._switch_retry_timer.isActive() is False
    assert win._switch_retry_count == 0

    win.close()
    app.processEvents()


def test_library_pause_warm_cancels_inflight_first_frame_warm(app, tmp_path, monkeypatch):
    """P1-2：pause_warm（隐藏/切角色）必须取消在飞的首帧预热。"""
    import pet.library as library_mod

    class CancelTrackingClip:
        def __init__(self, path, parent=None, first_frame_cache=None):
            self.path = Path(path)
            self.cancel_calls = 0

        def warm_meta(self):
            pass

        def warm_first_frame(self):
            pass

        def cancel_first_frame_warm(self):
            self.cancel_calls += 1

    monkeypatch.setattr(library_mod, "WebMClip", CancelTrackingClip)
    videos = tmp_path / "videos"
    folders = {
        "idle": ["待机呼吸休闲.webm"],
        "turn": ["东张西望.webm"],
        "move": ["螃蟹走路.webm"],
        "click": ["点击回应 - 开心跃动.webm"],
        "drag": ["被鼠标拖拽悬空反馈.webm"],
        "random": ["写代码.webm"],
    }
    for folder, files in folders.items():
        directory = videos / folder
        directory.mkdir(parents=True)
        for name in files:
            (directory / name).write_bytes(b"fake")
    lib = library_mod.MovieLibrary(asset_dir=videos)

    clips = list(lib._movies.values())
    assert clips, "库构造后应有已创建的 clip"
    assert all(c.cancel_calls == 0 for c in clips)

    lib.pause_warm()
    assert all(c.cancel_calls == 1 for c in clips), \
        "pause_warm 必须取消每个已创建 clip 的在飞首帧预热"
    app.processEvents()


# ============================================================================
# B7 复审（R2）遗留 1：移动路径感知切换失败
# ============================================================================
class _FakeScreen:
    """大屏：保证 _try_move 的空间检查一定通过，测试直达切换/计划逻辑。"""

    def availableGeometry(self):
        return QRect(0, 0, 2000, 1200)

    def devicePixelRatio(self):
        return 1.0


def test_switch_returns_bool_result(app, tmp_path):
    """R2-1：_switch 必须返回明确 bool（成功 True / 被拒 False），供移动路径判断。"""
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    assert win._switch(catalog.IDLE) is True, "可播放动画切换必须返回 True"

    lib.set_failing("写代码", True)
    assert win._switch("写代码") is False, "start() 被拒的切换必须返回 False"

    win.close()
    app.processEvents()


def test_try_move_switch_rejection_builds_no_move_plan(app, tmp_path):
    """R2-1：移动动画 start() 被拒时 _try_move 必须返回 False 且不建立移动计划
    （否则 idle/回退动画播放时仍按失败移动动画的 duration/坐标位移，
    画面、动画、窗口位移三者不一致）。"""
    lib = FakeLibrary(failing={catalog.MOVES[0]})
    win = _make_win(tmp_path, lib)
    win._screen_available = lambda: _FakeScreen()
    win.move(500, 300)

    assert win._try_move() is False, "移动动画被拒时不得建立移动计划"
    assert win._move_plan is None, "不得按失败移动动画建立移动计划"
    assert win._move_timer.isActive() is False, "不得启动移动定时器"
    assert win.anim == catalog.IDLE, "切换被拒必须回退到上一动画（idle）"
    assert win.movie is lib.movie(catalog.IDLE), "回退后 movie 必须是可播放的 idle"
    assert win.movie._running is True, "回退后上一动画必须在播"
    assert win._pending_switch == catalog.MOVES[0], "被拒移动动画必须登记待重试"
    assert win._switch_retry_timer.isActive(), "必须安排稍后重试"

    win.close()
    app.processEvents()


def test_trigger_move_switch_rejection_no_double_attempt(app, tmp_path):
    """R2-1：手动移动路径 _trigger_move 在切换被拒时不得再次 _switch 尝试
    （避免双重降级/重复重试计数）。"""
    lib = FakeLibrary(failing={catalog.MOVES[0]})
    win = _make_win(tmp_path, lib)
    win._screen_available = lambda: _FakeScreen()
    win.move(500, 300)

    win._trigger_move(catalog.MOVES[0])

    assert win._move_plan is None
    assert win._switch_retry_count == 1, "切换被拒只应安排一次重试，不得重复计数"
    assert win._pending_switch == catalog.MOVES[0]
    assert win.anim == catalog.IDLE, "必须回退到可播放动画"
    assert win.movie._running is True

    win.close()
    app.processEvents()


def test_try_move_success_still_builds_plan_and_moves(app, tmp_path):
    """R2-1：移动动画可播放时 _try_move 仍正常建立移动计划并返回 True。"""
    lib = FakeLibrary()
    win = _make_win(tmp_path, lib)
    win._screen_available = lambda: _FakeScreen()
    win.move(500, 300)

    assert win._try_move() is True
    assert win._move_plan is not None
    assert win._move_timer.isActive()
    assert win.anim == catalog.MOVES[0]

    win.close()
    app.processEvents()


# ============================================================================
# B7 复审（R2）遗留 2：Agent 联动 retry 的取消/覆盖语义
# ============================================================================
def test_link_retry_cancelled_when_agent_idle(app, tmp_path):
    """R2-2：联动动画失败后安排的重试，必须随 Agent 回到 idle 取消
    （否则 1.5s 后重试会重播已取消的过期联动动作）。"""
    lib = FakeLibrary(failing={"写代码"})
    win = _make_win(tmp_path, lib)
    win.request_link_anim("写代码")  # 待机中立即播放 → start 被拒 → 回退 + 安排重试
    assert win._pending_switch == "写代码"
    assert win._pending_switch_link is True, "联动失败重试必须标记来源"
    assert win._switch_retry_timer.isActive()

    win.request_link_idle()  # Agent 回到空闲
    assert win._pending_switch is None, "联动重试必须随 Agent idle 取消"
    assert win._pending_switch_link is False
    assert win._switch_retry_timer.isActive() is False
    assert win._switch_retry_count == 0

    win.close()
    app.processEvents()


def test_link_retry_kept_across_unrelated_successful_switch(app, tmp_path):
    """R2-2：无关动画的成功切换不得吞掉联动动画的待重试——重试绑定目标
    动画身份，只在该动画自身启动成功时清除。"""
    lib = FakeLibrary(failing={"写代码"})
    win = _make_win(tmp_path, lib)
    win.request_link_anim("写代码")  # 失败 → 联动重试待执行
    assert win._pending_switch == "写代码"
    assert win._switch_retry_timer.isActive()

    win._switch(catalog.TURN)  # 无关动画切换成功
    assert win.anim == catalog.TURN
    assert win._pending_switch == "写代码", "无关成功切换不得吞掉待重试"
    assert win._switch_retry_timer.isActive(), "待重试计时器必须保持运行"

    # 重试执行：恢复后重试成功切回联动动画
    lib.set_failing("写代码", False)
    win._on_switch_retry_timeout()
    assert win.anim == "写代码"
    assert win._pending_switch is None
    assert win._switch_retry_timer.isActive() is False

    win.close()
    app.processEvents()


def test_new_link_request_overrides_old_link_retry(app, tmp_path):
    """R2-2：新的联动请求覆盖旧的联动失败重试（最新覆盖旧的），旧重试不得
    在 1.5s 后顶掉新联动动作。"""
    lib = FakeLibrary(failing={"写代码"})
    win = _make_win(tmp_path, lib)
    win.request_link_anim("写代码")  # 失败 → 联动重试待执行
    assert win._pending_switch_link is True

    win.request_link_anim(catalog.CLICKS[0])  # 新请求覆盖旧重试并立即播放
    assert win.anim == catalog.CLICKS[0]
    assert win._pending_switch is None, "旧联动重试必须被新请求取消"
    assert win._pending_switch_link is False
    assert win._switch_retry_timer.isActive() is False

    win.close()
    app.processEvents()


def test_link_idle_keeps_non_link_retry(app, tmp_path):
    """R2-2：Agent 回到 idle 只取消联动来源的重试，不得影响其他来源的
    待重试（如移动动画失败）。"""
    lib = FakeLibrary(failing={catalog.MOVES[0]})
    win = _make_win(tmp_path, lib)
    win._screen_available = lambda: _FakeScreen()
    win.move(500, 300)
    win._try_move()  # 移动失败 → 非联动重试待执行
    assert win._pending_switch == catalog.MOVES[0]
    assert win._pending_switch_link is False

    win.request_link_idle()  # Agent idle：不得吞掉移动重试
    assert win._pending_switch == catalog.MOVES[0], "非联动重试不得被 Agent idle 取消"
    assert win._pending_switch_link is False
    assert win._switch_retry_timer.isActive()

    win.close()
    app.processEvents()
