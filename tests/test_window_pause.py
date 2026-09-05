# -*- coding: utf-8 -*-
"""窗口隐藏/显示时暂停与恢复动画解码的回归测试。

背景：桌宠隐藏（托盘隐藏 / 全屏自动隐藏）后仍会继续 24fps 解码与重建
mask，多开时属于纯白烧。此测试锁定 hideEvent 暂停、showEvent 恢复的行为。
"""
from __future__ import annotations

import pytest
import shiboken6
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
    """与 WebMClip 接口兼容的极简假播放器，只记录启停状态。"""

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.stop_count = 0
        self.start_count = 0
        self.speed = 1.0
        self._pm = QPixmap(2, 2)
        self._pm.fill()

    def stop(self):
        self._running = False
        self.stop_count += 1

    def start(self):
        self._running = True
        self.start_count += 1

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
    """只包含核心动画名的假素材库，避免测试拉真 ffmpeg。"""

    def __init__(self):
        self._clips = {name: FakeClip() for name in NAMES}
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


class WarmRecordingLibrary(FakeLibrary):
    """记录 pause_warm/resume_warm 调用的假素材库（N1：原生隐藏→显示预热恢复）。"""

    def __init__(self):
        super().__init__()
        self.pause_calls = 0
        self.resume_calls = 0
        self.shutdown_calls = 0

    def pause_warm(self):
        self.pause_calls += 1

    def resume_warm(self):
        self.resume_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_hide_pauses_and_show_resumes_activity(app, tmp_path):
    lib = FakeLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.show()
    app.processEvents()

    idle = win.movie
    assert idle is not None
    assert idle._running is True
    assert win._hidden_paused is False

    win.hide()
    app.processEvents()
    assert win._hidden_paused is True
    assert idle._running is False
    assert not win._move_timer.isActive()
    assert not win._physics_timer.isActive()
    assert not win._self_talk_timer.isActive()
    assert not (win._fs_thread is not None and win._fs_thread.is_alive())
    # 隐藏时不产生任何可见气泡
    win.show_bubble("隐藏时不应显示")
    win.set_chat_status("thinking", "隐藏时不应显示")
    assert not win._speech_bubble.isVisible()

    win.show()
    app.processEvents()
    assert win._hidden_paused is False
    assert win.movie is not None
    assert win.movie._running is True

    win.close()
    app.processEvents()


def test_auto_hide_keeps_fullscreen_watcher_alive(app, tmp_path, monkeypatch):
    """全屏自动隐藏时 watcher 线程必须保持运行——它是退出全屏后 show() 回来的唯一路径。"""
    lib = FakeLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.auto_hide_fullscreen = True
    win.show()
    app.processEvents()
    win._start_fs_watch()  # 非 Windows 平台初始不启动，测试里强制模拟
    assert win._fs_thread is not None and win._fs_thread.is_alive()

    # 模拟全屏检测命中：_on_fullscreen_changed(True) 先置 _auto_hidden 再 hide()
    win._on_fullscreen_changed(True)
    app.processEvents()
    assert win._hidden_paused is True
    assert win._fs_thread is not None and win._fs_thread.is_alive()  # 关键：watcher 不能被暂停

    # 模拟退出全屏：_on_fullscreen_changed(False) 应把桌宠 show 回来并恢复活动
    win._on_fullscreen_changed(False)
    app.processEvents()
    assert win._auto_hidden is False
    assert win._hidden_paused is False
    assert win.movie is not None and win.movie._running is True

    win._stop_fs_watch()
    win.close()
    app.processEvents()


def test_disable_auto_hide_restores_pet(app, tmp_path):
    """自动隐藏状态下关闭开关：桌宠应立即恢复显示（玩游戏也想看到桌宠的场景）。"""
    lib = FakeLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.auto_hide_fullscreen = True
    win.show()
    app.processEvents()

    # 模拟已被全屏自动隐藏
    win._auto_hidden = True
    win.hide()
    app.processEvents()
    assert not win.isVisible()

    # 用户通过菜单关闭「全屏时自动隐藏」
    win.set_auto_hide_fullscreen(False)
    app.processEvents()
    assert win._auto_hidden is False
    assert win._hidden_paused is False
    assert win.isVisible()
    assert win.cfg.get('auto_hide_fullscreen') is False

    win.close()
    app.processEvents()


def test_native_set_visible_cycle_recovers_warm(app, tmp_path):
    """N1：不经自定义 hide() 的原生 setVisible(False)→setVisible(True) 周期
    必须完整恢复预热——hideEvent 设置 _hidden_paused，showEvent 据此走
    _resume_activity() 并 resume_warm()；否则预热被永久停用。"""
    lib = WarmRecordingLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    win.show()
    app.processEvents()
    assert lib.pause_calls == 0 and lib.resume_calls == 0, "首次显示不触发预热暂停/恢复"

    win.setVisible(False)  # 原生隐藏直进 hideEvent
    app.processEvents()
    assert win._hidden_paused is True, "原生隐藏必须设置 _hidden_paused（showEvent 恢复的依据）"
    assert lib.pause_calls >= 1, "原生隐藏必须暂停预热"

    win.setVisible(True)  # 原生显示直进 showEvent
    app.processEvents()
    assert win._hidden_paused is False
    assert lib.resume_calls >= 1, "原生隐藏→显示周期后必须恢复预热，不得永久停用"
    assert lib.shutdown_calls == 0, "隐藏是可恢复生命周期，不得永久关闭素材库"

    bubble = win._speech_bubble
    win.close()
    app.processEvents()
    assert lib.shutdown_calls == 1, "真正关闭窗口时才应永久关闭素材库"
    assert not shiboken6.isValid(bubble), "关闭窗口时应同步销毁外置气泡"
    win.close()
    app.processEvents()
    assert lib.shutdown_calls == 1, "重复 close 不得重复关闭素材库"


def test_fullscreen_geometry_hit_requires_borderless(app, tmp_path):
    """全屏判定：覆盖整屏几何且无标题栏（WS_CAPTION）= 真全屏。

    回归一：Windows「自动隐藏任务栏」下最大化窗口铺满整屏，旧实现只看几何把它
    误判成真全屏而隐藏桌宠 → 带标题栏的窗口不命中。
    回归二：已最大化后按 F11（IsZoomed 仍为真、但应用清掉标题栏）应命中——
    几何 + IsZoomed 都无法区分该场景与普通最大化窗口，标题栏才是可靠信号。
    """
    lib = FakeLibrary()
    win = PetWindow(lib, Config(base=tmp_path))
    geom = QRect(0, 0, 1920, 1080)  # 含任务栏区域的整屏几何

    # 真全屏（游戏/视频/浏览器 F11、最大化后按 F11、无边框全屏游戏）：
    # 无标题栏 + 覆盖整屏 → 命中，应隐藏桌宠
    assert win._fullscreen_geometry_hit(0, 0, 1920, 1080, geom, has_caption=False) is True

    # 自动隐藏任务栏 + 普通最大化窗口：铺满整屏但带标题栏 → 不命中（修复目标）
    assert win._fullscreen_geometry_hit(0, 0, 1920, 1080, geom, has_caption=True) is False

    # 任务栏常驻 + 普通最大化窗口：带标题栏且只到工作区 → 不命中
    assert win._fullscreen_geometry_hit(0, 0, 1920, 1040, geom, has_caption=True) is False

    # 无标题栏但未覆盖整屏（普通无边框窗口）→ 不命中，几何仍是必要条件
    assert win._fullscreen_geometry_hit(100, 100, 1500, 900, geom, has_caption=False) is False

    # 带标题栏的普通窗口 → 不命中
    assert win._fullscreen_geometry_hit(100, 100, 1500, 900, geom, has_caption=True) is False

    # Unity/UE 系游戏（如绝区零）全屏时保留 WS_CAPTION 但必带 WS_EX_TOPMOST：
    # 置顶 + 覆盖整屏 → 命中（PR #18 的纯标题栏判据对这类游戏漏检）
    assert win._fullscreen_geometry_hit(0, 0, 1920, 1080, geom, has_caption=True, topmost=True) is True

    # 置顶但未覆盖整屏（小窗置顶播放器）→ 几何仍是必要条件
    assert win._fullscreen_geometry_hit(100, 100, 1500, 900, geom, has_caption=True, topmost=True) is False

    win.close()
    app.processEvents()
