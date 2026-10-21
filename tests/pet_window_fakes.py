# -*- coding: utf-8 -*-
"""PetWindow 轻量替身（窗口级测试共用测试基建）。

原先住在 ``tests/test_collision_window.py``；4.4a 停用多进程多宠退役层后，
该文件随「窗口侧碰撞客户端」测试一起删除，但它的**素材库替身**被多个窗口级
测试复用（movement / feature_gating / effects_integration / agent_link_threads /
throw_egg / single_process_spawn），故按测试基建收编到本模块，避免复制粘贴。

约定：本模块只提供替身与最小装配，不含任何断言；`make_pet_window` 不再接受
``collision_session``（4.4a 起 PetWindow 没有碰撞 IPC 会话参数）。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap

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
    frameChanged = Signal(int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.speed = 1.0
        self._pm = QPixmap(100, 100)
        self._pm.fill()

    def stop(self):
        self._running = False

    def start(self):
        self._running = True

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

    def movie(self, name, **_kw):
        return self._clips[name]

    def frames(self, name):
        return 1

    def duration(self, name):
        return 1.0


def make_pet_window(tmp_path, runtime_id="test-slot-pid1-abc12345", *,
                    collision_enabled=True, lock_position=False,
                    config=None) -> PetWindow:
    """建一个显示中的 PetWindow（替身素材库 + 临时配置），返回窗口。"""
    if config is None:
        config = Config(str(tmp_path / f"cfg_{runtime_id}.json"))
    config.set("collision_enabled", collision_enabled)
    config.set("lock_position", lock_position)
    win = PetWindow(FakeLibrary(), config)
    win.resize(100, 100)
    win.show()
    return win
