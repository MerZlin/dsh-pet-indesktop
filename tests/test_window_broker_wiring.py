# -*- coding: utf-8 -*-
"""broker 窗口接线的收尾门控回归测试（终审 P1-2 = DS 终审 P2-1）。

锁定：shareable 会话的收尾按「注册时的身份 (name, movie)」执行，不按
「当下 _broker_shareable()」——运行期关闭 collision_enabled（或 detach）
后开关已变，若按当下判定，收尾会被跳过，已建立的发布 session / 订阅 /
预算位残留到 app shutdown。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pet import catalog
from pet.config import Config
from pet.decode_fanout import DecodeFanoutHub
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
    """与 WebMClip 接口兼容的极简假播放器（同 test_window_pause）。"""

    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.speed = 1.0
        self._pm = QPixmap(2, 2)
        self._pm.fill()
        self._publish_sink = None
        self._feed_source = None
        # 批5.3：hub（DecodeFanoutHub）fan-out 接缝所需的最小属性
        self.path = str(Path("assets/characters/shenshen/videos/idle/待机呼吸休闲.webm"))
        self.playback_speed = 1.0
        self.decode_throttle_divisor = 1
        self.decode_pace_external = False

    def set_decode_throttle(self, divisor):
        self.decode_throttle_divisor = max(1, int(divisor))

    def set_decode_pace_external(self, value):
        self.decode_pace_external = bool(value)

    def stop(self):
        self._running = False

    def start(self):
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
    """只包含核心动画名的假素材库（同 test_window_pause）。"""

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


class FakeBrokerFacade:
    """记录 shareable_start/end 调用的 facade 替身（不碰 ipc/shm）。"""

    def __init__(self, role: str = "publish"):
        self.enabled = True
        self._role = role
        self.started = []
        self.ended = []
        self.unbind_calls = 0

    def shareable_start(self, name, movie):
        self.started.append(name)
        return self._role

    def shareable_end(self, name, movie, natural=True):
        self.ended.append((name, natural))

    def unbind(self):
        self.unbind_calls += 1


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _make_window(tmp_path):
    # broker_facade=None 构造（走与历史一致的首 idle 直起播路径），
    # 之后注入替身 facade——只测 _broker_register/_broker_unregister 的门控。
    win = PetWindow(FakeLibrary(), Config(base=tmp_path))
    return win


def test_unregister_follows_registered_identity_not_current_toggle(app, tmp_path):
    """运行期关掉共享解码后，已注册会话的收尾仍须执行。

    批5.3：共享解码与 collision_enabled 解耦——`_broker_active` 只取决于
    facade（DecodeFanoutHub）的 enabled。运行期停用 hub（facade.enabled=False）
    等价于历史上关 collision：`_broker_shareable()` 变 False，但收尾仍按
    「注册时的身份 (name, movie)」执行。"""
    win = _make_window(tmp_path)
    facade = FakeBrokerFacade()
    win._broker_facade = facade
    win._broker_register(win.idle, win.movie)
    assert facade.started == [win.idle]
    assert win._broker_registered == (win.idle, win.movie)

    # 运行期停用共享解码 hub：_broker_shareable() 变 False——收尾仍按身份执行
    facade.enabled = False
    assert win._broker_shareable(win.idle) is False
    win._broker_unregister(win.idle, win.movie, natural=False)
    assert facade.ended == [(win.idle, False)]
    assert win._broker_registered is None
    # 幂等：第二次收尾 no-op
    win._broker_unregister(win.idle, win.movie, natural=False)
    assert len(facade.ended) == 1
    win.close()
    app.processEvents()


def test_unregister_without_registration_is_noop(app, tmp_path):
    """未注册过的 (name, movie) 收尾请求不得触达 facade。"""
    win = _make_window(tmp_path)
    facade = FakeBrokerFacade()
    win._broker_facade = facade
    win._broker_unregister(win.idle, win.movie, natural=False)
    assert facade.ended == []
    win.close()
    app.processEvents()


def test_register_local_role_does_not_record_identity(app, tmp_path):
    """facade 回 'local'（角色未定/预算满等）时不得记录身份，后续收尾 no-op。"""
    win = _make_window(tmp_path)
    facade = FakeBrokerFacade(role="local")
    win._broker_facade = facade
    win._broker_register(win.idle, win.movie)
    assert facade.started == [win.idle]
    assert win._broker_registered is None
    win._broker_unregister(win.idle, win.movie, natural=False)
    assert facade.ended == []
    win.close()
    app.processEvents()


def test_detach_collision_session_tears_down_broker_first(app, tmp_path):
    """detach（运行期关碰撞路径）先按身份收尾 broker 会话再 unbind。"""
    win = _make_window(tmp_path)
    facade = FakeBrokerFacade()
    win._broker_facade = facade
    win._broker_register(win.idle, win.movie)
    win.detach_collision_session()
    assert facade.ended == [(win.idle, False)]
    assert facade.unbind_calls == 1
    assert win._broker_registered is None
    # movie 钩子摘除：broker 停用期间复用旧 clip 不得误用上一轮 sink/feed
    assert win.movie._publish_sink is None
    assert win.movie._feed_source is None
    win.close()
    app.processEvents()


def test_hub_register_unregister_teardown(app, tmp_path):
    """批5.3：用真实 DecodeFanoutHub 走窗口接线——注册身份 (name, movie) 被
    记录；收尾仍按身份执行、摘掉 movie 钩子并释放源（幂等）。"""
    win = _make_window(tmp_path)
    hub = DecodeFanoutHub(enabled=True)
    win._broker_facade = hub
    win._broker_register(win.idle, win.movie)
    assert win._broker_registered == (win.idle, win.movie)
    assert win.movie._publish_sink is not None, "首发窗应成为源发布者"
    assert win.movie._feed_source is None
    source = hub._sources[win.movie.path]
    assert source.publisher is win.movie

    win._broker_unregister(win.idle, win.movie, natural=False)
    assert win._broker_registered is None
    assert win.movie._publish_sink is None
    assert win.movie._feed_source is None
    assert not hub._sources, "无订阅者离开 → 源应被释放"
    # 幂等：第二次收尾 no-op
    win._broker_unregister(win.idle, win.movie, natural=False)
    assert win._broker_registered is None
    win.close()
    app.processEvents()
