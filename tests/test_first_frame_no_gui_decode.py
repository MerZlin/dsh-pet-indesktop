# -*- coding: utf-8 -*-
"""GUI 线程永不阻塞首帧解码（硬不变量）回归。

实测定案：冷首帧 GUI 同步解码 100-337ms 冻结（webm.ff_gui_decode 打点），
有界等待也有 120ms 上限。最终形态（套件级 A/B 验证）：
1. jumpToFrame(0) 冷路径立即返回，不解码、不等待、也不 kick 后台线程——
   （曾短暂 kick 后台 warm：套件内大量并发后台解码导致全量测试
   access violation，已移除，教训见 jumpToFrame 注释）；窗口在播放首帧
   到达前继续显示旧帧（_rebuild_frame 空帧跳过）；
2. 播放交付的源帧 0 顺手进首帧缓存（播过留热）——warm 闸门语义不变；
3. 在飞 warm 不被 jumpToFrame 干扰（原子认领语义不被破坏）。

全部用事件同步，不用 sleep 猜时序（唯一的轮询带上限兜底）。
"""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from pet.webm_clip import WebMClip


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _RecordingClip(WebMClip):
    """记录 _decode_first_qimage 的调用线程与次数，可阻塞等待放行。"""

    def __init__(self, path, parent=None):
        super().__init__(path, parent)
        self.decode_count = 0
        self.decode_threads = []
        self.decode_entered = threading.Event()
        self.decode_release = threading.Event()
        self._counter_lock = threading.Lock()

    def _decode_first_qimage(self, gen=None):
        with self._counter_lock:
            self.decode_count += 1
            self.decode_threads.append(threading.get_ident())
        self.decode_entered.set()
        self.decode_release.wait(5.0)
        return QImage(2, 2, QImage.Format.Format_RGBA8888)


def test_jump_to_frame_cold_never_decodes_or_waits(app):
    """硬不变量：jumpToFrame(0) 冷路径立即返回，任何线程都不为此解码。"""
    clip = _RecordingClip("dummy.webm")
    t0 = time.monotonic()
    assert clip.jumpToFrame(0) is True
    assert time.monotonic() - t0 < 0.5, "jumpToFrame 不得等待首帧解码"
    assert clip._current_pixmap is None  # 旧帧由窗口层顶着（_rebuild_frame 空帧跳过）
    time.sleep(0.3)  # 给任何（不应存在的）后台动作留窗口
    assert clip.decode_count == 0, "jumpToFrame 冷路径不得触发任何首帧解码"
    clip.cleanup()
    app.processEvents()


def test_jump_to_frame_does_not_disturb_inflight_warm(app):
    """在飞 warm（持锁解码中）时 jumpToFrame：不抢锁、不等待、不双解码。"""
    clip = _RecordingClip("dummy.webm")
    t = threading.Thread(target=clip.warm_first_frame, daemon=True)
    t.start()
    assert clip.decode_entered.wait(5.0), "后台 warm 必须已认领并持有锁"
    gui_tid = threading.get_ident()
    t0 = time.monotonic()
    assert clip.jumpToFrame(0) is True
    assert time.monotonic() - t0 < 0.5, "jumpToFrame 绝不被在飞 warm 卡住"
    assert gui_tid not in clip.decode_threads
    clip.decode_release.set()
    t.join(5.0)
    assert clip.decode_count == 1, "在飞 warm 与 jumpToFrame 不得双解码"
    assert clip._first_image is not None
    clip.cleanup()
    app.processEvents()


def test_played_frame_zero_populates_first_frame_cache(app):
    """播过留热：播放交付源帧 0 时顺手写入首帧缓存（warm 闸门语义来源）。"""
    clip = WebMClip("dummy.webm")
    clip._w, clip._h, clip._bpp = 2, 2, 4
    data = bytes(range(16))
    clip._process_frame((data, 0))
    assert clip._first_image is not None, "源帧 0 播放交付必须进首帧缓存"
    first = clip._first_image
    clip._process_frame((data, 0))  # 圈回绕重播同帧：幂等不重复登记
    assert clip._first_image is first
    clip.cleanup()
    app.processEvents()


def test_cached_first_frame_path_unchanged(app):
    """回归：首帧已缓存时 jumpToFrame 直接应用，零解码零等待。"""
    clip = _RecordingClip("dummy.webm")
    clip._first_image = QImage(2, 2, QImage.Format.Format_RGBA8888)
    assert clip.jumpToFrame(0) is True
    assert clip._current_pixmap is not None
    assert clip.decode_count == 0
    clip.cleanup()
    app.processEvents()
